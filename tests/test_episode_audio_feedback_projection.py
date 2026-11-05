import copy
import json
import pytest
from types import SimpleNamespace

from novel_manga.application.agents.episode import current_task, prompts
from novel_manga.util import atomic_write_json


def context(tmp_path):
    ctx = SimpleNamespace(work_dir=tmp_path/'sandbox', episode_dir=tmp_path/'book/book_1',
                          novel_dir=tmp_path/'book', run_dir=tmp_path/'run')
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips':[
        {'clip_id':'clip_01', 'shot_indexes':[1]}]})
    return ctx


def test_completed_review_list_reaches_current_task_without_changing_authority(tmp_path):
    ctx = context(tmp_path)
    status = {'adopted':True, 'repair_actions':{'clip_01':['retake','keep']},
        'next_action':{'action':'agent'}, 'job':{'id':'job-5','action':'review',
        'status':'completed','result':{'ok':True,'clips':[{'clip_id':'clip_01','severity':'pass'}]}}}
    before = copy.deepcopy(status)
    task = current_task.prepare(ctx, status, {'issues':[]}, [])
    assert task['targets'][0]['actions'] == ['retake','keep']
    assert 'grant' not in task['targets'][0]['input']
    assert task['last_operation']['action'] == 'review'
    assert status == before


def test_current_audio_findings_survive_both_projections_without_raw_evidence(tmp_path):
    ctx = context(tmp_path)
    issue = {'problem':'Missing planned Foley', 'start':0.0, 'end':15.681,
        'clip_ids':['clip_01'], 'source_basis':'Shot 1 requires room tone and wood creak.',
        'evidence':'The reviewed audio window did not contain these sounds.'}
    audio = {'complete':True, 'passed':False, 'issues':[issue],
        'windows':[{'response':'RAW_WINDOW'}], 'identity':{'model':'RAW_IDENTITY'},
        'source_identity':{'source':'RAW_SOURCE'}}
    status = {'adopted':True, 'repair_actions':{'clip_01':['retake']},
        'next_action':{'action':'agent'}, 'final_audio_review':audio,
        'job':{'action':'finish','status':'completed','result':{
            'ok':False,'error':'final_audio_review_requires_repair','required_action':'apply'}}}
    task = current_task.prepare(ctx, status, {'issues':[]}, [])
    expected = {'complete':True, 'passed':False, 'issues':[issue]}
    assert task['final_audio_review'] == expected
    prompt = prompts.resume_prompt(task)
    delivered = json.loads(prompt.split('Current actionable state:\n',1)[1])
    assert delivered['final_audio_review'] == expected
    assert delivered['full_status'] == 'input/current-status.json'
    assert all(value not in prompt for value in ('RAW_WINDOW','RAW_IDENTITY','RAW_SOURCE'))
    assert status['final_audio_review'] == audio


def audio_repair_status(*,takes=2,actions=None):
    return {'adopted':True,'next_action':{'action':'agent'},
        'audio_mix':{'command':'mix_audio','candidates':[]},
        'repair_actions':{'clip_01':['edit','retake','keep'] if actions is None else actions},
        'remaining_takes':{'clip_01':takes},'remaining_input_attempts':{'clip_01':0},
        'input_operations':{'clip_01':{'new':{'allowed':False,'reason':'input allowance used'}}},
        'final_audio_review':{'complete':True,'passed':False,'errors':[],'issues':[
            {'problem':'Missing source-planned physical sound','clip_ids':['clip_01'],'start':0,'end':5}]}}


def test_empty_mix_candidates_do_not_hide_separately_granted_native_audio_repair(tmp_path):
    ctx=context(tmp_path);status=audio_repair_status();before=copy.deepcopy(status)
    task=current_task.prepare(ctx,status,{'issues':[]},[])
    rule=next(rule for rule in task['execution_rules'] if 'Empty audio_mix.candidates' in rule)
    assert 'only mix_audio' in rule and 'not mean no legal repair remains' in rule
    assert 'edit' in rule and 'retake' in rule and 'remaining_takes > 0' in rule
    assert 'input allowance' in rule and 'BGM or arbitrary noise' in rule
    assert 'unchanged failed operation' in rule
    assert rule in prompts.resume_prompt(task)
    assert task['targets'][0]['actions']==['edit','retake','keep']
    assert task['targets'][0]['remaining_takes']==2 and task['targets'][0]['input']['remaining_attempts']==0
    assert status==before


@pytest.mark.parametrize('variant',['no_takes','unknown_takes','no_actions','incomplete_review','review_error','other_clip','candidate_exists'])
def test_audio_permission_hint_requires_current_content_failure_and_actual_rights(tmp_path,variant):
    ctx=context(tmp_path);status=audio_repair_status()
    if variant=='no_takes':status['remaining_takes']['clip_01']=0
    elif variant=='unknown_takes':status['remaining_takes']['clip_01']=None
    elif variant=='no_actions':status['repair_actions']['clip_01']=['keep']
    elif variant=='incomplete_review':status['final_audio_review']['complete']=False
    elif variant=='review_error':status['final_audio_review']['errors']=['review unavailable']
    elif variant=='other_clip':status['final_audio_review']['issues'][0]['clip_ids']=['clip_02']
    else:status['audio_mix']['candidates']=[{'id':'existing'}]
    task=current_task.prepare(ctx,status,{'issues':[]},[])
    assert not any('Empty audio_mix.candidates' in rule for rule in task['execution_rules'])
