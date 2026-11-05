"""Adaptation produce bridge: validated creator candidate → series-merged material."""
import json
from pathlib import Path

import pytest

from novel_manga.interactive import creative_material
from novel_manga.util import atomic_write_json


def series_base(tmp_path):
    base = tmp_path/'series'/'material'
    base.mkdir(parents=True)
    atomic_write_json(base/'material.json', {
        'profile': {'style': 'poolrooms-live', 'frame': '9:16', 'width': 1080, 'height': 1920, 'fps': 25},
        'bible': {'locations': ['旧场：白砖浅水'], 'characters': [{'name': '弥夏'}], 'props': [], 'continuity_rules': []},
        'voice_specs': {'弥夏': {'sample_text': 'x', 'prompt': 'y'}},
        'reference_images': [{'source': 'ref.png'}], 'asset_status': {'ready': True},
    })
    return base


def draft():
    return {
        'title': 'EP02-A', 'logline': '闭园广播',
        'chapter_script': {'shots': [
            {'index': 1, 'scene_id': 'pool_platform', 'location': '干平台自由短语', 'duration_seconds': 7,
             'start_state': '坐在平台', 'event': '收好水瓶', 'end_state': '起身', 'camera': 'selfie',
             'turns': [{'speaker': '弥夏', 'text': '水和吃的收好。'}], 'sfx': ['水声', '布料摩擦']},
            {'index': 2, 'scene_id': 'new_park', 'location': '日光公园外缘另一短语', 'duration_seconds': 8,
             'start_state': '出暗口', 'event': '眯眼看公园', 'end_state': '迈步', 'camera': 'rear',
             'turns': [], 'sfx': '广播声'},
        ]},
        'clips': [{'shot_indexes': [1], 'request_seconds': 7}, {'shot_indexes': [2]}],
        'interaction': {'question': '先做哪件？', 'options': [{'id': 'A', 'label': 'A｜沿广播核对'}]},
        'spatial_plan': {'scenes': [
            {'scene_id': 'pool_platform', 'location': '池室·干平台', 'anchors': ['白砖干平台']},
            {'scene_id': 'new_park', 'location': '日光公园外缘', 'anchors': ['步道与指示牌']},
        ]},
    }


def manifest():
    return {'mode': 'adaptation', 'project': 'suspense', 'profile': {'style': 'poolrooms-live'},
            'source_archive': 'pkg.zip'}


def test_adaptation_bridge_merges_series_and_normalizes_the_draft(tmp_path):
    base = series_base(tmp_path)
    script = tmp_path/'pkg'/'EP02-A.md'; script.parent.mkdir(parents=True); script.write_text('# EP02-A\n原稿')
    style = tmp_path/'style.json'; style.write_text('{"visual_style": "poolrooms-live"}')
    out = creative_material.write_adaptation_material(
        draft(), manifest(), tmp_path/'material',
        series_material=base, package_script=script, style_source=style)
    value = json.loads((out/'material.json').read_text())
    shots = value['script']['shots']
    assert [s['location'] for s in shots] == ['池室·干平台', '日光公园外缘']
    assert shots[0]['sfx'] == '水声；布料摩擦' and shots[1]['sfx'] == '广播声'
    assert shots[0]['shot_id'] == 'S01' and shots[0]['segment_id'] == 'seg_1'
    assert shots[0]['source_kind'] == 'adapted-agent'
    assert [g['shot_indexes'] for g in value['groups']] == [[1], [2]]
    assert value['groups'][0]['location'] == '池室·干平台'
    assert value['profile']['h3_speech_policy'] == 'no_reference_performance_first'
    assert value['profile']['style'] == 'poolrooms-live'
    assert '日光公园外缘：' in ''.join(value['bible']['locations'])
    assert value['voice_specs'] == {'弥夏': {'sample_text': 'x', 'prompt': 'y'}}
    assert (out/'source.md').read_text().startswith('# EP02-A')
    assert json.loads((out/'style.json').read_text())['visual_style'] == 'poolrooms-live'
    assert value['target_seconds'] == 7 + 8 + 3


def test_adaptation_bridge_rejects_uncovered_or_sceneless_drafts(tmp_path):
    base = series_base(tmp_path)
    script = tmp_path/'s.md'; script.write_text('x')
    broken = draft(); broken['clips'] = [{'shot_indexes': [1]}]
    with pytest.raises(ValueError, match='cover every shot'):
        creative_material.write_adaptation_material(broken, manifest(), tmp_path/'m1',
            series_material=base, package_script=script)
    ghost = draft(); ghost['chapter_script']['shots'][0]['scene_id'] = 'missing'
    with pytest.raises(ValueError, match='spatial_plan has no scene'):
        creative_material.write_adaptation_material(ghost, manifest(), tmp_path/'m2',
            series_material=base, package_script=script)


def test_adaptation_keeps_supplied_identities_and_adopts_authored_new_designs(tmp_path):
    base=series_base(tmp_path);source=tmp_path/'source.md';source.write_text('原稿中的小兽出现。')
    value=draft();value['chapter_script']['shots'][1]['characters']=['小兽']
    value['chapter_script']['shots'][1]['in_frame']=['小兽']
    value['production_design']={'characters':[
        {'name':'弥夏','appearance':'must not replace supplied identity','wardrobe':'different'},
        {'name':'小兽','appearance':'source-supported small animal','wardrobe':'natural fur'}],
        'props':[{'name':'纸袋','appearance':'plain paper bag'}]}
    out=creative_material.write_adaptation_material(value,manifest(),tmp_path/'out',series_material=base,package_script=source)
    actual=json.loads((out/'material.json').read_text())['bible']
    assert actual['characters'][0]=={'name':'弥夏'}
    assert actual['characters'][1]['name']=='小兽'
    assert actual['props'][0]['name']=='纸袋'
    assert json.loads((base/'material.json').read_text())['bible']['characters']==[{'name':'弥夏'}]


def test_unknown_actor_without_design_is_returned_to_creator_before_handoff(tmp_path):
    base=series_base(tmp_path);value=draft();value['chapter_script']['shots'][1]['characters']=['小兽']
    issues=creative_material.adaptation_issues(value,{**manifest(),'task':{'series_material':str(base)}})
    assert any('小兽' in issue and 'production_design.characters' in issue for issue in issues)


def test_two_scenes_in_one_new_place_create_one_location_reference(tmp_path):
    base=series_base(tmp_path);source=tmp_path/'s.md';source.write_text('source')
    value=draft();scenes=value['spatial_plan']['scenes']
    scenes[1]['location']=scenes[0]['location'];scenes[1]['anchors']=scenes[0]['anchors']
    out=creative_material.write_adaptation_material(value,manifest(),tmp_path/'out',series_material=base,package_script=source)
    locations=json.loads((out/'material.json').read_text())['bible']['locations']
    assert sum(s.startswith('池室·干平台：') for s in locations)==1


def test_authored_location_design_reaches_reference_generation_with_spatial_anchors(tmp_path):
    base=series_base(tmp_path);source=tmp_path/'s.md';source.write_text('source')
    value=draft();value['production_design']={'locations':[
        {'name':'日光公园外缘','description':'Open daylight path beside a glass-roofed indoor potting table.'}]}
    out=creative_material.write_adaptation_material(value,manifest(),tmp_path/'out',series_material=base,package_script=source)
    locations=json.loads((out/'material.json').read_text())['bible']['locations']
    row=next(s for s in locations if s.startswith('日光公园外缘：'))
    assert 'glass-roofed indoor potting table' in row
    assert '步道与指示牌' in row
