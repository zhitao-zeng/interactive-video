"""Original brief to an isolated reviewed film using the existing Agents and tools."""
from __future__ import annotations

import copy
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json


def run(root, task_path, run_dir, edition, *, config=None, resume=False):
    from . import creation, creative_material, authored, production, runtime
    root=Path(root).resolve();run_dir=Path(run_dir).resolve()
    if not edition or Path(edition).name!=edition:
        raise ValueError('edition must be a new output directory name')
    task=copy.deepcopy(read_json(Path(task_path),{}))
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
    task.setdefault('h3_input_mode','first-frame')
    task.setdefault('music_mode','timeline')
    book=root/'outputs'/edition
    saved=read_json(run_dir/'state.json',{})
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
    state={**saved,'task':task,'edition':edition,'root':str(root),'status':'running'}
    def stage(value):
        state['stage']=value;atomic_write_json(run_dir/'state.json',state)
    try:
        stage('creation')
        creator_dir=run_dir/'creator'
        author_state=read_json(creator_dir/'creator-state.json',{})
        result=creation.run(frozen,creator_dir,config=config,
                            resume=bool(author_state) and author_state.get('status')!='creative_candidate')
        state['creator']=result
        if result.get('status')!='creative_candidate':
            state.update(status='blocked',reason='Creative Agent did not produce an accepted structured candidate')
            atomic_write_json(run_dir/'state.json',state);return state
        stage('material_handoff')
        work=creator_dir/'sandbox'
        manifest=read_json(work/'input/manifest.json')
        draft=read_json(work/'output/episode.json')
        style_source=work/manifest['selected_style']['file'] if manifest.get('selected_style')else None
        if task.get('mode')=='adaptation':
            material=creative_material.write_adaptation_material(draft,manifest,run_dir/'material',
                series_material=root/task['series_material'] if not Path(task['series_material']).is_absolute() else Path(task['series_material']),
                package_script=work/'input/package'/task['source_script'],style_source=style_source)
        else:
            material=creative_material.write_material(draft,manifest,run_dir/'material',style_source=style_source)
        runtime.configure(root,'h3',project=task['project'])
        if not book.exists():
            authored.prepare_authored(material,book,family=task['project'])
        from .first_frame_entry import select_mode
        select_mode(book,manifest['profile'].get('h3_input_mode'))
        episode=book/(book.name+'_1')
        if not (episode/'clip_plan.json').is_file():
            authored.pack_authored(episode)
        if not state.get('assets_complete'):
            stage('source_assets')
            runtime.runner_for(root,book,'h3',project=task['project']).build_assets()
            state['assets_complete']=True
            atomic_write_json(run_dir/'state.json',state)
        stage('production_and_review')
        # Once entered, any retry is a resume of this same bounded production.
        production_started=bool(state.get('production_started'))
        state['production_started']=True;atomic_write_json(run_dir/'state.json',state)
        receipt=production.run_episode(root,'h3',project=task['project'],edition=edition,
            resume=production_started,agent_config=config)
        state.update(status=receipt.get('status','blocked'),delivery=receipt,stage='finished')
    except Exception as error:
        state.update(status='blocked',error=type(error).__name__+': '+str(error)[:600])
    atomic_write_json(run_dir/'state.json',state)
    return state
