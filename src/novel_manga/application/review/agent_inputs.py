"""Formal review uses the same source/request/frame preparation as repair."""
from __future__ import annotations

import json
import copy
from pathlib import Path

from novel_manga.application.repair.agent import prepare_inputs, stage_video_tools
from novel_manga.application.repair.history import generation_counts, generation_limit
from novel_manga.application.repair.history import accepted_clip_material
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json, atomic_write_json


def prepare(directory, targets, contexts, work, config, video_overrides=None):
    directory, work = Path(directory), Path(work)
    clips = {c['clip_id']: c for c in read_json(directory/'clip_plan.json', {})['clips'] if c.get('kind') == 'video'}
    counts = generation_counts(directory)
    primary = {'phase': 'review', 'target_clips': targets, 'context_clips': contexts,
               'clips': {}, 'audio_scope': 'ASR text only; no hearing audit',
               'generation_allowed': False, 'reading_scope': 'Current takes first; extra sources only for a specific fact.'}
    takes = []
    for cid in dict.fromkeys([*targets, *contexts]):
        original, evidence = prepare_inputs(directory, clips[cid], {}, work,
            namespace=cid, include_history=False, video_override=(video_overrides or {}).get(cid))
        current = next((t for t in evidence['takes'] if t['current']), None)
        if current is None:
            raise ValueError(f'{cid}: no readable selected take')
        video = read_json(work/current['path']/'frames/frame-evidence.json', {})['source_video']
        primary['clips'][cid] = {**original, 'current_take': str(Path(current['path']).relative_to('input')),
             'current_video': video, 'take': take_identity(Path(video)),
             'clip_snapshot': accepted_clip_material(clips[cid]),
             'generation_count': counts.get(cid, 0), 'generation_limit': generation_limit(directory, cid),
             'remaining_video_takes': max(0, generation_limit(directory, cid)-counts.get(cid, 0)),
             'evidence_file': f'input/{cid}/evidence.json'}
        takes.extend(evidence['takes'])
    atomic_write_json(work/'input/evidence.json', {'takes': takes})
    stage_video_tools(work, config)
    add_boundary_frames(work, primary)
    write_primary(work, primary)
    return primary


def write_primary(work, primary):
    """Index all targets; read complete source/requests one target at a time."""
    public = copy.deepcopy(primary)
    for cid, clip in list(public['clips'].items()):
        clip.pop('clip_snapshot', None)
        clip.pop('review_input_snapshot', None)
        if clip.get('planned_request') == clip.get('actual_request'):
            clip.pop('planned_request', None)
        if clip.get('current_request') == clip.get('actual_request'):
            clip.pop('current_request', None)
        relative = f'input/targets/{cid}.json'
        atomic_write_json(Path(work)/relative, clip)
        public['clips'][cid] = {'input': relative, **{key: clip[key] for key in (
            'current_take', 'current_frames', 'references', 'generation_count',
            'generation_limit', 'remaining_video_takes') if key in clip}}
    public['reading_scope'] = ('Read one clips[clip_id].input completely, inspect its time-labelled images, '
        'and save that target before opening another target. Full source and requests remain in those files; '
        'do not dump every target into context before reaching a decision.')
    atomic_write_json(Path(work)/'input/primary.json', public)


def collect(work):
    """Export tool metadata and successful image Reads, never reasoning text."""
    work = Path(work)
    calls, results, sessions = {}, {}, set()
    summaries = []
    for log in sorted((work/'attempts').glob('*/session.jsonl')):
        for line in log.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('session_id'):
                sessions.add(event['session_id'])
            blocks = event.get('message', {}).get('content', [])
            for block in blocks if isinstance(blocks, list) else []:
                if block.get('type') == 'tool_use':
                    data = block.get('input') or {}
                    calls[block['id']] = {'name': block.get('name'), 'file_path': data.get('file_path'),
                                          'command': data.get('command')}
                if block.get('type') == 'tool_result':
                    value = block.get('content')
                    results[block['tool_use_id']] = {'is_error': block.get('is_error', False),
                       'image': isinstance(value, list) and any(b.get('type') == 'image' for b in value if isinstance(b, dict))}
            if event.get('type') == 'result':
                summaries.append({k: event[k] for k in ('subtype', 'is_error', 'api_error_status', 'num_turns', 'usage') if k in event})
    seen = {c['file_path'].removeprefix('/work/') for cid, c in calls.items()
            if c['name'] == 'Read' and c.get('file_path') and results.get(cid, {}).get('image')
            and not results[cid]['is_error']}
    return {'sessions': sorted(sessions), 'seen_images': sorted(seen),
            'tool_calls': calls, 'tool_results': results, 'sdk_result_metadata': summaries}


def include_decoded_frames(work, primary):
    path = Path(work)/'output/frame_requests.jsonl'
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        record = json.loads(line)
        for clip in primary['clips'].values():
            if record['source_video'] == 'input/'+clip['current_take']+'/clip.mp4':
                if not any(f['path'] == record['path'] for f in clip['current_frames']):
                    clip['current_frames'].append({'path': record['path'], 'seconds': record['actual_seconds']})


def add_boundary_frames(work, primary):
    """Uniform samples miss opening and terminal overlays seen in supported takes."""
    from novel_manga.application.repair.video_frames import inspect
    from novel_manga.util import media_duration
    work = Path(work)
    for clip in primary['clips'].values():
        duration = media_duration(Path(clip['current_video']))
        for seconds in (0.0, max(0.0, duration-0.2)):
            if any(abs(f.get('seconds', -1)-seconds) < 0.05 for f in clip['current_frames']):
                continue
            record = inspect(work, clip['current_take'], seconds)
            clip['current_frames'].extend(record['frames'])
        clip['current_frames'].sort(key=lambda frame: frame['seconds'])
    primary['visual_scope'] = 'uniform samples plus decoded opening and terminal frames; no full motion or hearing claim'
