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
    authored = draft()
    for shot in authored['chapter_script']['shots']:
        shot['visual_prompt'] = 'STALE_VISUAL_MUST_NOT_SURVIVE'
        shot['motion_prompt'] = 'STALE_MOTION_MUST_NOT_SURVIVE'
    out = creative_material.write_adaptation_material(
        authored, manifest(), tmp_path/'material',
        series_material=base, package_script=script, style_source=style)
    value = json.loads((out/'material.json').read_text())
    shots = value['script']['shots']
    assert [s['visual_prompt'] for s in shots] == [s['start_state'] for s in shots]
    assert [s['motion_prompt'] for s in shots] == [s['event'] for s in shots]
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


def unregistered_location_draft():
    value=draft()
    value['clips'][1]['request_seconds']=8
    for shot in value['chapter_script']['shots']:
        shot.update(scene_id='entrance',purpose='观察入口证据',source_quote='原文',characters=['弥夏'],in_frame=['弥夏'])
        for turn in shot['turns']:
            turn.update(speaker_name=turn.pop('speaker'),delivery_mode='visible_dialogue',emotion='calm')
    value['chapter_script']['shots'][0]['location']='旧灯塔门外'
    value['chapter_script']['shots'][1]['location']='旧灯塔入口内侧'
    value['production_design']={'locations':[{'name':'旧灯塔门外','description':'门外石阶与门槛'}]}
    value['spatial_plan']['scenes']=[{'scene_id':'entrance','shot_indexes':[1,2],
        'anchors':['门槛','石墙'],'placed':['弥夏'],'assumptions':'尺寸未定'}]
    value['interaction'].update(selected=None,options=[{'id':'A','label':'门','reason':'已见'},
        {'id':'B','label':'石阶','reason':'已见'}])
    value['self_review']='已通读'
    return value


def test_creator_rejects_unregistered_secondary_scene_location_before_material_handoff(tmp_path):
    import copy
    from novel_manga.interactive import creative_contract
    base=series_base(tmp_path);value=unregistered_location_draft();before=copy.deepcopy(value)
    source=tmp_path/'source.txt';source.write_text('原文')
    spec={**manifest(),'task':{'series_material':str(base),'production_handoff':True},
        'source_documents':['source.txt'],'target_seconds':[14,20]}
    issues=creative_contract.validate(value,spec,tmp_path)
    error=next(issue for issue in issues if '旧灯塔入口内侧' in issue)
    assert 'shot 2' in error and 'entrance' in error
    assert '旧灯塔门外' in error and 'spatial_plan' in error
    assert value==before
    with pytest.raises(ValueError,match='旧灯塔入口内侧'):
        creative_material.write_adaptation_material(value,spec,tmp_path/'out',series_material=base,package_script=source)
    assert not (tmp_path/'out/material.json').exists()


def test_author_location_registration_matches_material_and_actual_reference_packer(tmp_path):
    import copy
    from novel_manga.application.packing.assets import build_references
    from novel_manga.models.bible import StoryBible
    base=series_base(tmp_path);value=unregistered_location_draft();source=tmp_path/'source.txt';source.write_text('原文')
    spec={**manifest(),'task':{'series_material':str(base)}}
    value['production_design']['locations'].append({'name':'旧灯塔入口内侧','description':'入口内侧石墙与工具箱'})
    # A design name alone does not repair the scene registration used by handoff.
    assert any('旧灯塔入口内侧' in issue for issue in creative_material.adaptation_issues(value,spec))
    value['chapter_script']['shots'][1]['scene_id']='entrance_inside'
    value['spatial_plan']['scenes'][0].update(location='旧灯塔门外',shot_indexes=[1])
    value['spatial_plan']['scenes'].append({'scene_id':'entrance_inside','location':'旧灯塔入口内侧',
        'shot_indexes':[2],'anchors':['门槛内侧','工具箱'],'placed':['弥夏'],'assumptions':'尺寸未定'})
    before=copy.deepcopy(value)
    assert creative_material.adaptation_issues(value,spec)==[]
    out=creative_material.write_adaptation_material(value,spec,tmp_path/'out',series_material=base,package_script=source)
    actual=json.loads((out/'material.json').read_text());locations=actual['bible']['locations']
    bible=StoryBible(novel_title='test',genre='generic',visual_style='live action',palette='natural',
        style_fingerprint='test',locations=locations)
    mapping={loc.split('：',1)[0]:loc for loc in locations}
    for shot in actual['script']['shots']:
        refs,_,_=build_references([],shot['location'],bible,mapping)
        assert any(ref['role']=='location' and ref['name']==shot['location'] for ref in refs)
        assert shot['visual_prompt']==shot['start_state'] and shot['motion_prompt']==shot['event']
    assert actual['voice_specs']==json.loads((base/'material.json').read_text())['voice_specs']
    assert actual['reference_images']==[{'source':'ref.png'}]
    assert value==before


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


def _style_with_attachment(tmp_path):
    examples = tmp_path/'style-examples'
    examples.mkdir()
    attachment = examples/'look.png'
    attachment.write_bytes(b'frozen-style-attachment-v1')
    style = tmp_path/'style.json'
    atomic_write_json(style, {'visual_style': 'poolrooms-live', 'examples': [
        {'kind': 'original_style_board', 'path': 'style-examples/look.png'}]})
    return style, attachment


def _snapshot_tree(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def test_adaptation_material_retry_preserves_an_identical_frozen_snapshot(tmp_path):
    import os
    base = series_base(tmp_path)
    source = tmp_path/'source.md'; source.write_text('原稿')
    style, _ = _style_with_attachment(tmp_path)
    out = tmp_path/'out'
    value = draft()
    creative_material.write_adaptation_material(value, manifest(), out,
        series_material=base, package_script=source, style_source=style)
    for path in out.rglob('*'):
        if path.is_file():
            os.utime(path, (1, 1))
    before = _snapshot_tree(out)
    creative_material.write_adaptation_material(value, manifest(), out,
        series_material=base, package_script=source, style_source=style)
    assert _snapshot_tree(out) == before


@pytest.mark.parametrize('change', ['material', 'source', 'style_attachment'])
def test_adaptation_material_retry_rejects_changed_frozen_inputs(tmp_path, change):
    base = series_base(tmp_path)
    source = tmp_path/'source.md'; source.write_text('原稿')
    style, attachment = _style_with_attachment(tmp_path)
    out = tmp_path/'out'
    value = draft()
    creative_material.write_adaptation_material(value, manifest(), out,
        series_material=base, package_script=source, style_source=style)
    before = _snapshot_tree(out)
    if change == 'material':
        value['chapter_script']['shots'][0]['event'] = '不同的事件'
    elif change == 'source':
        source.write_text('不同的原稿')
    else:
        attachment.write_bytes(b'changed-style-attachment')
    with pytest.raises(ValueError, match='existing|frozen|new edition'):
        creative_material.write_adaptation_material(value, manifest(), out,
            series_material=base, package_script=source, style_source=style)
    assert _snapshot_tree(out) == before


def test_film_reviews_the_same_normalized_adaptation_it_publishes(tmp_path, monkeypatch):
    import copy
    from types import SimpleNamespace
    from novel_manga.application.preparation.actions import reusable_creative_review
    from novel_manga.application.preparation.audit import creative_fingerprint
    from novel_manga.interactive import film, creation, authored, runtime, production
    from novel_manga.util import read_json

    base = series_base(tmp_path)
    task_path = tmp_path/'task.json'
    atomic_write_json(task_path, {'mode': 'adaptation', 'project': 'suspense',
        'brief': '改编', 'series_material': str(base), 'source_script': 'EP02-A.md',
        'video_lane': 'sd20-fast'})
    value = draft()
    for shot in value['chapter_script']['shots']:
        shot['visual_prompt'] = 'STALE_VISUAL'
        shot['motion_prompt'] = 'STALE_MOTION'
    reviews = []

    def create(path, directory, **kwargs):
        atomic_write_json(directory/'sandbox/input/manifest.json',
            {**manifest(), 'task': {'brief': '改编', 'series_material': str(base)}})
        package = directory/'sandbox/input/package/EP02-A.md'
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text('# EP02-A\n原稿')
        (directory/'sandbox/input/brief.md').write_text('改编')
        atomic_write_json(directory/'sandbox/output/episode.json', value)
        return {'status': 'creative_candidate', 'seconds_used': 10, 'allowance_seconds': 90}

    def review(episode, sources, **kwargs):
        reviews.append(copy.deepcopy(episode))
        answer = {'source_readable': True, 'source_problem': '', 'issues': []}
        return {'fingerprint': creative_fingerprint(episode, sources),
                'answer': answer, 'raw': answer, 'confirmation': None}

    def prepare(material_dir, book, **kwargs):
        material_value = read_json(material_dir/'material.json')
        episode = book/(book.name+'_1')
        atomic_write_json(episode/'chapter_script.json', material_value['script'])
        atomic_write_json(episode/'segments.json', material_value['segments'])
        (episode/'authored_source.md').write_text('原稿')
        atomic_write_json(episode/'clip_plan.json', {'clips': []})

    monkeypatch.setattr(creation, 'run', create)
    monkeypatch.setattr('novel_manga.application.preparation.audit.creative_audit', review)
    monkeypatch.setattr(authored, 'prepare_authored', prepare)
    monkeypatch.setattr('novel_manga.interactive.sd_first_frames.select_mode', lambda *a: None)
    monkeypatch.setattr(runtime, 'configure', lambda *a, **k: None)
    monkeypatch.setattr(runtime, 'runner_for',
        lambda *a, **k: SimpleNamespace(build_assets=lambda: None))
    monkeypatch.setattr(production, 'run_episode', lambda *a, **k: {'status': 'accepted'})

    run_dir = tmp_path/'run'
    result = film.run(tmp_path, task_path, run_dir, 'adapted', config={'model': 'offline'})
    assert result['status'] == 'accepted'
    produced_shots = read_json(run_dir/'material/material.json')['script']['shots']
    assert len(reviews) == 1
    assert reviews[0]['chapter_script']['shots'] == produced_shots
    assert [shot['location'] for shot in produced_shots] == ['池室·干平台', '日光公园外缘']
    episode_dir = tmp_path/'outputs/adapted/adapted_1'
    assert reusable_creative_review(episode_dir) is not None
