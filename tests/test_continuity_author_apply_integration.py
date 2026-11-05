"""A verified opening clarification goes through the ordinary input repair route."""
import copy
import json

from PIL import Image

from novel_manga.application.agents.episode import first_frames, opening_source, readiness
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json, read_json
from support.input_repair import setup


def test_author_clarification_check_apply_then_review_existing_image(setup,monkeypatch):
    ctx,plan,_=setup
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS','1')
    atomic_write_json(ctx.novel_dir/'profile.json',{'h3_input_mode':'first-frame'})
    for filename in ('a.jpeg','b.jpeg','room.jpeg'):
        Image.new('RGB',(8,16)).save(ctx.novel_dir/'cards'/filename)
    script=read_json(ctx.episode_dir/'chapter_script.json')
    previous,current=script['shots'][:2]
    previous.update(scene_id='same_scene',end_state='甲未背回物品。')
    current.update(scene_id='same_scene',start_state='甲站在门口，道具按场景保持。',
                   opening_frame={'image_prompt':'甲站在门口，道具按场景保持。',
                                  'visible_characters':['甲'],'reference_subjects':['甲','房间']})
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    plan['clips']=[plan['clips'][1]]
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'render_ready':False,'frames':[
        {'clip_id':'clip_02','extra_references':[]}]})
    images=[];checks=[]
    def build(ctx,row,path):
        images.append(row['clip_id']);Image.new('RGB',(8,16)).save(path)
    def check(*args):
        checks.append(1);return {'findings':[]}
    monkeypatch.setattr(first_frames,'validate_existing_request',lambda *a:None)
    monkeypatch.setattr(first_frames,'ensure_current_h3',lambda *a:None)
    monkeypatch.setattr(first_frames,'review_opening_sources',lambda *a:{})
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=check)
    assert operation.prepare({})['ok']
    before=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']
    pixels=(ctx.novel_dir/before['path']).read_bytes()
    candidate=(ctx.novel_dir/before['path']).parent
    original_input=(candidate/'input.json').read_bytes()
    original_review=(candidate/'reviews/review_001.json').read_bytes()
    monkeypatch.setattr(first_frames,'review_opening_sources',opening_source.review_batch)
    preflights=[]
    def ask(parts,schema,**kw):
        payload=json.loads(parts[0]['text'].rsplit('\n',1)[1])
        preflights.append(payload)
        result=[]
        for row in payload['clips']:
            findings=[]
            if row['clip_id']=='clip_02' and row['opening']['start_state']==current['start_state']:
                findings=[{'kind':'continuity_clarification','origin':'source','relation':'underspecified',
                    'observed':'','expected':'','reason':'请作者判断当前道具状态。',
                    'basis':['opening.start_state','previous_shot_context.end_state']}]
            result.append({'clip_id':row['clip_id'],'findings':findings})
        return {'reviews':result}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    live_plan=read_json(ctx.episode_dir/'clip_plan.json')
    clip=live_plan['clips'][0]
    compiled=first_frames.compile_opening(ctx,clip,{'clip_id':'clip_02','extra_references':[]})
    verdict=opening_source.review_batch(ctx,[compiled])['clip_02']
    assert verdict['findings'][0]['continuity_verified'] is True
    grant=first_frames.current_frame_grant(ctx,clip)
    assert grant['operation']=='input' and grant['shot_indexes']==[2]
    assert set(grant['shot_fields'])=={'opening_frame','start_state'}
    decision={'action':'input','clip_id':'clip_02','reason':'澄清当前开场状态',
              'source_quote':'甲坐在宿舍','shots':[{'index':2,
              'start_state':'甲站在门口，物品放在桌上。',
              'opening_frame':{**current['opening_frame'],'image_prompt':'甲站在门口，物品放在桌上。'}}]}
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[decision]})
    tools=EpisodeTools(ctx)
    checked=tools.check({'file':'output/repair.json'})
    assert checked['valid'],checked
    applied=tools.apply({'file':'output/repair.json'})
    assert applied['ok'],applied
    revised=read_json(ctx.episode_dir/'chapter_script.json')
    assert revised['shots'][0]==previous and revised['shots'][1]['turns']==current['turns']
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    status={'adopted':True,'first_frames':first_frames.status(ctx,plan),
            'job':{'action':'apply','status':'completed','result':applied}}
    request=readiness.automatic_request(ctx,status)
    assert request is not None,readiness.next_action(ctx,status)
    selected=next(row for row in request['document']['frames'] if row['clip_id']=='clip_02')
    assert selected['reuse_candidate']==before['candidate']
    atomic_write_json(ctx.work_dir/request['payload']['file'],request['document'])
    assert operation.prepare({})['ok']
    after=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']
    assert images==['clip_02'] and len(checks)==2
    assert after['candidate']==before['candidate'] and (ctx.novel_dir/after['path']).read_bytes()==pixels
    assert (candidate/'input.json').read_bytes()==original_input
    assert (candidate/'reviews/review_001.json').read_bytes()==original_review
    assert len(preflights)==2 and preflights[-1]['clips'][0]['opening']['start_state']=='甲站在门口，物品放在桌上。'
