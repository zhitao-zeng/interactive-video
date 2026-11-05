"""Native Read feedback for consecutive rereads of an unchanged image.

This runs inside the existing sandbox using only the standard library. It does
not judge pictures or choose review verdicts. A new view, a changed file or a
different Read resets the consecutive-image counter.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import shlex
import sys
import time

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
FEEDBACK = (
    'This unchanged image has already been read successfully twice in succession. '
    'Reading it again adds no evidence. Use the images already in context to save '
    'the current grounded finding and decision. If a specific fact remains unclear, '
    'state that missing fact and obtain a different time/view or reference. '
    'Do not invent defects or force a pass; then continue the remaining task.'
)


def handle(event: dict, state_path: Path) -> dict:
    if event.get('tool_name') != 'Read':
        return {}
    name = event.get('tool_input', {}).get('file_path', '')
    path = Path(name)
    if not path.is_absolute():
        path = Path(event['cwd']) / path
    event_name = event.get('hook_event_name')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            if event_name == 'PostToolUse':
                state_path.write_text('{}\n')
            return {}
        stat = path.stat()
        image = {'path': str(path.resolve()), 'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}
        count = state.get('successful_reads', 0) if state.get('image') == image else 0
        if event_name == 'PreToolUse' and count >= 2:
            record = {'action': 'blocked_repeat', 'path': str(path),
                      'tool_use_id': event.get('tool_use_id'), 'successful_reads': count}
            with state_path.with_suffix('.jsonl').open('a') as stream:
                stream.write(json.dumps(record) + '\n')
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny', 'permissionDecisionReason': FEEDBACK}}
        if event_name == 'PostToolUse':
            count += 1
            state_path.write_text(json.dumps({'image': image, 'successful_reads': count}) + '\n')
            with state_path.with_suffix('.jsonl').open('a') as stream:
                stream.write(json.dumps({'action': 'read_success', 'path': str(path),
                            'tool_use_id': event.get('tool_use_id'), 'successful_reads': count}) + '\n')
            if count == 2:
                return {'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                        'additionalContext': FEEDBACK}}
    return {}


def execution_feedback(event, deadline_path):
    """Make the host's existing deadline visible; never supply a visual answer."""
    if event.get('hook_event_name') != 'PostToolUse' or not deadline_path:
        return {}
    data = json.loads(Path(deadline_path).read_text())
    remaining = max(0, int(data['deadline_at']-time.time()))
    message = (f'Host execution progress: {remaining} seconds remain before the original deadline. '
               'Save each completed target and its decision now, before further optional investigation. '
               'No content verdict is supplied by this reminder. ')
    if remaining <= 180:
        message += (('Save output/repair.json for the current focus target, call check and then the permitted host action. '
                     if data.get('phase') == 'episode' else
                     'Finalize review.json, per-clip decisions and review.md using the evidence already read. ')
                    +
                    'For a genuinely unresolved fact, record uncertain/stop and name the missing fact. '
                    'Do not invent a pass or a defect to finish. No new time window or generation.')
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message}}


def image_evidence_context(event):
    """Bind the image just returned by Read to its file/time, never its contents."""
    if event.get('hook_event_name') != 'PostToolUse' or event.get('tool_name') != 'Read':
        return {}
    work = Path(event['cwd'])
    path = Path(event.get('tool_input', {}).get('file_path', ''))
    if not path.is_absolute():
        path = work / path
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        return {}
    try:
        relative = str(path.relative_to(work))
    except ValueError:
        return {}
    primary_path = work/'input/primary.json'
    if not primary_path.is_file():
        return {}
    primary = json.loads(primary_path.read_text())
    clips = primary.get('clips') or {'current': primary}
    decoded_path = work/'output/frame_requests.jsonl'
    decoded = ([json.loads(line) for line in decoded_path.read_text().splitlines()]
               if decoded_path.is_file() else [])
    for cid, clip in clips.items():
        frame = next((f for f in clip.get('current_frames', []) if f['path'] == relative), None)
        new_frame = next((r for r in reversed(decoded) if r.get('path') == relative
                          and r.get('source_video') == f"input/{clip.get('current_take')}/clip.mp4"), None)
        if frame is None and new_frame:
            frame = {'path': relative, 'seconds': new_frame['actual_seconds']}
        if frame:
            time_label = f"host evidence time={float(frame['seconds']):.6f}s"
            for record in decoded:
                if record.get('path') == relative:
                    time_label = f"decoded source PTS={float(record['actual_seconds']):.6f}s"
            message = (f"Image just returned by Read tool_use_id={event.get('tool_use_id', '')}: "
                       f'CURRENT-TAKE VIDEO FRAME; clip={cid}; file={relative}; '
                       f'{time_label}. Bind observations to this exact file and time. '
                       'Read order is not time order: a later Read may show an earlier moment. '
                       'Reconstruct motion chronologically by the supplied times, not frame numbers or reading sequence. '
                       'This metadata supplies no visual observation or content verdict.')
            break
        ref = next((r for r in clip.get('references', []) if r.get('path') == relative), None)
        if ref:
            message = (f"Image just returned by Read tool_use_id={event.get('tool_use_id', '')}: "
                       f'REFERENCE IMAGE; clip={cid}; file={relative}; '
                       f"role={ref.get('role', '')}; name={ref.get('name', '')}. "
                       'This is not a movie frame and has no source time; do not cite it as timed defect evidence.')
            break
    else:
        return {}
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message}}


def review_checkpoint(event, state_path, deadline_path):
    """Keep observed review work on disk before touring another target's images."""
    if not deadline_path or event.get('tool_name') != 'Read':
        return {}
    scope = json.loads(Path(deadline_path).read_text())
    if scope.get('phase') != 'review':
        return {}
    path = Path(event.get('tool_input', {}).get('file_path', ''))
    work = Path(event['cwd'])
    if not path.is_absolute():
        path = work / path
    target_input = path.parent == work/'input/targets' and path.suffix == '.json'
    if (path.suffix.lower() not in IMAGE_SUFFIXES and not target_input) or not path.is_file():
        return {}
    target = next((cid for cid in scope['targets']
                   if cid in path.parts or (target_input and cid == path.stem)), None)
    if target is None:  # Explicit context images are not additional review targets.
        return {}
    progress_path = state_path.with_name('review-target-progress.json')
    progress = json.loads(progress_path.read_text()) if progress_path.is_file() else {}
    previous = progress.get('target')
    if event.get('hook_event_name') == 'PreToolUse' and previous and previous != target:
        review_path = work / 'output/review.json'
        try:
            review = json.loads(review_path.read_text())
            row = next((r for r in review.get('clips', []) if r.get('clip_id') == previous), {})
            checkpoint = row.get('decision_file')
            saved = bool(checkpoint and (work / checkpoint).is_file())
        except (OSError, ValueError, TypeError, AttributeError):
            saved = False
        if not saved:
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny', 'permissionDecisionReason': (
                        f'Before reading another target ({target}), save your current observations for {previous} '
                        'in output/review.json and its decision_file. This is a progress checkpoint, not a content answer. '
                        'If evidence is incomplete, record uncertain/stop and name the missing fact; you may revise '
                        'that finding after the needed comparison. Do not invent a pass or a defect. '
                        'Then continue with the next target within the original deadline.')}}
    if event.get('hook_event_name') == 'PostToolUse':
        progress_path.write_text(json.dumps({'target': target}) + '\n')
    return {}


def settings(state_path: str, script_path: str, deadline_path=None) -> dict:
    command = f'python3 {shlex.quote(script_path)} --state {shlex.quote(state_path)}'
    if deadline_path:
        command += f' --deadline {shlex.quote(deadline_path)}'
    return {'hooks': {event: [{'matcher': 'Read|Bash|Write' if deadline_path and event == 'PostToolUse' else 'Read',
                     'hooks': [{'type': 'command', 'command': command}]}]
                     for event in ('PreToolUse', 'PostToolUse')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--deadline', type=Path)
    arguments = parser.parse_args()
    event = json.load(sys.stdin)
    result = review_checkpoint(event, arguments.state, arguments.deadline)
    if not result:
        result = handle(event, arguments.state)
    for feedback in (image_evidence_context(event), execution_feedback(event, arguments.deadline)):
        if feedback:
            hook = result.setdefault('hookSpecificOutput', {'hookEventName': 'PostToolUse'})
            hook['additionalContext'] = hook.get('additionalContext', '') + feedback['hookSpecificOutput']['additionalContext']
    print(json.dumps(result))
