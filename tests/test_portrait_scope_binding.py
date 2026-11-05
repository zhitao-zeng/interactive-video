"""Imported portrait scope reaches packing, continuous dialogue and real requests."""
import copy
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novel_manga.application.packing.assets import build_references
from novel_manga.interactive.authored import pack_authored, prepare_authored
from novel_manga.interactive.conversation import compile_conversation_clip
from novel_manga.media.generation import build_request
from novel_manga.models.bible import Character, StoryBible
from novel_manga.story.dialogue import dialogue_punctuation
from novel_manga.story.h3 import compose, subject_lines
from tests.test_scene_compilation import options

ROOT = Path(__file__).resolve().parents[1]


def bible():
    return StoryBible(novel_title='肖像范围回归', genre='romance', visual_style='真人',
        palette='木色', style_fingerprint='test', locations=['厨房：北侧料理台'],
        characters=[Character(name='嘉宾甲', appearance='成年黑发女性，面部自然', wardrobe='墨绿过膝连衣裙，米白平底鞋'),
                    Character(name='嘉宾乙', appearance='成年短发男性，自然眉眼', wardrobe='浅蓝衬衫，炭灰长裤')])


def portrait(root, *, scope='identity_portrait', expressions=False):
    card=root/'series_assets/characters/character_001/turnaround.jpeg'
    card.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (32,48), 'gray').save(card)
    if scope is not None:
        card.with_suffix('.jpeg.origin.json').write_text(json.dumps({'reference_scope':scope}))
    if expressions:
        Image.new('RGB', (32,48), 'gray').save(card.parent/'expressions.jpeg')
    return card


def subject_request(refs):
    clip={'clip_id':'clip_01','kind':'video','request_seconds':12,
          'render_family':'photo','h3_style_line':'Photorealistic live-action footage.',
          'references':refs,'shot_timing':[{'stage':1,'seconds':12}]}
    return compose(clip,['The people stand at the kitchen counter.'],[('',[])],authored=True)


def test_imported_portrait_scope_survives_pack_conversation_and_actual_requests(tmp_path):
    source=tmp_path/'material';source.mkdir()
    data=json.loads((ROOT/'tests/fixtures/interactive/minimal-material.json').read_text())
    data['bible']=bible().model_dump(mode='json')
    data['profile'].update(style='modern-live',two_views='off')
    shot=data['script']['shots'][0]
    shot.update(characters=['嘉宾甲','嘉宾乙'],in_frame=['嘉宾甲','嘉宾乙'],
        location='厨房',scene_id='厨房',duration_seconds=12,authored_duration_seconds=12,
        visual_prompt='嘉宾甲在西侧，嘉宾乙在东侧，两人站在料理台前。',
        start_state='嘉宾甲在西侧，嘉宾乙在东侧，两人站在料理台前。',
        end_state='两人保持原站位，小碟留在料理台西侧。',camera='南侧固定双人中景',
        motion_prompt='嘉宾甲示意小碟，嘉宾乙回应。',props=[],
        turns=[{'speaker_name':'嘉宾甲','text':'这个先给你？','delivery_mode':'visible_dialogue','emotion':'自然'},
               {'speaker_name':'嘉宾乙','text':'好，我来拿。','delivery_mode':'visible_dialogue','emotion':'自然'}],
        authored_frame='A continuous two-person shot. <Subject 1> wears a dark green knee-length dress and cream flat shoes. '
            '<Subject 2> wears a pale blue shirt and charcoal trousers. <DIALOGUE_1> <DIALOGUE_2> '
            'Both people remain at the same kitchen counter.')
    data['groups']=[{'shot_indexes':[1],'source_segment':'原镜头1'}]
    data['voice_specs']={}
    data['reference_images']=[]
    for index, scope in [(1,'identity_portrait'),(2,'supplied-reference')]:
        image=source/f'person{index}.png';Image.new('RGB',(32,48),'gray').save(image)
        data['reference_images'].append({'source':image.name,
            'path':f'series_assets/characters/character_{index:03d}/turnaround.jpeg','scope':scope})
    image=source/'kitchen.png';Image.new('RGB',(64,48),'gray').save(image)
    data['reference_images'].append({'source':image.name,
        'path':'series_assets/locations/location_001/establishing.jpeg','scope':'location'})
    (source/'source.md').write_text(shot['source_quote'])
    (source/'material.json').write_text(json.dumps(data,ensure_ascii=False))
    episode=prepare_authored(source,tmp_path/'episode',family='romance')
    packed=pack_authored(episode)['clips'][0]
    first=packed['references'][0]
    assert first['reference_scope']=='identity_portrait'
    assert first['wardrobe']==data['bible']['characters'][0]['wardrobe']
    assert '墨绿过膝连衣裙' in packed['prompt']
    assert '只采用五官、发型、体型和服装' not in packed['prompt'].split('<嘉宾乙>')[0]
    before=copy.deepcopy(packed)
    clip=compile_conversation_clip(packed,shot,performance={'自然':'Respond naturally to the partner.'})
    assert packed==before
    assert '墨绿过膝连衣裙' in clip['prompt']
    assert '嘉宾甲采用@图片1的身份与衣装' not in clip['prompt']
    assert '嘉宾乙采用@图片2的身份与衣装' in clip['prompt']
    assert clip['references']==packed['references']
    assert 'dark green knee-length dress' in clip['prompt_h3']
    assert 'Identity and outfit design follow <Picture 1>' not in clip['prompt_h3']
    assert 'identity and outfit design of <Picture 1>' not in clip['prompt_h3']
    assert 'partially_preserved' in clip['prompt_h3']
    assert not re.search(r'[\u3400-\u9fff]',re.sub(r'<d>.*?</d>','',clip['prompt_h3']))
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url=''),feedback={},novel_dir=episode.parent,
        state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False)),
        options=SimpleNamespace(voice_budget=15),provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':768}))
    sd,sd_refs,_,_=build_request(ctx,clip,1)
    ctx.settings.local_h3_base_url='pool'
    h3,h3_refs,_,_=build_request(ctx,clip,1)
    assert sd['prompt']==clip['prompt'] and h3['prompt']==clip['prompt_h3']
    assert sd_refs==h3_refs==tuple(episode.parent/r['path'] for r in packed['references'])
    for turn in shot['turns']:
        assert sd['prompt'].count(turn['text'])==1
        assert h3['prompt'].count(dialogue_punctuation(turn['text']))==1
    for prompt in (sd['prompt'],h3['prompt']):
        assert prompt.index('这个先给你')<prompt.index('我来拿')


@pytest.mark.parametrize('two_views',['off','all'])
def test_portrait_only_cards_never_supply_whole_body_or_costume(tmp_path,two_views):
    portrait(tmp_path,expressions=two_views=='all')
    refs,bindings,_=build_references(['嘉宾甲'],None,bible(),{},novel_dir=tmp_path,
        settings=replace(options(15),two_views=two_views))
    assert refs[0]['reference_scope']=='identity_portrait'
    assert all(r.get('reference_scope')=='identity_portrait' for r in refs)
    assert '墨绿过膝连衣裙' in bindings[0]
    assert '定身体比例、服装版型' not in bindings[0]
    prompt=subject_request(refs)
    subject=prompt.split('subject_definitions:',1)[1].split('retention_analysis:',1)[0]
    assert 'body proportions,' not in subject and 'outfit design follow' not in subject
    assert 'Body proportions and clothing follow the shot description' in subject
    assert 'outfit design of' not in prompt.split('retention_analysis:',1)[1]
    assert 'shot description' in subject


@pytest.mark.parametrize('two_views',['off','all'])
@pytest.mark.parametrize('scope',[None,'supplied-reference'])
def test_legacy_full_body_cards_keep_existing_bindings(tmp_path,two_views,scope):
    portrait(tmp_path,scope=scope,expressions=two_views=='all')
    refs,bindings,_=build_references(['嘉宾甲'],None,bible(),{},novel_dir=tmp_path,
        settings=replace(options(15),two_views=two_views))
    prompt=subject_request(refs)
    if two_views=='off':
        assert '只采用五官、发型、体型和服装' in bindings[0]
        assert 'Identity and outfit design follow <Picture 1>.' in prompt
    else:
        assert '@图片1定身体比例、服装版型、主色和配饰' in bindings[0]
        assert 'face, hair, age and skin tone from <Picture 2>' in prompt
        assert 'garment cut, main colours and accessories from <Picture 1>' in prompt
    assert 'fully_preserved - the identity and outfit design' in prompt


def test_formal_portrait_plus_fullbody_keeps_body_design_reference():
    refs=[{'role':'character','name':'嘉宾甲','path':'portrait.jpeg','reference_scope':'identity_portrait'},
          {'role':'character','name':'嘉宾甲','path':'full-body.jpeg','view':'turnaround'}]
    definitions,mapping=subject_lines({'references':refs})
    assert mapping=={'嘉宾甲':1}
    assert len(definitions)==1
    assert 'face, hair, age and skin tone from <Picture 1>' in definitions[0]
    assert 'garment cut, main colours and accessories from <Picture 2>' in definitions[0]
    assert 'fully_preserved - the identity and outfit design' in subject_request(refs)


def test_portrait_with_worn_prop_keeps_identity_without_inventing_body_from_face():
    refs=[{'role':'character','name':'嘉宾甲','path':'portrait.jpeg','reference_scope':'identity_portrait'},
          {'role':'prop','name':'围裙','path':'apron.jpeg','wearers':['嘉宾甲']}]
    prompt=subject_request(refs)
    assert 'face, hair and build follow <Picture 1>' not in prompt
    assert 'Other clothing follows <Picture 1>' not in prompt
    assert 'item shown in <Picture 2>' in prompt
    assert 'other clothing from <Picture 1>' not in prompt
    assert 'shot description' in prompt


@pytest.mark.parametrize('performance_table',[False,True])
def test_native_conversation_binds_authored_performance_once_in_actual_request(tmp_path,performance_table):
    from tests.test_romance_conversation import sample
    clip,shot=sample()
    clip['h3_speech_policy']='no_reference_performance_first'
    cues=['Ask the partner while searching for the cup.',
          'Answer casually while pointing to the table.',
          'Acknowledge the answer with a small relieved smile.']
    for turn,cue in zip(shot['turns'],cues):turn['emotion']=cue
    performance={cue:cue for cue in cues} if performance_table else {}
    compiled=compile_conversation_clip(clip,shot,performance=performance)
    for ref in compiled['references']:(tmp_path/ref['path']).write_bytes(b'reference')
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),feedback={},novel_dir=tmp_path,
        state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False)),
        options=SimpleNamespace(voice_budget=15),provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':768}))
    request,refs,_,_=build_request(ctx,compiled,1)
    assert request['reference_audios']==[]
    assert len(refs)==5
    assert 'Performance:' not in request['prompt']
    previous=0
    for cue,turn in zip(cues,shot['turns']):
        assert request['prompt'].count(cue)==1
        text=dialogue_punctuation(turn['text'])
        assert request['prompt'].count(text)==1
        assert previous < request['prompt'].index(cue) < request['prompt'].index(text)
        previous=request['prompt'].index(text)
