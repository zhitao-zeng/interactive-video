"""Run each provider in its own directory against the same prepared episode."""
from pathlib import Path
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from novel_manga.interactive.runtime import configure, runner_for
from novel_manga.util import atomic_write_json, read_json
from novel_manga.interactive.intake import PROJECTS


def _agent_task_config(episode: Path, requested: dict | None) -> dict | None:
    """Persist the selected non-secret task route so an ordinary resume reuses it."""
    path = episode/'work/interactive-agent-config.json'
    receipt = read_json(path, {})
    if requested is not None:
        selected = {**requested, '_agent_config_explicit': True}

        def safe(value):
            if isinstance(value, dict):
                return {key:safe(item) for key,item in value.items()
                        if key.lower() not in {'key','api_key','token','access_token','secret','password'}
                        and not key.lower().endswith('_secret')}
            if isinstance(value, list):
                return [safe(item) for item in value]
            return value

        stored = safe(selected)
        previous = receipt.get('history') or []
        if receipt.get('selected') != stored:
            previous = [*previous, stored]
        atomic_write_json(path, {'schema':'interactive-agent-task-config-v1',
            'selected':stored, 'history':previous})
        return selected
    saved = receipt.get('selected')
    return ({**saved, '_agent_config_explicit':True} if isinstance(saved,dict) else None)


def edition_directory(root: Path, project: str, edition: str) -> Path:
    if Path(edition).name!=edition:
        raise ValueError('Edition must be an output directory name')
    novel=(root/'outputs'/edition).resolve()
    directory=novel/(edition+'_1')
    if not (directory/'clip_plan.json').is_file():
        raise FileNotFoundError('Prepare the edition before rendering')
    owner=read_json(directory/'intake.json',{}).get('project')
    owner=owner or next((name for name in PROJECTS if edition.startswith(name+'-')),None)
    if owner and owner!=project:
        raise ValueError(f'Edition belongs to project {owner}, not {project}')
    return novel


def edition_status(root: Path, project: str, edition: str) -> dict:
    """Read retained state without instantiating a renderer or accepting old verdicts."""
    from novel_manga.application.repair.history import generation_counts
    novel=edition_directory(root,project,edition);source_directory=novel/(novel.name+'_1')
    directory=source_directory;active_run=None
    for name in ('first-frame-agent-run.json','music-agent-run.json'):
        marker=read_json(novel/name,{})
        selected_path=marker.get('active_episode_dir')
        selected=Path(selected_path) if selected_path else None
        if selected and selected.is_dir():
            directory=selected.resolve()
            run_path=marker.get('run_dir')
            active_run=Path(run_path) if run_path else None
            break
    report=read_json(directory/'thin_media_report.json',{})
    final=read_json(directory/'final_cut_review.json',{})
    run_state=read_json(active_run/'agent-run.json',{}) if active_run and active_run.is_dir() else {}
    quality=report.get('quality_review') or {}
    status=report.get('status','not_completed')
    if quality.get('passed') is False or final.get('passed') is False:
        status='candidate'
    counts=generation_counts(directory)
    return {'project':project,'edition':edition,'episode_dir':str(directory),
            'source_episode_dir':str(source_directory),'active_episode_dir':str(directory),
            'active_run_dir':str(active_run) if active_run else None,
            'active_agent_status':run_state.get('status'),'active_phase':run_state.get('phase'),
            'status':status,
            'source_status':report.get('status'),'generation_counts':counts,
            'generation_total':sum(counts.values()),'final_review_complete':final.get('complete'),
            'final_review_passed':final.get('passed'),
            'remaining_findings':len(final.get('issues',[])) if final.get('complete') is True else None,
            'final_video':final.get('movie') or (report.get('assembly') or {}).get('final_video')}


def active_runner(root: Path, lane: str, project: str = 'adventure') -> int | None:
    """A saved failure report is not the state of a currently running resume."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc/'cwd').resolve() != root:
                continue
            args=(proc/'cmdline').read_bytes().decode().split('\0')
            if 'scripts/interactive_video.py' in args and 'run' in args and '--lane' in args:
                selected=args[args.index('--project')+1] if '--project' in args else 'adventure'
                if args[args.index('--lane')+1] == lane and selected==project:
                    return int(proc.name)
        except (OSError, UnicodeError, IndexError):
            continue
    return None


def pack(directory: Path):
    if (directory/'suspense_groups.json').is_file():
        from novel_manga.interactive.suspense import pack_suspense
        return pack_suspense(directory)
    if (directory/'authored_groups.json').is_file():
        script=read_json(directory/'chapter_script.json',{})
        if not any(s.get('authored_frame') for s in script.get('shots',[])):
            from novel_manga.interactive.authored import pack_authored
            return pack_authored(directory)
        from novel_manga.interactive.romance import pack_romance
        return pack_romance(directory)
    from novel_manga.application.packing.context import load_context
    from novel_manga.application.packing.service import compile_plan
    from novel_manga.application.preparation.readiness import plan_issues
    script=read_json(directory/'chapter_script.json',{})
    context=load_context(directory,directory.parent/'story_bible.json',None,None,None,None)
    plan,decisions=compile_plan(script,context)
    issues=plan_issues(plan,script)
    if issues:raise ValueError(f'Invalid prepared plan: {issues}')
    atomic_write_json(directory/'clip_plan.json',plan)
    atomic_write_json(directory/'pack_decisions.json',decisions)
    return plan


def lane_workspace(root: Path, lane: str, project: str = 'adventure') -> Path:
    configure(root,lane,project=project)
    master=root/f'outputs/{project}-master'
    novel=root/f'outputs/{project}-{lane}'
    if novel.exists():
        return novel.resolve()
    episode=master/(master.name+'_1')
    pack(episode)  # includes the common voice reference now present on disk
    plan=read_json(episode/'clip_plan.json',{})
    missing={ref['path'] for c in plan['clips'] for ref in c.get('references',[])
             if not (master/ref['path']).is_file()}
    if missing:raise FileNotFoundError(f'Common assets not ready: {sorted(missing)}')
    shutil.copytree(master,novel,ignore=shutil.ignore_patterns('work','probe','*.log'))
    (novel/(master.name+'_1')).rename(novel/(novel.name+'_1'))
    (novel/'owner').write_text(f'Codex / {project} full demo / provider={lane} / authorized 2026-10-12\n')
    atomic_write_json(novel/'comparison.json',{'provider':lane,'source':str(master.relative_to(root)),
                      'source_script':str(episode/'chapter_script.json'),
                      'requested_body_seconds':sum(c['request_seconds'] for c in plan['clips']),
                      'resolution_note':'Native sampling recorded per provider; final frame follows the project profile'})
    return novel


def can_resume_unsubmitted(episode: Path, report: dict) -> bool:
    return (report.get('status') == 'clips_failed' and bool(report.get('clips'))
            and all(not c.get('attempts') and not c.get('selected') for c in report['clips'])
            and not any((episode/'work/clips').rglob('*.task.json')))


def run_episode(root: Path, lane: str, *, project: str = 'adventure', resume_unsubmitted: bool = False, resume: bool = False,
                edition: str | None = None, h3_input_mode: str | None = None,
                sd_input_mode: str | None = None, agent_config: dict | None = None,
                postproduction_plan: Path | None = None) -> dict:
    if sd_input_mode is not None and lane!='sd20-fast':raise ValueError('SD first-frame entry requires sd20-fast')
    if edition:
        novel=edition_directory(root,project,edition)
    else:
        novel=lane_workspace(root,lane,project)
    episode=novel/(novel.name+'_1')
    agent_config = _agent_task_config(episode, agent_config)
    if postproduction_plan is not None:
        from novel_manga.application.agents.episode.postproduction import authorize_cutaway
        authorize_cutaway(episode,read_json(postproduction_plan,{}))
    if h3_input_mode is not None and lane!='h3':raise ValueError('first-frame mode requires H3')
    if lane!='h3' and read_json(novel/'profile.json',{}).get('h3_input_mode')=='first-frame':
        raise ValueError('first-frame mode requires H3; prepare a separate SD edition instead of switching this task')
    if lane!='sd20-fast' and read_json(novel/'profile.json',{}).get('sd_input_mode')=='first-frame':
        raise ValueError('Saved SD first-frame edition requires sd20-fast')
    from .first_frame_entry import select_mode
    select_mode(novel,h3_input_mode)
    if sd_input_mode is not None:
        from .sd_first_frames import select_mode as select_sd_mode
        select_sd_mode(novel,sd_input_mode)
    music_plan=read_json(episode/'chapter_script.json',{}).get('music_plan')
    if read_json(novel/'profile.json',{}).get('sd_input_mode')=='first-frame' and not music_plan:
        configure(root,lane,project=project)
        from .sd_first_frames import prepare as prepare_sd_frames
        prepare_sd_frames(root,novel)
    runner=runner_for(root,novel,lane,project=project)
    report_path=episode/'thin_media_report.json'
    label=edition.removeprefix(project+'-') if edition else lane
    destination=(root/f'delivery/{project}/{label}').resolve()
    if read_json(novel/'profile.json',{}).get('h3_input_mode')=='first-frame' or music_plan:
        if music_plan and read_json(novel/'profile.json',{}).get('h3_input_mode')!='first-frame':
            from novel_manga.application.repair.history import generation_counts
            if any(generation_counts(episode).values()):
                raise ValueError('Keep this existing production and its take budgets; story-music Agent entry requires a new prepared edition')
        from .first_frame_entry import run as run_first_frames
        return run_first_frames(root,novel,project=project,destination=destination,resume=resume,config=agent_config,lane=lane)
    saved_final=read_json(episode/'final_cut_review.json',{})
    saved_movie=Path(saved_final.get('movie') or '')
    if saved_movie.is_file() and saved_movie.parent.parent.name=='versions':
        saved_destination=saved_movie.parent.parent.parent
        saved_receipt=read_json(saved_destination/'delivery.json',{})
        if saved_receipt.get('final_video')==str(saved_movie):
            destination=saved_destination
    # A completed attempt is inspected/exported, never silently given a new take budget.
    retry = resume_unsubmitted and report_path.is_file()
    if retry:
        previous=read_json(report_path,{})
        if not can_resume_unsubmitted(episode,previous):
            raise ValueError('Existing submission/take found; retain its budget and use the existing repair workflow')
        atomic_write_json(episode/'unsubmitted-failure.json',previous)
    if report_path.is_file() and not retry and not resume:
        report=read_json(report_path,{})
    else:
        if lane=='h3':
            from novel_manga.application.rendering.h3 import convert
            plan=read_json(episode/'clip_plan.json',{})
            def translate(clip):
                problems=[]
                convert(clip,episode_dir=episode,problems=problems)
                return clip['clip_id'],problems
            with ThreadPoolExecutor(2) as pool:
                problems=dict(pool.map(translate,[c for c in plan['clips'] if c['kind']=='video']))
            atomic_write_json(episode/'clip_plan.json',plan)
            atomic_write_json(episode/'translation-result.json',problems)
            failed=[c['clip_id'] for c in plan['clips'] if c['kind']=='video' and not c.get('prompt_h3')]
            if failed:raise RuntimeError(f'H3 translation not ready: {failed}')
            runner=runner_for(root,novel,lane,project=project)
        from novel_manga.interactive.agent import run
        selected_agent_config = None if agent_config is None else {
            **agent_config, '_agent_config_explicit': True}
        report=run(runner,lane,resume=resume,destination=destination,config=selected_agent_config)
    from novel_manga.interactive.delivery import deliver, finish_reviewed_delivery
    receipt=(finish_reviewed_delivery(runner,report,destination)
             if report.get('final_cut_review') else deliver(runner,report,destination))
    print(json.dumps(receipt,ensure_ascii=False,indent=2),flush=True)
    return receipt
