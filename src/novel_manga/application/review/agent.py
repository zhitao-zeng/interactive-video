"""The formal, read-only Agent review entry; existing executor and repair input packer."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import time

from novel_manga.application.agents.sandbox import load_config, run_agent
from novel_manga.application.review import agent_inputs
from novel_manga.review.agent_contract import validate_review, episode_report
from novel_manga.util import read_json, atomic_write_json


def _outputs(work):
    review = read_json(work/'output/review.json', {})
    if not isinstance(review, dict) or not isinstance(review.get('clips'), list):
        raise ValueError('review.json must contain a clips array')
    if not all(isinstance(r, dict) for r in review['clips']):
        raise ValueError('every clips item must be an object')
    if not all(isinstance(r.get('clip_id'), str) for r in review['clips']):
        raise ValueError('every clips item requires clip_id')
    decisions = {row['clip_id']: read_json(work/row.get('decision_file', ''), {})
                 for row in review.get('clips', []) if row.get('decision_file')}
    if not all(isinstance(d, dict) for d in decisions.values()):
        raise ValueError('every decision must be an object')
    return review, decisions


def _saved_summary(work, review, decisions):
    """Readable receipt from validated saved decisions; no new visual judgment."""
    lines = ['# Saved Agent review', '',
             'Host summary of validated saved Agent outputs; native execution warnings remain in state.json. '
             'This receipt does not independently certify picture or sound quality.', '']
    for row in review['clips']:
        cid = row['clip_id']
        lines.extend([f'## {cid}: {row["verdict"]} / {decisions[cid]["action"]}',
                      row.get('finding', ''), decisions[cid].get('reason', ''), ''])
    (work/'output/review.md').write_text('\n'.join(lines) + '\n')


def review_agent_episode(directory, targets, report_dir, *, contexts=(), config=None,
                         video_overrides=None, timeout_seconds=1200, resume=False):
    """No producer, publication, speech refresh or episode writes are called here."""
    if not 0 < timeout_seconds <= 1200:
        raise ValueError('Agent review must use its existing deadline, at most1200 seconds')
    started = time.monotonic()
    deadline = started + timeout_seconds
    directory, report_dir = Path(directory), Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    if report_dir.resolve() == directory.resolve():
        raise ValueError('Review output must be independent of the production episode directory')
    previous = read_json(report_dir/'state.json', {})
    if previous and not resume:
        raise ValueError('Review run already exists; use explicit resume within its original deadline')
    if resume and (not previous or previous.get('status') != 'incomplete'):
        raise ValueError('Only an incomplete existing review can resume')
    if config is None:
        from novel_manga.application.repair.agent import repair_config
        base = load_config()
        config = repair_config(directory, base)
        # Review has its agreed budget; do not inherit a repair-only16k override.
        config['extra_body'] = copy.deepcopy(base.get('extra_body') or {})
    config = copy.deepcopy(config)
    config.setdefault('extra_body', {}).setdefault('max_tokens', 65536)
    config.setdefault('max_retries', 1)
    config['extra_args'] = [*config.get('extra_args', []), '--tools', 'Read,Bash,Write']
    if resume:
        from datetime import datetime
        state = previous
        work = Path(state['work'])
        name = state['run_name']
        primary = read_json(report_dir/'primary.json', {})
        targets = state['targets']
        notes_snapshot = read_json(report_dir/'notes-snapshot.json', {})
        # Early task builds lacked an epoch field; recover the original start, never a new window.
        origin = state.get('started_at')
        if origin is None:
            origin = datetime.strptime(Path(state['attempts'][0]['directory']).name[:15], '%Y%m%d-%H%M%S').timestamp()
        state.setdefault('started_at', origin)
        deadline = time.monotonic() + (origin + state['timeout_seconds'] - time.time())
        sessions = state.get('sessions', [])
        if len(sessions) != 1 or len(state['attempts']) >= 2:
            raise ValueError('No bounded same-session continuation remains')
        config['extra_args'] += ['--resume', sessions[0]]
        (work/'prompt.txt').write_text('Continue your interrupted review from the existing session. Save each target promptly, then review.md. Read the host contract feedback if present. Only the ORIGINAL deadline remains; no generation or new budget.')
    else:
        name = f'review-repair-{directory.name}-{time.time_ns()}'
        work = Path(config['runs_root'])/name
        targets, contexts = list(targets), list(contexts)
        primary = agent_inputs.prepare(directory, targets, contexts, work, config, video_overrides)
        notes_snapshot = read_json(directory/'review_feedback.json', {})
        primary['execution_scope'] = {'timeout_seconds': timeout_seconds, 'generation_allowed': False}
        agent_inputs.write_primary(work, primary)
        (work/'owner').write_text(f'Formal visual review / {directory.name} / no generation or publication\n')
        first = primary['clips'][targets[0]]['current_frames']
        frame_paths = ', '.join('/work/'+f['path'] for f in first)
        skill = (work/'.claude/skills/video-repair/SKILL.md').read_text()
        prompt = ('Review and choose bounded repair/keep decisions for existing footage. '
            'First read input/primary.json, then the complete input file named by '
            f'primary.clips["{targets[0]}"].input and these current JPEGs together: '+frame_paths+'. '
            'The current video-repair Skill follows below; use its REVIEW phase, not its single-request repair phase. '
            'Each target input contains its actual submitted request, source facts, reference paths, qualified take name and live budget. '
            'Resolve and save the current target before loading another target input; the complete index remains available. '
            'No old review answers are provided. Read extra source files only for a specific unresolved fact. '
            'When needed use python3 tools/video_frames.py <clip/take> --seconds <time>; it decodes new evidence, '
            'then Read its JPEG. No need to inspect tool code, install libraries or prepare a decoder. '
            'Before saving, compare early and late frames: confine each finding to where you actually see it. '
            'Do not extend an early abnormality into a later normal shot. '
            'Each image Read has a file/time or reference label; bind your observations to that label, '
            'not to filename ordinals or the order you happened to read images. '
            'Before claiming an action repeats or reverses, inspect that short source interval chronologically '
            'with the existing frame tool. A first-frame assumption, occlusion or cut alone does not prove replay. '
            'Save each target promptly, then finish review.md; do not spend the window exhausting history. '
            'No video, image, audio generation, production changes, external calls from Bash or extra budgets. '
            'This is a review-only task under one shared '+str(timeout_seconds)+'-second deadline.\n\n'+skill)
        (work/'prompt.txt').write_text(prompt)
        state = {'status': 'running', 'run_name': name, 'work': str(work), 'targets': targets,
                 'model': config['model'], 'timeout_seconds': timeout_seconds, 'attempts': [],
                 'generation_allowed': False, 'audio_scope': 'not_audited'}
        (report_dir/'owner').write_text(f'Formal review output / {name} / no generation\n')
        atomic_write_json(report_dir/'state.json', state)
        state['started_at'] = time.time() - (time.monotonic()-started)
        atomic_write_json(report_dir/'notes-snapshot.json', notes_snapshot)
        atomic_write_json(report_dir/'primary.json', primary)
        atomic_write_json(report_dir/'state.json', state)
    state['status'] = 'running'
    atomic_write_json(report_dir/'state.json', state)
    atomic_write_json(work/'input/execution-deadline.json', {
        'deadline_at': state['started_at']+state['timeout_seconds'], 'targets': targets, 'phase': 'review'})
    proof, review, decisions, errors = agent_inputs.collect(work), {}, {}, []
    known = {str(Path(a['directory'])) for a in state['attempts']}
    gaps = [str(p) for p in (work/'attempts').glob('*') if p.is_dir() and str(p) not in known] if resume else []
    state['unrecorded_attempts'] = gaps
    if gaps:
        errors.append('A native attempt has no host completion receipt; preserve it without another invocation')
    for turn in range(2 if gaps else len(state['attempts']), 2):
        remaining = int(deadline-time.monotonic())
        if remaining <= 0:
            errors.append('Execution deadline exhausted; no new window is granted')
            break
        attempt = run_agent(name, config=config, timeout=remaining)
        state['attempts'].append({'directory': str(attempt.directory), 'exit': attempt.exit_code,
                                  'seconds': attempt.seconds, 'allowed_seconds': remaining})
        proof = agent_inputs.collect(work)
        agent_inputs.include_decoded_frames(work, primary)
        try:
            review, decisions = _outputs(work)
            errors = validate_review(primary, review, decisions, set(proof['seen_images']))
            from novel_manga.application.repair.agent import validate_request
            for cid, decision in decisions.items():
                decision.update(run=name, attempt=attempt.directory.name)
                if decision.get('action') == 'edit':
                    decision['prompt'] = (work/'output'/cid/'prompt.txt').read_text().strip()
                    original = read_json(directory/'clip_plan.json', {})['clips']
                    clip = next(c for c in original if c['clip_id'] == cid)
                    validate_request(clip, decision['prompt'])
                    field = 'prompt_h3' if clip.get('prompt_h3') else 'prompt'
                    if decision['prompt'] == clip.get(field, '').strip():
                        errors.append(f'{cid}: agent edit made no effective change')
                if decision.get('action') == 'input':
                    decision['source_patch'] = read_json(work/'output'/cid/'source_patch.json', {})
                    if not decision['source_patch']:
                        errors.append(f'{cid}: input needs a concrete source_patch.json')
        except (ValueError, TypeError, KeyError) as error:
            errors = [f'Invalid current review output: {error}']
            review, decisions = {'clips': []}, {}
        except OSError as error:
            errors = [f'Missing candidate file: {error.filename}']
        execution_warnings = []
        if attempt.exit_code not in {0, 124}:
            execution_warnings.append(f'Native Agent exited{attempt.exit_code}')
        if proof['sdk_result_metadata'] and proof['sdk_result_metadata'][-1].get('is_error'):
            execution_warnings.append('Native SDK result reports an execution error; see native attempt')
        if execution_warnings:
            state.setdefault('execution_warnings', []).append({
                'attempt': str(attempt.directory), 'issues': execution_warnings,
                'api_error_status': (proof['sdk_result_metadata'][-1].get('api_error_status')
                                     if proof['sdk_result_metadata'] else None)})
        if not errors:
            # A failed final API completion must not erase already saved, fully
            # validated target results or spend another turn asking for prose.
            if not (work/'output/review.md').is_file():
                _saved_summary(work, review, decisions)
                state['summary_origin'] = 'host_from_validated_agent_outputs'
            break
        state['validation_errors'] = errors
        atomic_write_json(report_dir/'state.json', state)
        if turn or attempt.exit_code == 124 or len(proof['sessions']) != 1 or deadline-time.monotonic() < 1:
            break
        saved = attempt.directory/'original-output'
        for path in (work/'output').rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.md'}:
                target = saved/path.relative_to(work/'output')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        feedback = ('Host contract validation of YOUR saved outputs found:\n'+'\n'.join(errors)+
             '\nCorrect these structural/evidence-scope contradictions using your own observations. '
             'No new expected content answer is supplied. A pass means keep even with no budget; '
             'do not change visual judgments merely to fit an action. '
             'Do not reread sufficient images or start over. Save corrected JSON/decisions and review.md, then finish. '
             'Only the ORIGINAL deadline remains; no generation or new budget.\n')
        (work/'contract-feedback.txt').write_text(feedback)
        (work/'prompt.txt').write_text(feedback)
        config['extra_args'] += ['--resume', proof['sessions'][0]]
    report = episode_report(primary, review if not errors else {'clips': []}, decisions if not errors else {})
    report.update(episode=directory.name, video_name='clip.mp4')
    for cid, row in report['clips'].items():
        row['agent_protocol_valid'] = not errors
        row['agent_notes_snapshot'] = notes_snapshot.get(cid, '')
    if errors:
        # Invalid protocol never certifies content or launches a blind retake.
        report['feedback'] = {}
        report['flags'] = []
        for row in report['clips'].values():
            row.update(severity='review_error', tier='needs_evidence', protocol_errors=errors)
            row['verify']['verdict'] = None
    state.update(status='complete' if not errors else 'incomplete', validation_errors=errors,
                 elapsed_seconds=round(time.time()-state['started_at'], 3), sessions=proof.get('sessions', []))
    report['agent_execution'] = state
    atomic_write_json(report_dir/'state.json', state)
    atomic_write_json(report_dir/'execution-evidence.json', proof)
    atomic_write_json(report_dir/'primary.json', primary)
    atomic_write_json(report_dir/'episode_review.json', report)
    for filename in ('review.json', 'review.md'):
        if (work/'output'/filename).is_file():
            shutil.copy2(work/'output'/filename, report_dir/filename)
    return report
