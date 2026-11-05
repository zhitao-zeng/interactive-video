"""One persistent, file-only creator using the existing episode Agent SDK."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

from novel_manga.application.agents.episode.inputs import _install_skill, SKILL_REFERENCES
from novel_manga.application.agents.episode.session import run_session, SESSION_FILE
from novel_manga.application.agents.sandbox import load_config
from novel_manga.planning.methods import METHODS
from novel_manga.util import atomic_write_json, read_json
from .importing import import_package
from .creative_contract import validate, read_evidence, export


def prepare(task_path, run_dir):
    task_path, run_dir = Path(task_path).resolve(), Path(run_dir).resolve()
    task = read_json(task_path, {})
    if task.get('mode') not in {'adaptation', 'original'} or not task.get('brief'):
        raise ValueError('task requires mode adaptation/original and a complete brief')
    project = task.get('project', 'adventure')
    if project not in {'adventure', 'romance', 'suspense'}:
        raise ValueError('project must be adventure, romance or suspense')
    previous = read_json(run_dir / 'sandbox/input/manifest.json', {})
    if previous:
        if previous['task'] != task:
            raise ValueError('Keep this frozen creation; use a new output directory for a changed task')
        return previous
    work = run_dir / 'sandbox'
    (work / 'input').mkdir(parents=True, exist_ok=True)
    (work / 'output').mkdir(exist_ok=True)
    (run_dir / 'owner').write_text('Unified creator / independent text-only draft; original production stays read-only.\n')
    sources, assets, archive = [], [], None
    if task['mode'] == 'adaptation':
        archive = Path(task['source_archive']).expanduser()
        if not archive.is_absolute():
            archive = task_path.parent / archive
        archive = archive.resolve()
        shutil.copy2(archive, run_dir / 'original-package.zip')
        index = import_package(archive, work / 'input/package')
        atomic_write_json(run_dir / 'package-index.json', index)
        for entry in index['files']:
            path = Path('input/package') / entry['path']
            if path.suffix.lower() in {'.md', '.txt', '.json'}:
                sources.append(path.as_posix())
            elif path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                assets.append({'path': path.as_posix(), 'role': 'supplied-reference',
                               'name': path.stem, 'generated': False})
        if not sources:
            raise ValueError('Package needs original readable Markdown, text or JSON documents')
    (work / 'input/brief.md').write_text(task['brief'])
    atomic_write_json(work / 'input/task.json', task)
    atomic_write_json(work / 'input/creation-methods.json', [asdict(m) for m in METHODS.values()])
    skill, refs = _install_skill(SimpleNamespace(work_dir=work))
    genre = {'adventure': 'travel_authoring', 'romance': 'romance_authoring',
             'suspense': 'suspense_authoring'}[project]
    required = [skill, 'input/manifest.json', 'input/brief.md', 'input/creation-methods.json',
                *[refs[k] for k in ('output_contract', 'creative_authoring', 'authoring', 'dialogue_craft',
                                   'camera_language', 'spatial_blockout', genre, 'repair_playbook')],
                *sources, *[r['path'] for r in assets]]
    target = task.get('target_seconds', [180, 300])
    if len(target) != 2 or not 0 < target[0] <= target[1]:
        raise ValueError('target_seconds requires a positive minimum/maximum pair')
    manifest = {'phase': 'creation_only', 'input_mode': 'source', 'mode': task['mode'],
        'project': project, 'task': task, 'target_seconds': target,
        'source_archive': str(archive) if archive else None, 'source_documents': sources,
        'asset_catalog': assets, 'skill': skill, 'skill_references': refs,
        'selected_genre': genre, 'required_reads': required, 'media_generation_allowed': False,
        'generation_group_limits': {'min_seconds': 4, 'max_seconds': 15, 'max_shots': 6},
        'available_tools': ['Read', 'Write', 'Edit', 'Glob', 'Grep'],
        'output_contract': 'output/episode.json; creator contract, no H3 prompts/render request',
        'authority': 'Current brief governs permitted adaptation; original package remains source evidence.'}
    atomic_write_json(work / 'input/manifest.json', manifest)
    atomic_write_json(work / 'input/current-task.json', {'phase': 'creation_only',
        'goal': 'Complete screenplay, storyboard, interactive options and spatial design in one author session',
        'tools': manifest['available_tools'], 'media_generation_allowed': False})
    return manifest


def run(task_path, run_dir, *, config=None, resume=False, feedback=None):
    run_dir = Path(run_dir).resolve()
    manifest = prepare(task_path, run_dir)
    work = run_dir / 'sandbox'
    state_path = run_dir / 'creator-state.json'
    state = read_json(state_path, {})
    if state.get('status') == 'creative_candidate' and feedback is None:
        return state
    # A real continuation reads current guidance; completed/no-op tasks returned above.
    _install_skill(SimpleNamespace(work_dir=work))
    # Existing author sessions keep their task; refresh this newly shared read
    # only when continuing real work, retaining the preceding input contract.
    dialogue = (Path(manifest['skill']).parent / SKILL_REFERENCES['dialogue_craft']).as_posix()
    if manifest['skill_references'].get('dialogue_craft') != dialogue or dialogue not in manifest['required_reads']:
        before = run_dir/'manifest-before-dialogue-craft.json'
        if not before.exists():
            shutil.copy2(work/'input/manifest.json', before)
        manifest['skill_references']['dialogue_craft'] = dialogue
        if dialogue not in manifest['required_reads']:
            manifest['required_reads'].append(dialogue)
        atomic_write_json(work/'input/manifest.json', manifest)
    config = load_config() if config is None else config
    execute = run_session
    slice_seconds = int(config.get('source_agent_slice_seconds', 1200))
    allowance = int(config.get('timeout_seconds', slice_seconds))
    used = float(state.get('seconds_used', 0))
    prompt = ('Read input/manifest.json, its complete required_reads and the creation-only contract. '
              'You are the single author from original input to complete script, storyboard, interaction '
              'and spatial/blockout design. Write output/creative-notes.md and the complete output/episode.json. '
              'Use file tools only, preserve source facts and current brief; no media generation. '
              'Read independent resources in batches. Review and correct the full draft before finishing. '
              'Do not end with a plan or offer to continue.')
    if resume:
        prompt = ('Continue this same author session and its existing complete draft. Read the '
                  'current Skill entry and input/creative-validation.json if present. Reuse the '
                  'sources and resources already read in this session; consult relevant original '
                  'passages and refreshed references for the current revision. Preserve valid '
                  'work, write the complete corrected output/episode.json and self_review, '
                  'and finish the draft. File operations only; no media generation.')
    camera_task = (
        ' For this draft/revision, read the current camera guide at '
        + manifest['skill_references']['camera_language']
        + ". Choose coverage from each scene's information change and actual space. "
        'Explain key choices with shot numbers in output/creative-notes.md, and implement '
        'the trigger, actor/camera paths, endpoint and hold in the actual shot camera text. '
        'If you select a dolly zoom, low stabilized tracking, symmetric swish-pan/zoom, '
        'shared actor-camera floating move or split-field focus, first read the matching '
        'section of ' + (Path(manifest['skill']).parent / SKILL_REFERENCES['director_camera_styles']).as_posix()
        + '. Apply its relevant staging guidance and record which shots actually use it; '
        'distinguish a considered option from an adopted design. No technique quota. '
        'Self-review the resulting shots and correct mismatches, rather than reporting only that a guide was read.')
    prompt += camera_task
    if feedback is not None:
        if not resume:
            raise ValueError('Feedback revises the same author; use resume=True')
        folder = work/'input/feedback'; folder.mkdir(exist_ok=True)
        saved = folder/f'revision-{len(list(folder.glob("*.md")))+1:02d}.md'
        shutil.copy2(feedback, saved)
        prompt += (' Read '+saved.relative_to(work).as_posix()+' and apply this editorial feedback '
                   'to the existing complete draft, preserving source authority, valid work and open choices. '
                   'Record actual changes in self_review; do not merely promise them.')
    for correction in range(2):
        remaining = int(allowance - used)
        if remaining <= 0:
            state.update(status='time_budget_used', seconds_used=used)
            atomic_write_json(state_path, state)
            return state
        started = time.monotonic()
        attempt = execute(work, prompt, resume=resume, timeout=min(remaining, slice_seconds), config=config,
                              expected_output='episode.json', enable_tools=False)
        used += time.monotonic() - started
        reads = read_evidence(work)
        seen = {r['path'] for r in reads}
        missing = [p for p in manifest['required_reads'] if p not in seen]
        candidate = read_json(work / 'output/episode.json', {})
        issues = validate(candidate, manifest, work) if candidate else ['Complete output/episode.json is missing']
        notes = work / 'output/creative-notes.md'
        if not notes.is_file() or not notes.read_text().strip():
            issues.append('Write output/creative-notes.md with the actual camera/cut choices and shot numbers')
        issues += ['Read the actual required source/Skill resource: ' + p for p in missing]
        report = {'issues': issues, 'required_reads_observed': len(manifest['required_reads']) - len(missing),
                  'required_reads_total': len(manifest['required_reads']), 'reads': reads,
                  'media_generation_allowed': False}
        atomic_write_json(attempt.directory / 'creative-validation.json', report)
        atomic_write_json(work / 'input/creative-validation.json', report)
        atomic_write_json(run_dir / 'skill-read-evidence.json', report)
        state = {'status': 'agent_failed' if not attempt.ok else 'draft_invalid' if issues else 'creative_candidate',
            'run_dir': str(run_dir), 'session_id': attempt.record.get('session_id'),
            'seconds_used': round(used, 3), 'allowance_seconds': allowance,
            'slice_seconds': slice_seconds,
            'last_attempt': str(attempt.directory), 'agent_exit': attempt.exit_code,
            'model': attempt.record.get('model'), 'issues': issues,
            'new_video_generations': 0, 'new_image_generations': 0, 'new_tts_generations': 0,
            'production_adopted': False, 'read_evidence': str(run_dir / 'skill-read-evidence.json')}
        atomic_write_json(state_path, state)
        if not attempt.ok:
            return state
        if not issues:
            export(candidate, manifest, run_dir / 'delivery', notes=work/'output/creative-notes.md')
            state['delivery'] = str(run_dir / 'delivery')
            atomic_write_json(state_path, state)
            return state
        if correction == 0:
            resume = True
            prompt = ('Continue this same creator session and the existing output/episode.json. '
                      'Read input/creative-validation.json, correct all named issues while preserving '
                      'valid choices, finish the full draft and self-review. Use only file tools; no media.') + camera_task
    return state


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'status'))
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--feedback', type=Path, help='Editorial feedback for this same resumed creator.')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.task, args.output_dir)
    elif args.command == 'status':
        result = read_json(args.output_dir / 'creator-state.json', {'status': 'not_started'})
    else:
        result = run(args.task, args.output_dir, resume=args.resume, feedback=args.feedback,
                     config=load_config(args.config) if args.config else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command != 'run' or result['status'] == 'creative_candidate' else 2
