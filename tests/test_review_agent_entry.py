"""Formal entry and bounded feedback; all model returns here are synthetic."""
import json
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.review import agent, agent_inputs, episode
from novel_manga.util import atomic_write_json


def fake_prepare(directory, targets, contexts, work, config, overrides):
    (work/'.claude/skills/video-repair').mkdir(parents=True)
    (work/'.claude/skills/video-repair/SKILL.md').write_text('review phase fixture')
    (work/'input').mkdir()
    return {'target_clips': targets, 'context_clips': list(contexts), 'clips': {
        cid: {'current_frames': [{'path': f'input/{cid}/frame.jpg', 'seconds': 2.0}],
              'current_video': f'/episode/{cid}.mp4', 'take': [1, 2, 3],
              'remaining_video_takes': 0} for cid in targets}}


def test_public_review_index_retains_complete_target_evidence_without_loading_every_request(tmp_path):
    from novel_manga.util import read_json
    primary={'target_clips':['a','b'],'clips':{
        cid:{'actual_request':f'complete request {cid}', 'planned_request':f'complete request {cid}',
             'current_request':f'complete request {cid}', 'source_passages':[{'text':f'source {cid}'}],
             'current_frames':[{'path':f'input/{cid}/frame.jpg','seconds':0}],
             'references':[{'path':f'input/{cid}/reference.jpeg','role':'character'}],
             'remaining_video_takes':0}for cid in ['a','b']}}
    agent_inputs.write_primary(tmp_path,primary)
    index=read_json(tmp_path/'input/primary.json')
    for cid in ['a','b']:
        assert 'actual_request' not in index['clips'][cid]
        scoped=read_json(tmp_path/index['clips'][cid]['input'])
        assert scoped['actual_request']==primary['clips'][cid]['actual_request']
        assert scoped['source_passages']==primary['clips'][cid]['source_passages']
        assert scoped['references']==primary['clips'][cid]['references']
        assert 'current_request' not in scoped and 'planned_request' not in scoped
    assert primary['clips']['a']['current_request']=='complete request a'
    primary['clips']['a']['planned_request']='a different intended request'
    primary['clips']['a']['current_request']='a different current request'
    agent_inputs.write_primary(tmp_path,primary)
    scoped=read_json(tmp_path/'input/targets/a.json')
    assert scoped['planned_request']=='a different intended request'
    assert scoped['current_request']=='a different current request'


def test_formal_episode_entry_routes_to_agent_without_overwriting_production(tmp_path, monkeypatch):
    directory = tmp_path/'episode'; directory.mkdir()
    (directory/'episode_review.json').write_text('{"old":"preserved"}')
    calls = []
    def review(*args, **kwargs):
        calls.append((args, kwargs))
        return {'clips': {}, 'feedback': {}, 'flags': [], 'agent_execution': {'status': 'complete'}}
    monkeypatch.setattr(agent, 'review_agent_episode', review)
    episode.review_episode(directory, backend='sandbox_agent', targets=['a'], report_dir=tmp_path/'new')
    assert calls and calls[0][0][1] == ['a']
    assert json.loads((directory/'episode_review.json').read_text()) == {'old': 'preserved'}


def test_same_agent_corrects_pass_stop_with_only_original_remaining_time(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_inputs, 'prepare', fake_prepare)
    clock = [100.0]
    monkeypatch.setattr(agent.time, 'monotonic', lambda: clock[0])
    config = {'runs_root': str(tmp_path/'runs'), 'model': 'synthetic'}
    calls = []
    def run(name, **kwargs):
        calls.append(kwargs)
        work = Path(config['runs_root'])/name
        attempt = work/'attempts'/str(len(calls)); attempt.mkdir(parents=True)
        p = 'input/a/frame.jpg'
        events = [{'type': 'system', 'session_id': 'same-session'},
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'image-read', 'name': 'Read', 'input': {'file_path': '/work/'+p}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'image-read', 'content': [{'type': 'image'}]}]}}]
        (attempt/'session.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        atomic_write_json(work/'output/review.json', {'clips': [{'clip_id': 'a', 'verdict': 'pass', 'finding': 'natural',
            'evidence': [{'image': p, 'seconds': 2.0, 'observation': 'no defect'}], 'findings': [],
            'decision_file': 'output/a/decision.json'}]})
        atomic_write_json(work/'output/a/decision.json', {'action': 'stop' if len(calls)==1 else 'keep', 'reason': 'accepted', 'evidence': [p]})
        (work/'output/review.md').write_text('synthetic review, no hearing')
        clock[0] += 100
        return SimpleNamespace(exit_code=0, directory=attempt, seconds=100)
    monkeypatch.setattr(agent, 'run_agent', run)
    result = agent.review_agent_episode(tmp_path/'episode', ['a'], tmp_path/'report', config=config)
    assert [r['timeout'] for r in calls] == [1200, 1100]
    assert calls[1]['config']['extra_args'][-2:] == ['--resume', 'same-session']
    assert result['agent_execution']['status'] == 'complete'
    assert result['clips']['a']['agent_decision']['action'] == 'keep'
    first = Path(result['agent_execution']['attempts'][0]['directory'])/'original-output/a/decision.json'
    assert json.loads(first.read_text())['action'] == 'stop'
    assert not (tmp_path/'episode/episode_review.json').exists()


def test_native_interrupt_resumes_same_session_without_renewing_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_inputs, 'prepare', fake_prepare)
    clock = [100.0]
    monkeypatch.setattr(agent.time, 'monotonic', lambda: clock[0])
    config = {'runs_root': str(tmp_path/'runs'), 'model': 'synthetic'}
    calls = []
    def run(name, **kwargs):
        calls.append(kwargs['timeout'])
        work = Path(config['runs_root'])/name
        attempt = work/'attempts'/str(len(calls)); attempt.mkdir(parents=True)
        events = [{'type': 'system', 'session_id': 'same-session'}]
        if len(calls) == 2:
            p = 'input/a/frame.jpg'
            events += [{'message': {'content': [{'type': 'tool_use', 'id': 'r', 'name': 'Read', 'input': {'file_path': '/work/'+p}}]}},
                       {'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'r', 'content': [{'type': 'image'}]}]}}]
            atomic_write_json(work/'output/review.json', {'clips': [{'clip_id':'a', 'verdict':'pass', 'evidence':[{'image':p,'seconds':2.0}], 'findings':[], 'decision_file':'output/a/decision.json'}]})
            atomic_write_json(work/'output/a/decision.json', {'action':'keep','reason':'fine','evidence':[p]})
            (work/'output/review.md').write_text('finished')
        (attempt/'session.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        clock[0] += 100
        return SimpleNamespace(exit_code=1 if len(calls)==1 else 0, directory=attempt, seconds=100)
    monkeypatch.setattr(agent, 'run_agent', run)
    report = agent.review_agent_episode(tmp_path/'episode', ['a'], tmp_path/'report', config=config)
    assert calls == [1200, 1100]
    assert report['agent_execution']['status'] == 'complete'


def test_output_directory_cannot_replace_production_report(tmp_path):
    import pytest
    with pytest.raises(ValueError, match='independent'):
        agent.review_agent_episode(tmp_path, ['a'], tmp_path, config={})


def test_malformed_native_output_is_archived_as_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_inputs, 'prepare', fake_prepare)
    config={'runs_root':str(tmp_path/'runs'),'model':'synthetic'}
    def run(name, **kwargs):
        work=Path(config['runs_root'])/name
        attempt=work/'attempts/one';attempt.mkdir(parents=True,exist_ok=True)
        (attempt/'session.jsonl').write_text('{}\n')
        atomic_write_json(work/'output/review.json', {'clips':[{'clip_id':'a','findings':[None], 'decision_file':'output/a/decision.json'}]})
        atomic_write_json(work/'output/a/decision.json', [])
        return SimpleNamespace(exit_code=0,directory=attempt,seconds=1)
    monkeypatch.setattr(agent,'run_agent',run)
    report=agent.review_agent_episode(tmp_path/'episode',['a'],tmp_path/'report',config=config)
    assert report['agent_execution']['status']=='incomplete'
    assert report['clips']['a']['severity']=='review_error'
    assert json.loads((tmp_path/'report/state.json').read_text())['status']=='incomplete'


def test_saved_valid_targets_survive_final_api_failure_without_another_model_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_inputs, 'prepare', fake_prepare)
    config = {'runs_root':str(tmp_path/'runs'), 'model':'synthetic'}
    calls = []
    def run(name, **kwargs):
        calls.append(name)
        work = Path(config['runs_root'])/name
        attempt = work/'attempts/one';attempt.mkdir(parents=True)
        p = 'input/a/frame.jpg'
        events = [{'type':'system','session_id':'same'},
            {'message':{'content':[{'type':'tool_use','id':'r','name':'Read','input':{'file_path':'/work/'+p}}]}},
            {'message':{'content':[{'type':'tool_result','tool_use_id':'r','content':[{'type':'image'}]}]}},
            {'type':'result','is_error':True,'api_error_status':524,'result':'API Error: 524'}]
        (attempt/'session.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        atomic_write_json(work/'output/review.json', {'clips':[
            {'clip_id':'a','verdict':'pass','finding':'natural','evidence':[{'image':p,'seconds':2.0}],
             'findings':[],'decision_file':'output/a/decision.json'}]})
        atomic_write_json(work/'output/a/decision.json', {'action':'keep','reason':'natural','evidence':[p]})
        return SimpleNamespace(exit_code=1,directory=attempt,seconds=100)
    monkeypatch.setattr(agent,'run_agent',run)
    report = agent.review_agent_episode(tmp_path/'episode',['a'],tmp_path/'report',config=config)
    state = report['agent_execution']
    assert len(calls)==1 and state['status']=='complete' and not state['validation_errors']
    assert state['execution_warnings'] and state['summary_origin']=='host_from_validated_agent_outputs'
    assert state['execution_warnings'][0]['api_error_status']==524
    assert report['clips']['a']['agent_decision']['action']=='keep'
    assert (tmp_path/'report/review.md').is_file()
    assert not (tmp_path/'episode/episode_review.json').exists()


def test_explicit_workflow_cli_choice_reaches_execution(tmp_path, monkeypatch):
    import sys
    from novel_manga.application.review import cli
    calls=[]
    monkeypatch.setattr(sys,'argv',['thin_review','episode','--episode-dir',str(tmp_path),'--backend','workflow'])
    monkeypatch.setattr(cli,'review_only',lambda *a,**kw:(calls.append(kw) or {'flags':[],'feedback':{}}))
    assert cli.main()==0
    assert calls[0]['backend']=='workflow'


def test_unchanged_edit_cannot_enter_repair(tmp_path, monkeypatch):
    from novel_manga.story.h3 import compose
    monkeypatch.setattr(agent_inputs,'prepare',fake_prepare)
    config={'runs_root':str(tmp_path/'runs'),'model':'synthetic'}
    d=tmp_path/'episode';d.mkdir()
    clip={'clip_id':'a','references':[],'shot_indexes':[1]}
    clip['prompt_h3']=compose(clip,['A person stands by a door.'],[('',[])])
    atomic_write_json(d/'clip_plan.json',{'clips':[clip]})
    def run(name,**kwargs):
        w=Path(config['runs_root'])/name;a=w/'attempts/one';a.mkdir(parents=True)
        p='input/a/frame.jpg'
        events=[{'message':{'content':[{'type':'tool_use','id':'r','name':'Read','input':{'file_path':'/work/'+p}}]}},
                {'message':{'content':[{'type':'tool_result','tool_use_id':'r','content':[{'type':'image'}]}]}}]
        (a/'session.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        atomic_write_json(w/'output/review.json',{'clips':[{'clip_id':'a','verdict':'needs_repair',
            'evidence':[{'image':p,'seconds':2.0}], 'findings':[{'must_fix':True,'evidence':[p],'start_seconds':2.0,'end_seconds':2.0}],
            'decision_file':'output/a/decision.json'}]})
        atomic_write_json(w/'output/a/decision.json',{'action':'edit','reason':'same','evidence':[p]})
        (w/'output/a/prompt.txt').write_text(clip['prompt_h3'])
        (w/'output/review.md').write_text('unchanged edit')
        return SimpleNamespace(exit_code=0,directory=a,seconds=1)
    monkeypatch.setattr(agent,'run_agent',run)
    report=agent.review_agent_episode(d,['a'],tmp_path/'report',config=config)
    assert report['agent_execution']['status']=='incomplete'
    assert any('no effective change' in e for e in report['agent_execution']['validation_errors'])


def test_interrupted_host_cannot_create_a_third_native_attempt(tmp_path, monkeypatch):
    import time
    work=tmp_path/'runs/old';(work/'input').mkdir(parents=True)
    for name in ['one','two']:(work/'attempts'/name).mkdir(parents=True)
    reportdir=tmp_path/'report';reportdir.mkdir()
    atomic_write_json(reportdir/'state.json',{'status':'incomplete','run_name':'old','work':str(work),'targets':['a'],
        'sessions':['same'],'started_at':time.time()-500,'timeout_seconds':1200,
        'attempts':[{'directory':str(work/'attempts/one'),'exit':1}]})
    atomic_write_json(reportdir/'primary.json',{'target_clips':['a'],'clips':{'a':{'current_video':'a.mp4','take':[1,2,3]}}})
    def forbidden(*a,**kw):raise AssertionError('must not invoke a third native attempt')
    monkeypatch.setattr(agent,'run_agent',forbidden)
    out=agent.review_agent_episode(tmp_path/'episode',['a'],reportdir,config={'model':'mock'},resume=True)
    assert out['agent_execution']['status']=='incomplete'
    assert out['agent_execution']['unrecorded_attempts']==[str(work/'attempts/two')]
