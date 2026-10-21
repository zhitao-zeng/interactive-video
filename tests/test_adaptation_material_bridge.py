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
