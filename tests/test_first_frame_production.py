"""Optional first-frame mode: actual conditions, freshness and source authority."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy
import base64

from PIL import Image
import pytest

from novel_manga.providers.local_h3 import LocalH3MediaProvider
from novel_manga.media import generation
from novel_manga.media.first_frames import opening_contract, require_current, video_metadata


def test_first_frame_is_raw_keyframe_zero_and_first_reference(tmp_path):
    frame=tmp_path/'frame.png'; Image.new('RGB',(10,20),'red').save(frame)
    provider=object.__new__(LocalH3MediaProvider)
    provider.settings=NS(video_model='h3');provider.ratio='9:16';provider.short_edge=768
    ordinary=provider._payload('words',[frame],(),8)
    assert [r['role'] for r in ordinary['conditions']]==['reference']
    payload=provider._payload('words',[frame],(),8,first_frame=frame)
    assert [(r['role'],r.get('frame_index')) for r in payload['conditions']]==[('reference',None),('keyframe',0)]
    assert base64.b64decode(payload['conditions'][0]['uri'].split(',',1)[1])==frame.read_bytes()
    assert payload['conditions'][0]['uri']==payload['conditions'][1]['uri']


def clip_case(tmp_path):
    original=[{'role':'location','path':'scene.png','name':'pool'},
              {'role':'character','path':'actor.png','name':'A'},
              {'role':'character','path':'new.png','name':'B'},
              {'role':'voice','path':'voice.wav','name':'A'}]
    clip={'clip_id':'clip_01','references':original,'shots':[{'start_state':'A alone','camera':'front','location':'pool','in_frame':['A'],'event':'B enters','turns':[]} ]}
    for name in ['scene.png','actor.png','new.png','frame.png']:Image.new('RGB',(4,8)).save(tmp_path/name)
    ctx=NS(novel_dir=tmp_path,profile={'h3_input_mode':'first-frame'})
    from novel_manga.util import atomic_write_json
    atomic_write_json(tmp_path/'profile.json',ctx.profile)
    clip['first_frame']={'path':'frame.png','extra_references':['new.png'],'visible_characters':['A'],'review':{'consistent':True,'problems':[]},'source_contract':opening_contract(clip,['scene.png','actor.png'])}
    return ctx,clip


def test_actual_images_select_frame_and_only_supplement_without_changing_sources(tmp_path):
    ctx,clip=clip_case(tmp_path);before=copy.deepcopy(clip['references'])
    assert generation.image_paths(ctx,clip)==(tmp_path/'frame.png',tmp_path/'new.png')
    assert [r['path'] for r in video_metadata(clip)]==['frame.png','new.png']
    assert clip['references']==before
    del clip['first_frame']
    assert generation.image_paths(ctx,clip)==tuple(tmp_path/r['path'] for r in before if r['role']!='voice')


def test_opening_changes_block_billing_but_dialogue_and_motion_do_not(tmp_path):
    ctx,clip=clip_case(tmp_path)
    require_current(ctx,clip)
    clip['shots'][0]['event']='B walks faster';clip['shots'][0]['turns']=[{'text':'new words'}]
    require_current(ctx,clip)
    clip['shots'][0]['camera']='rear'
    with pytest.raises(ValueError,match='stale'):require_current(ctx,clip)


def test_authored_opening_prompt_is_part_of_freshness_without_invalidating_legacy(tmp_path):
    ctx,clip=clip_case(tmp_path);old=copy.deepcopy(clip['first_frame']['source_contract'])
    assert 'opening_frame'not in old['opening']
    require_current(ctx,clip)
    clip['shots'][0]['opening_frame']={'image_prompt':'A alone beside the column.','visible_characters':['A']}
    with pytest.raises(ValueError,match='stale'):require_current(ctx,clip)


def test_frame_tool_uses_creator_prompt_and_refuses_untracked_rewrite(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import authored_row
    from novel_manga.util import atomic_write_json
    ctx,clip=clip_case(tmp_path);ctx.episode_dir=tmp_path/'book_1';ctx.episode_dir.mkdir()
    clip['shot_indexes']=[1]
    opening={'image_prompt':'A stands alone beside the column before B enters.','visible_characters':['A'],'reference_subjects':['A','pool']}
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[{'index':1,'opening_frame':opening}]})
    row=authored_row(ctx,clip,{'clip_id':'clip_01'})
    assert row['image_prompt']==opening['image_prompt']and row['visible_characters']==['A']
    with pytest.raises(ValueError,match='source opening_frame'):
        authored_row(ctx,clip,{'image_prompt':'A different untracked opening.'})


def test_real_authored_packing_keeps_creator_opening_prompt_for_each_source_shot(tmp_path):
    from novel_manga.interactive.authored import prepare_authored,pack_authored
    from novel_manga.interactive.first_frame_entry import select_mode
    from novel_manga.util import atomic_write_json,read_json
    project=Path(__file__).resolve().parents[1]/'projects/adventure/laughing-country'
    directory=prepare_authored(project,tmp_path/'edition');book=directory.parent
    script=read_json(directory/'chapter_script.json')
    for shot in script['shots']:
        shot['opening_frame']={'image_prompt':f'Approved static opening for shot {shot["index"]}.',
            'visible_characters':shot.get('in_frame',[])}
    atomic_write_json(directory/'chapter_script.json',script)
    profile=read_json(book/'profile.json');profile['h3_speech_policy']='reference';atomic_write_json(book/'profile.json',profile)
    select_mode(book,'first-frame');plan=pack_authored(directory)
    for clip in plan['clips']:
        expected=script['shots'][clip['shot_indexes'][0]-1]['opening_frame']
        assert clip['first_frame_opening'][0]['opening_frame']==expected


def test_missing_and_rejected_first_frames_block_billing(tmp_path):
    ctx,clip=clip_case(tmp_path)
    clip['first_frame']['review']['consistent']=False
    with pytest.raises(ValueError,match='approved'):require_current(ctx,clip)
    del clip['first_frame']
    with pytest.raises(ValueError,match='missing'):require_current(ctx,clip)


def test_first_frame_tool_rejects_old_take_and_keeps_rejected_candidates(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
    from novel_manga.util import atomic_write_json, read_json
    ctx,clip=clip_case(tmp_path);clip.pop('first_frame')
    ctx.episode_dir=tmp_path/'book_1';ctx.episode_dir.mkdir();ctx.run_dir=tmp_path/'run';ctx.work_dir=ctx.run_dir/'sandbox';(ctx.work_dir/'output').mkdir(parents=True)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',clip['references'])
    atomic_write_json(ctx.episode_dir/'segments.json',[{'segment_id':'s','text':'A alone then B enters'}])
    clip['segment_ids']=['s'];atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    row={'clip_id':'clip_01','image_prompt':'A at the pool','image_references':['scene.png','actor.png'],
         'visible_characters':['A'],'extra_references':['new.png'],'prompt_h3':'mapped words','source_quote':'A alone then B enters'}
    calls=[]
    def build(ctx,row,path):calls.append(path);Image.new('RGB',(10,20)).save(path)
    def reject(*a):return {'consistent':False,'problems':['wrong starting view']}
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',lambda *a:None)
    op=FirstFrameOperations(ctx,builder=build,checker=reject)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    assert op.prepare({})['ok'] is False
    assert 'first_frame' not in read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    assert op.prepare({})['ok'] is False
    assert len(calls)==1 # repeat rejected candidate does not spend a new image
    row['image_references']=['work/clips/clip_01/attempt_01/frame.jpg']
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    with pytest.raises(ValueError,match='registered source'):op.prepare({})


def test_opt_in_splits_authored_groups_and_keeps_three_second_source(tmp_path,monkeypatch):
    from novel_manga.interactive.first_frame_entry import select_mode
    from novel_manga.util import atomic_write_json,read_json
    ctx,clip=clip_case(tmp_path)
    episode=tmp_path/(tmp_path.name+'_1');episode.mkdir()
    atomic_write_json(episode/'authored_groups.json',[{'source_segment':'S18-S19','shot_indexes':[18,19]}])
    atomic_write_json(episode/'chapter_script.json',{'shots':[{'index':18,'duration_seconds':8,'shot_id':'S18'},{'index':19,'duration_seconds':3,'shot_id':'S19'}]})
    atomic_write_json(tmp_path/'profile.json',{'h3_speech_policy':'reference'})
    monkeypatch.setattr('novel_manga.interactive.authored.pack_authored',lambda directory:{'clips':[]})
    select_mode(tmp_path,'first-frame')
    assert [g['shot_indexes'] for g in read_json(episode/'authored_groups.json')]==[[18],[19]]
    assert read_json(episode/'chapter_script.json')['shots'][1]['duration_seconds']==3
    assert read_json(tmp_path/'profile.json')['h3_extra_reference_policy']=='needed'


def test_opt_in_does_not_rebind_a_live_edition(tmp_path):
    from novel_manga.interactive.first_frame_entry import select_mode
    from novel_manga.util import atomic_write_json
    episode=tmp_path/(tmp_path.name+'_1');(episode/'work/clips/c').mkdir(parents=True)
    (episode/'work/clips/c/clip.mp4.task.json').write_text('{"id":"paid"}')
    atomic_write_json(tmp_path/'profile.json',{})
    with pytest.raises(ValueError,match='new unrendered edition'):select_mode(tmp_path,'first-frame')


def test_real_render_adapter_forwards_keyframe(tmp_path,monkeypatch):
    from novel_manga.media.adapters import FramedLocalH3
    provider=object.__new__(FramedLocalH3);provider.prompt_aliases={}
    calls=[];provider.local=NS(create_video=lambda *args,**kw:calls.append((args,kw)))
    frame=tmp_path/'frame.png'
    provider.create_video('prompt',None,tmp_path/'clip.mp4',5,additional_images=(frame,),first_frame=frame)
    assert calls[0][1]['first_frame']==frame


def test_frozen_plan_and_h3_stamp_cover_actual_first_frame(tmp_path):
    from novel_manga.application.profiles import plan_fingerprint,h3_stamp
    ctx,clip=clip_case(tmp_path);clip.update(prompt='text',request_seconds=5)
    a=(plan_fingerprint({'clips':[clip]}),h3_stamp(clip))
    clip['first_frame']['path']='frame-v2.png'
    b=(plan_fingerprint({'clips':[clip]}),h3_stamp(clip))
    assert all(x!=y for x,y in zip(a,b))


def test_collapsed_subject_bindings_keep_voice_ids_and_newcomer_extra():
    from support.episode_agent import episode,CATALOG,SOURCES
    from novel_manga.application.agents.episode.contracts import adopt,validate_existing_request
    _,plan=adopt(episode(),catalog=CATALOG,source_segments=SOURCES,profile={})
    clip=plan['clips'][0]
    # Original pictures: B 1, scene 2, A 3. New actual pictures: opening 1, entering B card 2.
    clip['first_frame']={'path':'opening.png','extra_references':['cards/b.jpeg'],'visible_characters':['甲']}
    import re
    prompt=re.sub(r'<Picture (\d+)>',lambda m:f'<Picture { {1:2,2:1,3:1}[int(m[1])]}>',clip['prompt_h3'])
    validate_existing_request(clip,prompt)
    assert '<Audio 1>' in prompt
    with pytest.raises(ValueError,match='Subject/Picture'):validate_existing_request(clip,clip['prompt_h3'])


def test_short_source_timing_reaches_packer_with_native_floor(tmp_path,monkeypatch):
    from novel_manga.application.packing import service
    class ReachedPacking(Exception):pass
    monkeypatch.setattr(service,'clip_cast',lambda *a,**k:(_ for _ in ()).throw(ReachedPacking()))
    row={'kind':'video','shots':[{'index':19}],'request_seconds':4,'seconds':3,'shot_timing':[{'seconds':3}]}
    ctx={'bible':None,'overrides':{},'profile':{'h3_input_mode':'first-frame'}}
    with pytest.raises(ReachedPacking):service.clip_entry(row,'clip_19',ctx)
    ctx['profile']={}
    with pytest.raises(ValueError,match='timing'):service.clip_entry(row,'clip_19',ctx)


def test_only_a_new_reviewed_candidate_counts_as_agent_progress(tmp_path):
    from novel_manga.application.agents.episode import progress
    ctx=NS(work_dir=tmp_path/'sandbox',run_dir=tmp_path)
    before=progress.progress(ctx,{})
    failed={'job':{'id':'one','action':'first_frames','status':'completed','result':{'ok':False,'changed':[],'reviewed_candidates':['c/candidate_01']}}}
    assert progress.made_progress(ctx,before,{},failed)
    repeated={'job':{'id':'two','action':'first_frames','status':'completed','result':{'ok':False,'changed':[],'reviewed_candidates':[]}}}
    assert not progress.made_progress(ctx,before,failed,repeated)


def test_source_recompile_retains_opening_for_freshness_check(tmp_path):
    from novel_manga.media.first_frames import retain_opening
    ctx,clip=clip_case(tmp_path)
    after={k:copy.deepcopy(v) for k,v in clip.items() if k!='first_frame'}
    retain_opening(clip,after)
    assert after['first_frame']==clip['first_frame']
    assert after['first_frame'] is not clip['first_frame']
    require_current(ctx,after)
    after['shots'][0]['camera']='changed camera'
    with pytest.raises(ValueError,match='stale'):require_current(ctx,after)


def test_frame_tool_accepts_structured_h3_using_existing_source_compiler(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
    from novel_manga.util import atomic_write_json,read_json
    ctx,clip=clip_case(tmp_path);clip.pop('first_frame');clip['segment_ids']=['s']
    ctx.episode_dir=tmp_path/'book_1';ctx.episode_dir.mkdir();ctx.run_dir=tmp_path/'run';ctx.work_dir=ctx.run_dir/'sandbox';(ctx.work_dir/'output').mkdir(parents=True)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]});atomic_write_json(ctx.run_dir/'catalog.json',clip['references'])
    atomic_write_json(ctx.episode_dir/'segments.json',[{'segment_id':'s','text':'A alone then B enters'}])
    row={'clip_id':'clip_01','image_prompt':'A opening','image_references':['scene.png','actor.png'],'visible_characters':['A'],'extra_references':['new.png'],'h3':{'shots':['A waits. B enters. <DIALOGUE_1>'],'soundscape':'Water.'},'source_quote':'A alone then B enters'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]});seen=[]
    def compose(clip,h3):seen.append((clip,h3));return 'source-preserving mapped H3'
    monkeypatch.setattr('novel_manga.application.agents.episode.requests.compose_request',compose)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',lambda *a:None)
    def build(ctx,row,path):Image.new('RGB',(10,20)).save(path)
    result=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok']
    assert seen[0][0]['first_frame']['extra_references']==['new.png']
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['prompt_h3']=='source-preserving mapped H3'


def test_structured_pov_with_supplemental_scene_still_declares_opening_picture():
    from support.episode_agent import episode,CATALOG,SOURCES
    from novel_manga.application.agents.episode.contracts import adopt,validate_existing_request
    from novel_manga.application.agents.episode.requests import compose_request
    draft=episode(turns=[{'speaker_name':'甲','text':'你来啦。','delivery_mode':'offscreen_dialogue'}])
    _,plan=adopt(draft,catalog=CATALOG,source_segments=SOURCES,profile={});clip=plan['clips'][0]
    clip['references']=[r for r in clip['references'] if r['role']!='character']
    clip['cast']=[]
    clip['first_frame']={'path':'opening.png','extra_references':['cards/room.jpeg'],'visible_characters':[]}
    prompt=compose_request(clip,{'shots':['A rear-camera POV studies the water. <DIALOGUE_1>'],'soundscape':'Water.'})
    validate_existing_request(clip,prompt)
    assert '<Picture 1>' in prompt and '<Picture 2>' in prompt


@pytest.mark.parametrize('frame,expected',[('9:16','9:16'),('16:9','16:9')])
def test_first_frame_image_api_follows_selected_production_frame(tmp_path,monkeypatch,frame,expected):
    from novel_manga.application.agents.episode.first_frames import build
    from novel_manga.util import atomic_write_json
    ctx=NS(novel_dir=tmp_path,run_dir=tmp_path/'run')
    atomic_write_json(tmp_path/'profile.json',{'frame':frame})
    monkeypatch.setattr('novel_manga.config.Settings.from_env',lambda **kw:NS())
    calls=[]
    class Provider:
        def __init__(self,settings):pass
        def create_image(self,*args,**kwargs):calls.append(kwargs)
    monkeypatch.setattr('novel_manga.providers.phanrouter.PhanRouterMediaProvider',Provider)
    build(ctx,{'image_prompt':'single opening','image_references':['actor.png','scene.png']},tmp_path/'opening.png')
    assert calls[0]['aspect_ratio']==expected
