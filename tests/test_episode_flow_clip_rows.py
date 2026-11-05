from types import SimpleNamespace
import pytest

from novel_manga.application.agents.episode import flow,readiness
from novel_manga.util import atomic_write_json
from support.episode_runtime import episode_context as ctx,sandbox_config as config,install_fake_runtime


@pytest.mark.parametrize('clip_rows',[
    [{'clip_id':'clip_01','severity':'pass'},{'clip_id':'clip_02','severity':'pass'},{'clip_id':'clip_03','severity':'pass'}],
    ['clip_01','clip_02','clip_03'],
    {'clip_01':{'repair_grant':{'operation':'input'}}},
])
def test_controller_accepts_review_lists_and_first_frame_grants_without_touching_takes(ctx,config,monkeypatch,clip_rows):
    calls=install_fake_runtime(monkeypatch,ctx);original=flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video'}]})
    atomic_write_json(ctx.work_dir/'episode-session.json',{'session_id':'retained-author'})
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'retrying','job':{
        'id':'job-5','action':'first_frames' if isinstance(clip_rows,dict) else 'review',
        'status':'completed','result':{'ok':True,'clips':clip_rows}}})
    videos=[]
    for index in (1,2,3):
        path=ctx.episode_dir/f'clip_{index:02d}.mp4';path.write_bytes(b'original native video');videos.append(path)
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='status':result['adopted']=True
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    monkeypatch.setattr(flow,'prepare_operations',lambda *a:(None,None))
    monkeypatch.setattr(readiness,'automatic_request',lambda *a,**k:None)
    monkeypatch.setattr(readiness,'next_action',lambda *a:{'action':'first_frames' if isinstance(clip_rows,dict) else 'agent'})
    turns=[]
    def invoke(context,bridge,state,selected,prompt,resume,remaining):
        turns.append(prompt)
        directory=ctx.work_dir/'attempt-list-handoff';directory.mkdir()
        bridge.handle('finish',{})
        return SimpleNamespace(ok=True,exit_code=0,directory=directory,record={},produced=[]),remaining,remaining
    monkeypatch.setattr(flow,'invoke',invoke)
    result=flow.run_episode(ctx,config={**config,'production_mode':'agent'})
    assert result['passed'] is True and len(turns)==1
    assert 'render' not in calls
    assert all(path.read_bytes()==b'original native video' for path in videos)


def test_review_list_does_not_mask_unrelated_execution_failure(ctx,config,monkeypatch):
    install_fake_runtime(monkeypatch,ctx)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'retrying','job':{
        'id':'job-5','action':'review','status':'completed','result':{'ok':True,'clips':['clip_01']}}})
    monkeypatch.setattr(flow,'prepare_operations',lambda *a:(None,None))
    monkeypatch.setattr(readiness,'automatic_request',lambda *a,**k:None)
    monkeypatch.setattr(readiness,'next_action',lambda *a:{'action':'render','error':'media_execution_failed','reason':'unknown execution failure'})
    monkeypatch.setattr(flow,'invoke',lambda *a:pytest.fail('unknown execution failure must still stop'))
    result=flow.run_episode(ctx,config={**config,'production_mode':'agent'})
    assert result['status']=='blocked' and result['error']=='media_execution_failed'
    assert result['reason']=='unknown execution failure'
