"""A current repair view; complete state and history remain available on disk."""
from __future__ import annotations

import os

from novel_manga.util import atomic_write_json,read_json
from .memory_summary import summarize_case


def _context_clip_ids(ctx,plan,script,cid,grant):
    target=next((clip for clip in plan.get('clips',[]) if clip.get('clip_id')==cid),None)
    if target is None:return set()
    row=None
    if grant and grant.get('operation')=='input' and grant.get('shot_indexes'):
        row={'clip_id':cid,'clip_ids':[cid],
             'shots':[{'index':index} for index in grant['shot_indexes']]}
    else:
        from .input_records import latest_input_records
        saved=latest_input_records(ctx.episode_dir).get(cid)
        if saved and saved[1].get('decision'):
            row=saved[1]['decision']
    if row is None:return set()
    from .input_candidate import resolve_input_scope
    from .input_contracts import expand_shared_scope
    from .input_repair import _source_review_shot_context
    row=expand_shared_scope(row,plan)
    scope=resolve_input_scope(ctx,row,plan,script,target)
    context=set(scope.targets)-{cid}
    shots=script.get('shots') or []
    known_indexes={shot.get('index',index) for index,shot in enumerate(shots,1)}
    review_indexes=known_indexes&set(scope.source_review_indexes)
    reviewed=_source_review_shot_context(shots,shots,review_indexes) if review_indexes else []
    sibling_indexes={sibling['index'] for row in reviewed for sibling in row['source_group']}
    context.update(clip['clip_id'] for clip in plan.get('clips',[])
                   if clip['clip_id']!=cid and sibling_indexes.intersection(clip.get('shot_indexes') or []))
    return context


def prepare(ctx,status,diagnosis,memories):
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    script=read_json(ctx.episode_dir/'chapter_script.json',{})
    by_id={c['clip_id']:c for c in plan.get('clips',[])}
    media_rows={r['clip_id']:r for r in read_json(ctx.episode_dir/'thin_media_report.json',{}).get('clips',[])}
    adopted=bool(status.get('adopted',bool(by_id)))
    actions=status.get('repair_actions') or {}
    job=status.get('job') or {}
    outcome=job.get('result') or {}
    route=status.get('next_action') if isinstance(status.get('next_action'),dict) else {}
    explicit=set()
    for value in (route.get('blocked'),route.get('source_input_conflicts'),
                  outcome.get('blocked'),outcome.get('argument_errors'),
                  outcome.get('invalid_fields'),outcome.get('source_input_conflicts')):
        if isinstance(value,dict):explicit.update(set(value)&set(by_id))
    if explicit:
        selected=explicit
    else:
        selected=set(status['first_pass_remaining']) if 'first_pass_remaining' in status else (
            {cid for cid,ops in actions.items() if ops} | set(status.get('unreviewed_clips') or [])
            | set(status.get('publication_blockers') or []))
    selected.update(cid for issue in diagnosis.get('issues',[]) for cid in issue.get('clips') or [] if cid in by_id)
    if 'first_pass_remaining' in status:selected&=set(status['first_pass_remaining'])
    primary=set(selected)
    frame_rows=(status.get('first_frames') or {}).get('clips') or {}
    outcome_clips=outcome.get('clips') or {}
    if isinstance(outcome_clips,list):
        outcome_clips={row['clip_id']:row for row in outcome_clips if isinstance(row,dict)}
    context=set()
    for cid in primary:
        grant=(frame_rows.get(cid) or {}).get('repair_grant') or (outcome_clips.get(cid) or {}).get('repair_grant')
        context.update(_context_clip_ids(ctx,plan,script,cid,grant))
    context-=primary
    selected=primary|context
    targets=[]
    from .requests import dialogue_slots
    for cid in sorted(selected):
        clip=by_id.get(cid,{})
        target={'clip_id':cid,'shot_indexes':clip.get('shot_indexes',[]),
            'dialogue_slots':dialogue_slots(clip),
            'request_seconds':clip.get('request_seconds'),
            'references':[{k:r[k] for k in ('name','role','asset_id','image') if k in r}
                          for r in clip.get('references',[])]}
        if cid in context:
            target['context_only']=True
            targets.append(target)
            continue
        saved=(status.get('input_repairs') or {}).get(cid) or {}
        target.update(actions=actions.get(cid,[]),
            upstream_actions=(status.get('upstream_actions')or{}).get(cid,[]),
            risk_choices=(status.get('risk_choices') or {}).get(cid,{}),
            remaining_takes=(status.get('remaining_takes') or {}).get(cid),
            input={'operations':(status.get('input_operations') or {}).get(cid, {}),
                'remaining_attempts':(status.get('remaining_input_attempts') or {}).get(cid),
                'resume_saved':saved.get('resume_action'),
                'can_revise_saved':bool(saved.get('revision_available')),
                'failed_step':saved.get('step'),'error':saved.get('error'),
                'error_details':saved.get('error_details'),'next':saved.get('next_action')})
        grant=(frame_rows.get(cid) or {}).get('repair_grant') or (outcome_clips.get(cid) or {}).get('repair_grant')
        if grant:target['input']['grant']=grant
        from novel_manga.application.review.confirmed import clip_review_criteria
        criteria=clip_review_criteria(ctx.episode_dir,cid)
        if criteria:
            target['review_criteria']=criteria
        if (media_rows.get(cid)or{}).get('error'):
            target['last_media_error']=media_rows[cid]['error']
            target['media_error_evidence']='input/latest-render.json'
        targets.append(target)
    issues=[r for r in diagnosis.get('issues',[]) if not status.get('adopted')
            or not r.get('clips') or primary.intersection(r['clips'])]
    music_needed=(route.get('required_action')=='select_music' or route.get('action')=='select_music'
                  or job.get('action')=='select_music')
    audio_needed=(route.get('required_action')=='mix_audio' or route.get('action')=='mix_audio'
                  or job.get('action')=='mix_audio')
    names=['source.txt','segments.json','chapter_script.json','clip_plan.json','catalog.json',
        'latest-preparation.json','latest-first-frames.json','latest-render.json','latest-review.json',
        'preparation/exception.json','source-draft-check.json','request-draft.json','manifest.json',
        'reading/identity_binding_review.json']
    if music_needed:names+=['latest-music-search.json','latest-music-apply.json']
    files=[f'input/{name}' for name in names if (ctx.work_dir/'input'/name).is_file()]
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
    last={k:job.get(k) for k in ('id','action','status','error')}
    last['result']={k:outcome[k] for k in ('ok','passed','error','changed','input_repaired','input_reused',
        'unresolved_clips','validated_clips','validation_targets','validation_scope','required_action','message',
        'image_candidates_allocated','retryable','service_interrupted') if k in outcome}
    if 'pending'in outcome and job.get('action')=='apply' and job.get('status')=='completed':
        last['result']['unresolved_clips']=outcome['pending']
    for field in ('blocked','argument_errors','invalid_fields','source_input_conflicts'):
        if isinstance(outcome.get(field),dict):last['result'][field]={k:v for k,v in outcome[field].items() if k in primary}
    if job.get('action')=='first_frames':
        if outcome.get('argument_errors'):
            last['result']['retry_clips']=sorted(outcome['argument_errors'])
        last['result']['first_frame_failures']={cid:{'error':row.get('error'),
            'problems':(row.get('review')or{}).get('problems',[]),'evidence':row.get('evidence'),
            **{k:row[k] for k in ('repair_origin','repair_grant','invalid_fields') if k in row}}
            for cid,row in outcome_clips.items()if cid in primary and row.get('ok')is False}
        last['result']['video_input_failures']={cid:row['error']
            for cid,row in (outcome.get('video_stream')or{}).items()if cid in primary and row.get('error')}
    if job.get('action')=='select_music':
        for field in ('search_id','query','candidates','unavailable_ids','requires_explicit_adoption',
                      'retrieval_method','backend_message','raw_result','music_adopted','music_declined',
                      'assessment','source_review','music'):
            if field in outcome:last['result'][field]=outcome[field]
    frame_brief=None
    if status.get('first_frames'):
        frame_brief={key:status['first_frames'][key] for key in
                     ('mode','extra_reference_policy','command','schema') if key in status['first_frames']}
        projected={}
        for cid in sorted(selected):
            if cid not in frame_rows:continue
            row=dict(frame_rows[cid])
            grant=(row.get('repair_grant') or {}).get('shot_fields') or []
            if cid in context:
                for key in ('authored_opening_frame','repair_grant','next_action'):row.pop(key,None)
                row['context_only']=True
            elif 'opening_frame' not in grant:
                row.pop('authored_opening_frame',None)
            projected[cid]=row
        frame_brief['clips']=projected
    first_pass = os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1'
    audio=status.get('final_audio_review') or {}
    audio_brief={key:audio[key] for key in ('complete','passed') if key in audio}
    if audio:
        audio_brief['issues']=[{key:issue[key] for key in
            ('problem','start','end','clip_ids','source_basis','evidence') if key in issue}
            for issue in audio.get('issues') or []]
    audio_targets={cid for issue in audio.get('issues') or [] for cid in issue.get('clip_ids') or []}
    native_audio_repair=(audio.get('complete') is True and audio.get('passed') is False
        and not audio.get('errors') and (status.get('audio_mix') or {}).get('candidates')==[]
        and any(target['clip_id'] in audio_targets and {'edit','retake'}.intersection(target.get('actions') or [])
                and type(target.get('remaining_takes')) is int and target['remaining_takes']>0
                for target in targets))
    brief={'phase':read_json(ctx.work_dir/'input/manifest.json',{}).get('agent_phase'),
        'workflow_scope':'first_pass' if first_pass else 'full_production',
        'mode':'adopted_repair' if adopted else 'provisional_draft',
        'projection':'current_exception_v1',
        'next_action':status.get('next_action'),
        'author_session':read_json(ctx.work_dir/'episode-session.json',{}).get('session_id'),
        'upstream_route':status.get('upstream_route'),
        **({'repair_capabilities':status['repair_capabilities']} if status.get('repair_capabilities') else {}),
        **({'first_frames':frame_brief} if frame_brief else {}),
        **({'music_selection':status['music_selection']} if music_needed and status.get('music_selection') else {}),
        **({'audio_mix':status['audio_mix']} if audio_needed and status.get('audio_mix') else {}),
        **({'final_audio_review':audio_brief} if audio_brief else {}),
        **({'postproduction':postproduction} if postproduction else {}),
        'targets':targets,'issues':issues,'last_operation':last,'evidence_files':files,
        'full_status':'input/current-status.json','reference_catalog':'input/catalog.json','related_history':recent,
        'tool_source':'/opt/novel-code/novel_manga/application/agents/episode',
        'execution_rules':[
            ('This phase only prepares and generates missing first videos; film review and finish wait for the next phase.'
             if first_pass else
             'Follow the current required action; unrelated stages remain with the host.')
        ]+([
            'Only targets; context_only entries are read-only dependencies and do not expand grants.',
            'repair.json decisions:[{clip_id,action:"input",reason,source_quote,shots:[{index}]}]; '
            'complete opening_frame=image_prompt+visible_characters+reference_subjects; speaker_map=old_speaker_name→new_speaker_name. Check then apply.'] if adopted else [
            'No adopted plan exists. Repair the saved provisional output/episode.json using the planner failure and source; call check, then adopt.',
            'For missing draft assets only, use output/repair.json with source-backed asset requests and apply; bind returned IDs in the draft.',
            'Empty adopted targets do not mean the draft is complete. Read the saved draft/check evidence.'])+[
            'resume:true reuses the saved decision alone; never add patches.',
            'remaining_attempts does not renew a spent revision of the same pending candidate.',
            *(['For an assembly ambience/silence problem with passed native takes, inspect audio_mix and source evidence before proposing new video. '
               + ('Empty audio_mix.candidates means only mix_audio is unavailable; it does not mean no legal repair remains. '
                  'For a target explicitly listing edit or retake with remaining_takes > 0, source-supported request edits or native-audio retakes remain legal; '
                  'an exhausted input allowance does not revoke these separately listed actions. Choose using current source and audio evidence. '
                  'Do not use BGM or arbitrary noise to bypass acceptance, or repeat an unchanged failed operation without new evidence.'
                  if native_audio_repair else '')]
              if audio_needed or postproduction or native_audio_repair else []),
            'Use exact risk_choices values. Accepted tool job means submitted; inspect its completed result.',
            *(['A rejected opening is not an unsubmitted image: read first_frames.clips[clip_id].last_rejection and its evidence. '
               'Repeating the unchanged first_frames row reuses the rejection; first correct source-supported opening inputs or reference selection.']
              if frame_brief or job.get('action')=='first_frames' else []),
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
            *(['Only a target with host-provided review_criteria may use review_file on keep. The current production session authors one output/scoped-review-<clip_id>.json per clip with schema scoped-agent-review-v1, exact author_session from episode-session.json, clip_id, current selected video path/take, selected raw task_id, exact source_segment_ids and source_evidence [{segment_id,text}] copied from current segments.json, exact review_criteria, assessment pass/minor/fail, judgment, observations [{kind:video_frame|reference,file,times or time_seconds,observation}], and reviewed_findings [{finding,disposition:resolved|not_demonstrated|upheld}] addressing every current automated blocker. Optionally include independent_review_file pointing to the supplied independent reviewer JSON in this work directory. Set review_file to this path on that clip keep decision, then call check and apply. This does not skip speech or media gates and is unavailable for clips without host criteria.']
              if any(row.get('review_criteria') for row in targets) else []),
            'For deterministic tool bugs, inspect the read-only source and report the concrete failing field; do not repeat the same rejected operation.']}
    if job.get('action')=='first_frames' and outcome.get('argument_errors'):
        retry=last['result']['retry_clips'];outcome_clips=outcome.get('clips') or {}
        source=[cid for cid in retry if (outcome_clips.get(cid) or {}).get('repair_origin')=='source']
        registration=[cid for cid in retry if (outcome_clips.get(cid) or {}).get('repair_origin')=='registration']
        invalid=outcome.get('invalid_fields') or {}
        if (outcome.get('required_action')=='apply' and not outcome_clips and invalid
                and set(invalid)==set(retry)
                and all(fields==['opening_frame.reference_subjects'] for fields in invalid.values())):
            source=list(retry)
        ordinary=[cid for cid in retry if cid not in {*source,*registration}]
        if source:
            brief['execution_rules'].append(
                f'For {source!r}, correct source field opening_frame.reference_subjects with the granted complete '
                'opening_frame input decision; call check then apply. Do not retry unchanged first_frames or alter other story fields.')
        if registration:
            brief['execution_rules'].append(
                f'For {registration!r}, reference registration is unresolved. Restore the registered clip binding, '
                'file or frozen hash before first_frames. Registration does not authorize story edits or an input grant.')
        if ordinary:
            brief['execution_rules'].append(
                f'For this first_frames argument retry, submit only {ordinary!r}. Preserve validated_clips and their valid fields; '
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
