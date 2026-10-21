from pathlib import Path
from types import SimpleNamespace as NS

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json,read_json


def test_service_error_returns_completed_work_and_settled_video_progress(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import first_frames,first_take_stream,local_preparation
    ctx=NS(work_dir=tmp_path/'work')
    (ctx.work_dir/'output').mkdir(parents=True)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{}],'render_ready':True})
    events=[]
    class Frames:
        def __init__(self,ctx,on_accepted=None):self.accepted=on_accepted
        def prepare(self,payload):
            self.partial_outcome={'changed':['clip_01'],'reviewed_candidates':['clip_01/candidate_01'],
                                  'clips':{'clip_01':{'ok':True}}}
            self.accepted('clip_01')
            raise RuntimeError('review service 503')
    class Stream:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def submit(self,cid):events.append(cid)
        def __exit__(self,*a):events.append('settled')
        def result(self):
            assert events[-1]=='settled'
            return {'video_progress':True,'video_stream':{'clip_01':{'ok':True}}}
    monkeypatch.setattr(first_frames,'FirstFrameOperations',Frames)
    monkeypatch.setattr(first_take_stream,'FirstTakeStream',Stream)
    monkeypatch.setattr(local_preparation,'source_gate',lambda ctx:None)
    tools=object.__new__(EpisodeTools);tools.ctx=ctx;tools.media=object()
    result=tools.first_frames({})
    assert not result['ok'] and '503' in result['error']
    assert result['changed']==['clip_01'] and result['video_progress']
    assert read_json(ctx.work_dir/'input/latest-first-frames.json')==result


def test_real_image_loop_preserves_completed_candidate_before_later_service_error(tmp_path,monkeypatch):
    from support.first_frame_cases import case
    from PIL import Image
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
    import pytest
    ctx,_=case(tmp_path,monkeypatch)
    def build(ctx,row,path):Image.new('RGB',(8,16)).save(path)
    def check(ctx,path,row,contract):
        if row['clip_id']=='clip_02':raise RuntimeError('review service 503')
        return {'consistent':True,'problems':[]}
    operation=FirstFrameOperations(ctx,builder=build,checker=check)
    with pytest.raises(RuntimeError,match='503'):operation.prepare({})
    assert operation.partial_outcome['changed']==['clip_01']
    assert operation.partial_outcome['reviewed_candidates']==['clip_01/candidate_01']
    assert (ctx.novel_dir/'first_frames/book_1/clip_02/candidate_01/opening.png').is_file()


def test_rejected_candidate_is_an_actionable_repair_not_only_missing_image(tmp_path,monkeypatch):
    from support.first_frame_cases import case
    from PIL import Image
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations,status
    ctx,_=case(tmp_path,monkeypatch)
    def build(ctx,row,path):Image.new('RGB',(8,16)).save(path)
    op=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':False,'problems':['headlamp appears too early']})
    op.prepare({})
    row=status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))['clips']['clip_01']
    assert row['last_rejection']['problems']==['headlamp appears too early']
    assert row['last_rejection']['evidence']=='input/first_frames/clip_01/candidate_01'
    # Cached rejections live in the per-candidate review input cache now: an unchanged
    # review input returns the saved rejection without a new image (see current_review);
    # the actionable path is source input repair or one bounded retake_candidate.
    assert 'input repair' in row['next_action'] and 'retake_candidate' in row['next_action']
    assert row['remaining_image_candidates']==2


def test_current_task_includes_current_frame_and_video_failures_and_evidence(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare
    ctx=NS(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path,
           novel_dir=tmp_path/'book')
    atomic_write_json(ctx.novel_dir/'profile.json',{'h3_input_mode':'first-frame'})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','shot_indexes':[1]}]})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'clips':[{'clip_id':'clip_01','error':'lamp timing conflict'}]})
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',{})
    status={'adopted':True,'repair_actions':{'clip_01':['input']},
            'job':{'id':'job-1','action':'first_frames','status':'completed','result':{
                'ok':False,'clips':{'clip_01':{'ok':False,'error':'rejected','review':{'problems':['wrong lamp']},'evidence':'input/first_frames/clip_01/candidate_01'},
                                   'clip_old':{'ok':False,'error':'old failure'}},
                'video_stream':{'clip_01':{'ok':False,'error':'lamp timing conflict'}}}}}
    task=prepare(ctx,status,{'issues':[]},[])
    assert task['targets'][0]['last_media_error']=='lamp timing conflict'
    assert task['last_operation']['result']['first_frame_failures']['clip_01']['problems']==['wrong lamp']
    assert task['last_operation']['result']['video_input_failures']=={'clip_01':'lamp timing conflict'}
    assert 'input/latest-first-frames.json'in task['evidence_files']
    assert 'old failure'not in str(task)
