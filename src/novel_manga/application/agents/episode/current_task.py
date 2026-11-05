"""A current repair view; complete state and history remain available on disk."""
from __future__ import annotations

import os

from novel_manga.util import atomic_write_json,read_json
from .memory_summary import summarize_case


def prepare(ctx,status,diagnosis,memories):
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    by_id={c['clip_id']:c for c in plan.get('clips',[])}
    media_rows={r['clip_id']:r for r in read_json(ctx.episode_dir/'thin_media_report.json',{}).get('clips',[])}
    adopted=bool(status.get('adopted',bool(by_id)))
    actions=status.get('repair_actions') or {}
    selected=set(status['first_pass_remaining']) if 'first_pass_remaining' in status else (
        {cid for cid,ops in actions.items() if ops} | set(status.get('unreviewed_clips') or [])
        | set(status.get('publication_blockers') or []))
    targets=[]
    from .requests import dialogue_slots
    for cid in sorted(selected):
        clip=by_id.get(cid,{})
        saved=(status.get('input_repairs') or {}).get(cid) or {}
        target={'clip_id':cid,'shot_indexes':clip.get('shot_indexes',[]),
            'dialogue_slots':dialogue_slots(clip),
            'request_seconds':clip.get('request_seconds'),
            'references':[{k:r[k] for k in ('name','role','asset_id','image') if k in r}
                          for r in clip.get('references',[])],
            'actions':actions.get(cid,[]),
            'upstream_actions':(status.get('upstream_actions')or{}).get(cid,[]),
            'risk_choices':(status.get('risk_choices') or {}).get(cid,{}),
            'remaining_takes':(status.get('remaining_takes') or {}).get(cid),
            'input':{'operations':(status.get('input_operations') or {}).get(cid, {}),
                'remaining_attempts':(status.get('remaining_input_attempts') or {}).get(cid),
                'resume_saved':saved.get('resume_action'),
                'can_revise_saved':bool(saved.get('revision_available')),
                'failed_step':saved.get('step'),'error':saved.get('error'),
                'error_details':saved.get('error_details'),'next':saved.get('next_action')}}
        from novel_manga.application.review.confirmed import clip_review_criteria
        criteria=clip_review_criteria(ctx.episode_dir,cid)
        if criteria:
            target['review_criteria']=criteria
        if (media_rows.get(cid)or{}).get('error'):
            target['last_media_error']=media_rows[cid]['error']
            target['media_error_evidence']='input/latest-render.json'
        targets.append(target)
    issues=[r for r in diagnosis.get('issues',[]) if not status.get('adopted')
            or not r.get('clips') or selected.intersection(r['clips'])]
    files=[f'input/{name}' for name in ('source.txt','segments.json','chapter_script.json',
        'clip_plan.json','catalog.json','latest-preparation.json','latest-first-frames.json','latest-render.json','latest-review.json',
        'preparation/exception.json','source-draft-check.json','request-draft.json','manifest.json',
        'reading/identity_binding_review.json')
        if (ctx.work_dir/'input'/name).is_file()]
    from .postproduction import current_task_summary
    postproduction=current_task_summary(ctx.episode_dir)
    if postproduction:
        source=ctx.episode_dir/'work/postproduction/cutaway.json'
        atomic_write_json(ctx.work_dir/postproduction['plan_file'],read_json(source,{}))
        status['postproduction']=postproduction
        files.append(postproduction['plan_file'])
    else:
        status.pop('postproduction',None)
    if not adopted and (ctx.work_dir/'output/episode.json').is_file():files.append('output/episode.json')
    recent=[summarize_case(memory) for memory in memories[:2]]
    job=status.get('job') or {}
    outcome=job.get('result') or {}
    last={k:job.get(k) for k in ('id','action','status','error')}
    last['result']={k:outcome[k] for k in ('ok','passed','error','changed','input_repaired','input_reused','unresolved_clips','validated_clips','validation_targets','validation_scope') if k in outcome}
    if 'pending'in outcome and job.get('action')=='apply' and job.get('status')=='completed':
        last['result']['unresolved_clips']=outcome['pending']
    for field in ('blocked','argument_errors'):
        if isinstance(outcome.get(field),dict):last['result'][field]={k:v for k,v in outcome[field].items() if k in selected}
    if job.get('action')=='first_frames':
        if outcome.get('argument_errors'):
            last['result']['retry_clips']=sorted(outcome['argument_errors'])
        last['result']['first_frame_failures']={cid:{'error':row.get('error'),
            'problems':(row.get('review')or{}).get('problems',[]),'evidence':row.get('evidence')}
            for cid,row in (outcome.get('clips')or{}).items()if cid in selected and row.get('ok')is False}
        last['result']['video_input_failures']={cid:row['error']
            for cid,row in (outcome.get('video_stream')or{}).items()if cid in selected and row.get('error')}
    first_pass = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
    brief={'phase':read_json(ctx.work_dir/'input/manifest.json',{}).get('agent_phase'),
        'workflow_scope':'first_pass' if first_pass else 'full_production',
        'mode':'adopted_repair' if adopted else 'provisional_draft',
        'next_action':status.get('next_action'),
        'author_session':read_json(ctx.work_dir/'episode-session.json',{}).get('session_id'),
        'upstream_route':status.get('upstream_route'),
        **({'repair_capabilities':status['repair_capabilities']} if status.get('repair_capabilities') else {}),
        **({'first_frames':status['first_frames']} if status.get('first_frames') else {}),
        **({'music_selection':status['music_selection']} if status.get('music_selection') else {}),
        **({'audio_mix':status['audio_mix']} if status.get('audio_mix') else {}),
        **({'postproduction':postproduction} if postproduction else {}),
        'targets':targets,'issues':issues,'last_operation':last,'evidence_files':files,
        'full_status':'input/current-status.json','reference_catalog':'input/catalog.json','related_history':recent,
        'tool_source':'/opt/novel-code/novel_manga/application/agents/episode',
        'execution_rules':[
            ('This phase only prepares and generates missing first videos; film review and finish wait for the next phase.'
             if first_pass else
             'Full scope: obey host review/music/mix/finish.')
        ]+([
            'Only current targets; old errors are history.',
            'check then apply output/repair.json. input: clip_id/reason/source_quote/shots:[{index}]; '
            'speaker_map=old_speaker_name→new_speaker_name, not dialogue.'] if adopted else [
            'No adopted plan exists. Repair the saved provisional output/episode.json using the planner failure and source; call check, then adopt.',
            'For missing draft assets only, use output/repair.json with source-backed asset requests and apply; bind returned IDs in the draft.',
            'Empty adopted targets do not mean the draft is complete. Read the saved draft/check evidence.'])+[
            'resume:true executes the saved decision unchanged; do not combine it with new shots/assets/source_quote.',
            'remaining_attempts does not renew a spent revision of the same pending candidate.',
            'For an assembly ambience/silence problem with passed native takes, inspect audio_mix and source evidence before proposing new video.',
            'Use exact risk_choices values. Accepted tool job means submitted; inspect its completed result.',
            'A rejected opening is not an unsubmitted image: read first_frames.clips[clip_id].last_rejection and its evidence. '
            'Repeating the unchanged first_frames row reuses the rejection; first correct source-supported opening inputs or reference selection. '
            'last_media_error is the retained last failure for that clip, not proof it survives a newer input repair; compare current inputs and repair results.',
            'unresolved_clips (pending in old completed apply receipts) are remaining repair targets, not running jobs. When busy=false and '
            'job.status=completed, take the next legal action or finish; do not wait for these clips to change on their own.',
            'Related history distinguishes changed operations, independently accepted clips and failed attempts. '
            'An accepted episode does not prove every attempted remedy worked. Match the concrete issue to current evidence; '
            'not_recorded means no repair recipe was retained, and unresolved cases are not verified recipes.',
            'repair_assets/repack are separate upstream operations; source/image candidates do not grant video or certify acceptance.',
            *(['A user-authorized cutaway is already registered. Read input/postproduction-plan.json; do not edit or replace this plan. '
               'Continue the current review and call finish; the host applies the cutaway during normal assembly.']
              if postproduction else []),
            'Only a target with host-provided review_criteria may use review_file on keep. The current production session authors one output/scoped-review-<clip_id>.json per clip with schema scoped-agent-review-v1, exact author_session from episode-session.json, clip_id, current selected video path/take, selected raw task_id, exact source_segment_ids and source_evidence [{segment_id,text}] copied from current segments.json, exact review_criteria, assessment pass/minor/fail, judgment, observations [{kind:video_frame|reference,file,times or time_seconds,observation}], and reviewed_findings [{finding,disposition:resolved|not_demonstrated|upheld}] addressing every current automated blocker. Optionally include independent_review_file pointing to the supplied independent reviewer JSON in this work directory. Set review_file to this path on that clip keep decision, then call check and apply. This does not skip speech or media gates and is unavailable for clips without host criteria.',
            'For deterministic tool bugs, inspect the read-only source and report the concrete failing field; do not repeat the same rejected operation.']}
    if job.get('action')=='first_frames' and outcome.get('argument_errors'):
        retry=last['result']['retry_clips']
        brief['execution_rules'].append(
            f'For this first_frames argument retry, submit only {retry!r}. Preserve validated_clips and their valid fields; '
            'do not rewrite or resubmit validated rows. Correct the returned invalid_fields, then run first_frames again.')
    playbook=read_json(ctx.work_dir/'input/manifest.json',{}).get('skill_references',{}).get('repair_playbook')
    if playbook:
        brief['repair_playbook']=playbook
        brief['execution_rules'].append('For review/input repair, read repair_playbook if it is new to this session; '
            'then use the section matching current evidence and capabilities. A resumed old conversation also receives this route.')
    if not isinstance(brief.get('next_action'),dict):
        from .readiness import next_action
        brief['next_action']=next_action(ctx,status)
    if not isinstance(status.get('next_action'),dict):
        status['next_action']=brief['next_action']
    atomic_write_json(ctx.work_dir/'input/current-status.json',status)
    atomic_write_json(ctx.work_dir/'input/current-task.json',brief)
    return brief
