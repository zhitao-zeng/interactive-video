"""Run each provider in its own directory against the same prepared episode."""
from pathlib import Path
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from novel_manga.interactive.runtime import configure, runner_for
from novel_manga.util import atomic_write_json, read_json


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
        return novel
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
                edition: str | None = None) -> dict:
    if edition:
        if Path(edition).name != edition:raise ValueError('Edition must be an output directory name')
        novel=root/'outputs'/edition
        if not (novel/(edition+'_1')/'clip_plan.json').is_file():raise FileNotFoundError('Prepare the edition before rendering')
    else:
        novel=lane_workspace(root,lane,project)
    episode=novel/(novel.name+'_1')
    runner=runner_for(root,novel,lane,project=project)
    report_path=episode/'thin_media_report.json'
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
        report=run(runner,lane,resume=resume)
    from novel_manga.interactive.delivery import deliver
    label=edition.removeprefix(project+'-') if edition else lane
    receipt=deliver(runner,report,root/f'delivery/{project}/{label}')
    print(json.dumps(receipt,ensure_ascii=False,indent=2),flush=True)
    return receipt
