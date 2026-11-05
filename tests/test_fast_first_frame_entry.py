"""Fast uses its own request shape and source-checked API openings."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy
import pytest
from PIL import Image

from novel_manga.util import atomic_write_json,read_json
from novel_manga.media import generation
from novel_manga.media.adapters import FramedPhanRouter
from novel_manga.providers.phanrouter_video import video_payload
from novel_manga.interactive.sd_first_frames import prepare,select_mode


def test_task_asset_channel_can_use_supplier_urls_instead_of_an_old_tunnel(tmp_path,monkeypatch):
    from novel_manga.interactive import runtime
    import os
    from unittest.mock import patch
    atomic_write_json(tmp_path/'configs/interactive.json',{'services':{'text_endpoint':'phanthy-ds41'}})
    atomic_write_json(tmp_path/'.runtime/romance-demo/asset-channel.json',{'public_base':''})
    monkeypatch.setattr(runtime,'environment',lambda *args:{'PHANROUTER_ASSET_PUBLIC_BASE':'https://expired.invalid'})
    monkeypatch.setattr('novel_manga.application.configuration.model_resources_environment',lambda *args:{})
    with patch.dict(os.environ,{},clear=True):
        runtime.configure(tmp_path,'sd20-fast',project='romance')
        assert os.environ['PHANROUTER_ASSET_PUBLIC_BASE']==''


def test_normal_generation_reaches_fast_first_frame_payload(tmp_path,monkeypatch):
    frame=tmp_path/'opening.png';Image.new('RGB',(18,32)).save(frame)
    provider=object.__new__(FramedPhanRouter)
    provider.settings=NS(video_model='sd2.0-fast');provider.video_ratio='9:16';provider.video_resolution='480p'
    provider.prompt_aliases={};provider.client=None;provider.video_headers={}
    provider._restore_image_url=lambda image:'asset://approved-opening'
    seen=[]
    monkeypatch.setattr('novel_manga.providers.phanrouter.video.generate_video',
        lambda settings,client,headers,payload,output,*args,**kwargs:seen.append(payload) or output)
    ctx=NS(novel_dir=tmp_path,settings=NS(local_h3_base_url=None),provider=provider)
    clip={'request_seconds':12,'first_frame':{'path':'opening.png'}}
    generation.submit(ctx,clip,{'prompt':'Approved dialogue','reference_audios':[]},tmp_path/'clip.mp4',(frame,))
    assert seen[0]['model']=='sd2.0-fast'
    assert seen[0]['content']==[{'type':'text','text':'Approved dialogue'},
        {'type':'image_url','image_url':{'url':'asset://approved-opening'},'role':'first_frame'}]
    assert seen[0]['generate_audio'] is True


@pytest.mark.parametrize('model,image,extras,audios',[
    ('sd2.5','asset://frame',(),()),('sd2.0-fast',None,(),()),
    ('sd2.0-fast','asset://frame',('asset://portrait',),()),
    ('sd2.0-fast','asset://frame',(),('audio',))])
def test_unsupported_first_frame_mixes_fail_before_submission(model,image,extras,audios):
    with pytest.raises(ValueError,match='first-frame'):
        video_payload(NS(video_model=model),'dialogue',image,12,extras,audios,first_frame=True)


def fixture_book(tmp_path):
    book=tmp_path/'romance-fast';episode=book/(book.name+'_1');episode.mkdir(parents=True)
    Image.new('RGB',(18,32)).save(book/'actor.png')
    atomic_write_json(book/'profile.json',{'sd_input_mode':'first-frame','aspect_ratio':'9:16'})
    opening={'image_prompt':'One actor standing by the table.','visible_characters':['A'],'reference_subjects':['A']}
    shot={'index':1,'camera':'wide','in_frame':['A'],'opening_frame':opening}
    clip={'clip_id':'clip_01','kind':'video','request_seconds':12,'shot_indexes':[1],'shots':[shot],
          'h3_speech_policy':'no_reference_performance_first',
          'references':[{'role':'character','name':'A','path':'actor.png'}]}
    atomic_write_json(episode/'chapter_script.json',{'shots':[shot]})
    atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    return book,episode


def test_api_opening_is_reviewed_once_and_resume_reuses_it(tmp_path,monkeypatch):
    book,episode=fixture_book(tmp_path);calls=[]
    monkeypatch.setattr('novel_manga.style_packs.frame_style',lambda novel:{})
    monkeypatch.setattr('novel_manga.style_packs.first_frame_prompt',lambda text,style:text)
    def build(ctx,row,path):calls.append('image');Image.new('RGB',(18,32)).save(path)
    def check(*args):calls.append('review');return {'consistent':True,'problems':[]}
    result=prepare(tmp_path,book,builder=build,checker=check)
    assert result['clips'][0]['first_frame']['extra_references']==[]
    prepare(tmp_path,book,builder=build,checker=check)
    assert calls==['image','review']
    assert 'h3_input_mode' not in read_json(book/'profile.json')


def parallel_book(tmp_path,monkeypatch,count):
    book,episode=fixture_book(tmp_path)
    profile=read_json(book/'profile.json');profile['inflight']=2;atomic_write_json(book/'profile.json',profile)
    clip=read_json(episode/'clip_plan.json')['clips'][0];shot=read_json(episode/'chapter_script.json')['shots'][0]
    clips=[];shots=[]
    for index in range(1,count+1):
        row=copy.deepcopy(shot);row['index']=index;shots.append(row)
        entry=copy.deepcopy(clip);entry.update(clip_id=f'clip_{index:02d}',shot_indexes=[index],shots=[row]);clips.append(entry)
    atomic_write_json(episode/'chapter_script.json',{'shots':shots});atomic_write_json(episode/'clip_plan.json',{'clips':clips})
    monkeypatch.setattr('novel_manga.style_packs.frame_style',lambda novel:{})
    monkeypatch.setattr('novel_manga.style_packs.first_frame_prompt',lambda text,style:text)
    return book,episode


def test_fast_images_overlap_and_ready_frame_is_saved_before_slow_sibling(tmp_path,monkeypatch):
    import threading
    from novel_manga.interactive import sd_first_frames
    book,episode=parallel_book(tmp_path,monkeypatch,2)
    owner=threading.get_ident();slow=threading.Event();adopted=threading.Event()
    def write(path,value):
        if path==episode/'clip_plan.json':assert threading.get_ident()==owner
        atomic_write_json(path,value)
        if path==episode/'clip_plan.json'and value['clips'][1].get('first_frame'):adopted.set()
    monkeypatch.setattr(sd_first_frames,'atomic_write_json',write)
    def build(ctx,row,path):
        if path.parent.parent.name=='clip_01':slow.set();assert adopted.wait(3)
        else:assert slow.wait(3)
        Image.new('RGB',(18,32)).save(path)
    plan=prepare(tmp_path,book,builder=build,checker=lambda *a:{'consistent':True,'problems':[]})
    assert all(c.get('first_frame')for c in plan['clips'])


def test_fast_failure_drains_started_images_retains_task_and_resumes_without_new_submission(tmp_path,monkeypatch):
    import threading
    book,episode=parallel_book(tmp_path,monkeypatch,3);started=threading.Event();failed=threading.Event()
    def build(ctx,row,path):
        cid=path.parent.parent.name
        assert cid!='clip_03'
        if cid=='clip_01':started.set();assert failed.wait(3)
        else:assert started.wait(3)
        Image.new('RGB',(18,32)).save(path);atomic_write_json(path.with_suffix('.png.task.json'),{'task_id':cid})
    def check(ctx,path,*args):
        if path.parent.parent.name=='clip_02':failed.set();raise RuntimeError('review unavailable')
        return {'consistent':True,'problems':[]}
    with pytest.raises(RuntimeError,match='unavailable'):prepare(tmp_path,book,builder=build,checker=check)
    folder=book/'first_frames'/episode.name
    assert read_json(episode/'clip_plan.json')['clips'][0].get('first_frame')
    assert not(folder/'clip_03').exists()
    task=folder/'clip_02/candidate_01/opening.png.task.json';assert read_json(task)['task_id']=='clip_02'
    def resumed(ctx,row,path):
        assert path.parent.parent.name=='clip_03';Image.new('RGB',(18,32)).save(path)
    plan=prepare(tmp_path,book,builder=resumed,checker=lambda *a:{'consistent':True,'problems':[]})
    assert all(c.get('first_frame')for c in plan['clips'])
    assert read_json(task)['task_id']=='clip_02'
    assert all(len(list((folder/c['clip_id']).glob('candidate_*')))==1 for c in plan['clips'])


def test_all_source_openings_are_checked_before_any_image_charge(tmp_path,monkeypatch):
    book,episode=fixture_book(tmp_path)
    plan=read_json(episode/'clip_plan.json');second=copy.deepcopy(plan['clips'][0]);second.update(clip_id='clip_02',shot_indexes=[2],shots=[])
    plan['clips'].append(second);atomic_write_json(episode/'clip_plan.json',plan)
    monkeypatch.setattr('novel_manga.style_packs.frame_style',lambda novel:{})
    monkeypatch.setattr('novel_manga.style_packs.first_frame_prompt',lambda text,style:text)
    with pytest.raises(ValueError,match='creator opening_frame required'):
        prepare(tmp_path,book,builder=lambda *args:pytest.fail('No partial image spend'))


def test_fast_prompt_does_not_point_at_removed_portrait_numbers():
    ctx=NS(settings=NS(local_h3_base_url=None),profile={'sd_input_mode':'first-frame'},feedback={})
    clip={'clip_id':'clip_01','first_frame':{'path':'opening.png'},
          'prompt':'【人物】A对应@图片2\n【场景】@图片3\n【动作】A说：{你好}，保持@图片2衣装'}
    prompt=generation.clip_base(ctx,clip)
    assert '@图片2'not in prompt and '@图片3'not in prompt
    assert '【人物】'not in prompt and '{你好}' in prompt and '【首帧】' in prompt


def test_mode_cannot_be_added_to_an_existing_take(tmp_path):
    book,episode=fixture_book(tmp_path);atomic_write_json(book/'profile.json',{})
    task=episode/'work/clips/clip_01/attempt_01/clip.mp4.task.json';atomic_write_json(task,{'task_id':'retained'})
    with pytest.raises(ValueError,match='new unrendered edition'):select_mode(book,'first-frame')
    assert read_json(task)=={'task_id':'retained'}


def test_fast_entry_stays_in_normal_sd_production(tmp_path,monkeypatch):
    from novel_manga.interactive import production
    book,episode=fixture_book(tmp_path);seen=[]
    monkeypatch.setattr(production,'edition_directory',lambda *args:book)
    monkeypatch.setattr(production,'configure',lambda root,lane,**kwargs:seen.append(('configure',lane)))
    monkeypatch.setattr('novel_manga.interactive.sd_first_frames.prepare',lambda *args:seen.append(('openings','sd20-fast')))
    runner=NS()
    monkeypatch.setattr(production,'runner_for',lambda root,book,lane,**kwargs:seen.append(('runner',lane))or runner)
    monkeypatch.setattr('novel_manga.interactive.first_frame_entry.run',lambda *args,**kwargs:pytest.fail('No H3 Agent'))
    monkeypatch.setattr('novel_manga.interactive.agent.run',lambda runner,lane,**kwargs:seen.append(('review-loop',lane))or {})
    monkeypatch.setattr('novel_manga.interactive.delivery.deliver',lambda *args:{'status':'candidate'})
    production.run_episode(tmp_path,'sd20-fast',project='romance',edition=book.name,sd_input_mode='first-frame')
    assert ('review-loop','sd20-fast') in seen
