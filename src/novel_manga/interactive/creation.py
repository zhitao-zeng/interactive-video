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
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY, native_speech_selected
    profile=dict(task.get('profile') or {})
    profile.setdefault('h3_speech_policy',NATIVE_SPEECH_POLICY)
    if task.get('voice_reference_policy')=='none' and profile['h3_speech_policy']=='reference':
        raise ValueError('Conflicting speech policy: voice_reference_policy=none conflicts with reference mode')
    native_speech_selected(profile)  # Reject unknown explicit policies before any author session.
    for key in ('style','h3_input_mode','sd_input_mode'):
        if task.get(key):
            profile[key]=task[key]
    work = run_dir / 'sandbox'
    (work / 'input').mkdir(parents=True, exist_ok=True)
    (work / 'output').mkdir(exist_ok=True)
    (run_dir / 'owner').write_text('Unified creator / independent text-only draft; original production stays read-only.\n')
    sources, assets, archive = [], [], None
    if task['mode'] == 'adaptation':
        archive = Path(task['source_archive']).expanduser()
        if not archive.is_absolute():
            # Task-relative first; a fresh worktree often means the project root instead.
            candidate = (task_path.parent / archive).resolve()
            archive = candidate if candidate.is_file() else (Path.cwd() / archive).resolve()
        archive = archive.resolve()
        shutil.copy2(archive, run_dir / 'original-package.zip')
        package_dir = work / 'input/package'
        if package_dir.exists():
            # A previous failed start left its import behind; re-binding the same
            # archive must be retryable instead of dying on the existing directory.
            shutil.rmtree(package_dir)
        index = import_package(archive, package_dir)
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
    methods = [asdict(m) for m in METHODS.values()]
    atomic_write_json(work / 'input/creation-methods.json', methods)
    atomic_write_json(work / 'input/creation-method-index.json',
                      [{k: m[k] for k in ('key', 'name', 'strategy')} for m in methods])
    skill, refs = _install_skill(SimpleNamespace(work_dir=work))
    genre = {'adventure': 'travel_authoring', 'romance': 'romance_authoring',
             'suspense': 'suspense_authoring'}[project]
    selected_style = None
    style_reads = []
    if task.get('style'):
        from novel_manga.style_packs import freeze_selected_style
        selected_style, style_images = freeze_selected_style(task['style'], work)
        assets.extend(style_images)
        style_reads.append(selected_style['file'])
    required = [skill, 'input/manifest.json', 'input/brief.md', 'input/creation-method-index.json',
                *[refs[k] for k in ('creative_authoring', 'dialogue_craft', 'sound_direction', 'camera_selection', 'spatial_blockout', genre)],
                *sources, *style_reads, *[r['path'] for r in assets]]
    dialogue_source = task.get('dialogue_source')
    if dialogue_source:
        if dialogue_source not in sources:
            raise ValueError('dialogue_source must name one imported source document from input/package')
        from .source_dialogue import extract_markdown_dialogue
        rows = extract_markdown_dialogue((work/dialogue_source).read_text())
        atomic_write_json(work/'input/protected-dialogue.json', rows)
    target = task.get('target_seconds', [180, 300])
    if len(target) != 2 or not 0 < target[0] <= target[1]:
        raise ValueError('target_seconds requires a positive minimum/maximum pair')
    manifest = {'phase': 'creation_only', 'input_mode': 'source', 'mode': task['mode'],
        'project': project, 'task': task, 'profile':profile, 'target_seconds': target,
        'source_archive': str(archive) if archive else None, 'source_documents': sources,
        'asset_catalog': assets, 'skill': skill, 'skill_references': refs,
        'selected_genre': genre, 'required_reads': required, 'media_generation_allowed': False,
        'method_library': 'input/creation-methods.json',
        'generation_group_limits': {'min_seconds': 4, 'max_seconds': 15, 'max_shots': 6},
        'available_tools': ['Read', 'Write', 'Edit', 'Glob', 'Grep'],
        'output_contract': 'output/episode.json; creator contract, no H3 prompts/render request',
        'authority': 'Current brief governs permitted adaptation; original package remains source evidence.'}
    if selected_style:
        manifest['selected_style'] = selected_style
    if dialogue_source:
        manifest['dialogue_source'] = dialogue_source
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
    recorded_seconds=sum(float(read_json(path,{}).get('seconds',0))
                         for path in (work/'attempts').glob('*/attempt.json'))
    used = max(float(state.get('seconds_used', 0)),recorded_seconds)
    revision = resume and (work/'output/episode.json').is_file()
    previous_issues = read_json(work/'input/creative-validation.json', {}).get('issues')
    prompt = ('Read input/manifest.json, its complete required_reads and the creation-only contract. '
              'You are the single author from original input to complete script, storyboard, interaction '
              'and spatial/blockout design. Write output/creative-notes.md and the complete output/episode.json. '
              'Use file tools only, preserve source facts and current brief; no media generation. '
              'When shell tools are available, use packaged scripts/read-text.py for text, one bounded page per result; '
              'continue from end until complete. Use --section for a matching detailed guide. '
              'Use the listed paths directly. Read one small resource or named section per tool result; '
              'if a response is truncated, continue only its missing range. Reuse material already read '
              'in this session. Review and correct the full draft before finishing. '
              'Do not end with a plan or offer to continue.')
    if resume:
        draft_state=('Revise the existing output/episode.json. ' if (work/'output/episode.json').is_file()
                     else 'No output/episode.json has been delivered. Write its first complete candidate now from the frozen brief and creator contract. ')
        prompt = ('Continue this same author session. '+draft_state+'Read the '
                  'current Skill entry and input/creative-validation.json if present. Reuse the '
                  'sources and resources already read in this session; consult relevant original '
                  'passages for the named corrections. Repair only the issues in the current validation '
                  'and explicit editorial feedback; preserve valid work and do not reopen unrelated '
                  'camera, style or story choices. Write the complete corrected output/episode.json '
                  'and self_review, then finish. The storyboard lives in chapter_script.shots; do not search '
                  'SDK logs or prior attempts for another output schema. File operations only; no media generation.')
    camera_task = (
        ' For this draft/revision, read the current camera guide at '
        + (Path(manifest['skill']).parent / SKILL_REFERENCES['camera_selection']).as_posix()
        + ". Choose coverage from each scene's information change and actual space. "
        'Explain key choices with shot numbers in output/creative-notes.md, and implement '
        'the trigger, actor/camera paths, endpoint and hold in the actual shot camera text. '
        'If you select a dolly zoom, low stabilized tracking, symmetric swish-pan/zoom, '
        'shared actor-camera floating move or split-field focus, first read the matching '
        'section of ' + (Path(manifest['skill']).parent / SKILL_REFERENCES['director_camera_styles']).as_posix()
        + '. Apply its relevant staging guidance and record which shots actually use it; '
        'distinguish a considered option from an adopted design. No technique quota. '
        'Self-review the resulting shots and correct mismatches, rather than reporting only that a guide was read.')
    if not revision:
        prompt += camera_task
    style_task = (' Read selected_style frozen rules and style-reference images; reuse them if already read. '
                  'Use them to choose the scene\'s visual focus, palette, material and light in camera/light/visual_prompt; '
                  'these examples do not supply character identities, story events, layout or a new camera POV. '
                  'Keep source-backed identities, wardrobe, story facts and authored time of day.'
                  if manifest.get('selected_style') else '')
    if not revision:
        prompt += style_task
    design_task=''
    if any(manifest['task'].get(key)=='first-frame'or manifest['task'].get('profile',{}).get(key)=='first-frame'
           for key in ('h3_input_mode','sd_input_mode')):
        design_task+=(' Author every shot opening_frame.image_prompt and visible_characters now, along with its storyboard; '
            'specify one static time-zero composition and logical reference_subjects. '
            'Describe scene, actually visible subjects, current state, framing and essential continuity facts in short labeled sections. '
            'Keep full cast/seat maps in spatial_plan; do not enumerate offscreen guests or future exchanges in this image prompt. '
            'Preserve current clothing, object ownership, fixed attachments, repaired/damaged state and other source-backed continuity details; '
            'do not shorten by deleting them or force cropped-out details into view. '
            'Production binds each uploaded card by actual image number and appends appearance-only style cues without rewriting your facts. '
            'Keep later action/arrivals and camera movement in event/video direction. ')
    if manifest['task'].get('music_mode')=='timeline':
        design_task+=' Author chapter_script.music_plan cues and silences with stable shot start/end anchors, musical query, reason and verbatim source_quote. Plan entries/withdrawals/fades/listening gaps now; actual track selection and mixing happen in production. '
    if manifest.get('profile'):
        design_task += (' Follow manifest.profile for the selected speech contract and read sound-direction.md. '
                   'For no_reference_performance_first, write each spoken turn emotion in English now; '
                   'production inserts it once before the unchanged words. Keep physical acting in the shot. ')
    if manifest['task'].get('production_handoff'):
        design_task+=(' This original task continues to automatic production. Include production_design in episode.json: '
            'palette:string, characters:[{name,appearance,wardrobe,role,gender,age}], '
            'locations:[{name,description}], props:[{name,appearance,material,owner}], continuity_rules:[string]. '
            'Design stable appearances and clear scene geometry now; cover every named visible/speaking character, '
            'shot location and registered prop without changing the story. Names must match shots exactly. '
            'Each shot also needs shot_scale matching its camera; each generation group stays in one location. '
            'For original music_plan source_quote, copy a short '
            'phrase verbatim from a shot event, dialogue or physical sound. Do not supply file paths, generated images '
            'or H3 prompts; the host binds your design and executes the existing production tools. ')
    if not revision:
        prompt += design_task
    if manifest.get('dialogue_source'):
        prompt += (' Preserve input/protected-dialogue.json exactly, including every source speaker; '
                   'a long utterance may span shots but words, owners and order must remain unchanged. ')
    if revision:
        prompt += (' After splitting or renumbering shots, synchronize clips and music anchors in this same '
                   'revision. Reuse sources already read, consult only relevant ranges, update self_review '
                   'with actual fixes and end this turn. Do not repeat a full editorial polish. ')
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
        call_config = dict(config)
        if revision:
            # Check a bounded correction promptly instead of another long self-edit loop.
            turns = config.get('max_turns')
            call_config['max_turns'] = 16 if turns is None else min(int(turns), 16)
        attempt = execute(work, prompt, resume=resume, timeout=min(remaining, slice_seconds), config=call_config,
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
        stalled = bool(issues) and issues == previous_issues
        # A FIRST-PASS draft that passes every structural/read check is a candidate even
        # when the sandbox process was killed at its slice edge: the validator already
        # judged the artifact. Revision turns keep the stricter contract: a timed-out
        # correction leaves the previous draft unconfirmed and stays agent_failed.
        accepted_despite_exit = (not revision and bool(candidate) and not issues
                                 and attempt.exit_code in {0, 124})
        state = {'status': 'revision_stalled' if stalled else 'agent_failed' if not attempt.ok and not accepted_despite_exit else 'draft_invalid' if issues else 'creative_candidate',
            'run_dir': str(run_dir), 'session_id': attempt.record.get('session_id'),
            'seconds_used': round(used, 3), 'allowance_seconds': allowance,
            'slice_seconds': slice_seconds,
            'last_attempt': str(attempt.directory), 'agent_exit': attempt.exit_code,
            'accepted_despite_agent_exit': accepted_despite_exit,
            'model': attempt.record.get('model'), 'issues': issues,
            'new_video_generations': 0, 'new_image_generations': 0, 'new_tts_generations': 0,
            'production_adopted': False, 'read_evidence': str(run_dir / 'skill-read-evidence.json')}
        if stalled:
            state['stop_reason'] = 'Same validation issues remain after correction; retain the candidate and remaining allowance instead of repeating it automatically'
        atomic_write_json(state_path, state)
        if stalled:
            return state
        if not attempt.ok:
            return state
        if not issues:
            export(candidate, manifest, run_dir / 'delivery', notes=work/'output/creative-notes.md',
                   style_source=work/manifest['selected_style']['file'] if manifest.get('selected_style') else None)
            state['delivery'] = str(run_dir / 'delivery')
            atomic_write_json(state_path, state)
            return state
        if correction == 0:
            resume = True
            revision = True
            previous_issues = issues
            prompt = ('Continue this same creator session and the existing output/episode.json. '
                      'Read input/creative-validation.json and repair only its named issues. Preserve all '
                      'valid story, source dialogue, camera and style choices; consult relevant source ranges '
                      'only when needed. After splitting or renumbering shots, synchronize clips and music '
                      'anchors. Write the complete corrected draft and actual self_review, then finish; '
                      'do not repeat full editorial polish. Use only file tools; no media.')
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
