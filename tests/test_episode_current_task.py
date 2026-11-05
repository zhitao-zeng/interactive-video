from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""Current repair context excludes obsolete targets but retains full evidence files."""
from types import SimpleNamespace
from pathlib import Path

from novel_manga.util import atomic_write_json,read_json


def test_current_task_and_resume_preserve_actual_input_field_capabilities(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.prompts import resume_prompt
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    capabilities={'adopted_input':{'shot_fields':['shot_scale','opening_frame','visual_prompt']},
                  'source_review_required':True}
    status={'adopted':True,'repair_capabilities':capabilities,
            'next_action':{'action':'agent','reason':'Repair source fields with check then apply.'}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert task['repair_capabilities']==capabilities
    prompt=resume_prompt(task)
    assert 'shot_scale' in prompt and 'opening_frame' in prompt and 'visual_prompt' in prompt


def test_first_frame_argument_retry_preserves_validated_clips(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    status={'adopted':True,'job':{'id':'job-2','action':'first_frames','status':'completed','result':{
        'ok':False,'argument_errors':{'clip_08':['bad quote']},
        'validated_clips':['clip_01','clip_02','clip_08'],
        'validation_targets':['clip_01','clip_02','clip_08']}}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert task['last_operation']['result']['retry_clips']==['clip_08']
    assert any('only [' in rule and 'clip_08' in rule and 'validated_clips' in rule
               for rule in task['execution_rules'])


def test_completed_old_pending_receipt_is_presented_as_remaining_work(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    status={'adopted':True,'busy':False,'repair_actions':{'clip_02':['input']},'job':{
        'id':'job-1','action':'apply','status':'completed','result':{'ok':True,'pending':['clip_02']}}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert task['last_operation']['result']['unresolved_clips']==['clip_02']
    assert 'pending'not in task['last_operation']['result']
    assert any('busy=false' in rule and 'do not wait' in rule for rule in task['execution_rules'])


def test_brief_contains_only_current_targets_and_exact_operation_choices(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path / 'sandbox', episode_dir=tmp_path / 'book/book_1',
                        novel_dir=tmp_path / 'book', run_dir=tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[
        {'clip_id':'clip_02','shot_indexes':[2]}, {'clip_id':'clip_07','shot_indexes':[9,10],
         'references':[{'name':'甲','role':'character','image':'input/refs/a.jpeg'}]}]})
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'agent_phase':'input_repair'})
    status={'first_pass_remaining':['clip_07'],'repair_actions':{'clip_02':[],'clip_07':['input','accept_risk']},
        'remaining_input_attempts':{'clip_07':1},'risk_choices':{'clip_07':{'wearer_card':['甲']}},
        'input_repairs':{'clip_02':{'error':'old failure'}},
        'execution':{'blocked':{'clip_02':'old failure'}},'job':None}
    brief=prepare(ctx,status,{'issues':[]},[{'episode':'old','outcome':'unresolved','attempts':[{'blocked':'X'*5000}]}])
    assert [r['clip_id'] for r in brief['targets']]==['clip_07']
    assert brief['targets'][0]['risk_choices']=={'wearer_card':['甲']}
    assert 'old failure' not in str(brief)
    assert len(str(brief))<4500
    assert read_json(ctx.work_dir/'input/current-status.json')==status
    assert read_json(ctx.work_dir/'input/current-task.json')==brief


def test_current_task_keeps_provisional_draft_and_adoption_workflow(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path / 'sandbox', episode_dir=tmp_path / 'book/book_1', run_dir=tmp_path)
    task=prepare(ctx,{'adopted':False,'can_adopt_draft':True},
        {'issues':[{'clips':['clip_01'],'detail':'planner failed'}]},[])
    assert task['mode']=='provisional_draft'
    assert any('output/episode.json' in rule and 'adopt' in rule for rule in task['execution_rules'])
    assert not any(rule.startswith('Only these current targets') for rule in task['execution_rules'])
    assert task['issues'][0]['detail']=='planner failed'


def test_current_task_gives_complete_input_decision_protocol(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    task=prepare(ctx,{'adopted':True},{'issues':[]},[])
    assert any('reason' in rule and 'source_quote' in rule and 'shots' in rule
               and 'old_speaker_name' in rule and 'index' in rule
               for rule in task['execution_rules'])
    assert any('decisions' in rule and 'action' in rule and 'complete opening_frame' in rule
               for rule in task['execution_rules'])


def test_related_history_keeps_effectful_repair_and_failed_choice_before_finish(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.recovery import recall
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'run/book/book_2',
        run_dir=tmp_path/'run',source_novel_dir=tmp_path/'book')
    atomic_write_json(ctx.source_novel_dir/'.production-memory/old.json',{
        'episode':'book_1','outcome':'accepted','updated_at':1,
        'issues':[{'kind':'input','clips':['clip_01'],'detail':'base card still wears armor'}],
        'attempts':[
            {'action':'apply','ok':False,'changed':['clip_01'],'accepted_clips':[],
             'blocked':{'clip_02':'revision allowance used'},'remedies':[
                 {'action':'input','clip':'clip_01','reason':'bind the source-backed unarmored phase'},
                 {'action':'input','clip':'clip_02','reason':'repeat the rejected proposal'}]},
            {'action':'review','ok':True,'changed':[],'remedies':[]},
            {'action':'finish','ok':True,'changed':[],'remedies':[]}]})
    diagnosis={'issues':[{'kind':'input','clips':['clip_01'],'detail':'wrong costume'}]}
    memories=recall(ctx,diagnosis)
    task=prepare(ctx,{'adopted':True,'repair_actions':{'clip_01':['input']}},diagnosis,memories)
    case=task['related_history'][0]
    assert case['issues'][0]['detail']=='base card still wears armor'
    assert case['effective_operations']==[{'action':'input','clip':'clip_01',
        'reason':'bind the source-backed unarmored phase','receipt':'changed'}]
    assert case['failed_operations'][0]['error']=='revision allowance used'
    assert case['failed_operations'][0]['clip']=='clip_02'
    assert case['outcome']=='accepted'
    assert 'last_result' not in case


def test_accepted_finish_alone_is_not_a_known_repair(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    memory={'episode':'old','outcome':'accepted','issues':[{'kind':'speech','detail':'old ASR flag'}],
        'attempts':[{'action':'finish','ok':True,'changed':[],'remedies':[]}]}
    task=prepare(ctx,{'adopted':True},{'issues':[]},[memory])
    assert task['related_history'][0]['effective_operations']==[]
    assert task['related_history'][0]['repair_details']=='not_recorded'


def test_history_change_is_still_unresolved_until_formal_acceptance(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    memory={'episode':'old','outcome':'unresolved','issues':[{'kind':'speech','detail':'extra sentence'}],
        'attempts':[{'action':'apply','ok':True,'changed':['clip_01'],'remedies':[
            {'action':'edit','clip':'clip_01','reason':'replace the conflicting narration'}]}]}
    task=prepare(ctx,{'adopted':True},{'issues':[]},[memory])
    case=task['related_history'][0]
    assert case['outcome']=='unresolved'
    assert case['effective_operations'][0]['receipt']=='changed'
    assert case['effective_operations'][0]['reason']=='replace the conflicting narration'


def test_summary_prefers_concrete_change_and_does_not_promote_blocked_candidate():
    from novel_manga.application.agents.episode.memory_summary import summarize_case
    summary=summarize_case({'outcome':'accepted','attempts':[
        {'action':'apply','changed':['clip_01','clip_02'],
         'blocked':{'clip_02':'dependent clip cannot be compiled'},'remedies':[
             {'action':'input','clip':'clip_01','reason':'bind the corrected phase'},
             {'action':'input','clip':'clip_02','reason':'an incomplete candidate'}]},
        {'action':'apply','accepted_clips':['clip_03','clip_04','clip_05','clip_06'],
         'remedies':[{'action':'keep','clip':cid,'reason':'audio recheck passed'}
                     for cid in ['clip_03','clip_04','clip_05','clip_06']]},
        {'action':'finish','ok':True}]})
    assert summary['effective_operations'][0]['clip']=='clip_01'
    assert len(summary['effective_operations'])==3
    assert all(r['clip']!='clip_02' for r in summary['effective_operations'])
    assert summary['failed_operations'][0]['error']=='dependent clip cannot be compiled'


def test_recall_includes_retrospective_lessons_without_overriding_live_outcome(tmp_path):
    from novel_manga.application.agents.episode.recovery import recall
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(source_novel_dir=tmp_path/'book',work_dir=tmp_path/'run/sandbox',
        episode_dir=tmp_path/'run/book/book_2',run_dir=tmp_path/'run')
    folder=ctx.source_novel_dir/'.production-memory'
    atomic_write_json(folder/'old.json',{'episode':'book_1','outcome':'unresolved','updated_at':2,
        'issues':[{'kind':'input','detail':'the base card still wears armor'}],'attempts':[]})
    atomic_write_json(folder/'summaries/review/summary.json',{'cases':[
        {'source_memory':str(folder/'old.json'),'recorded_formal_acceptance':True,
         'case_summary':'A phase card was bound before the old acceptance.',
         'lesson_and_limits':'Check the actual phase image before another take.'}]})
    diagnosis={'issues':[{'kind':'input','detail':'wrong costume'}]}
    task=prepare(ctx,{'adopted':True},diagnosis,recall(ctx,diagnosis))
    case=task['related_history'][0]
    assert case['retrospective']['lesson_and_limits']=='Check the actual phase image before another take.'
    assert case['outcome']=='unresolved'
    assert case['effective_operations']==[]


def test_current_task_routes_resumed_agents_to_the_installed_repair_playbook(tmp_path):
    from novel_manga.application.agents.episode.inputs import _install_skill
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    _,references=_install_skill(ctx)
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'agent_phase':'review','skill_references':references})
    task=prepare(ctx,{'adopted':True},{'issues':[]},[])
    assert (ctx.work_dir/task['repair_playbook']).is_file()
    assert task['repair_playbook']==references['repair_playbook']
    assert any('repair_playbook' in rule and 'read' in rule for rule in task['execution_rules'])


def test_current_task_exposes_only_current_grant(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.input_contracts import opening_grant
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clip={'clip_id':'clip_03','shot_indexes':[3,4]}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    findings=[{'origin':'source','relation':'contradiction','basis':['opening.opening_frame.image_prompt']}]
    grant=opening_grant(clip,findings,{'opening':{'opening_frame':{'image_prompt':'source'}}}).as_dict()
    status={'adopted':True,'first_pass_remaining':['clip_03'],'repair_actions':{'clip_03':['input']},
        'repair_capabilities':{'adopted_input':{'shot_fields':['visual_prompt','opening_frame']}},
        'first_frames':{'clips':{'clip_03':{'repair_grant':grant}}},
        'next_action':{'action':'agent','reason':'repair opening'}}
    task=prepare(ctx,status,{'issues':[]},[])
    actual=task['targets'][0]['input']['grant']
    assert actual['operation']=='input' and actual['shot_indexes']==[3]
    assert actual['shot_fields']==['opening_frame'] and actual['evidence']['fingerprint']
    assert 'visual_prompt' not in actual['shot_fields']
    assert 'visual_prompt' in task['repair_capabilities']['adopted_input']['shot_fields']


def test_source_reference_errors_require_apply_while_row_errors_retry_first_frames(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clip={'clip_id':'clip_01','shot_indexes':[1]}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    grant={'operation':'input','shot_indexes':[1],'shot_fields':['opening_frame'],
           'evidence':{'origin':'source','basis':['opening.opening_frame.reference_subjects']}}
    source={'ok':False,'required_action':'apply','image_candidates_allocated':0,
        'argument_errors':{'clip_01':['opening_frame.reference_subjects is unregistered']},
        'invalid_fields':{'clip_01':['opening_frame.reference_subjects']},
        'clips':{'clip_01':{'ok':False,'repair_origin':'source','repair_grant':grant}}}
    status={'adopted':True,'first_pass_remaining':['clip_01'],
        'first_frames':{'clips':{'clip_01':{'repair_origin':'source','repair_grant':grant}}},
        'job':{'id':'source','action':'first_frames','status':'completed','result':source}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert task['last_operation']['result']['required_action']=='apply'
    assert task['last_operation']['result']['invalid_fields']==source['invalid_fields']
    assert task['targets'][0]['input']['grant']==grant
    assert any('source field opening_frame.reference_subjects' in rule and 'check then apply' in rule
               for rule in task['execution_rules'])
    assert not any('first_frames argument retry' in rule for rule in task['execution_rules'])

    ordinary={'ok':False,'argument_errors':{'clip_01':['extra_references requires distinct strings']},
        'invalid_fields':{'clip_01':['extra_references']},'validated_clips':[],
        'validation_targets':['clip_01']}
    status['first_frames']={'clips':{'clip_01':{}}}
    status['job']={'id':'row','action':'first_frames','status':'completed','result':ordinary}
    task=prepare(ctx,status,{'issues':[]},[])
    assert 'required_action' not in task['last_operation']['result']
    assert any('first_frames argument retry' in rule for rule in task['execution_rules'])
    assert not any('source field opening_frame.reference_subjects' in rule
                   for rule in task['execution_rules'])


def test_mixed_first_frame_errors_get_per_clip_recovery_instructions(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clips=[{'clip_id':f'clip_{index:02d}','shot_indexes':[index]} for index in (1,2,3)]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    outcome={'ok':False,'required_action':'apply',
        'argument_errors':{cid:[cid+' error'] for cid in ('clip_01','clip_02','clip_03')},
        'invalid_fields':{'clip_01':['opening_frame.reference_subjects'],
            'clip_02':['extra_references'],'clip_03':['image_references']},
        'clips':{'clip_01':{'ok':False,'repair_origin':'source'},
            'clip_02':{'ok':False},'clip_03':{'ok':False,'repair_origin':'registration'}}}
    status={'adopted':True,'first_pass_remaining':[clip['clip_id'] for clip in clips],
        'first_frames':{'clips':{}},
        'job':{'id':'mixed','action':'first_frames','status':'completed','result':outcome}}
    task=prepare(ctx,status,{'issues':[]},[])
    rules=task['execution_rules']
    source=next(rule for rule in rules if 'source field opening_frame.reference_subjects' in rule)
    ordinary=next(rule for rule in rules if 'first_frames argument retry' in rule)
    registration=next(rule for rule in rules if 'reference registration' in rule)
    assert 'clip_01' in source and 'clip_02' not in source and 'clip_03' not in source
    assert 'clip_02' in ordinary and 'clip_01' not in ordinary and 'clip_03' not in ordinary
    assert 'clip_03' in registration and 'clip_01' not in registration and 'clip_02' not in registration
    assert 'does not authorize story edits' in registration


def test_legacy_apply_fallback_guesses_source_only_for_reference_subject_fields(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clips=[{'clip_id':f'clip_{index:02d}','shot_indexes':[index]} for index in (1,2)]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    outcome={'ok':False,'required_action':'apply',
        'argument_errors':{'clip_01':['source issue'],'clip_02':['row issue']},
        'invalid_fields':{'clip_01':['opening_frame.reference_subjects'],'clip_02':['extra_references']}}
    status={'adopted':True,'first_pass_remaining':['clip_01','clip_02'],'first_frames':{'clips':{}},
        'job':{'id':'legacy','action':'first_frames','status':'completed','result':outcome}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert not any('source field opening_frame.reference_subjects' in rule for rule in task['execution_rules'])
    ordinary=next(rule for rule in task['execution_rules'] if 'first_frames argument retry' in rule)
    assert 'clip_01' in ordinary and 'clip_02' in ordinary

    outcome['invalid_fields']={'clip_01':['opening_frame.reference_subjects'],
        'clip_02':['opening_frame.reference_subjects']}
    task=prepare(ctx,status,{'issues':[]},[])
    source=next(rule for rule in task['execution_rules'] if 'source field opening_frame.reference_subjects' in rule)
    assert 'clip_01' in source and 'clip_02' in source


def test_exception_projection_includes_input_scope_context_without_expanding_grants(tmp_path):
    import json
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.prompts import resume_prompt
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clips=[{'clip_id':f'clip_{index:02d}','kind':'video','shot_indexes':[shot]}
           for index,shot in [(1,1),(2,2),(3,2),(4,3),(5,4)]]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[
        {'index':1,'source_shot':1,'source_kind':'whole'},
        {'index':2,'source_shot':8,'source_kind':'split'},
        {'index':3,'source_shot':8,'source_kind':'split'},
        {'index':4,'source_shot':4,'source_kind':'whole'}]})
    grant={'operation':'input','shot_indexes':[2],'shot_fields':['opening_frame'],
           'evidence':{'origin':'source','basis':['opening.opening_frame.reference_subjects']}}
    frame_rows={clip['clip_id']:{'current':False,
        'authored_opening_frame':{'image_prompt':'complete-opening-'+clip['clip_id']}}
        for clip in clips}
    frame_rows['clip_02']['repair_grant']=grant
    frame_rows['clip_03']['repair_grant']={'operation':'input','shot_indexes':[2],
        'shot_fields':['event'],'evidence':{'origin':'source'}}
    frame_rows['clip_03']['next_action']='edit the context clip'
    conflicts={'clip_02':[{'kind':'source','basis':['opening.opening_frame.reference_subjects']}]}
    status={'adopted':True,
        'next_action':{'action':'agent','required_action':'apply','blocked':{'clip_02':['bad reference']},
                       'source_input_conflicts':conflicts},
        'first_frames':{'mode':'first-frame','clips':frame_rows},
        'music_selection':{'command':'select_music'},'audio_mix':{'command':'mix_audio'},
        'job':{'id':'job-4','action':'first_frames','status':'completed','result':{
            'ok':False,'required_action':'apply','message':'correct the registered source reference',
            'invalid_fields':{'clip_02':['opening_frame.reference_subjects']},
            'source_input_conflicts':conflicts}}}
    task=prepare(ctx,status,{'issues':[{'kind':'input','clips':['clip_02'],'detail':'bad reference'}]},[])
    assert [row['clip_id'] for row in task['targets']]==['clip_02','clip_03','clip_04']
    primary=task['targets'][0]
    assert primary['input']['grant']==grant and primary.get('context_only') is not True
    for row in task['targets'][1:]:
        assert row['context_only'] is True
        assert 'grant' not in row.get('input',{}) and not row.get('actions')
    assert set(task['first_frames']['clips'])=={'clip_02','clip_03','clip_04'}
    assert task['first_frames']['clips']['clip_02']['authored_opening_frame']
    for cid in ('clip_03','clip_04'):
        assert 'authored_opening_frame' not in task['first_frames']['clips'][cid]
        assert 'repair_grant' not in task['first_frames']['clips'][cid]
        assert 'next_action' not in task['first_frames']['clips'][cid]
        assert task['first_frames']['clips'][cid]['context_only'] is True
    serialized=json.dumps(task,ensure_ascii=False)
    assert 'complete-opening-clip_01' not in serialized and 'complete-opening-clip_05' not in serialized
    assert 'complete-opening-clip_03' not in serialized and 'complete-opening-clip_04' not in serialized
    assert 'select_music' not in serialized and 'mix_audio' not in serialized
    prompt=resume_prompt(task)
    assert 'select_music' not in prompt and 'mix_audio' not in prompt
    assert task['last_operation']['result']['required_action']=='apply'
    assert task['last_operation']['result']['invalid_fields']=={
        'clip_02':['opening_frame.reference_subjects']}
    assert task['last_operation']['result']['source_input_conflicts']==conflicts


def test_last_operation_retains_service_and_source_failure_structure(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','shot_indexes':[1]}]})
    outcome={'ok':False,'required_action':'reconcile_submission','message':'submission outcome unknown',
        'invalid_fields':{'clip_01':['opening_frame.reference_subjects']},
        'service_interrupted':{'http_status':502,'submission_uncertain':True},
        'source_input_conflicts':{'clip_01':[{'problem':'current conflict'}]}}
    status={'adopted':True,'first_pass_remaining':['clip_01'],
        'job':{'id':'job-5','action':'first_frames','status':'completed','result':outcome}}
    task=prepare(ctx,status,{'issues':[]},[])
    result=task['last_operation']['result']
    for key in ('required_action','message','invalid_fields','service_interrupted','source_input_conflicts'):
        assert result[key]==outcome[key]


def test_first_pass_explicit_failures_limit_primary_and_resume_keeps_all_of_them(tmp_path):
    import json
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.prompts import resume_prompt
    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    clips=[{'clip_id':f'clip_{index:02d}','kind':'video','shot_indexes':[shot]}
           for index,shot in [(1,1),(2,2),(3,2),(4,4),(5,5)]]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[
        {'index':index,'source_shot':index,'source_kind':'whole'} for index in (1,2,4,5)]})
    grants={cid:{'operation':'input','shot_indexes':[shot],'shot_fields':['opening_frame'],
                 'evidence':{'origin':'source','basis':['opening.opening_frame.reference_subjects']}}
            for cid,shot in [('clip_01',1),('clip_02',2),('clip_03',2)]}
    frame_rows={clip['clip_id']:{'current':False,
        'authored_opening_frame':{'image_prompt':'full-opening-'+clip['clip_id']},
        'repair_grant':grants.get(clip['clip_id'])} for clip in clips}
    conflicts={'clip_01':[{'problem':'first independent source conflict'}]}
    status={'adopted':True,'first_pass_remaining':[clip['clip_id'] for clip in clips],
        'repair_actions':{clip['clip_id']:['input'] for clip in clips},
        'next_action':{'action':'agent','required_action':'apply','source_input_conflicts':conflicts,
                       'blocked':{'clip_01':['source conflict']}},
        'first_frames':{'mode':'first-frame','clips':frame_rows},
        'job':{'id':'job-independent','action':'first_frames','status':'completed','result':{
            'ok':False,'required_action':'apply','invalid_fields':{
                'clip_02':['opening_frame.reference_subjects']}}}}
    diagnosis={'issues':[{'kind':'input','clips':['clip_01'],'detail':'first'},
                         {'kind':'input','clips':['clip_02'],'detail':'second'}]}
    task=prepare(ctx,status,diagnosis,[])
    expected=['clip_01','clip_02','clip_03']
    assert [row['clip_id'] for row in task['targets']]==expected
    assert set(task['first_frames']['clips'])==set(expected)
    context=task['targets'][2]
    assert context['context_only'] is True
    assert 'actions' not in context and 'input' not in context
    assert 'repair_grant' not in task['first_frames']['clips']['clip_03']
    assert 'authored_opening_frame' not in task['first_frames']['clips']['clip_03']
    state=json.loads(resume_prompt(task).split('Current actionable state:\n',1)[1])
    assert [row['clip_id'] for row in state['targets']]==expected
    assert set(state['first_frames']['clips'])==set(expected)
