from pathlib import Path
from types import SimpleNamespace as NS
import threading
import pytest

from PIL import Image

from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
from novel_manga.application.agents.episode.first_take_stream import FirstTakeStream,take_inputs
from novel_manga.util import atomic_write_json,read_json


from support.first_frame_cases import case


def test_video_starts_before_next_image_finishes_and_resume_never_repeats(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    started=threading.Event();calls=[];built=[]
    def render(self,cid):
        from novel_manga.application.preparation.store import clip_video
        if clip_video(ctx.episode_dir,cid)is not None:return {'ok':True,'kept_existing':True}
        calls.append(cid)
        # The accepted candidate and adopted plan must be published before video.
        plan=read_json(ctx.episode_dir/'clip_plan.json')
        clip=next(c for c in plan['clips']if c['clip_id']==cid)
        assert (ctx.novel_dir/clip['first_frame']['path']).parent.joinpath('accepted.json').exists()
        output=ctx.episode_dir/'work/clips'/cid/'attempt_01/clip.mp4'
        output.parent.mkdir(parents=True);output.write_bytes(b'existing failed take')
        if cid=='clip_01':started.set()
        return {'ok':True,'technical_passed':False}
    monkeypatch.setattr(FirstTakeStream,'_render',render)
    def build(ctx,row,path):
        if row['clip_id']=='clip_02':
            assert started.wait(2),'video must start while the second image is still pending'
        built.append(row['clip_id']);Image.new('RGB',(8,16)).save(path)
    check=lambda *a:{'consistent':True,'problems':[]}
    for _ in range(2):
        with FirstTakeStream(ctx,None)as stream:
            result=FirstFrameOperations(ctx,builder=build,checker=check,on_accepted=stream.submit).prepare({})
        assert result['ok']
    assert calls==['clip_01','clip_02']
    assert built==['clip_01','clip_02']


def test_rejected_image_never_enters_video_queue(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch);queued=[]
    def build(ctx,row,path):Image.new('RGB',(8,16)).save(path)
    def check(ctx,path,row,contract):
        return {'consistent':row['clip_id']=='clip_02','problems':[]if row['clip_id']=='clip_02'else['wrong opening']}
    result=FirstFrameOperations(ctx,builder=build,checker=check,on_accepted=queued.append).prepare({})
    assert not result['ok'] and queued==['clip_02']


@pytest.mark.parametrize('changed', [{'prompt_h3':'Changed actual video directions'},{'repair_take':1}])
def test_stream_report_preserves_siblings_and_does_not_certify_changed_inputs(tmp_path,monkeypatch,changed):
    ctx,clips=case(tmp_path,monkeypatch)
    before=(ctx.episode_dir/'clip_plan.json').read_bytes()
    row1={'clip_id':'clip_01','attempts':[{'generated_this_run':True}],
          'selected':{'video':'first.mp4','passed':False},
          'stream_inputs':take_inputs(clips[0],'')}
    row2={'clip_id':'clip_02','attempts':[],
          'selected':{'video':'second.mp4','passed':True},
          'stream_inputs':take_inputs(clips[1],'')}
    with FirstTakeStream(ctx,None)as stream:
        stream._save(row1);stream._save(row2)
        report=read_json(ctx.episode_dir/'thin_media_report.json')
        assert report['clips']==[row1,row2] and report['pending_clips']==[]
        assert report['clip_plan_fingerprint']!='streaming-first-takes'
        assert (ctx.episode_dir/'clip_plan.json').read_bytes()==before
        clips[0].update(changed)
        atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
        stream._save(row2)
        assert read_json(ctx.episode_dir/'thin_media_report.json')['clip_plan_fingerprint']=='streaming-first-takes'


def test_source_audit_reused_for_new_frames_but_invalidated_for_script(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import local_preparation
    ctx,_=case(tmp_path,monkeypatch);calls=[]
    script=ctx.episode_dir/'chapter_script.json';script.write_text('{"shots":[]}')
    monkeypatch.setattr(local_preparation,'inputs',lambda d:{'files':{'script':script.read_text()},'plan':read_json(d/'clip_plan.json')})
    monkeypatch.setattr(local_preparation,'audit',lambda d:calls.append(d)or{'source_readable':True,'issues':[]})
    assert local_preparation.source_gate(ctx)is None
    plan=read_json(ctx.episode_dir/'clip_plan.json');plan['clips'][0]['first_frame']={'path':'new.png'}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    assert local_preparation.source_gate(ctx)is None and len(calls)==1
    script.write_text('{"shots":[{"index":1}]}')
    assert local_preparation.source_gate(ctx)is None and len(calls)==2


@pytest.mark.parametrize('request_matches',[True,False])
def test_existing_take_recovers_report_without_generating_or_certifying_changed_request(tmp_path,monkeypatch,request_matches):
    ctx,clips=case(tmp_path,monkeypatch)
    from novel_manga.application.agents.episode import first_take_stream as module
    video=ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True);video.write_bytes(b'original failed first take')
    atomic_write_json(video.parent/'request.json',{'prompt':'original request'})
    analyses=[]
    def analyse(clip,path):
        analyses.append(path)
        return {'video':str(path),'passed':False,'chunks':[],'issues':['actual speech defect']}
    runner=NS(context=NS(clip_plan={'clips':clips},feedback={},options=NS(),
                        state=NS(clip=lambda cid:NS(generated=False))),
              analyse_clip=analyse,
              generate_clip=lambda *a:pytest.fail('existing failed take must never be regenerated'))
    media=NS(runner=lambda:runner,evidence=NS(voice_recording_blocks=lambda plan:{}))
    monkeypatch.setattr('novel_manga.application.rendering.reviewed._load_current',lambda r:None)
    monkeypatch.setattr('novel_manga.media.first_frames.require_current',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.contracts.validate_existing_request',lambda *a:None)
    monkeypatch.setattr('novel_manga.media.generation.build_request',lambda *a:({},[],[],''))
    monkeypatch.setattr('novel_manga.media.cache.request_matches',lambda *a,**kw:request_matches)
    monkeypatch.setattr(module.history,'complete_generation',lambda *a:None)
    monkeypatch.setattr(module.history,'generation_counts',lambda *a:{'clip_01':1})
    monkeypatch.setattr(module.history,'begin_trial',lambda *a,**kw:pytest.fail('recovery must retain original trial'))
    with FirstTakeStream(ctx,media)as stream:stream.submit('clip_01')
    result=stream.result()
    row=read_json(ctx.episode_dir/'thin_media_report.json')['clips'][0]
    assert video.read_bytes()==b'original failed first take'
    assert result['generation_counts']=={'clip_01':1} and not result['video_progress']
    if request_matches:
        assert analyses==[video] and 'error'not in row
        assert row['selected']['passed']is False and row['stream_inputs']==take_inputs(clips[0],'')
    else:
        assert not analyses and 'existing take differs' in row['error']
        assert 'stream_inputs'not in row
