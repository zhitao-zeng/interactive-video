"""Traceable picture-only inserts consumed by the ordinary episode assembler."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess

from novel_manga.media.results import selected_video
from novel_manga.util import atomic_write_json, read_json

FPS = 25


def _identity(path):
    stat = Path(path).stat()
    return {'path': str(Path(path).resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    video = next((row for row in data.get('streams', []) if row.get('codec_type') == 'video'), None)
    audio = next((row for row in data.get('streams', []) if row.get('codec_type') == 'audio'), None)
    if not video:
        raise ValueError(f'cutaway input has no video stream: {path}')
    duration = video.get('duration')
    if duration in (None, 'N/A'):
        duration = (data.get('format') or {}).get('duration')
    return {'video': video, 'audio': audio, 'format': data.get('format', {}),
            'video_duration': float(duration) if duration not in (None, 'N/A') else None}


def _video_frames(probe):
    video = probe['video']
    if video.get('r_frame_rate') != '25/1' or video.get('avg_frame_rate') != '25/1':
        raise ValueError('cutaway source and target must be constant 25 fps')
    if video.get('nb_frames') in (None, 'N/A'):
        raise ValueError('cutaway source and target need a probed frame count')
    return int(video['nb_frames'])


def _body_frames(probe):
    # Match the existing delivery normalizer's CFR body-frame calculation for
    # VFR and non-25-fps native providers.
    from novel_manga.interactive.delivery import cfr_body_frames
    return cfr_body_frames(probe['video'])


def _frame(seconds, label):
    frame = round(float(seconds) * FPS)
    if abs(float(seconds) - frame / FPS) > 1e-6:
        raise ValueError(f'{label} must align to a 25 fps frame boundary')
    return frame


def _clip_rows(plan, report):
    clips = {row['clip_id']: row for row in plan.get('clips', [])}
    media = {row['clip_id']: row for row in report.get('clips', [])}
    return clips, media


def _selected_path(row):
    selected = row.get('selected') or {}
    path = selected_video(selected)
    if not path or not Path(path).is_file() or selected.get('passed') is not True:
        raise ValueError(f"{row.get('clip_id')}: cutaway requires a selected, passed current video")
    return str(Path(path).resolve())


def _normalize_decision(row):
    required = ('source_clip_id', 'source_start_seconds', 'source_end_seconds',
                'target_clip_id', 'target_start_seconds', 'target_end_seconds', 'reason')
    if any(key not in row for key in required) or not str(row.get('reason') or '').strip():
        raise ValueError('cutaway needs source/target clips, exact intervals and a reason')
    source_id, target_id = row['source_clip_id'], row['target_clip_id']
    if source_id == target_id or row.get('clip_id') != target_id:
        raise ValueError('cutaway clip_id must be its distinct target clip')
    source_start = _frame(row['source_start_seconds'], 'source_start_seconds')
    source_end = _frame(row['source_end_seconds'], 'source_end_seconds')
    target_start = _frame(row['target_start_seconds'], 'target_start_seconds')
    target_end = _frame(row['target_end_seconds'], 'target_end_seconds')
    if source_end <= source_start or target_end <= target_start or source_end-source_start != target_end-target_start:
        raise ValueError('cutaway source and target intervals must have the same positive frame length')
    decision = {'clip_id':target_id, 'action':'cutaway',
                **{key:row[key] for key in required}}
    return decision


def _authorization_details(row):
    return {key:copy.deepcopy(row[key]) for key in
            ('author','video_operation','audio','preserve','scope','source_contract','source_evidence_frames')
            if key in row}


def authorize_cutaway(episode_dir, plan):
    """Register an approved small cutaway plan before the normal render/assembly pass."""
    raw = {**plan, 'clip_id':plan.get('target_clip_id')}
    decision = _normalize_decision(raw)
    path = Path(episode_dir)/'work/postproduction/cutaway.json'
    previous = read_json(path, None)
    if previous:
        if previous.get('decision') != decision:
            raise ValueError('a different picture cutaway is already registered for this episode')
        return {'authorized':True,'reused':True,'cutaway':previous}
    candidate = {'schema':'episode-picture-cutaway-v1','status':'authorized',
                 'decision':decision,'authorization':_authorization_details(plan),'bindings':[]}
    path.parent.mkdir(parents=True,exist_ok=True)
    (path.parent/'owner').write_text('User-authorized cutaway plan for the normal assembly path\n')
    atomic_write_json(path,candidate)
    return {'authorized':True,'reused':False,'cutaway':candidate}


def transfer_cutaway(source_episode, agent_episode):
    """Carry a pre-authorized edit into the isolated first-frame Agent episode."""
    source = Path(source_episode)/'work/postproduction/cutaway.json'
    candidate = read_json(source, None)
    if not candidate:
        return None
    destination = Path(agent_episode)/'work/postproduction/cutaway.json'
    current = read_json(destination, None)
    if current and current.get('decision') != candidate.get('decision'):
        raise ValueError('first-frame Agent episode has a different registered cutaway')
    if current:
        known = {row.get('binding_id') for row in current.get('bindings', [])}
        bindings = [*current.get('bindings', []), *[row for row in candidate.get('bindings', [])
                    if row.get('binding_id') not in known]]
        candidate = {**candidate, **current, 'bindings':bindings}
    destination.parent.mkdir(parents=True, exist_ok=True)
    (destination.parent/'owner').write_text('User-authorized cutaway transferred from the prepared edition\n')
    atomic_write_json(destination,candidate)
    return candidate


def current_task_summary(episode_dir):
    """Small read-only view of the already-authorized cutaway plan."""
    candidate = read_json(Path(episode_dir)/'work/postproduction/cutaway.json', None)
    if not candidate:
        return None
    decision = candidate.get('decision') or {}
    binding = (candidate.get('bindings') or [{}])[-1]
    source_binding, target_binding = binding.get('source') or {}, binding.get('target') or {}

    def endpoint(name):
        bound = source_binding if name == 'source' else target_binding
        frame_range = bound.get('frames') or {}
        return {'clip_id':decision.get(f'{name}_clip_id'),
                'range_seconds':[decision.get(f'{name}_start_seconds'),decision.get(f'{name}_end_seconds')],
                **({'range_frames':[frame_range['start'],frame_range['end']]}
                   if 'start' in frame_range and 'end' in frame_range else {})}

    return {'status':candidate.get('status'),'authorized':candidate.get('status') in {'authorized','prepared'},
        'source':endpoint('source'),'target':endpoint('target'),
        'reason':decision.get('reason'),'binding_id':binding.get('binding_id') or None,
        'plan_file':'input/postproduction-plan.json',
        'next_action':'Call finish; the host applies this registered cutaway during normal assembly.'}


def _bind_candidate(ctx, report, candidate):
    decision = candidate['decision']
    source_id, target_id = decision['source_clip_id'], decision['target_clip_id']
    source_start = _frame(decision['source_start_seconds'], 'source_start_seconds')
    source_end = _frame(decision['source_end_seconds'], 'source_end_seconds')
    target_start = _frame(decision['target_start_seconds'], 'target_start_seconds')
    target_end = _frame(decision['target_end_seconds'], 'target_end_seconds')

    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    plans, media = _clip_rows(plan, report)
    if source_id not in plans or target_id not in plans:
        raise ValueError('cutaway source and target must be current plan clips')
    if any(plans[cid].get('kind') != 'video' for cid in (source_id, target_id)):
        raise ValueError('cutaway source and target must be video clips')
    if source_id not in media or target_id not in media:
        raise ValueError('cutaway source and target need current selected takes')
    source_video, target_video = _selected_path(media[source_id]), _selected_path(media[target_id])
    source_probe, target_probe = _probe(source_video), _probe(target_video)
    source_frames, target_frames = _body_frames(source_probe), _body_frames(target_probe)
    if (source_end > source_frames or target_end > target_frames
            or source_end/FPS > source_probe['video_duration'] + .001
            or target_end/FPS > target_probe['video_duration'] + .001):
        raise ValueError('cutaway interval extends past its selected take')
    target_size = (int(target_probe['video']['width']), int(target_probe['video']['height']))
    if target_probe['audio'] is None:
        raise ValueError('cutaway target must retain its selected audio stream')

    source = {'clip_id': source_id, 'video': source_video, 'take': _identity(source_video),
                   'frames': {'start': source_start, 'end': source_end},
                   'seconds': {'start': source_start/FPS, 'end': source_end/FPS}}
    target = {'clip_id': target_id, 'video': target_video, 'take': _identity(target_video),
                   'frames': {'start': target_start, 'end': target_end},
                   'seconds': {'start': target_start/FPS, 'end': target_end/FPS},
                   'audio_source': target_video, 'audio_take': _identity(target_video)}
    previous = candidate.get('bindings') or []
    if previous and previous[-1].get('source', {}).get('take') == source['take'] and previous[-1].get('target', {}).get('take') == target['take']:
        return candidate
    binding = {'source':source,'target':target,'frame_count':source_end-source_start,
               'fps':FPS,'frame_size':list(target_size),
               'binding_id':f'binding-{len(previous)+1:03d}',
               'directory':str(ctx.episode_dir/'work/postproduction'/f'binding-{len(previous)+1:03d}')}
    return {**candidate,'status':'prepared','bindings':[*previous,binding]}


def record_cutaway(ctx, row, *, check_only=False):
    """Persist one approved source-to-target edit through the existing apply tool."""
    decision = _normalize_decision(row)
    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    plans = {clip['clip_id']:clip for clip in plan.get('clips',[])}
    if (decision['source_clip_id'] not in plans or decision['target_clip_id'] not in plans
            or any(plans[cid].get('kind') != 'video' for cid in
                   (decision['source_clip_id'],decision['target_clip_id']))):
        raise ValueError('cutaway source and target must be current video clips')
    path = ctx.episode_dir/'work/postproduction/cutaway.json'
    previous = read_json(path, None)
    candidate = previous or {'schema':'episode-picture-cutaway-v1','status':'authorized',
        'decision':decision,'authorization':_authorization_details(row),'bindings':[]}
    if candidate.get('decision') != decision:
        raise ValueError('a different picture cutaway is already registered for this episode')
    if check_only:
        return {'valid':True,'decision':decision}
    report = read_json(ctx.episode_dir/'thin_media_report.json', {})
    if not report.get('clips'):
        path.parent.mkdir(parents=True,exist_ok=True)
        (path.parent/'owner').write_text(f'User-authorized cutaway plan / {ctx.run_dir.name}\n')
        atomic_write_json(path,candidate)
        return {'prepared':True,'authorized':True,'reused':bool(previous),'cutaway':candidate}
    from novel_manga.application.review.execution import media_matches
    if not media_matches(plan,report,notes=read_json(ctx.episode_dir/'review_feedback.json',{})):
        path.parent.mkdir(parents=True,exist_ok=True)
        atomic_write_json(path,candidate)
        return {'prepared':True,'authorized':True,'bound':False,'reused':bool(previous),'cutaway':candidate}
    if report.get('assembly') and report['assembly'].get('final_video') and not report['assembly'].get('postproduction_pending'):
        raise ValueError('register the cutaway before the normal render/assembly pass to avoid repeating full-film assembly')
    candidate = _bind_candidate(ctx,report,candidate)
    path.parent.mkdir(parents=True,exist_ok=True)
    (path.parent/'owner').write_text(f'User-authorized cutaway plan / {ctx.run_dir.name}\n')
    atomic_write_json(path, candidate)
    report['status'] = 'clips_ready'
    assembly = report.setdefault('assembly', {})
    assembly['postproduction_pending'] = True
    assembly['postproduction_input'] = str(path)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', report)
    return {'prepared': True, 'authorized':True, 'reused': bool(previous), 'cutaway': candidate}


def _audio_payload(path):
    return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
        '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', 'pipe:1'])


def _validate_derived(binding, output):
    source, target = binding['source'], binding['target']
    source_now, target_now = _identity(source['video']), _identity(target['video'])
    if source_now != source['take'] or target_now != target['take']:
        raise ValueError('cutaway source/target take changed during assembly')
    source_probe, target_probe = _probe(source['video']), _probe(target['video'])
    source_frames, target_frames = _body_frames(source_probe), _body_frames(target_probe)
    start, end = target['frames']['start'], target['frames']['end']
    source_start, source_end = source['frames']['start'], source['frames']['end']
    if max(end, source_end) <= 0 or end > target_frames or source_end > source_frames:
        raise ValueError('cutaway window is outside the approved selected take')
    width, height = binding['frame_size']
    derived = _probe(output)
    frames = _video_frames(derived)
    if frames != target_frames:
        raise ValueError('derived cutaway changed the target clip frame count')
    if int(derived['video']['width']) != width or int(derived['video']['height']) != height:
        raise ValueError('derived cutaway changed the target frame size')
    if _audio_payload(target['video']) != _audio_payload(output):
        raise ValueError('derived cutaway changed the target AAC payload')
    audio = derived['audio']
    if not audio or audio.get('codec_name') != target_probe['audio'].get('codec_name'):
        raise ValueError('derived cutaway changed the selected target audio codec')
    return {'derived_video':str(Path(output).resolve()),'derived_take':_identity(output),
            'audio_copy_verified':True,'derived_frames':frames}


def _derive(candidate, binding, folder):
    source, target = binding['source'], binding['target']
    start, end = target['frames']['start'], target['frames']['end']
    source_start, source_end = source['frames']['start'], source['frames']['end']
    width, height = binding['frame_size']
    source_probe, target_probe = _probe(source['video']), _probe(target['video'])
    target_frames = _body_frames(target_probe)
    output = folder/'target-picture-cutaway.mp4'
    partial = folder/'target-picture-cutaway.partial.mp4'
    graph = (
        f'[0:v]setpts=PTS-STARTPTS,fps=25:round=up,tpad=stop_mode=clone:stop_duration=0.04,'
        f'trim=end_frame={target_frames},setpts=PTS-STARTPTS,split=2[before_in][after_in];'
        f'[before_in]trim=start_frame=0:end_frame={start},setpts=PTS-STARTPTS,'
        f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
        'setsar=1,format=yuv420p[before];'
        f'[1:v]setpts=PTS-STARTPTS,fps=25:round=up,trim=start_frame={source_start}:end_frame={source_end},setpts=PTS-STARTPTS,'
        f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
        'setsar=1,format=yuv420p[insert];'
        f'[after_in]trim=start_frame={end}:end_frame={target_frames},setpts=PTS-STARTPTS,'
        f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
        'setsar=1,format=yuv420p[after];'
        '[before][insert][after]concat=n=3:v=1:a=0[v]')
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '4',
        '-i', target['video'], '-i', source['video'], '-filter_complex', graph,
        '-map', '[v]', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-r', '25', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', str(partial)]
    subprocess.run(command, check=True, capture_output=True)
    validation = _validate_derived(binding,partial)
    os.replace(partial,output)
    trace = {'status':'derived','binding_id':binding['binding_id'],
        'decision':candidate['decision'],'authorization':candidate.get('authorization',{}),
        'source':source,'target':target,'frame_count':binding['frame_count'],
        'fps':binding['fps'],'frame_size':binding['frame_size'],
        **validation,'derived_video':str(output.resolve()),
        'derived_take':_identity(output),'command':command}
    atomic_write_json(folder/'receipt.json', trace)
    return trace


def assembly_inputs(ctx, report):
    """Return ephemeral assembler rows; durable selected takes remain untouched."""
    path = ctx.episode_dir/'work/postproduction/cutaway.json'
    candidate = read_json(path, None)
    if not candidate:
        return report['clips'], []
    candidate = _bind_candidate(ctx,report,candidate)
    atomic_write_json(path,candidate)
    binding = candidate['bindings'][-1]
    binding_dir = Path(binding['directory'])
    attempts = binding_dir/'attempts'
    attempts.mkdir(parents=True,exist_ok=True)
    trace = None
    for folder in sorted(attempts.glob('attempt-*')):
        output, receipt = folder/'target-picture-cutaway.mp4', read_json(folder/'receipt.json',None)
        if receipt and receipt.get('binding_id') == binding['binding_id'] and output.is_file():
            if receipt.get('derived_take') == _identity(output):
                trace = receipt
                break
        elif output.is_file():
            try:
                validation = _validate_derived(binding,output)
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                atomic_write_json(folder/'verification-error.json',{'error':f'{type(error).__name__}: {error}'})
            else:
                trace = {'status':'derived','binding_id':binding['binding_id'],
                    'decision':candidate['decision'],'authorization':candidate.get('authorization',{}),
                    'source':binding['source'],'target':binding['target'],
                    'frame_count':binding['frame_count'],'fps':binding['fps'],
                    'frame_size':binding['frame_size'],**validation,
                    'derived_video':str(output.resolve()),'derived_take':_identity(output),
                    'recovered_unreceipted_output':True}
                atomic_write_json(folder/'receipt.json',trace)
                break
        partial = folder/'target-picture-cutaway.partial.mp4'
        if trace is None and partial.is_file():
            try:
                validation = _validate_derived(binding, partial)
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                atomic_write_json(folder/'partial-verification-error.json',{'error':f'{type(error).__name__}: {error}'})
            else:
                os.replace(partial, output)
                trace = {'status':'derived','binding_id':binding['binding_id'],
                    'decision':candidate['decision'],'authorization':candidate.get('authorization',{}),
                    'source':binding['source'],'target':binding['target'],
                    'frame_count':binding['frame_count'],'fps':binding['fps'],
                    'frame_size':binding['frame_size'],**validation,
                    'derived_video':str(output.resolve()),'derived_take':_identity(output),
                    'recovered_partial_output':True}
                atomic_write_json(folder/'receipt.json',trace)
                break
    if trace is None:
        number = len(list(attempts.glob('attempt-*'))) + 1
        folder = attempts/f'attempt-{number:03d}'
        folder.mkdir(parents=True)
        (folder/'owner').write_text('Temporary picture-only target clip for the normal episode assembler\n')
        try:
            trace = _derive(candidate,binding,folder)
        except Exception as error:
            atomic_write_json(folder/'failure.json',{'error':f'{type(error).__name__}: {error}'})
            raise
    rows = copy.deepcopy(report['clips'])
    target = next(row for row in rows if row['clip_id'] == binding['target']['clip_id'])
    selected = target.get('selected') or {}
    original_audio_video = selected_video(selected)
    if original_audio_video:
        target.setdefault('selected', {})['native_audio_path'] = selected.get('native_audio_path') or str(Path(original_audio_video).parent/'native.wav')
    target.setdefault('selected', {})['video'] = trace['derived_video']
    if 'postmix_video' in target['selected']:
        target['selected']['postmix_video'] = trace['derived_video']
    assembly_trace = {key: trace[key] for key in ('binding_id','decision','authorization','source','target',
        'frame_count','fps','frame_size','derived_video','derived_take','audio_copy_verified')}
    return rows, [assembly_trace]
