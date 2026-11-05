"""Editable SDK examples. Run: python tools/media.py request.json (from workspace).

No production stages or judgment: explicit files in, real task/output receipts out.
"""
import base64
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

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
        image_model='gpt-image-2', card_fallback_image_model='', video_model='sd2.0-fast',
        inline_reference_images=False, reference_images_via_assets=True,
        phanrouter_asset_group_id=os.getenv('PHANROUTER_ASSET_GROUP_ID'),
        phanrouter_asset_public_base=os.getenv('PHANROUTER_ASSET_PUBLIC_BASE'),
        request_timeout=float(os.getenv('NOVEL_REQUEST_TIMEOUT', '180')),
        poll_timeout=float(os.getenv('NOVEL_POLL_TIMEOUT', '900')))


def recorded_request(output, request):
    record = output.with_suffix(output.suffix + '.request.json')
    if record.exists() and read_json(record) != request:
        raise ValueError('Output already belongs to different inputs. Keep it and use another output filename.')
    if not record.exists():
        atomic_write_json(record, request)


def record_submission(client, output, refs, kind, first_frame=False):
    """Stop hidden second POSTs; preserve uncertainty even if legacy SDK removes its task file."""
    original = client.post
    folder = output.with_suffix(output.suffix + '.wire')
    def post(url, *args, **kwargs):
        endpoint = str(url).split('?', 1)[0].rstrip('/')
        if not endpoint.endswith(('/v3/images/generations', '/api/v3/contents/generations/tasks', '/v1/videos')):
            return original(url, *args, **kwargs)
        if (folder / 'request.json').exists():
            raise RuntimeError('Already submitted once for this output. Query the retained task; do not resubmit an unknown or failed task implicitly. A deliberate new take needs a new filename and available task budget.')
        payload = kwargs['json']
        if kind == 'image':
            images = payload.get('base64Files') or ([payload['base64File']] if payload.get('base64File') else [])
            roles = ['reference'] * len(images)
        elif kind == 'h3':
            images = [x['uri'] for x in payload['conditions'] if x['type'] == 'image' and x['role'] == 'reference']
            roles = [x['role'] for x in payload['conditions'] if x['type'] == 'image']
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
        # No headers, bearer tokens, or authenticated URLs are logged.
        atomic_write_json(folder / 'request.json', {'kind': kind, 'model': payload.get('model'),
            'prompt': payload.get('prompt') or next((x.get('text') for x in payload.get('content', []) if x.get('type') == 'text'), ''),
            'attachments': attached, 'image_roles': roles, 'first_frame_bound': bool(first_frame),
            'duration': payload.get('duration', payload.get('seconds'))})
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


def execute(request):
    action = request['action']
    if action == 'inspect':
        source = file(request['path'])
        metadata = probe(source)
        duration = float(metadata['format']['duration'])
        folder = file(request.get('output_dir', str(source) + '.frames'))
        folder.mkdir(parents=True, exist_ok=True)
        frames = []
        for i, t in enumerate(request.get('times', [duration * .1, duration * .5, duration * .9])):
            if not 0 <= t < duration:
                raise ValueError(f'Timestamp {t} is outside {duration}s')
            path = folder / f'{i:03d}-{t:.3f}.jpg'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(t), '-i', str(source),
                            '-frames:v', '1', '-y', str(path)], check=True)
            frames.append({'seconds': t, 'path': str(path)})
        return {'frames': frames, 'streams': metadata['streams'], 'duration': duration,
                'note': 'Read these actual image paths with native read; frames cannot establish audio.'}
    if action == 'audio':
        runtime = read_json(WORK / 'audio-runtime.json')
        # This example takes explicit windows. The Agent chooses sampling or full coverage.
        folder = file(request.get('output_dir', str(file(request['path'])) + '.audio'))
        folder.mkdir(parents=True, exist_ok=True)
        jobs = []
        for i, window in enumerate(request['windows']):
            audio, output = folder / f'{i}.wav', folder / f'{i}.json'
            if output.exists() or audio.exists():
                raise ValueError('Audio evidence directory exists: use a new directory when rechecking edited media.')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(window['start']),
                            '-i', str(file(request['path'])), '-t', str(window['end']-window['start']),
                            '-vn', '-ac', '1', '-ar', '16000', str(audio)], check=True)
            jobs.append({'audio': str(audio), 'output': str(output), 'source_window': window})
        job = folder / 'job.json'
        atomic_write_json(job, {'runtime': runtime, 'jobs': jobs})
        subprocess.run([runtime['python'], str(Path(__file__).with_name('audio_worker.py')), str(job)], check=True)
        return {'observations': [read_json(Path(row['output'])) for row in jobs],
                'note': 'Perception evidence for requested windows, not a pass/fail gate or human listening.'}
    provider = PhanRouterMediaProvider(settings(), ratio=request.get('ratio', '9:16'), resolution='720p')
    try:
        if action == 'upload':
            from novel_manga.providers.phanrouter_tasks import task_data
            source = file(request['path'])
            with Image.open(source) as image:
                image.load()
            uri = provider._references()._asset_reference(ImageResult(path=source))
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
        prompt, refs = references(request['prompt'], request.get('references', []), request.get('purposes', []))
        first = file(request['first_frame']) if request.get('first_frame') else None
        if first:
            prompt = 'Picture 1 is the actual opening image at t=0. Continue its state.\n' + prompt
        if first and (not refs or refs[0] != first):
            raise ValueError('The first_frame must be the first explicitly supplied reference file.')
        if action == 'video' and first and len(refs) != 1:
            raise ValueError('Current SD first-frame SDK accepts exactly one image. Use references=[first_frame].')
        output = file(request['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        recorded_request(output, request)
        if output.exists():
            raise ValueError('Output already exists. Inspect it or choose a new filename; no cached output is silently returned.')
        if action == 'image':
            record_submission(provider.client, output, refs, 'image')
            provider.create_image(prompt, output, refs[0] if refs else None, tuple(refs[1:]), aspect_ratio=request.get('ratio', '9:16'))
        elif action in {'video', 'h3'}:
            if action == 'h3':
                from novel_manga.providers.local_h3 import LocalH3MediaProvider
                local = LocalH3MediaProvider(replace(settings(), video_model='MiniMax-H3'), request['endpoint'], ratio=request.get('ratio', '9:16'))
                try:
                    record_submission(local.h3_client, output, refs, 'h3', bool(first))
                    local.create_video(prompt, None, output, request['seconds'], additional_images=tuple(refs), first_frame=first)
                finally:
                    local.h3_client.close()
                    local.client.close()
            else:
                record_submission(provider.client, output, refs, 'video', bool(first))
                provider.create_video(prompt, None, output, request['seconds'], additional_images=tuple(refs), **({'first_frame': first} if first else {}))
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
