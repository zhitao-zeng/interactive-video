import copy

import pytest

from novel_manga.interactive.creative_contract import validate
from novel_manga.util import atomic_write_json
from support.creative_candidates import episode


def case(tmp_path):
    (tmp_path/'source.txt').write_text('入口有一块牌，旅客看完后停步。')
    manifest = {'mode':'adaptation','task':{},'source_documents':['source.txt'],
                'target_seconds':[180,300]}
    draft = episode()
    draft['chapter_script']['shots'][0]['opening_frame'] = {
        'image_prompt':'The traveler stands at the entrance.',
        'visible_characters':['旅客'],'reference_subjects':['旅客','入口']}
    return draft, manifest


@pytest.mark.parametrize('subject', [
    'Image #1: 旅客-身份基准（面部与身份）',
    'Image #2: 旅客-综合视觉卡（服装、体态、装备）',
    'Image #3/Image #4: travel-live style only',
    'input/refs/character_001.jpeg',
])
def test_image_labels_and_paths_return_precise_creator_feedback(tmp_path, subject):
    draft, manifest = case(tmp_path)
    draft['chapter_script']['shots'][0]['opening_frame']['reference_subjects'] = [subject]
    before = copy.deepcopy(draft)
    issues = validate(draft, manifest, tmp_path)
    assert len(issues) == 1
    assert 'shot 1' in issues[0] and 'opening_frame.reference_subjects' in issues[0]
    assert subject in issues[0] and "['入口', '旅客']" in issues[0]
    assert draft == before


@pytest.mark.parametrize('subjects', [None, [], ['旅客','入口']])
def test_omitted_empty_and_exact_subjects_remain_valid(tmp_path, subjects):
    draft, manifest = case(tmp_path)
    opening = draft['chapter_script']['shots'][0]['opening_frame']
    if subjects is None:
        opening.pop('reference_subjects')
    else:
        opening['reference_subjects'] = subjects
    assert validate(draft, manifest, tmp_path) == []


def test_scene_subject_uses_the_existing_adaptation_canonical_name(tmp_path):
    draft, manifest = case(tmp_path)
    draft['spatial_plan']['scenes'][0]['location'] = '登记入口'
    opening = draft['chapter_script']['shots'][0]['opening_frame']
    opening['reference_subjects'] = ['登记入口']
    assert validate(draft, manifest, tmp_path) == []
    opening['reference_subjects'] = ['入口']
    issues = validate(draft, manifest, tmp_path)
    assert len(issues) == 1 and "['旅客', '登记入口']" in issues[0]
    assert draft['chapter_script']['shots'][0]['location'] == '入口'


def test_empty_shot_needs_no_actor_reference(tmp_path):
    draft, manifest = case(tmp_path)
    shot = draft['chapter_script']['shots'][0]
    shot.update(characters=[], in_frame=[])
    shot['opening_frame'].update(visible_characters=[],reference_subjects=['入口'])
    assert validate(draft, manifest, tmp_path) == []


def test_malformed_scene_list_keeps_the_existing_structural_feedback(tmp_path):
    draft, manifest = case(tmp_path)
    draft['spatial_plan']['scenes'] = 3
    assert any('spatial_plan.scenes must be a list' in issue
               for issue in validate(draft, manifest, tmp_path))


def test_supplied_prop_owner_is_legal_only_when_bound_to_this_shot(tmp_path):
    draft, manifest = case(tmp_path)
    base = tmp_path/'series';base.mkdir()
    image = base/'provided-object.png';image.write_bytes(b'provided original image')
    material = {'bible':{'characters':[{'name':'旅客'},{'name':'别处人物'}],
        'props':[{'name':'铜灯','appearance':'an old copper lamp'}], 'locations':[]},
        'reference_images':[{'source':str(image),'path':'series_assets/props/prop_001/turnaround.jpeg'}]}
    atomic_write_json(base/'material.json', material)
    manifest['task']['series_material'] = str(base)
    shot = draft['chapter_script']['shots'][0]
    shot.update(characters=[],in_frame=[],props=['铜灯'])
    shot['opening_frame'].update(visible_characters=[],reference_subjects=['铜灯'])
    assert validate(draft, manifest, tmp_path) == []
    shot['props'] = []
    assert '铜灯' in validate(draft, manifest, tmp_path)[0]
    shot['props'] = ['铜灯']
    material['reference_images'] = []
    atomic_write_json(base/'material.json', material)
    assert '铜灯' in validate(draft, manifest, tmp_path)[0]
    shot['opening_frame']['reference_subjects'] = ['别处人物']
    assert '别处人物' in validate(draft, manifest, tmp_path)[0]
