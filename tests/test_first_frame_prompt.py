"""The image API receives source facts and bindings for its actual upload order."""
import copy
from types import SimpleNamespace as NS

from PIL import Image

from novel_manga.util import atomic_write_json,read_json


def test_visual_suffix_excludes_story_and_other_camera_rules_but_keeps_look():
    from novel_manga.style_packs import first_frame_prompt
    style={'name':'Whole episode','render_family':'photo','visual_style':'Real fabric and skin.',
        'h3_style_line':'Natural observational photography.',
        'art_direction':{'palette':{'base':['ivory'],'accent':['green']},
            'intent':'A later invitation changes the pairing.',
            'composition':['Establish all six seats before the later invitation.'],
            'lighting':['Interview room lighting is fixed.'],
            'asset_guidance':{'character':['Another guest wears an apron.']}}}
    before=copy.deepcopy(style)
    result=first_frame_prompt('Static two-shot at dusk.',style)
    assert 'Real fabric and skin.'in result and 'Natural observational photography.'in result
    assert 'ivory'in result and 'green'in result
    assert 'six seats'not in result and 'Interview room'not in result and 'Another guest'not in result
    assert 'later invitation'not in result and style==before


def test_reference_numbering_follows_uploaded_subset_order_and_preserves_continuity_prose():
    from novel_manga.media.first_frames import compose_opening_image_prompt
    references=[{'path':'actor.png','role':'character','name':'A','reference_scope':'identity_portrait'},
        {'path':'room.png','role':'location','name':'Room'},{'path':'cup.png','role':'prop','name':'Cup'}]
    before=copy.deepcopy(references)
    authored='Two adults across the same table. The cloth is held by two clips; the dark-grey shim stays under the left leg.'
    light='19:00 terrace dusk, warm practical fill, neutral skin; darker than preparation on the same day.'
    result=compose_opening_image_prompt(authored,{},['room.png','actor.png'],references,opening={'light':light})
    assert 'Image 1: location Room'in result and 'Image 2: character A'in result
    assert 'face and hair only'in result and 'Cup'not in result
    assert authored in result and light in result and references==before


def test_review_uses_same_appearance_scope_and_keeps_current_continuity(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import check
    captured={}
    def ask(parts,*a,**kw):
        captured['text']=parts[0]['text']
        return {'observations':['Actual image observed'],'findings':[]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda *a:{'type':'text','text':'actual image'})
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:None)
    contract={'opening':{'start_state':'Two clips secure the cloth; grey shim stays under the left leg.'},
        'style':{'visual_style':'Natural skin and warm wood.',
            'art_direction':{'composition':['Establish all six seats.'],'intent':'Later switch the pairing.'}}}
    before=copy.deepcopy(contract)
    check(NS(novel_dir=tmp_path),tmp_path/'opening.png',{'image_prompt':'Static two-shot.',
        'visible_characters':['A','B'],'image_references':['a.png']},contract)
    assert 'six seats'not in captured['text']and 'switch the pairing'not in captured['text']
    assert 'grey shim'in captured['text']and 'Natural skin'in captured['text']and contract==before


def test_fast_preparation_compiles_bindings_and_keeps_authored_current_state(tmp_path,monkeypatch):
    from test_fast_first_frame_entry import fixture_book
    from novel_manga.interactive.sd_first_frames import prepare
    book,episode=fixture_book(tmp_path)
    Image.new('RGB',(8,16)).save(book/'room.png')
    script=read_json(episode/'chapter_script.json')
    body='A waits by the table; two clips secure the same cloth and the dark-grey shim stays under its left leg.'
    script['shots'][0]['opening_frame']['image_prompt']=body
    script['shots'][0]['light']='19:00 natural dusk with warm house light and neutral skin.'
    script['shots'][0]['opening_frame']['reference_subjects']=['Room','A']
    atomic_write_json(episode/'chapter_script.json',script)
    plan=read_json(episode/'clip_plan.json');plan['clips'][0]['references']=[
        {'path':'room.png','role':'location','name':'Room'},
        {'path':'actor.png','role':'character','name':'A','reference_scope':'identity_portrait'}]
    atomic_write_json(episode/'clip_plan.json',plan)
    captured=[]
    def build(ctx,row,path):captured.append(copy.deepcopy(row));Image.new('RGB',(8,16)).save(path)
    prepare(tmp_path,book,builder=build,checker=lambda *a:{'consistent':True,'problems':[]})
    assert captured[0]['image_references']==['room.png','actor.png']
    assert 'Image 1: location Room'in captured[0]['request_prompt']
    assert 'Image 2: character A'in captured[0]['request_prompt']
    assert body in captured[0]['request_prompt']
    assert script['shots'][0]['light']in captured[0]['request_prompt']


def test_h3_preparation_binds_reordered_registered_references(tmp_path,monkeypatch):
    from test_first_frame_production import clip_case
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
    ctx,clip=clip_case(tmp_path);clip.pop('first_frame')
    clip['shots'][0]['light']='Warm practical light at dusk; same day.'
    ctx.episode_dir=tmp_path/'book_1';ctx.episode_dir.mkdir()
    ctx.run_dir=tmp_path/'run';ctx.work_dir=ctx.run_dir/'sandbox';(ctx.work_dir/'output').mkdir(parents=True)
    clip['segment_ids']=['s'];atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',clip['references'])
    atomic_write_json(ctx.episode_dir/'segments.json',[{'segment_id':'s','text':'A alone then B enters'}])
    row={'clip_id':'clip_01','image_prompt':'A waits. The cloth remains secured by two clips.',
        'image_references':['scene.png','actor.png'],'visible_characters':['A'],'extra_references':[],
        'prompt_h3':'already supplied request','source_quote':'A alone then B enters'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',lambda *a:None)
    captured=[]
    def build(ctx,row,path):captured.append(copy.deepcopy(row));Image.new('RGB',(8,16)).save(path)
    assert FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})['ok']
    request=captured[0]['request_prompt']
    assert 'Image 1: location'in request and 'Image 2: character A'in request
    assert row['image_prompt']in request
    assert clip['shots'][0]['light']in request


def test_build_reuses_exact_frozen_request_with_same_uploaded_files(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import build
    captured={}
    atomic_write_json(tmp_path/'profile.json',{'frame':'9:16'})
    class Provider:
        def __init__(self,settings):pass
        def create_image(self,prompt,output,**kwargs):captured.update(prompt=prompt,**kwargs)
    monkeypatch.setattr('novel_manga.providers.phanrouter.PhanRouterMediaProvider',Provider)
    monkeypatch.setattr('novel_manga.config.Settings.from_env',lambda **kw:NS())
    row={'image_prompt':'source prompt','request_prompt':'Exact previously frozen request.',
        'image_references':['room.png','actor.png']}
    build(NS(novel_dir=tmp_path,run_dir=tmp_path/'run'),row,tmp_path/'opening.png')
    assert captured['prompt']=='Exact previously frozen request.'
    assert captured['reference']==tmp_path/'room.png'and captured['additional_references']==(tmp_path/'actor.png',)
