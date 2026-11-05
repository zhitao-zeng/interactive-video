"""Evidence export and final-request proposals through the existing sandbox executor."""
from __future__ import annotations
import copy
import os
import json
import re
import shutil
import time
from pathlib import Path

from novel_manga.application.agents.sandbox import load_config, run_agent
from novel_manga.application.configuration import project_root
from novel_manga.application.profiles import h3_stamp
from novel_manga.repair.proposal import RepairProposal
from novel_manga.story.h3 import request_issues
from novel_manga.story.dialogue import dialogue_punctuation
from novel_manga.application.review.evidence import clip_frames
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json, atomic_write_json

FRAME_HANDOFF_POLICY = 'same-take-frames-v5-exact-reader-and-reviewed-decision'

SECTIONS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
            'overall_soundscape', 'non_diegetic_music')


def enabled(directory: Path) -> bool:
    return (os.environ.get('NOVEL_INTERACTIVE_REPAIR_LANE') in {'h3','sd25'}
            or read_json(directory.parent/'profile.json', {}).get('repair_backend') == 'sandbox_agent')


def request_lane(clip):
    explicit = os.environ.get('NOVEL_INTERACTIVE_REPAIR_LANE')
    return explicit or ('h3' if clip.get('prompt_h3') else 'sd25')


def check_request(clip, novel_dir):
    from novel_manga.application.preparation.request_check import request_consistency
    if request_lane(clip) == 'sd25':
        clip = {**clip, 'prompt_h3': ''}
    return request_consistency(clip, novel_dir)


def validate_request(clip, prompt):
    if request_lane(clip) == 'sd25':
        from novel_manga.application.repair.sd_request import validate
        validate(clip, prompt)
    else:
        validate_prompt(clip, prompt)


def sections(prompt):
    matches = list(re.finditer(r'(?m)^('+'|'.join(SECTIONS)+r'):\s*$', prompt))
    if tuple(m.group(1) for m in matches) != SECTIONS:
        raise ValueError('agent request must preserve the six H3 sections')
    return {m.group(1): prompt[m.end():matches[i+1].start() if i+1<len(matches) else len(prompt)].strip()
            for i,m in enumerate(matches)}


def validate_prompt(clip, prompt):
    old, new = sections(clip['prompt_h3']), sections(prompt)
    def definition_bindings(text):
        rows = list(re.finditer(r'(?m)^(<(?:Subject|Audio) \d+>)', text))
        return [(row[1], sorted(set(token for token in re.findall(r'<(?:Picture|Subject|Audio) \d+>|\(S\d+\)',
                    text[row.end():rows[i+1].start() if i+1<len(rows) else len(text)]) if token != row[1])))
                for i,row in enumerate(rows)]
    if definition_bindings(old['subject_definitions']) != definition_bindings(new['subject_definitions']):
        raise ValueError('agent changed protected reference binding: subject_definitions')
    def retention_bindings(text):
        rows = list(re.finditer(r'(?m)^(<(?:Subject|Audio) \d+>): (\w+)', text))
        return [(row[1], row[2], sorted(set(re.findall(r'<Picture \d+>',
                    text[row.end():rows[i+1].start() if i+1<len(rows) else len(text)]))))
                for i,row in enumerate(rows)]
    # The agent may clarify the actual clothes/visible state. Lock the reference
    # associations and retention mode, not every word of their descriptions.
    before_retention = retention_bindings(old['retention_analysis'])
    after_retention = retention_bindings(new['retention_analysis'])
    if [(row[0],row[1]) for row in before_retention] != [(row[0],row[1]) for row in after_retention]:
        raise ValueError('agent changed protected reference binding: retention_analysis')
    from novel_manga.story.dialogue import subject_map
    people={f'<Subject {number}>' for number in subject_map(clip).values()}
    defined_pictures={label:{token for token in tokens if token.startswith('<Picture ')}
                      for label,tokens in definition_bindings(old['subject_definitions'])}
    props={f'<Picture {number}>' for number,ref in enumerate(
        (r for r in clip.get('references',[]) if r.get('role')!='voice'),1) if ref.get('role')=='prop'}
    for before,after in zip(before_retention,after_retention):
        previous,current=set(before[2]),set(after[2])
        # A character can name an already-selected prop while clarifying its
        # wearing state. Identity definitions and all original associations
        # remain locked; the source consistency check still verifies that state.
        allowed=(props|defined_pictures.get(before[0],set())) if before[0] in people else set()
        if previous-current or current-previous-allowed:
            raise ValueError('agent changed protected reference binding: retention_analysis')
    old_dialogue = [dialogue_punctuation(text) for text in re.findall(r'<d>(.*?)</d>', clip['prompt_h3'], re.S)]
    new_dialogue = [dialogue_punctuation(text) for text in re.findall(r'<d>(.*?)</d>', prompt, re.S)]
    if old_dialogue != new_dialogue:
        raise ValueError('agent changed dialogue, delivery binding or shot timing')
    for pattern in (r'<Subject \d+> \(S\d+\) says(?: in an off-screen voiceover)?',
                    r'\[Shot \d+\]', r'At \d\d:\d\d\.\d+',
                    r'The speaking voice follows the timbre referenced from <Audio \d+>\.'):
        if re.findall(pattern, clip['prompt_h3'], re.S) != re.findall(pattern, prompt, re.S):
            raise ValueError('agent changed dialogue, delivery binding or shot timing')
    duration_pattern = r'Planned duration: ([\d.]+) seconds\.'
    old_durations = [float(value) for value in re.findall(duration_pattern, clip['prompt_h3'])]
    new_durations = [float(value) for value in re.findall(duration_pattern, prompt)]
    if old_durations != new_durations:
        timing = clip.get('shot_timing') or []
        expected = [float(row['seconds']) for row in timing]
        # Older requests can include a duration header for only some shots.
        # Filling those headers must still use the complete saved shot timing.
        if not expected or new_durations != expected:
            raise ValueError('agent changed planned durations; preserve the saved shot timing')
    for pattern in (r'<Subject \d+>', r'<Picture \d+>', r'<Audio \d+>', r'\(S\d+\)'):
        if set(re.findall(pattern, clip['prompt_h3'])) != set(re.findall(pattern, prompt)):
            raise ValueError('agent changed reference/speaker tags')
    problems = request_issues({**clip, 'prompt_h3': prompt})
    if problems:
        raise ValueError('; '.join(problems))


def provided_frames(directory: Path, clip_id: str, video: Path, target: Path):
    """Copy the explicit same-take frame list, including late actions, or extract."""
    review_path = directory/'episode_review.json'
    reviewed = (read_json(review_path, {}).get('clips') or {}).get(clip_id, {})
    identity = take_identity(video)
    target.mkdir(parents=True, exist_ok=True)
    for frame in target.glob('frame_*.jpg'):
        frame.unlink()
    cache = directory/'work/review'/clip_id
    evidence = read_json(cache/'frame-evidence.json', {})
    rows = evidence.get('frames') or []
    cached = [Path(row['path']) for row in rows]
    same_review = (reviewed.get('video') == str(video) and identity
                   and reviewed.get('take') == identity and reviewed.get('severity') != 'review_error'
                   and evidence.get('video') == str(video) and evidence.get('take') == identity)
    # A glob cannot distinguish stale tails whose timestamps look plausible.
    if (same_review and cached and all(p.is_file() and p.stat().st_size > 0
                            and identity[2] <= p.stat().st_mtime_ns <= review_path.stat().st_mtime_ns
                            for p in cached)):
        frames = []
        for source in cached:
            frame = target/source.name
            shutil.copy2(source, frame)
            frames.append(frame)
        atomic_write_json(target/'frame-evidence.json', {
            **evidence, 'frames': [{**row, 'path': str(frame)} for row, frame in zip(rows, frames)]})
        return frames, 'same_take_review_report_supported'
    return clip_frames(video, target, 4), 'host_uniform_10_to_90_percent'


def unique_takes(videos):
    """An archived copy of the same managed render is not another comparison take."""
    paths, seen, skipped = [], [], 0
    for video in dict.fromkeys(videos):
        marker = read_json(video.parent / 'request.json', {}).get('managed_generation')
        if isinstance(marker, dict) and marker:
            if marker in seen:
                skipped += 1
                continue
            seen.append(marker)
        paths.append(video)
    return paths, skipped


def repair_config(directory, config=None):
    config = config or load_config()
    profile = read_json(directory.parent/'profile.json', {})
    if endpoint := profile.get('repair_agent_endpoint'):
        from novel_manga.llm.config import ENDPOINTS
        selected = ENDPOINTS[endpoint]
        base = selected['QWEN38_LOCAL_BASE_URL']
        if ',' in base:
            raise ValueError('Repair SDK endpoint requires one configured API base')
        config = {**config, 'base_url':base.removesuffix('/v1'),
                  'model':selected['QWEN38_LOCAL_MODEL'],
                  'key_var':selected['QWEN38_LOCAL_API_KEY_VAR']}
    model = profile.get('repair_agent_model')
    if model:
        config = {**config, 'model': str(model)}
    if config.get('repair_extra_body'):
        config = {**config, 'extra_body': {**(config.get('extra_body') or {}),
                                         **config['repair_extra_body']}}
    config = {**config, 'extra_args': [*config.get('extra_args', []), '--tools', 'Read,Bash,Write']}
    return config


def stage_video_tools(work, config):
    tools = work/'tools'
    tools.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).with_name('video_frames.py'), tools/'video_frames.py')
    if config.get('video_decoder'):
        binary = tools/'bin/ffmpeg'
        binary.parent.mkdir(exist_ok=True)
        shutil.copy2(Path(config['video_decoder']), binary)
    skill = work/'.claude/skills/video-repair'
    skill.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root()/'skills/video-repair/SKILL.md', skill/'SKILL.md')


def submitted_references(clip, submitted):
    references = clip.get('references', [])
    actual = submitted.get('references') or []
    if actual and all(isinstance(path, str) and Path(path).is_file() for path in actual):
        return [{**(references[i] if len(references) == len(actual) else
                    {'role': 'reference', 'name': f'Picture {i+1}'}), 'path': path}
                for i, path in enumerate(actual)]
    return references


def review_input_snapshot(directory, clip, video):
    """Bind a reusable decision to its source addresses, passages and actual reference files."""
    directory = Path(directory)
    near = {index+delta for index in clip.get('shot_indexes', []) for delta in (-1, 0, 1)}
    shots = [shot for i, shot in enumerate(read_json(directory/'chapter_script.json', {}).get('shots', []), 1)
             if shot.get('index', i) in near]
    related = {str(value) for value in clip.get('segment_ids', [])}
    related.update(str(shot['segment_id']) for shot in shots if shot.get('segment_id'))
    submitted = read_json(Path(video).parent/'request.json', {}) if video else {}
    reference_stats = []
    for reference in submitted_references(clip, submitted):
        path = directory.parent/reference['path']
        stat = path.stat() if path.is_file() else None
        reference_stats.append({'path': str(path), 'stat': [stat.st_mtime_ns, stat.st_size] if stat else None})
    return {'segment_ids': copy.deepcopy(clip.get('segment_ids', [])),
            'source_passages': [row for row in read_json(directory/'segments.json', [])
                                if str(row.get('segment_id')) in related],
            'reference_stats': reference_stats}


def prepare_inputs(directory, clip, verdict, work, *, namespace='', include_history=True, video_override=None):
    """The same frozen source/request/frame handoff for formal review and repair."""
    lane = request_lane(clip)
    field = 'prompt_h3' if lane == 'h3' else 'prompt'
    inputs = work/'input'/namespace
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs/'prompt.txt').write_text(clip[field], encoding='utf-8')
    script = read_json(directory/'chapter_script.json', {})
    indexes = clip.get('shot_indexes') or []
    near = {i+d for i in indexes for d in (-1,0,1)}
    shots = [{**s, 'index': s.get('index', i)} for i,s in enumerate(script.get('shots', []),1) if s.get('index',i) in near]
    media = read_json(directory/'thin_media_report.json', {})
    row = next((r for r in media.get('clips',[]) if r['clip_id']==clip['clip_id']), {})
    history = read_json(directory/'repair_history/history.json', {})
    from novel_manga.story.h3 import REQUEST_CONTENT_RULES
    evidence = {'frame_handoff_policy':FRAME_HANDOFF_POLICY, 'provider':lane, 'request_field':field,
                'request_rules': REQUEST_CONTENT_RULES if lane=='h3' else 'Preserve all non-stage lines, stage headers, audio clauses, dialogue and reference tags. Edit only visual stage directions.', 'clip': clip, 'shots_and_neighbours': shots, 'segments':read_json(directory/'segments.json', []),
                'verdict':verdict, 'media':row, 'preparation_problems':media.get('blocked_clips',{}).get(clip['clip_id'],[]), 'feedback':read_json(directory/'review_feedback.json', {}).get(clip['clip_id'],''),
                'observations':history.get('observations',{}).get(clip['clip_id'],[]),
                'trials':[{k:t.get(k) for k in ('id','method','changes','expected_notes')}
                          for t in history.get('trials',[]) if clip['clip_id'] in t.get('clips',[])],
                'references':[], 'takes':[]}
    video = str(video_override) if video_override else (row.get('selected') or {}).get('postmix_video') or (row.get('selected') or {}).get('video') or verdict.get('video')
    submitted = read_json(Path(video).parent/'request.json', {}) if video else {}
    actual_refs = submitted.get('references') or []
    references = submitted_references(clip, submitted)
    for i,ref in enumerate(references,1):
        source = directory.parent/ref['path']; target = inputs/f'reference-{i}{source.suffix}'
        shutil.copy2(source,target)
        evidence['references'].append({**ref,'path':str(target.relative_to(work))})
    videos = ([Path(video)] if video else []) + (sorted((directory/'repair_history/takes'/clip['clip_id']).glob('*/clip.mp4'))[-3:] if include_history else [])
    videos, skipped = unique_takes(videos)
    evidence['duplicate_generation_copies_skipped'] = skipped
    for i,source in enumerate(videos,1):
        if not source.is_file(): continue
        target=inputs/f'take-{i}'; target.mkdir()
        for name in ('clip.mp4','request.json','asr.json'):
            path=source if name=='clip.mp4' else source.parent/name
            if path.is_file():shutil.copy2(path,target/name)
        frames, provenance = provided_frames(directory,clip['clip_id'],source,target/'frames')
        frame_record=read_json(target/'frames/frame-evidence.json',{})
        times={Path(r['path']).name:r.get('seconds') for r in frame_record.get('frames',[])}
        supplied=[{'path':str(frame.relative_to(work)),'seconds':times.get(frame.name)} for frame in frames]
        atomic_write_json(target/'frames/frame-evidence.json', {
            'video':str((target/'clip.mp4').relative_to(work)), 'source_video':str(source),
            'source_take':take_identity(source), 'provenance':provenance, 'frames':supplied})
        evidence['takes'].append({'path':str(target.relative_to(work)), 'current':str(source)==video,
            'request':str((target/'request.json').relative_to(work)) if (target/'request.json').is_file() else None,
            'asr':str((target/'asr.json').relative_to(work)) if (target/'asr.json').is_file() else None,
            'frame_provenance':provenance, 'frames':supplied})
    from novel_manga.application.preparation.imported import EPISODE_FILES, BOOK_FILES
    evidence['source_documents']=[]
    files=[(directory/name,Path(name)) for name in (*EPISODE_FILES,'source.txt')]
    trace=read_json(directory/'source_trace.json',{})
    for key in ('original','storyboard','original_package','approved_editorial'):
        if trace.get(key):
            source=Path(trace[key])
            if source.is_file():files.append((source,Path('source_documents')/(key+source.suffix)))
    checks=directory/'work/request_checks'
    if include_history:
        files += [(source,Path('request_checks')/source.name) for source in checks.glob(clip['clip_id']+'.*.json')]
        files.append((checks/(clip['clip_id']+'.json'),Path('request_checks')/(clip['clip_id']+'.json')))
    files += [(directory.parent/name,Path(name)) for name in (*BOOK_FILES,'story_bible.json')]
    if include_history:
        files.append((directory.parent/'profile.json',Path('profile.json')))
    files += [(directory/row['path'],Path(row['path']))
              for row in read_json(directory/'intake.json',{}).get('documents',[])]
    for source,relative in files:
        if not source.is_file():continue
        target=inputs/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        evidence['source_documents'].append(str(target.relative_to(work)))
    current = next((t for t in evidence['takes'] if t['current']), {})
    evidence['primary_reading'] = {
        'request': str((inputs/'prompt.txt').relative_to(work)),
        'current_frames': current.get('frames', []),
        'reference_images': [r['path'] for r in evidence['references'] if r.get('role') != 'voice'],
        'source': 'Use inline shots_and_neighbours and corresponding segments first; read extra source documents only for a specific unresolved fact.'}
    evidence['reading_scope'] = 'One failed request; history and full-episode documents are optional evidence, not a mandatory reading list.'
    reference_choices=[]
    for i,choice in enumerate((verdict.get('confirmed') or {}).get('reference_candidates',[]),1):
        source=Path(choice['source']);target=inputs/f'reference-choice-{i}{source.suffix}'
        shutil.copy2(source,target)
        reference_choices.append({**{k:v for k,v in choice.items() if k!='source'},
                                  'path':str(target.relative_to(work))})
    evidence['reference_choices']=reference_choices
    if not include_history:
        evidence['clip'] = {k:v for k,v in clip.items() if k not in {'prompt_h3_agent', 'prompt_sd25_agent'}}
        for name in ('verdict', 'feedback', 'observations', 'trials', 'media', 'preparation_problems'):
            evidence.pop(name, None)
    atomic_write_json(inputs/'evidence.json',evidence)
    related_segments = {str(value) for value in clip.get('segment_ids', [])}
    related_segments.update(str(shot['segment_id']) for shot in shots if shot.get('segment_id'))
    primary = {
        'current_frames': current.get('frames', []),
        'provider': lane,
        'request_field': field,
        'request_rules': evidence['request_rules'],
        'actual_request': submitted.get('prompt') or clip[field],
        'planned_request': clip[field],
        'actual_request_origin': 'submitted_take' if submitted.get('prompt') else 'plan_fallback_no_saved_request',
        'submitted_references': actual_refs,
        'current_request': current.get('request'),
        'request': str((inputs/'prompt.txt').relative_to(work)),
        'shots_and_neighbours': shots,
        'source_passages': [row for row in evidence['segments']
                            if str(row.get('segment_id')) in related_segments],
        'references': evidence['references'],
        'reference_choices': reference_choices,
        'verdict': verdict}
    if not include_history:
        primary.pop('verdict', None)
        primary['review_input_snapshot'] = review_input_snapshot(directory, clip, video)
    atomic_write_json(inputs/'primary.json', primary)
    return primary, evidence



def propose(directory: Path, clip: dict, verdict: dict, *, config=None):
    """No production writes. Container sees evidence copies, never the production directory."""
    config = repair_config(directory, config)
    lane = request_lane(clip)
    field = 'prompt_h3' if lane == 'h3' else 'prompt'
    if lane == 'h3':
        sections(clip.get(field, ''))
    run = f'repair-{directory.name}-{clip["clip_id"]}-{time.time_ns()}'
    work = Path(config['runs_root'])/run
    primary, evidence = prepare_inputs(directory, clip, verdict, work)
    stage_video_tools(work, config)
    skill = work/'.claude/skills/video-repair'
    current = next((t for t in evidence['takes'] if t['current']), {})
    (work/'owner').write_text(f'Existing bounded repair / {directory.name} / {clip["clip_id"]} / proposal only\n')
    frame_paths = ', '.join('/work/' + row['path'] for row in current.get('frames', []))
    (work/'prompt.txt').write_text('In your first parallel Read tool batch, read input/primary.json and open these current-take JPEGs: '
                                 + frame_paths + '. '
                                 'primary.json contains provider, request_field, the complete actual_request, request_rules, '
                                 'local shots, relevant source passages, reference paths and the confirmed finding. '
                                 'The video-repair skill is reproduced below; do not reread it or the same request from another file. '
                                 'Open only the relevant reference images together in one tool batch if needed. '
                                 'Diagnose this failed clip from input/. '
                                 'Write output/decision.json and, only for edit, output/prompt.txt; '
                                 'for input write output/source_patch.json using the skill\'s scoped source contract. '
                                 'Use primary.current_frames for actual current-take JPEGs and timestamps. '
                                 'Use python3 tools/video_frames.py take-1 to list them. Do not read MP4 with the text reader, '
                                 'install packages, or prepare a decoder. If supplied evidence is insufficient, write stop with the missing evidence. '
                                 'This is one request with a 600-second execution window. Use input/primary.json and its shot/source facts first. '
                                 'input/evidence.json retains complete history and whole-episode evidence; read it only to resolve a specific remaining question. '
                                 'Read history or whole-episode documents only when they resolve a specific contradiction; do not exhaust the file list. '
                                 'Once current frames, request and selected reference support a decision, write the final output immediately. '
                                 'For edit, write decision.json and the complete prompt.txt together in one tool turn, then finish; do not leave only a decision while waiting for another model response. '
                                 'Do not generate video. One proposal, no retries.\n\n'
                                 + (skill/'SKILL.md').read_text(encoding='utf-8'),encoding='utf-8')
    attempt=run_agent(run,config=config,timeout=600)
    timed_out = not attempt.ok and getattr(attempt, 'exit_code', None) == 124
    if (not attempt.ok and not timed_out) or 'decision.json' not in attempt.produced:
        raise ValueError('repair agent did not produce a current decision')
    answer=read_json(work/'output/decision.json',{})
    if answer.get('action') not in {'edit','input','retake','keep','stop'} or not answer.get('reason') or not answer.get('evidence'):
        raise ValueError('repair agent decision requires action, reason and evidence')
    answer.update(run=run,attempt=attempt.attempt if hasattr(attempt,'attempt') else attempt.directory.name)
    if timed_out:
        # The stopped process may have written a complete candidate before
        # timing out. Keep that status; all normal artifact/source checks apply.
        answer['execution'] = {'exit_code': 124, 'recovered_candidate': True}
    if answer['action']=='edit':
        if 'prompt.txt' not in attempt.produced:raise ValueError('repair agent did not produce a current prompt')
        answer['prompt']=(work/'output/prompt.txt').read_text(encoding='utf-8').strip()
        validate_request(clip,answer['prompt'])
        if answer['prompt']==clip[field].strip():raise ValueError('agent edit made no effective change')
    if answer['action']=='input':
        if 'source_patch.json' not in attempt.produced:
            raise ValueError('agent input repair did not produce a current source patch')
        answer['source_patch']=read_json(work/'output/source_patch.json',{})
    return answer


def reviewed_decision(directory, clip, verdict):
    """Reuse this same Agent's validated decision only for the exact reviewed inputs."""
    if verdict.get('review_backend') != 'sandbox_agent' or not verdict.get('agent_protocol_valid'):
        return None
    confirmed = verdict.get('confirmed')
    if confirmed:
        check = confirmed.get('check') or {}
        if (check.get('result') != 'resolved' or check.get('video') != verdict.get('video')
                or check.get('take') != verdict.get('take')):
            return None  # The effective repair goal changed after this Agent's decision.
    from novel_manga.application.repair import history
    from novel_manga.application.review.store import current_takes
    cid = clip['clip_id']
    current = current_takes(directory, {'clips': [clip]}, {'clips': {cid: verdict}}).get(cid)
    if (not current or current.get('video') != verdict.get('video') or current.get('take') != verdict.get('take')
            or history.accepted_clip_material(clip) != verdict.get('agent_clip_snapshot')
            or review_input_snapshot(directory, clip, current['video']) != verdict.get('agent_input_snapshot')
            or read_json(directory/'review_feedback.json', {}).get(cid, '') != verdict.get('agent_notes_snapshot')):
        return None
    shots = read_json(directory/'chapter_script.json', {}).get('shots', [])
    near = {index+delta for index in clip.get('shot_indexes', []) for delta in (-1, 0, 1)}
    if [{**s, 'index': s.get('index', i)} for i,s in enumerate(shots, 1) if s.get('index', i) in near] != verdict.get('agent_source_snapshot'):
        return None
    if verdict.get('agent_budget_snapshot') != {'generation_count': history.generation_counts(directory).get(cid, 0),
                                               'generation_limit': history.generation_limit(directory, cid)}:
        return None
    return copy.deepcopy(verdict.get('agent_decision'))


def final_proposal(directory, plan, clip, decision):
    """Retain source and unrelated requests; only the accepted English and seed revision change."""
    notes=read_json(directory/'review_feedback.json',{})
    candidate=copy.deepcopy(plan)
    target=next(c for c in candidate['clips'] if c['clip_id']==clip['clip_id'])
    validate_request(clip,decision['prompt'])
    lane=request_lane(clip)
    field='prompt_h3' if lane=='h3' else 'prompt'
    target[field]=decision['prompt']
    if lane=='h3':target['prompt_h3_of']=h3_stamp(target,notes.get(clip['clip_id'],''))
    target['repair_take']=int(clip.get('repair_take',0))+1
    target['prompt_h3_agent' if lane=='h3' else 'prompt_sd25_agent']={k:decision[k] for k in ('run','attempt','reason','evidence','execution') if k in decision}
    return RepairProposal(result={'changed':[clip['clip_id']],'why':decision['reason']},
                          script=read_json(directory/'chapter_script.json',{}),plan=candidate,notes=notes,
                          changes={clip['clip_id']:decision})


def source_proposal(directory, plan, clip, decision):
    """Compile SDK-proposed prop/wearing fields through the existing source compiler."""
    from novel_manga.application.agents.episode.input_contracts import normalize_arguments
    from novel_manga.application.repair.flow import rebuild_clips
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.models.bible import StoryBible
    from novel_manga.application.preparation.readiness import plan_issues
    if request_lane(clip)!='h3':
        raise ValueError('Source binding repair requires the existing H3 source compiler')
    row=normalize_arguments({'clip_id':clip['clip_id'],'action':'input','reason':decision['reason'],
                             **decision['source_patch']})
    if set(row)-{'clip_id','action','reason','shots','locations'}:
        raise ValueError('Source binding repair supports only scoped shot patches')
    patches=row.get('shots') or []
    locations=row.get('locations') or {}
    if not patches and not locations:raise ValueError('Source binding repair needs changed shot fields or a supplied view')
    script=read_json(directory/'chapter_script.json',{})
    candidate=copy.deepcopy(script)
    by_index={s.get('index',i):s for i,s in enumerate(candidate['shots'],1)}
    bible=StoryBible.model_validate_json((directory.parent/'story_bible.json').read_text())
    known={name for p in bible.props for name in [p.name,*p.aliases]}
    seen=set()
    for patch in patches:
        index=patch['index']
        if index not in clip['shot_indexes'] or index in seen:
            raise ValueError('Agent source patch is outside its failed clip or repeats a stage')
        seen.add(index)
        if set(patch)-{'index','props','wears'}:
            raise ValueError('Source binding repair preserves action, cast, dialogue, timing and scene')
        if any(name not in known for name in patch.get('props',[])):
            raise ValueError('Source binding repair must select registered prop names or aliases')
        if any(value and value not in known for value in (patch.get('wears') or {}).values()):
            raise ValueError('Source wearing repair must select registered prop names or aliases')
        by_index[index].update({k:copy.deepcopy(v) for k,v in patch.items() if k!='index'})
    if candidate==script:
        if not locations:raise ValueError('Agent source patch made no effective change')
        rebuilt=copy.deepcopy(plan)
    else:
        rebuilt,changed=rebuild_clips(directory,directory.parent/'story_bible.json',candidate,plan,
                                       {clip['clip_id']},preserve_timing=True)
        if changed!=[clip['clip_id']]:raise ValueError('Source binding repair changed unrelated clips')
    target=next(c for c in rebuilt['clips'] if c['clip_id']==clip['clip_id'])
    choices=(read_json(directory/'episode_review.json',{}).get('clips',{}).get(clip['clip_id'],{})
             .get('confirmed',{}).get('reference_candidates',[]))
    for name,selection in locations.items():
        if not isinstance(selection,dict):raise ValueError('Source view selection requires an offered reuse_asset_id')
        choice=next((c for c in choices if c['name']==name and c['asset_id']==selection.get('reuse_asset_id')),None)
        if choice is None:raise ValueError('Source view must select a supplied reference candidate')
        ref=next((r for r in target['references'] if r['role']=='location' and r['name']==name),None)
        if ref is None:raise ValueError('Source view changed the selected location identity')
        path=Path(choice['source']);relative=path.relative_to(directory.parent)
        if not path.is_file():raise ValueError('Supplied source view is missing')
        ref.update(path=str(relative),view=choice.get('scope','existing-source-detail'),
                   supplied_source_view=copy.deepcopy(choice))
    if target.get('lines')!=clip.get('lines') or target.get('shot_timing')!=clip.get('shot_timing'):
        raise ValueError('Source binding repair changed protected speech or timing')
    if problems:=plan_issues(rebuilt,candidate):
        raise ValueError('Source binding candidate compilation failed: '+str(problems))
    errors=[]
    note=''
    if locations:
        note=('A supplied existing-source detail view replaces the same location reference. '
              'Use its actually visible native lettering and the stated detail scope; keep the original '
              'dialogue, events, single-view framing and timing. The source description retains the wider '
              'setting; this picture is a physical detail reference rather than an extra scene. '
              + ' '.join(c.get('scope','') for c in choices if c['name'] in locations))
    convert(target,note=note,episode_dir=directory,source_script=candidate,problems=errors)
    if note:
        for key in ('prompt_before_correction','prompt_correction_merged'):target.pop(key,None)
        target['prompt']=clip['prompt'];target['prompt_h3_of']=h3_stamp(target,read_json(directory/'review_feedback.json',{}).get(clip['clip_id'],''))
    if errors or not target.get('prompt_h3') or request_issues(target):
        raise ValueError('Source binding candidate H3 translation failed: '+str(errors))
    target['repair_take']=int(clip.get('repair_take',0))+1
    target['agent_source_repair']={k:decision[k] for k in ('run','attempt','reason','evidence','source_patch') if k in decision}
    return RepairProposal({'changed':[clip['clip_id']],'why':decision['reason']},script=candidate,
                          plan=rebuilt,notes=read_json(directory/'review_feedback.json',{}),
                          changes={clip['clip_id']:decision})
