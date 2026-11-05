"""Original brief to an isolated reviewed film using the existing Agents and tools."""
from __future__ import annotations

import copy
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json


def run(root, task_path, run_dir, edition, *, config=None, resume=False, lane=None):
    from . import creation, creative_material, authored, production, runtime
    root=Path(root).resolve();run_dir=Path(run_dir).resolve()
    if not edition or Path(edition).name!=edition:
        raise ValueError('edition must be a new output directory name')
    task=copy.deepcopy(read_json(Path(task_path),{}))
    saved=read_json(run_dir/'state.json',{})
    selected_lane=lane or task.get('video_lane') or saved.get('lane') or 'h3'
    if selected_lane not in runtime.VIDEO_LANES:
        raise ValueError('Unsupported video lane')
    if saved and (saved.get('lane') or saved.get('task',{}).get('video_lane') or 'h3')!=selected_lane:
        raise ValueError('Keep the frozen video lane; a changed production policy needs a new run')
    if lane is not None or 'video_lane' in task or 'video_lane' in saved.get('task',{}) or not saved:
        task['video_lane']=selected_lane
    if selected_lane!='h3' and task.get('h3_input_mode'):
        raise ValueError('H3 first-frame mode requires the h3 lane; select sd_input_mode for Fast')
    if selected_lane!='sd20-fast' and task.get('sd_input_mode'):
        raise ValueError('SD first-frame mode requires sd20-fast')
    if task.get('mode') not in {'original','adaptation'}:
        raise ValueError('produce accepts an original brief or a package adaptation')
    if task.get('mode')=='adaptation' and not task.get('series_material'):
        raise ValueError('adaptation produce needs series_material: an approved material dir from this series')
    if task.get('mode')=='adaptation' and not task.get('source_script'):
        raise ValueError('adaptation produce needs source_script: the package screenplay path bound by the creator')
    if task.get('end_card_seconds',3)!=3:
        raise ValueError('The existing delivery requires a three-second choice card')
    if task.get('world_card_seconds'):
        raise ValueError('produce does not yet bind world cards; use approved material intake')
    task.update(production_handoff=True,end_card_seconds=3)
    if selected_lane=='h3':task.setdefault('h3_input_mode','first-frame')
    task.setdefault('music_mode','timeline')
    book=root/'outputs'/edition
    if saved and (saved.get('task')!=task or saved.get('edition')!=edition or saved.get('root')!=str(root)):
        raise ValueError('Keep this frozen task; changed input needs a new run and edition')
    if book.exists() and not saved:
        raise ValueError('Existing edition belongs to another task; preserve it and its budgets')
    if saved.get('status') in {'accepted','candidate','blocked'} and not resume:
        return saved
    run_dir.mkdir(parents=True,exist_ok=True)
    (run_dir/'owner').write_text('Original creative-to-film run / automatic existing Agents and review / preserve all attempts\n')
    frozen=run_dir/'task.json'
    if not frozen.exists():atomic_write_json(frozen,task)
    state={**saved,'task':task,'edition':edition,'root':str(root),'lane':selected_lane,'status':'running','retryable':False}
    if book.exists() and saved.get('stage')=='material_handoff' and not saved.get('assets_complete') and not saved.get('production_started'):
        state['handoff_pending']=True
    state.pop('error',None);state.pop('reason',None)
    state.pop('service_interrupted',None);state.pop('required_action',None)
    def stage(value):
        state['stage']=value;atomic_write_json(run_dir/'state.json',state)
    creator_dir=run_dir/'creator'
    def creative_inputs(*, normalize=True):
        work=creator_dir/'sandbox'
        manifest=read_json(work/'input/manifest.json',{})
        draft=read_json(work/'output/episode.json',{})
        if normalize and manifest and draft:
            draft=creative_material.normalized_episode(draft,manifest)
        sources={}
        if manifest:
            brief=work/'input/brief.md'
            if brief.is_file():
                sources[str(brief.resolve())]=brief.read_text(encoding='utf-8')
            elif (manifest.get('task') or {}).get('brief'):
                sources[str(brief.resolve())]=manifest['task']['brief']
            for name in manifest.get('source_documents') or []:
                path=work/name
                sources[str(path.resolve())]=path.read_text(encoding='utf-8')
        return work,manifest,draft,sources
    try:
        stage('creation')
        runtime.configure(root,selected_lane,project=task['project'])
        work,manifest,draft,sources=creative_inputs(normalize=False)
        from novel_manga.application.preparation.audit import creative_audit,creative_fingerprint,feedback_issues
        pending_fingerprint=saved.get('pending_review_fingerprint')
        pending_feedback=saved.get('pending_feedback')
        current_fingerprint=(creative_fingerprint(creative_material.normalized_episode(draft,manifest),sources)
            if pending_fingerprint and manifest else None)
        feedback=None
        if pending_fingerprint and pending_feedback and current_fingerprint==pending_fingerprint:
            if saved.get('retryable') is False:
                state.update(status='blocked',retryable=False,stage=saved.get('stage'),
                    reason=saved.get('reason') or 'Creative review made no content progress')
                atomic_write_json(run_dir/'state.json',state);return state
            feedback=Path(pending_feedback)
        continuing=False
        while True:
            state.update(status='running',retryable=False)
            state.pop('reason',None)
            stage('creation')
            author_state=read_json(creator_dir/'creator-state.json',{})
            options={'resume':True,'feedback':feedback} if feedback is not None else {
                'resume':continuing or bool(author_state) and author_state.get('status')!='creative_candidate'}
            # Invalid drafts may not yet have an episode object or shot schema.
            draft_file=work/'output/episode.json'
            before=draft_file.read_text() if draft_file.is_file() else None
            result=creation.run(frozen,creator_dir,config=config,**options)
            state['creator']=result
            if result.get('status')!='creative_candidate':
                state.update(status='blocked',reason='Creative Agent did not produce an accepted structured candidate')
                progress_retry=((result.get('status')=='agent_failed' and result.get('agent_exit')==124
                    or result.get('status')=='draft_invalid' and result.get('draft_changed') is True)
                    and float(result.get('seconds_used',0))<float(result.get('allowance_seconds',0)))
                state['retryable']=result.get('retryable',progress_retry) is True
                if result.get('service_interrupted'):
                    state['service_interrupted']=result['service_interrupted']
                if result.get('required_action'):
                    state['required_action']=result['required_action']
                if (progress_retry and state['retryable'] and not result.get('service_interrupted')
                        and not result.get('required_action')):
                    after=draft_file.read_text() if draft_file.is_file() else None
                    if before!=after:
                        continuing=True
                        continue
                    state.update(retryable=False,reason='Creative review made no content progress')
                atomic_write_json(run_dir/'state.json',state);return state
            stage('material_handoff')
            work,manifest,draft,sources=creative_inputs()
            current_fingerprint=creative_fingerprint(draft,sources)
            if feedback is not None and current_fingerprint==pending_fingerprint:
                state.update(status='blocked',retryable=False,reason='Creative review made no content progress')
                atomic_write_json(run_dir/'state.json',state);return state
            review=creative_audit(draft,sources,cache_path=run_dir/'pre_render_story_audit.json')
            answer=review['answer']
            if not answer['source_readable']:
                state.pop('pending_feedback',None);state.pop('pending_review_fingerprint',None)
                state.update(status='blocked',retryable=False,
                    reason='Creative source is unreadable; no author revision requested')
                atomic_write_json(run_dir/'state.json',state);return state
            if not answer['issues']:
                break
            feedback_path=run_dir/'creative-feedback'/f"{review['fingerprint']}.json"
            atomic_write_json(feedback_path,{'fingerprint':review['fingerprint'],'issues':feedback_issues(answer['issues'])})
            allowance=float(result.get('allowance_seconds',0) or 0)
            used=float(result.get('seconds_used',0) or 0)
            state.update(status='blocked',retryable=not allowance or used<allowance,
                reason='Creative audit requires author revision',pending_feedback=str(feedback_path),
                pending_review_fingerprint=review['fingerprint'])
            atomic_write_json(run_dir/'state.json',state)
            if not state['retryable']:
                return state
            pending_fingerprint=review['fingerprint']
            feedback=feedback_path
            continuing=True
        state.pop('pending_feedback',None);state.pop('pending_review_fingerprint',None)
        style_source=work/manifest['selected_style']['file'] if manifest.get('selected_style')else None
        if task.get('mode')=='adaptation':
            material=creative_material.write_adaptation_material(draft,manifest,run_dir/'material',
                series_material=root/task['series_material'] if not Path(task['series_material']).is_absolute() else Path(task['series_material']),
                package_script=work/'input/package'/task['source_script'],style_source=style_source)
        else:
            material=creative_material.write_material(draft,manifest,run_dir/'material',style_source=style_source)
        if (book.exists() and state.get('handoff_pending')
                and not saved.get('assets_complete') and not saved.get('production_started')):
            if any(book.rglob('*.task.json')) or any(book.rglob('*.mp4')):
                raise ValueError('Retain existing media/task receipts; repair this adopted production through its original Agent')
            history=run_dir/'handoff-history';history.mkdir(exist_ok=True)
            archived=history/f'failed-{len(list(history.iterdir()))+1:03d}'
            archived.mkdir();book.rename(archived/book.name)
        materialized_handoff=False
        if not book.exists():
            authored.prepare_authored(material,book,family=task['project'])
            materialized_handoff=True
        if selected_lane=='h3':
            from .first_frame_entry import select_mode
            select_mode(book,manifest['profile'].get('h3_input_mode'))
        elif selected_lane=='sd20-fast':
            from .sd_first_frames import select_mode
            select_mode(book,manifest['profile'].get('sd_input_mode'))
        episode=book/(book.name+'_1')
        rebind=(resume and not state.get('assets_complete') and not state.get('production_started')
                and not state.get('handoff_pending'))
        if rebind and (any((episode/'work/clips').rglob('*.task.json')) or any((episode/'work/clips').rglob('*.mp4'))):
            raise ValueError('Keep filmed requests and their budgets; resume the existing production Agent')
        if not (episode/'clip_plan.json').is_file() or rebind:
            authored.pack_authored(episode)
        if materialized_handoff and not state.get('production_started'):
            from novel_manga.application.preparation.actions import creative_review_fingerprint
            atomic_write_json(episode/'pre_render_story_audit.json',{
                **review,'content_fingerprint':creative_review_fingerprint(episode)})
        state['handoff_pending']=False
        if not state.get('assets_complete'):
            stage('source_assets')
            runtime.runner_for(root,book,selected_lane,project=task['project']).build_assets()
            state['assets_complete']=True
            atomic_write_json(run_dir/'state.json',state)
        stage('production_and_review')
        # Once entered, any retry is a resume of this same bounded production.
        production_started=bool(state.get('production_started'))
        state['production_started']=True;atomic_write_json(run_dir/'state.json',state)
        receipt=production.run_episode(root,selected_lane,project=task['project'],edition=edition,
            resume=production_started,agent_config=config)
        status=receipt.get('status','blocked')
        state.update(status=status,delivery=receipt,stage='finished',
            retryable=status=='blocked' and receipt.get('retryable') is True)
        if receipt.get('service_interrupted'):
            state['service_interrupted']=receipt['service_interrupted']
        if receipt.get('required_action'):
            state['required_action']=receipt['required_action']
    except Exception as error:
        from novel_manga.service_errors import service_failure
        state.update(status='blocked',error=type(error).__name__+': '+str(error)[:600],
                     **service_failure(error))
    atomic_write_json(run_dir/'state.json',state)
    return state
