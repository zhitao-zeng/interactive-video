"""Editable SDK examples. Run: python tools/media.py request.json (from workspace).

No production stages or judgment: explicit files in, real task/output receipts out.
"""
import base64
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

WORK = Path(os.environ.get('PI_RUN_DIR', Path(__file__).resolve().parents[1])).resolve()
CONFIG = json.loads((WORK / 'local.json').read_text())
ROOT = Path(CONFIG['sdk_root'])
sys.path.insert(0, str(ROOT / 'src'))
from novel_manga.util import atomic_write_json, load_dotenv, read_json
load_dotenv(ROOT / '.env')
from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from PIL import Image


def file(name):
    return (WORK / name).resolve()


def audio_service_url():
    try:
        config = json.loads((WORK / 'local.json').read_text())
    except (OSError, ValueError):
        config = {}
    return (os.environ.get('AUDIO_SERVICE_URL') or config.get('audio_service_url') or '').rstrip('/')


def references(prompt, paths, purposes=()):
    refs = [file(name) for name in paths]
    for p in refs:
        with Image.open(p) as image:
            image.load()
    numbers = [int(next(v for v in match if v)) for match in re.findall(
        r'(?i)\b(?:Picture|Image)\s*(\d+)\b|@?图片\s*(\d+)', prompt)]
    invalid = sorted({n for n in numbers if not 1 <= n <= len(refs)})
    if invalid:
        raise ValueError(f'Unbound picture numbers {invalid}; actual ordered files: {paths}')
    table = '\n'.join(f'Picture {i+1} / @图片{i+1}: {p.name}; '
                      f'{purposes[i] if i < len(purposes) else "author-selected reference"}'
                      for i, p in enumerate(refs))
    return f'Actual attached images in order (metadata, not rendered text):\n{table or "None"}\n\n{prompt}', refs


def settings():
    return Settings(provider='phanrouter', output_root=WORK / 'media',
        phanrouter_base_url=os.getenv('PHANROUTER_BASE_URL', Settings.phanrouter_base_url),
        phanrouter_api_key=os.getenv('PHANROUTER_IMAGE_API_KEY'),
        phanrouter_image_api_key=os.getenv('PHANROUTER_IMAGE_API_KEY'),
        image_model='gpt-image-2', card_fallback_image_model='',
        video_model=(CONFIG.get('media_lane') if str(CONFIG.get('media_lane','')).startswith('sd2.0') else 'sd2.0-fast'),
        inline_reference_images=False, reference_images_via_assets=True,
        phanrouter_asset_group_id=os.getenv('PHANROUTER_ASSET_GROUP_ID'),
        phanrouter_asset_public_base=os.getenv('PHANROUTER_ASSET_PUBLIC_BASE'),
        request_timeout=float(os.getenv('NOVEL_REQUEST_TIMEOUT', '180')),
        poll_timeout=float(os.getenv('NOVEL_POLL_TIMEOUT', '900')))


def admission():
    """The original wall deadline also applies to new media/ASR submissions."""
    deadline = os.getenv('PI_DEADLINE_EPOCH')
    if deadline and time.time() >= float(deadline):
        raise TimeoutError('Original run deadline reached; query/download existing tasks only.')


def recorded_request(output, request):
    record = output.with_suffix(output.suffix + '.request.json')
    if record.exists() and read_json(record) != request:
        raise ValueError('Output already belongs to different inputs. Keep it and use another output filename.')
    if not record.exists():
        atomic_write_json(record, request)


def asset_reference(provider, source):
    """Persist CreateAsset uncertainty; never ask the SDK to rename and retry implicitly."""
    marker = source.with_suffix(source.suffix + '.submit.json')
    sidecar = source.with_suffix(source.suffix + '.asset.json')
    input_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    saved = read_json(marker, {})
    if saved:
        if saved.get('input_sha256') != input_hash:
            raise ValueError('Asset submission input changed; use a new source filename.')
        if saved.get('status') != 'completed':
            raise RuntimeError('Asset submission is uncertain; reconcile the retained request before any retry.')
        uri = saved.get('asset_uri')
        receipt = read_json(sidecar, {})
        if not isinstance(uri, str) or uri != 'asset://' + str(receipt.get('asset_id', '')):
            raise ValueError('Asset completion receipt is missing or inconsistent.')
        return uri
    atomic_write_json(marker, {'status': 'submitting', 'input_sha256': input_hash,
                               'source': source.name})
    try:
        uri = provider._references()._asset_reference(ImageResult(path=source))
        receipt = read_json(sidecar, {})
        if not isinstance(uri, str) or uri != 'asset://' + str(receipt.get('asset_id', '')):
            raise ValueError('Asset service returned no matching asset receipt.')
        atomic_write_json(marker, {'status': 'completed', 'input_sha256': input_hash,
                                   'source': source.name, 'asset_uri': uri})
        return uri
    except Exception:
        if read_json(marker, {}).get('status') != 'completed':
            atomic_write_json(marker, {'status': 'submission_uncertain',
                                       'input_sha256': input_hash, 'source': source.name})
        raise


def record_submission(client, output, refs, kind, first_frame=False, audio_refs=()):
    """Stop hidden second POSTs; preserve uncertainty even if legacy SDK removes its task file."""
    original = client.post
    folder = output.with_suffix(output.suffix + '.wire')
    def post(url, *args, **kwargs):
        endpoint = str(url).split('?', 1)[0].rstrip('/')
        if not endpoint.endswith(('/v3/images/generations', '/api/v3/contents/generations/tasks', '/v1/videos')):
            return original(url, *args, **kwargs)
        if (folder / 'request.json').exists():
            raise RuntimeError('Already submitted once for this output. Query the retained task; do not resubmit an unknown or failed task implicitly. A deliberate new take needs a new filename and available task budget.')
        admission()
        payload = kwargs['json']
        if kind == 'image':
            images = payload.get('base64Files') or ([payload['base64File']] if payload.get('base64File') else [])
            roles = ['reference'] * len(images)
        elif kind in {'h3', 'fasth3'}:
            images = [x['uri'] for x in payload['conditions'] if x['type'] == 'image' and x['role'] == 'reference']
            roles = [x['role'] for x in payload['conditions'] if x['type'] == 'image']
            if kind == 'fasth3' and any(role != 'reference' for role in roles):
                raise ValueError('FastH3-Omni accepts ordinary references only, not keyframes.')
            if first_frame and not any(x.get('frame_index') == 0 and x.get('role') == 'keyframe'
                                       and x.get('uri') == images[0] for x in payload['conditions']):
                raise ValueError('Actual H3 payload is missing its requested zero keyframe.')
        else:
            parts = [x for x in payload['content'] if x['type'] == 'image_url']
            images = [x['image_url']['url'] for x in parts]
            roles = [x.get('role') for x in parts]
            if roles != (['first_frame'] if first_frame else ['reference_image'] * len(refs)):
                raise ValueError('Actual SD attachment roles differ from requested conditioning.')
        if len(images) != len(refs):
            raise ValueError('Actual attachment count differs from requested references.')
        actual_audios = ([x['uri'] for x in payload.get('conditions', []) if x.get('type') == 'audio']
                         if kind in {'h3', 'fasth3'} else [x['audio_url']['url'] for x in payload.get('content', []) if x.get('type') == 'audio_url'])
        if len(actual_audios) != len(audio_refs):
            raise ValueError('Actual audio attachments differ from requested reference audio files.')
        folder.mkdir(parents=True, exist_ok=True)
        attached = []
        for index, (value, source) in enumerate(zip(images, refs), 1):
            entry = {'number': index, 'source_path': str(source)}
            if value.startswith('asset://'):
                receipt = read_json(source.with_suffix(source.suffix + '.asset.json'), {})
                if value != 'asset://' + receipt.get('asset_id', '') or receipt.get('status') != 'Active':
                    raise ValueError(f'No matching Active asset receipt for {source}')
                entry.update(asset_uri=value, status='Active')
            else:
                raw = base64.b64decode(value.split(',', 1)[-1], validate=True)
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    entry.update(dimensions=list(image.size), format=image.format)
                uploaded = folder / f'attachment-{index:02d}.image'
                uploaded.write_bytes(raw)
                entry['uploaded_file'] = str(uploaded)
            attached.append(entry)
        attached_audio = []
        for i, (value, source) in enumerate(zip(actual_audios, audio_refs), 1):
            raw = base64.b64decode(value.split(',', 1)[-1], validate=True)
            if raw != Path(source).read_bytes():
                raise ValueError('Actual reference audio bytes differ from the requested file.')
            target = folder / f'audio-{i:02d}{Path(source).suffix}'
            target.write_bytes(raw)
            attached_audio.append({'number': i, 'source_path': str(source), 'uploaded_file': str(target)})
        # No headers, bearer tokens, or authenticated URLs are logged.
        atomic_write_json(folder / 'request.json', {'kind': kind, 'model': payload.get('model'),
            'prompt': payload.get('prompt') or next((x.get('text') for x in payload.get('content', []) if x.get('type') == 'text'), ''),
            'attachments': attached, 'image_roles': roles, 'first_frame_bound': bool(first_frame),
            'audio_attachments': attached_audio,
            'duration': payload.get('duration', payload.get('seconds', payload.get('target', {}).get('duration_seconds'))),
            **({'target': payload.get('target'), 'seed': payload.get('seed'), 'endpoint': endpoint,
                'request_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()}
               if kind == 'fasth3' else {})})
        response = original(url, *args, **kwargs)
        # Task id is kept independently of the SDK's disposable polling sidecar.
        try:
            body = response.json()
            data = body.get('data') if isinstance(body.get('data'), dict) else body
            atomic_write_json(folder / 'response.json', {'http_status': response.status_code,
                'task_id': data.get('task_id') or data.get('id') or body.get('task_id') or body.get('id')})
        except ValueError:
            atomic_write_json(folder / 'response.json', {'http_status': response.status_code})
        return response
    client.post = post


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def perceive_audio(request):
    source = file(request['path'])
    folder = file(request.get('output_dir', str(source) + '.audio'))
    folder.mkdir(parents=True, exist_ok=True)
    runtime = read_json(WORK / 'audio-runtime.json')
    stat = source.stat()
    recorded_request(folder / 'audio', {'request': request,
        'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
        'model': runtime.get('audio_model_dir'), 'max_new_tokens': runtime.get('max_new_tokens', 256)})
    jobs = []
    for i, window in enumerate(request['windows']):
        start, end = float(window['start']), float(window['end'])
        if not 0 <= start < end:
            raise ValueError('Audio window must have 0 <= start < end.')
        audio, output = folder / f'{i}.wav', folder / f'{i}.json'
        if not audio.exists() and not output.exists():
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start),
                '-i', str(source), '-t', str(end-start), '-vn', '-ac', '1', '-ar', '16000', str(audio)], check=True)
        jobs.append({'audio': str(audio), 'output': str(output), 'source_window': window})
    if not jobs:
        raise ValueError('Supply at least one audio window.')
    def observations():
        return {'observations': [read_json(Path(row['output'])) for row in jobs],
                'note': 'Perception of these actual windows, not human listening or a pass/fail gate.'}
    if all(Path(row['output']).is_file() for row in jobs):
        return observations()
    service = audio_service_url()
    receipt = folder / 'service-job.json'
    if receipt.exists():
        record = read_json(receipt)
        service = record['service']
    elif service:
        record = {'job_id': uuid.uuid4().hex, 'service': service, 'jobs': jobs,
                  'runtime': runtime, 'created_at': time.time(), 'post_attempted': False}
        atomic_write_json(receipt, record)
    if service:
        def submit():
            admission()
            record['post_attempted'] = True
            atomic_write_json(receipt, record)  # caller id survives a lost response
            body = json.dumps({k: record[k] for k in ('job_id','jobs','runtime')}).encode()
            req = urllib.request.Request(service+'/audio', data=body, headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read())
            if data.get('job_id') != record['job_id']:
                raise RuntimeError('Audio service did not preserve the caller job id; do not resubmit.')
        if not record['post_attempted']:
            try:
                submit()
            except (urllib.error.URLError, TimeoutError):
                # No fallback: the service may already be computing this job.
                raise
        deadline = record['created_at'] + float(os.getenv('AUDIO_SERVICE_TIMEOUT', '3600'))
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(service+'/job/'+record['job_id'], timeout=30) as response:
                    state = json.loads(response.read())
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                submit()  # known absent/restarted service; same id is idempotent
                continue
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2)
                continue
            if state.get('status') == 'done':
                return observations()
            if state.get('status') == 'error':
                raise RuntimeError('Audio service job failed; retained '+str(receipt)+': '+str(state.get('error')))
            time.sleep(2)
        raise TimeoutError('Audio service deadline reached; retained '+str(receipt))
    admission()
    job = folder / 'job.json'
    atomic_write_json(job, {'runtime': runtime, 'jobs': [row for row in jobs if not Path(row['output']).exists()]})
    subprocess.run([runtime['python'], str(Path(__file__).with_name('audio_worker.py')), str(job)], check=True)
    return observations()


def execute(request):
    action = request['action']
    lane = CONFIG.get('media_lane') or ''
    first_frame_policy = CONFIG.get('first_frame_policy') or 'selective'
    # A role may narrow which actions this entry accepts (a reviewer keeps inspect/audio only).
    # Absent key means unrestricted, so producer workspaces are unchanged.
    allowed_actions = CONFIG.get('media_actions')
    if allowed_actions is not None and action not in allowed_actions:
        raise ValueError(f'media_actions={allowed_actions} for this role; action={action} is not permitted.')
    if lane.startswith('sd2.0') and action in {'h3', 'fasth3'}:
        raise ValueError(f'Pinned video lane is {lane}; action={action} is disabled. '
                         'Use action=video or change media_lane explicitly in local.json.')
    if lane == 'MiniMax-H3' and action in {'video', 'fasth3'}:
        raise ValueError('Pinned video lane is MiniMax-H3; action=video is disabled. '
                         'Use action=h3 or change media_lane explicitly in local.json.')
    if lane == 'FastH3-Omni' and action in {'video', 'h3'}:
        raise ValueError('Pinned video lane is FastH3-Omni; use action=fasth3 or change media_lane explicitly in local.json.')
    if action == 'fasth3' and request.get('first_frame'):
        raise ValueError('FastH3-Omni accepts ordinary references only; explicit first_frame is not supported.')
    if action == 'fasth3' and first_frame_policy == 'required':
        raise ValueError('first_frame_policy=required is incompatible with FastH3-Omni; selective does not verify a keyframe.')
    if first_frame_policy == 'none' and request.get('first_frame'):
        raise ValueError('first_frame_policy=none: first frames are disabled for this production.')
    if first_frame_policy == 'required' and action in {'video', 'h3'} and not request.get('first_frame'):
        raise ValueError('first_frame_policy=required: every video/h3 request must provide first_frame.')
    if action == 'music':
        # Search the licensed local catalogue; candidates are unreviewed and adoption stays explicit.
        from novel_manga.application.music_selection import search
        request = dict(request)
        wanted = request.pop('series_scene', '')
        limit = int(request.get('limit', 3))
        if wanted:
            request['limit'] = min(10, max(limit, limit * 6))
        result = search(ROOT, request, file(request.get('output_dir', 'music-searches')))
        if wanted:
            candidates = [c for c in result.get('candidates', []) if c.get('scene') == wanted][:limit]
            result['candidates'] = candidates
            result['returned_count'] = len(candidates)
            result['series_scene'] = wanted
        return result
    if action == 'inspect':
        source = file(request['path'])
        metadata = probe(source)
        duration = float(metadata['format']['duration'])
        folder = file(request.get('output_dir', str(source) + '.frames'))
        folder.mkdir(parents=True, exist_ok=True)
        max_side = int(request.get('max_side', 512))
        frames, thumbs = [], []
        for i, t in enumerate(request.get('times', [duration * .1, duration * .5, duration * .9])):
            if not 0 <= t < duration:
                raise ValueError(f'Timestamp {t} is outside {duration}s')
            path = folder / f'{i:03d}-{t:.3f}.jpg'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(t), '-i', str(source),
                            '-frames:v', '1', '-y', str(path)], check=True)
            with Image.open(path) as image:
                image = image.convert('RGB')
                if max(image.size) > max_side:
                    scale = max_side / max(image.size)
                    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
                image.save(path, 'JPEG', quality=82)
                thumb = image.copy()
            if max(thumb.size) > 320:
                scale = 320 / max(thumb.size)
                thumb = thumb.resize((round(thumb.width * scale), round(thumb.height * scale)), Image.LANCZOS)
            thumbs.append(thumb)
            frames.append({'seconds': t, 'path': str(path)})
        contact = folder / 'contact-sheet.jpg'
        if thumbs:
            cols = min(3, len(thumbs))
            rows = (len(thumbs) + cols - 1) // cols
            cell_w = max(t.width for t in thumbs)
            cell_h = max(t.height for t in thumbs)
            sheet = Image.new('RGB', (cols * cell_w, rows * cell_h), (18, 18, 18))
            for index, thumb in enumerate(thumbs):
                sheet.paste(thumb, ((index % cols) * cell_w, (index // cols) * cell_h))
            sheet.save(contact, 'JPEG', quality=82)
        return {'frames': frames, 'contact_sheet': str(contact) if thumbs else None,
                'streams': metadata['streams'], 'duration': duration,
                'note': 'Read the contact sheet first; open a single frame only when a detail matters. '
                        'Large image reads enlarge later provider requests; frames cannot establish audio.'}
    if action == 'audio':
        return perceive_audio(request)
    sd_resolution = '480p' if lane == 'sd2.0-mini' else '720p'
    provider = PhanRouterMediaProvider(settings(), ratio=request.get('ratio', '9:16'), resolution=sd_resolution)
    try:
        if action == 'upload':
            admission()
            from novel_manga.providers.phanrouter_tasks import task_data
            source = file(request['path'])
            with Image.open(source) as image:
                image.load()
            uri = asset_reference(provider, source)
            sidecar = source.with_suffix(source.suffix + '.asset.json')
            receipt = read_json(sidecar)
            fresh = provider.client.post(provider._references()._asset_base_url() + '/open/GetAsset',
                headers=provider.image_headers, json={'Id': receipt['asset_id']})
            fresh.raise_for_status()
            receipt['status'] = str(task_data(fresh.json()).get('Status') or '')
            if not receipt['status']:
                raise ValueError('Asset service returned no current status.')
            atomic_write_json(sidecar, receipt)
            provider._references()._await_active(sidecar, receipt)
            return {'path': str(source), 'asset_uri': uri,
                    'status': read_json(sidecar)['status'], 'receipt_file': str(sidecar)}
        original_prompt = request['prompt']
        prompt, refs = references(original_prompt, request.get('references', []), request.get('purposes', []))
        audio_names = request.get('reference_audios', request.get('audios', []))
        audios = tuple(file(name) for name in audio_names)
        for audio in audios:
            if not audio.is_file():
                raise FileNotFoundError(audio)
        if action == 'video' and audios:
            if sum(float(probe(audio)['format']['duration']) for audio in audios) > 15.2:
                raise ValueError('SD reference audio total must be <=15.2s before submission.')
        first = file(request['first_frame']) if request.get('first_frame') else None
        if first:
            prompt = 'Picture 1 is the actual opening image at t=0. Continue its state.\n' + prompt
        if first and (not refs or refs[0] != first):
            raise ValueError('The first_frame must be the first explicitly supplied reference file.')
        if action == 'video' and first and (len(refs) != 1 or audios):
            raise ValueError('Current SD first-frame SDK accepts exactly one image. Use references=[first_frame].')
        output = file(request['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        if action == 'fasth3':
            from novel_manga.providers.fast_h3 import normalize_endpoint
            frozen_endpoint = normalize_endpoint(CONFIG.get('fasth3_endpoint') or '')
            fasth3_endpoint = normalize_endpoint(request.get('endpoint') or frozen_endpoint)
            if fasth3_endpoint != frozen_endpoint:
                raise ValueError('FastH3 request endpoint differs from the frozen local.json endpoint; use a new workspace for a different service.')
        recorded_request(output, request)
        if output.exists() and action != 'fasth3':
            raise ValueError('Output already exists. Inspect it or choose a new filename; no cached output is silently returned.')
        if output.exists() and action == 'fasth3' and not output.with_suffix(output.suffix + '.task.json').is_file():
            raise ValueError('FastH3 output exists without a task receipt. Inspect it or choose a new filename.')
        if action == 'image':
            record_submission(provider.client, output, refs, 'image')
            provider.create_image(prompt, output, refs[0] if refs else None, tuple(refs[1:]), aspect_ratio=request.get('ratio', '9:16'))
        elif action in {'video', 'h3', 'fasth3'}:
            if action == 'fasth3':
                from novel_manga.providers.fast_h3 import FastH3MediaProvider
                fast = FastH3MediaProvider(settings(), fasth3_endpoint, ratio=request.get('ratio', '9:16'))
                try:
                    record_submission(fast.fast_client, output, refs, 'fasth3', False, audios)
                    fast.create_video(original_prompt, None, output, request['seconds'],
                        additional_images=tuple(refs), reference_audios=audios,
                        seed=request.get('seed'), short_edge=int(request.get('short_edge', 768)))
                finally:
                    fast.close()
            elif action == 'h3':
                if CONFIG.get('h3_mode') == 'cloud':
                    cloud = replace(settings(), video_model='MiniMax-H3',
                        phanrouter_api_key=os.getenv('PHANROUTER_H3CLOUD_API_KEY'),
                        inline_reference_images=True, reference_images_via_assets=False,
                        phanrouter_asset_public_base=None)
                    cloud_provider = PhanRouterMediaProvider(
                        cloud, ratio=request.get('ratio', '9:16'), resolution='720p')
                    try:
                        record_submission(cloud_provider.client, output, refs, 'video', False, audios)
                        cloud_provider.create_video(prompt, None, output, request['seconds'],
                            additional_images=tuple(refs), reference_audios=audios)
                    finally:
                        cloud_provider.client.close()
                else:
                    from novel_manga.providers.local_h3 import LocalH3MediaProvider
                    endpoint = request['endpoint']
                    if endpoint.startswith('pool') and CONFIG.get('h3_pool_config'):
                        endpoint = 'pool:' + CONFIG['h3_pool_config']
                    local = LocalH3MediaProvider(replace(settings(), video_model='MiniMax-H3'), endpoint, ratio=request.get('ratio', '9:16'))
                    try:
                        record_submission(local.h3_client, output, refs, 'h3', bool(first), audios)
                        local.create_video(prompt, None, output, request['seconds'], additional_images=tuple(refs), reference_audios=audios, first_frame=first)
                    finally:
                        local.h3_client.close()
                        local.client.close()
            else:
                record_submission(provider.client, output, refs, 'video', bool(first), audios)
                provider.create_video(prompt, None, output, request['seconds'], additional_images=tuple(refs), reference_audios=audios, **({'first_frame': first} if first else {}))
        else:
            raise ValueError(f'Unknown action: {action}')
        return {'path': str(output), 'first_frame_bound': bool(first),
                'reference_order': [str(p) for p in refs],
                'receipt': str(output.with_suffix(output.suffix + '.task.json')),
                'wire_directory': str(output.with_suffix(output.suffix + '.wire'))}
    finally:
        provider.client.close()


if __name__ == '__main__':
    print(json.dumps(execute(json.loads(Path(sys.argv[1]).read_text())), ensure_ascii=False, indent=2))
