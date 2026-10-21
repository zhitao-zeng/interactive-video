"""Validate Agent evidence/actions and project them into the existing review report."""
from __future__ import annotations

from novel_manga.review.contracts import POLICY


def validate_review(primary, review, decisions, seen_images):
    errors, found = [], set()
    rows = review.get('clips', [])
    for row in rows:
        cid = row.get('clip_id')
        if cid not in primary['target_clips'] or cid in found:
            errors.append(f'{cid}: unexpected or duplicate review')
            continue
        found.add(cid)
        clip = primary['clips'][cid]
        decision = decisions.get(cid, {})
        if not isinstance(decision, dict):
            errors.append(f'{cid}: decision must be an object')
            decision = {}
        frames = {f['path']: f.get('seconds') for f in clip['current_frames']}
        verdict = row.get('verdict')
        action = decision.get('action')
        if not decision.get('reason') or not decision.get('evidence'):
            errors.append(f'{cid}: decision requires reason and evidence')
        if verdict not in {'pass', 'needs_repair', 'uncertain'}:
            errors.append(f'{cid}: invalid content verdict')
        if action not in {'keep', 'edit', 'input', 'retake', 'stop'}:
            errors.append(f'{cid}: invalid or missing decision')
        if verdict == 'pass' and action != 'keep':
            errors.append(f'{cid}: pass requires keep, including at zero remaining takes')
        if verdict == 'uncertain' and action != 'stop':
            errors.append(f'{cid}: uncertain requires stop until the missing fact is available')
        if verdict in {'needs_repair', 'uncertain'} and action == 'keep':
            errors.append(f'{cid}: keep contradicts the content verdict')
        if action == 'retake' and clip.get('remaining_video_takes', 0) == 0:
            errors.append(f'{cid}: generation unavailable; do not propose an executable retake')
        evidence = row.get('evidence') or []
        if not isinstance(evidence, list) or not all(isinstance(i, dict) for i in evidence):
            errors.append(f'{cid}: evidence must be an array of objects')
            evidence = []
        observed = {}
        for item in evidence:
            path = str(item.get('image', '')).removeprefix('/work/')
            if path not in frames:
                errors.append(f'{cid}: evidence is not from the current take: {path}')
                continue
            if path not in seen_images:
                errors.append(f'{cid}: cited image was not read: {path}')
            seconds = item.get('seconds')
            actual = frames[path]
            if (not isinstance(seconds, (int, float)) or not isinstance(actual, (int, float))
                    or abs(seconds-actual) > 0.05):
                errors.append(f'{cid}: evidence time does not match the supplied/decoded frame: {path}')
            else:
                observed[path] = actual
        if verdict != 'uncertain' and not observed:
            errors.append(f'{cid}: content judgment needs current-take image evidence')
        findings = row.get('findings') or []
        if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
            errors.append(f'{cid}: findings must be an array of objects')
            findings = []
        required = [f for f in findings if f.get('must_fix')]
        if verdict == 'needs_repair' and not required:
            errors.append(f'{cid}: needs_repair requires a concrete must-fix finding')
        if verdict == 'pass' and required:
            errors.append(f'{cid}: pass contradicts must-fix findings')
        for finding in required:
            cited = finding.get('evidence') or []
            if not isinstance(cited, list):
                errors.append(f'{cid}: finding evidence must be an array of paths')
                cited = []
            paths = [str(p).removeprefix('/work/') for p in cited]
            times = [observed[p] for p in paths if p in observed]
            start, end = finding.get('start_seconds'), finding.get('end_seconds')
            if not paths or len(times) != len(paths):
                errors.append(f'{cid}: finding must cite its actual current-frame observations')
            elif (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                  or start > end or start < min(times)-0.05 or end > max(times)+0.05):
                errors.append(f'{cid}: defect range exceeds its observed evidence; narrow it to observed times')
    errors.extend(f'{cid}: missing review item' for cid in primary['target_clips'] if cid not in found)
    return errors


def episode_report(primary, review, decisions):
    rows = {r.get('clip_id'): r for r in review.get('clips', []) if isinstance(r, dict)}
    report = {'policy': POLICY, 'review_mode': 'verify', 'review_backend': 'sandbox_agent',
              'clips': {}, 'flags': [], 'feedback': {}, 'audio_scope': 'not_audited',
              'visual_scope': 'actually_read_sampled_frames'}
    for cid in primary['target_clips']:
        clip, row = primary['clips'][cid], rows.get(cid, {})
        verdict = row.get('verdict', 'uncertain')
        required = [f for f in (row.get('findings') or []) if isinstance(f, dict) and f.get('must_fix')]
        failed = verdict == 'needs_repair'
        severity = 'fail' if failed else 'pass' if verdict == 'pass' else 'review_error'
        item = {'video': clip['current_video'], 'take': clip['take'],
                'severity': severity, 'tier': 'must_fix' if failed else 'optional',
                'defect_issue': row.get('finding', ''), 'evidence': row.get('evidence', []),
                'findings': row.get('findings', []), 'notes': row.get('notes', []),
                'agent_decision': decisions.get(cid, {}), 'review_backend': 'sandbox_agent',
                'agent_clip_snapshot': clip.get('clip_snapshot'),
                'agent_source_snapshot': clip.get('shots_and_neighbours', []),
                'agent_input_snapshot': clip.get('review_input_snapshot'),
                'agent_budget_snapshot': {k:clip.get(k) for k in ('generation_count','generation_limit')},
                'verify': {'verdict': 'obvious' if failed else 'fine' if severity == 'pass' else None,
                           'evidence': row.get('evidence', [])}}
        kinds = {f.get('kind') for f in required}
        if 'clue_text' in kinds:
            item.update(story_ok=False, story_kind='关键线索内容被改写', story_issue=item['defect_issue'])
        if 'native_subtitles' in kinds:
            item.update(unplanned_subtitles=True, subtitle_issue=item['defect_issue'])
        if kinds:
            item['visual_defects'] = True
            item['visual_defect_kind'] = next((k for k in ('proportion', 'split_view', 'body_motion', 'spatial') if k in kinds), 'other')
        report['clips'][cid] = item
        if failed:
            report['feedback'][cid] = row.get('finding') or 'Current take has a confirmed visual defect'
            report['flags'].append(f'{cid}: {report["feedback"][cid]}')
    return report
