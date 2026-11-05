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
    from support.first_frame_cases import fixture_book
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
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',lambda *a:{})
    captured=[]
    def build(ctx,row,path):captured.append(copy.deepcopy(row));Image.new('RGB',(8,16)).save(path)
    prepare(tmp_path,book,builder=build,checker=lambda *a:{'consistent':True,'problems':[]})
    assert captured[0]['image_references']==['room.png','actor.png']
    assert 'Image 1: location Room'in captured[0]['request_prompt']
    assert 'Image 2: character A'in captured[0]['request_prompt']
    assert body in captured[0]['request_prompt']
    assert script['shots'][0]['light']in captured[0]['request_prompt']


def test_h3_preparation_uses_host_ordered_registered_references(tmp_path,monkeypatch):
    from support.first_frame_cases import clip_case
    from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
    ctx,clip=clip_case(tmp_path);clip.pop('first_frame')
    clip['shots'][0]['light']='Warm practical light at dusk; same day.'
    clip['shots'][0]['opening_frame']={'image_prompt':'A waits. The cloth remains secured by two clips.',
        'visible_characters':['A'],'reference_subjects':['pool','A']}
    ctx.episode_dir=tmp_path/'book_1';ctx.episode_dir.mkdir()
    ctx.run_dir=tmp_path/'run';ctx.work_dir=ctx.run_dir/'sandbox';(ctx.work_dir/'output').mkdir(parents=True)
    clip['segment_ids']=['s'];atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',clip['references'])
    atomic_write_json(ctx.episode_dir/'segments.json',[{'segment_id':'s','text':'A alone then B enters'}])
    row={'clip_id':'clip_01','extra_references':[]}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',lambda *a:{})
    from support.first_frame_cases import stub_host_h3
    stub_host_h3(monkeypatch)
    captured=[]
    def build(ctx,row,path):captured.append(copy.deepcopy(row));Image.new('RGB',(8,16)).save(path)
    assert FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})['ok']
    request=captured[0]['request_prompt']
    assert 'Image 1: location'in request and 'Image 2: character A'in request
    assert captured[0]['image_prompt']in request
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


def test_h3_and_sd_share_compiled_references(tmp_path):
    from support.first_frame_cases import fixture_book
    from novel_manga.media.first_frames import compile_opening
    book,episode=fixture_book(tmp_path)
    Image.new('RGB',(8,16)).save(book/'room.png')
    script=read_json(episode/'chapter_script.json')
    script['shots'][0]['in_frame']=['A','offscreen speaker']
    script['shots'][0]['opening_frame'].update(visible_characters=[],reference_subjects=['A'])
    atomic_write_json(episode/'chapter_script.json',script)
    plan=read_json(episode/'clip_plan.json');clip=plan['clips'][0]
    clip['shots']=script['shots']
    clip['references'].append({'path':'room.png','role':'location','name':'Room'})
    ctx=NS(novel_dir=book,episode_dir=episode,run_dir=tmp_path/'run')
    left=compile_opening(ctx,clip,{'clip_id':'clip_01'},lane='h3')
    right=compile_opening(ctx,clip,{'clip_id':'clip_01'},lane='sd20-fast')
    signature=lambda compiled:[(r.path,r.hash,r.role,r.reference_scope,r.order) for r in compiled.references]
    assert signature(left)==signature(right)
    assert [(r.path,r.order) for r in left.references]==[('actor.png',0)]
    assert len(left.references[0].hash)==64
    assert left.assets[0]['hash']==left.references[0].hash
    assert left.row['image_references']==[r.path for r in left.references]
    assert left.row['request_prompt']==right.row['request_prompt']
    assert left.draft_frame['visible_characters']==[]
    assert left.contract['opening']['in_frame']==['A','offscreen speaker']


def test_redundant_image_references_must_equal_host_compiled_opening(tmp_path):
    import pytest
    from support.first_frame_cases import fixture_book
    from novel_manga.media.first_frames import compile_opening
    book,episode=fixture_book(tmp_path)
    Image.new('RGB',(8,16)).save(book/'room.png')
    script=read_json(episode/'chapter_script.json')
    script['shots'][0]['opening_frame']['reference_subjects']=['Room','A']
    atomic_write_json(episode/'chapter_script.json',script)
    plan=read_json(episode/'clip_plan.json');clip=plan['clips'][0]
    clip['shots']=script['shots']
    clip['references'].append({'path':'room.png','role':'location','name':'Room'})
    clip['references'].reverse()
    ctx=NS(novel_dir=book,episode_dir=episode,run_dir=tmp_path/'run')
    expected=['room.png','actor.png']
    assert compile_opening(ctx,clip,{'clip_id':'clip_01'}).row['image_references']==expected
    assert compile_opening(ctx,clip,{'clip_id':'clip_01','image_references':expected,
        'extra_references':['new.png']}).row['extra_references']==['new.png']
    for references in (list(reversed(expected)),expected[:1]):
        with pytest.raises(ValueError,match='opening_frame.reference_subjects.*input repair'):
            compile_opening(ctx,clip,{'clip_id':'clip_01','image_references':references})


def test_compiled_opening_appends_frozen_style_examples_after_subject_and_wardrobe(tmp_path):
    from support.first_frame_cases import clip_case
    from novel_manga.media.first_frames import compile_opening
    ctx,clip=clip_case(tmp_path)
    ctx.episode_dir=tmp_path/'book_1';ctx.run_dir=tmp_path/'run'
    folder=tmp_path/'series_assets/characters/character_001';folder.mkdir(parents=True)
    face=folder/'turnaround.jpeg';body=folder/'wardrobe-card.jpeg'
    Image.new('RGB',(8,16),'blue').save(face);Image.new('RGB',(8,16),'green').save(body)
    atomic_write_json(face.with_name(face.name+'.origin.json'),{
        'kind':'supplied-reference','reference_scope':'identity-baseline'})
    atomic_write_json(body.with_name(body.name+'.origin.json'),{
        'kind':'supplied-reference','reference_scope':'wardrobe-visual-card'})
    actor={'role':'character','name':'A','asset_id':'character_001','path':str(face.relative_to(tmp_path))}
    clip['references']=[clip['references'][0],actor]
    clip['shots'][0]['opening_frame']={'image_prompt':'A waits.',
        'visible_characters':['A'],'reference_subjects':['pool','A']}
    examples=tmp_path/'style-examples';examples.mkdir()
    for name,color in [('z-last alphabetically.png','red'),('a-first alphabetically.png','blue')]:
        Image.new('RGB',(12,8),color).save(examples/name)
    atomic_write_json(tmp_path/'style.json',{'name':'Frozen style','visual_style':'Tactile materials',
        'examples':[{'kind':'original_style_board','path':'style-examples/z-last alphabetically.png',
                     'scope':'style_only_not_character_identity'},
                    {'kind':'historical_style_frame','path':'style-examples/a-first alphabetically.png',
                     'scope':'style_only_not_character_identity'}]})
    compiled=compile_opening(ctx,clip,{'clip_id':'clip_01'})
    expected=['scene.png',str(face.relative_to(tmp_path)),str(body.relative_to(tmp_path)),
              'style-examples/z-last alphabetically.png','style-examples/a-first alphabetically.png']
    assert [r.path for r in compiled.references]==expected
    assert [r.order for r in compiled.references]==list(range(5))
    assert all(len(r.hash)==64 for r in compiled.references)
    assert [r.reference_scope for r in compiled.references[-2:]]==[
        'style_only_not_character_identity','style_only_not_character_identity']
    assert compiled.row['image_references']==expected
    assert compiled.contract['image_references']==expected
    assert [row['hash'] for row in compiled.assets]==[r.hash for r in compiled.references]
    prompt=compiled.row['request_prompt']
    assert 'Image 4: style-reference' in prompt and 'Image 5: style-reference' in prompt
    assert ('color, light, materials and rendering only; do not copy character identity, '
            'wardrobe, story, text, layout or POV') in prompt
