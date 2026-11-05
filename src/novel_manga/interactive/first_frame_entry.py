"""Opt an unrendered edition into the existing persistent episode Agent."""
from __future__ import annotations

from pathlib import Path
from novel_manga.util import atomic_write_json,read_json


def select_mode(novel,mode):
    if mode is None:return
    if mode!='first-frame':raise ValueError('Unsupported H3 input mode')
    episode=novel/(novel.name+'_1');path=novel/'profile.json';profile=read_json(path,{})
    if profile.get('h3_input_mode')==mode and (episode/'first-frame-mode.json').is_file():return
    from novel_manga.application.repair.history import generation_counts
    if (any(generation_counts(episode).values()) or any((episode/'work').rglob('*.task.json'))
            or any((episode/'work').rglob('*.mp4'))):
        raise ValueError('first-frame mode requires a new unrendered edition; retain this edition and its history')
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
    if profile.get('h3_speech_policy')not in {'reference',NATIVE_SPEECH_POLICY}:
        raise ValueError('Select the H3 speech policy in profile.json before first-frame preparation; do not infer it from voice files')
    groups_path=episode/'authored_groups.json'
    groups=read_json(groups_path,None)
    if not groups:raise ValueError('first-frame entry requires approved authored shot groups')
    old=episode/'first-frame-original-groups.json'
    if not old.exists():atomic_write_json(old,groups)
    script=read_json(episode/'chapter_script.json')
    indexes=[i for g in groups for i in g['shot_indexes']]
    shots={s['index']:s for s in script['shots']}
    if set(indexes)!=set(shots) or len(indexes)!=len(shots):raise ValueError('authored groups must cover each source shot once')
    # Every authored cut gets its own native frame zero; no middle-keyframe fiction.
    atomic_write_json(groups_path,[{'source_segment':shots[i].get('shot_id') or str(i),
                                    'shot_indexes':[i],'request_seconds':max(4,shots[i]['duration_seconds'])} for i in indexes])
    profile.update(h3_input_mode=mode,h3_extra_reference_policy='needed')
    atomic_write_json(path,profile)
    from .authored import pack_authored
    pack_authored(episode)
    atomic_write_json(episode/'first-frame-mode.json',{'mode':mode,'source_shots':len(indexes)})
    budget_path=novel/'production-budget.json';budget=read_json(budget_path,{})
    if budget:
        budget['video_units']=len(indexes);atomic_write_json(budget_path,budget)


def run(root,novel,*,project,destination,resume=False,config=None,lane='h3'):
    from novel_manga.application.agents.episode.context import create_context
    from novel_manga.application.agents.episode.flow import run_episode
    from novel_manga.application.agents.sandbox import load_config
    from .runtime import configure,runner_for
    configure(root,lane,project=project)
    profile=read_json(novel/'profile.json',{})
    first_frame=profile.get('h3_input_mode')=='first-frame' or profile.get('sd_input_mode')=='first-frame'
    import os
    if profile.get('native_h3_short_edge'):os.environ['NOVEL_H3_SHORT_EDGE']=str(profile['native_h3_short_edge'])
    ctx=create_context(novel,novel.name+'_1',output_root=root/('outputs/episode-agent-firstframes'if first_frame else'outputs/episode-agent-music'),input_mode='script')
    from novel_manga.application.agents.episode.postproduction import transfer_cutaway
    transfer_cutaway(novel/(novel.name+'_1'),ctx.episode_dir)
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
    if not (ctx.run_dir/'frozen.json').is_file()and profile.get('h3_speech_policy')not in {'reference',NATIVE_SPEECH_POLICY}:
        raise ValueError('Select the H3 speech policy before starting a new H3 Agent task')
    ctx.run_dir.mkdir(parents=True,exist_ok=True)
    (ctx.run_dir/'owner').write_text('Interactive '+('first-frame'if first_frame else'story-music')+' edition / existing persistent Agent / '+str(novel)+'\n')
    atomic_write_json(ctx.run_dir/'interactive-delivery.json',{'destination':str(destination),'project':project})
    explicit_agent_config = config is not None
    config=load_config(root/'configs/agent_sandbox.json') if config is None else dict(config)
    # This is the existing Agent creation/production entry, with host media tools.
    # The separate fixed novel pipeline mode would run video before the Agent can supply openings.
    config={**config,'production_mode':'agent','_agent_config_explicit':explicit_agent_config}
    if resume:
        config['resume_session_on_retry']=True
    result=run_episode(ctx,config=config,retry_issues=resume)
    while result.get('status')=='deferred' and result.get('remaining_seconds',0)>0:
        # Continue only the existing scheduler slice; never renew a stopped budget.
        result=run_episode(ctx,config=config)
    atomic_write_json(novel/('first-frame-agent-run.json'if first_frame else'music-agent-run.json'),{'run_dir':str(ctx.run_dir),'active_novel_dir':str(ctx.novel_dir),
        'active_episode_dir':str(ctx.episode_dir),'result':result})
    report=read_json(ctx.episode_dir/'thin_media_report.json',{})
    if not report.get('assembly'):
        return {'status':'blocked','agent':result,'run_dir':str(ctx.run_dir)}
    from .delivery import finish_reviewed_delivery
    runner=runner_for(root,ctx.novel_dir,lane,project=project)
    receipt=finish_reviewed_delivery(runner,report,destination)
    return {**receipt,'agent_run_dir':str(ctx.run_dir),'agent_status':result.get('status')}
