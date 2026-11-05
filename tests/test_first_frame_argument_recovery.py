"""Queued argument rejection must reach the same Agent before any paid work."""
from dataclasses import replace
import json
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames, flow, readiness, turns
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context, sandbox_config, install_fake_runtime
from support.first_frame_cases import case


def rejected_openings(tmp_path,monkeypatch,*,candidates=1):
    ctx,_=case(tmp_path,monkeypatch)
    def build(ctx,row,path):Image.new('RGB',(8,16)).save(path)
    def reject(*args):return {'findings':[{
        'origin':'image','relation':'contradiction','reason':'wrong visible pose'}]}
    assert first_frames.FirstFrameOperations(ctx,builder=build,checker=reject).prepare({})['ok'] is False
    for index in range(1,candidates):
        atomic_write_json(ctx.work_dir/'output/first-frames.json',{'render_ready':False,'frames':[
            {'clip_id':cid,'extra_references':[],'retake_candidate':{
                'candidate':f'candidate_{index:02d}','reason':'correct visible pose'}}
            for cid in ['clip_01','clip_02']]})
        assert first_frames.FirstFrameOperations(ctx,builder=build,checker=reject).prepare({})['ok'] is False
    return ctx


def packet():
    return {'render_ready':False,'frames':[
        {'clip_id':'clip_01','extra_references':[],
         'retake_candidate':{'candidate':'candidate_01','reason':'correct visible pose'}},
        {'clip_id':'clip_02','extra_references':[],'reuse_candidate':'candidate_01'}]}


def test_operation_argument_error_preserves_valid_rows_and_current_permission_before_billing(tmp_path,monkeypatch):
    ctx=rejected_openings(tmp_path,monkeypatch)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',packet())
    def forbidden(*args):pytest.fail('argument validation must precede image/review work')
    operation=first_frames.FirstFrameOperations(ctx,builder=forbidden,checker=forbidden)
    with pytest.raises(ValueError):operation.prepare({})
    result=operation.partial_outcome
    assert result['image_candidates_allocated']==0 and result['validated_clips']==['clip_01']
    assert list(result['argument_errors'])==['clip_02']
    assert result['invalid_fields']=={'clip_02':['reuse_candidate']}
    row=result['clips']['clip_02']
    assert row['repair_grant']['operation']=='retake_candidate'
    assert row['repair_grant']['evidence']['candidate']=='candidate_01'
    assert row['remaining_image_candidates']==2 and row['image_candidate_limit']==3
    assert 'service_interrupted' not in result
    for cid in ['clip_01','clip_02']:
        assert len(list((ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid).glob('candidate_*')))==1


@pytest.mark.parametrize('bad_selector',['reuse','nonexistent_parent'])
def test_background_argument_rejection_and_unexecuted_valid_row_return_to_original_agent(
        tmp_path,monkeypatch,episode_context,sandbox_config,bad_selector):
    candidates=2 if bad_selector=='nonexistent_parent' else 1
    parent=f'candidate_{candidates:02d}'
    ff=rejected_openings(tmp_path/'frames',monkeypatch,candidates=candidates)
    ctx=replace(episode_context,novel_dir=ff.novel_dir,episode_dir=ff.episode_dir,
                run_dir=ff.run_dir,work_dir=ff.work_dir,input_mode='script')
    install_fake_runtime(monkeypatch,ctx)
    monkeypatch.setattr(flow,'prepare_operations',lambda *a:(None,None))
    images=[];reviews=[]
    def build(ctx,row,path):images.append(path);Image.new('RGB',(8,16),'blue').save(path)
    def check(*args):reviews.append(1);return {'findings':[]}
    monkeypatch.setattr(first_frames,'build',build)
    monkeypatch.setattr(first_frames,'check',check)
    fake_tools=flow.EpisodeTools
    class Tools(fake_tools):
        def __init__(self,context):super().__init__(context);self.ctx=context
        def dispatch(self,action,payload):
            if action=='first_frames':return {'action':action,**EpisodeTools.first_frames(self,payload)}
            result=super().dispatch(action,payload)
            if action=='status':result.update(adopted=True,first_frames=first_frames.status(ctx,
                read_json(ctx.episode_dir/'clip_plan.json')))
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    original_route=readiness.next_action
    def route(context,status=None):
        current=original_route(context,status)
        if current['action']=='render':return {**current,'retryable':False,'error':'synthetic_stop_before_video'}
        return current
    monkeypatch.setattr(readiness,'next_action',route)
    bridges=[];original_bridge=flow.EpisodeBridge
    def bridge(*args):
        value=original_bridge(*args);bridges.append(value);return value
    monkeypatch.setattr(flow,'EpisodeBridge',bridge)
    session={'backend':'codex','session_id':'00000000-0000-4000-8000-000000000001'}
    atomic_write_json(ctx.work_dir/'episode-session.json',session)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'deferred','remaining_seconds':25,
        'deadline':time.time()+25,'agent_seconds':7,'attempts':[]})
    resumes=[]
    def agent(work,prompt,**options):
        resumes.append(options['resume']);number=len(resumes)
        assert read_json(work/'episode-session.json')==session
        if number==1:
            draft=packet();assert images==reviews==[]
            draft['frames'][0]['retake_candidate']['candidate']=parent
            if bad_selector=='nonexistent_parent':
                draft['frames'][1].pop('reuse_candidate')
                draft['frames'][1]['retake_candidate']={'candidate':'candidate_03','reason':'correct visible pose'}
        elif number==2:
            brief=json.dumps(read_json(work/'input/current-task.json'))
            assert 'argument_errors' in brief and 'clip_02' in brief
            assert ('reuse_candidate' if bad_selector=='reuse' else 'candidate_03') in brief
            assert images==reviews==[]
            draft={'render_ready':False,'frames':[{'clip_id':'clip_02','extra_references':[],
                'retake_candidate':{'candidate':parent,'reason':'correct visible pose'}}]}
        elif number==3:
            current=read_json(ctx.run_dir/'agent-run.json')
            assert current['job']['result']['ok'] is True
            assert len(images)==len(reviews)==1
            brief=read_json(work/'input/current-task.json')
            grant=brief['next_action']['repair_grant']['clip_01']
            assert grant['operation']=='retake_candidate' and grant['evidence']['candidate']==parent
            full=read_json(work/brief['full_status'])
            assert full['first_frames']['clips']['clip_01']['remaining_image_candidates']==3-candidates
            draft={'render_ready':False,'frames':[packet()['frames'][0]]}
            draft['frames'][0]['retake_candidate']['candidate']=parent
        else:pytest.fail('unchanged protocol error must not loop')
        atomic_write_json(work/'output/first-frames.json',draft)
        queued=bridges[0].handle('first_frames',{'file':'output/first-frames.json'})
        assert queued['status']=='accepted' and queued['yield'] is True
        folder=work/f'fake-attempt-{number}';folder.mkdir()
        return SimpleNamespace(ok=True,exit_code=0,directory=folder,
            record={'session_id':session['session_id']},produced=['first-frames.json'])
    monkeypatch.setattr(turns,'run_session',agent)
    result=flow.run_episode(ctx,config={**sandbox_config,'backend':'codex','production_mode':'agent'})
    assert resumes==[True,True,True]
    assert result['error']=='synthetic_stop_before_video'
    assert 0<result['remaining_seconds']<=25 and 7<=result['agent_seconds']<25
    assert len(images)==len(reviews)==2
    for cid in ['clip_01','clip_02']:
        assert len(list((ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid).glob('candidate_*')))==candidates+1
    assert not list(ctx.episode_dir.glob('repair_budget_grants*'))
    assert not list(ctx.episode_dir.rglob('*.mp4'))


@pytest.mark.parametrize('boundary',['quota_empty','cached_no_change','unknown_error'])
def test_current_retakes_do_not_reopen_empty_quota_cache_noop_or_unknown_failure(
        monkeypatch,episode_context,sandbox_config,boundary):
    ctx=episode_context;calls=install_fake_runtime(monkeypatch,ctx)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video'}]})
    monkeypatch.setattr(flow,'prepare_operations',lambda *a:(None,None))
    def forbidden(*a,**k):pytest.fail('nonactionable/current grant must not reopen the Agent')
    monkeypatch.setattr(turns,'run_session',forbidden)
    base=flow.EpisodeTools
    class Tools(base):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='first_frames':
                result.update(ok=boundary!='unknown_error',changed=[] if boundary=='cached_no_change' else ['clip_02'])
                if boundary=='unknown_error':result.update(error='AssertionError: unknown bug',retryable=False)
            if action=='status':result.update(adopted=True,first_frames={'clips':{'clip_01':{
                'remaining_image_candidates':0 if boundary=='quota_empty' else 2,
                'repair_origin':'image','last_rejection':{'candidate':'candidate_01'}}}})
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    monkeypatch.setattr(readiness,'next_action',lambda *a:{'action':'first_frames',
        'blocked':{'clip_01':'rejected'},'repair_grant':{'clip_01':{'operation':'retake_candidate'}}})
    result=flow.run_episode(ctx,config={**sandbox_config,'backend':'codex','production_mode':'agent'})
    assert result['status']=='blocked' and result['retryable'] is False
    assert calls.count('first_frames')==1


@pytest.mark.parametrize('error',[ValueError('unexpected builder failure'),AssertionError('unknown invariant')])
def test_unknown_failure_outside_operation_validation_remains_terminal(tmp_path,monkeypatch,error):
    ctx=rejected_openings(tmp_path,monkeypatch)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{
        'render_ready':False,'frames':[packet()['frames'][0]]})
    def fail(*a):raise error
    operation=first_frames.FirstFrameOperations(ctx,builder=fail,checker=lambda *a:{'findings':[]})
    with pytest.raises(type(error)):operation.prepare({})
    assert 'argument_errors' not in operation.partial_outcome
    row=operation.partial_outcome['clips']['clip_01']
    assert row['retryable'] is False and 'service_interrupted' not in row and 'repair_grant' not in row


@pytest.mark.parametrize('bad',['missing_parent','retake_shape','empty_reason','both',
                                 'missing_image','missing_rejection','reuse_missing','reuse_shape'])
def test_selector_checks_are_collected_before_reservation(tmp_path,monkeypatch,bad):
    ctx=rejected_openings(tmp_path,monkeypatch,candidates=2)
    row={'clip_id':'clip_01','extra_references':[],'retake_candidate':{
        'candidate':'candidate_02','reason':'correct visible pose'}}
    root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_01'
    if bad=='missing_parent':row['retake_candidate']['candidate']='candidate_03'
    elif bad=='retake_shape':row['retake_candidate']='candidate_02'
    elif bad=='empty_reason':row['retake_candidate']['reason']=' '
    elif bad=='both':row['reuse_candidate']='candidate_02'
    elif bad=='missing_image':(root/'candidate_02/opening.png').unlink()
    elif bad=='missing_rejection':(root/'candidate_02/rejected.json').unlink()
    elif bad.startswith('reuse'):
        row.pop('retake_candidate');row['reuse_candidate']='candidate_03' if bad=='reuse_missing' else {'candidate':'candidate_02'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'render_ready':False,'frames':[row]})
    def forbidden(*a):pytest.fail('selector validation cannot submit or review an image')
    operation=first_frames.FirstFrameOperations(ctx,builder=forbidden,checker=forbidden)
    with pytest.raises(ValueError):operation.prepare({})
    result=operation.partial_outcome;failed=result['clips']['clip_01']
    assert result['image_candidates_allocated']==0
    assert result['argument_errors']['clip_01'] and result['invalid_fields']['clip_01']
    assert failed['selector_candidates']['retake_candidate']
    assert failed['remaining_image_candidates']==1 and failed['image_candidate_limit']==3
    assert len(list(root.glob('candidate_*')))==2 and not (root/'candidate_03').exists()


def test_exhausted_selector_does_not_become_a_correctable_argument_loop(tmp_path,monkeypatch):
    ctx=rejected_openings(tmp_path,monkeypatch,candidates=3)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{
        'clip_id':'clip_01','retake_candidate':{'candidate':'candidate_04','reason':'wrong pose'}}]})
    operation=first_frames.FirstFrameOperations(ctx)
    with pytest.raises(ValueError,match='budget used'):operation.prepare({})
    assert 'argument_errors' not in operation.partial_outcome
    failed=operation.partial_outcome['clips']['clip_01']
    assert failed['retryable'] is False and failed['remaining_image_candidates']==0


def test_missing_saved_replacement_is_state_failure_not_selector_feedback(tmp_path,monkeypatch):
    ctx=rejected_openings(tmp_path,monkeypatch,candidates=2)
    parent=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_01/candidate_02'
    atomic_write_json(parent/'retake-decision.json',{'replacement_candidate':'candidate_03'})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{
        'clip_id':'clip_01','retake_candidate':{'candidate':'candidate_02','reason':'wrong pose'}}]})
    operation=first_frames.FirstFrameOperations(ctx)
    with pytest.raises(ValueError,match='saved retake replacement candidate is missing'):operation.prepare({})
    assert 'argument_errors' not in operation.partial_outcome
    assert operation.partial_outcome['clips']['clip_01']['retryable'] is False
