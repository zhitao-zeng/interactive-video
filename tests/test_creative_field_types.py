"""Author field-shape mistakes must return feedback before material compilation."""
import copy

import pytest

from novel_manga.interactive.creative_contract import validate
from novel_manga.interactive.creative_material import normalized_episode
from support.creative_candidates import episode


def candidate(tmp_path):
    (tmp_path/'source.md').write_text('入口有一块牌，旅客看完后停步。')
    manifest={'mode':'adaptation', 'task':{}, 'profile':{}, 'target_seconds':[180,300],
              'source_documents':['source.md']}
    return episode(), manifest


def test_b10_camera_objects_and_scene_prose_are_reported_together_without_rewriting(tmp_path):
    draft,manifest=candidate(tmp_path)
    template=draft['chapter_script']['shots'][0]
    shots=[{**copy.deepcopy(template), 'index':i, 'duration_seconds':8,
            'camera':{'start':'At the pier.', 'trigger':'When the hand stops.',
                      'actor_path':'Toward the water.', 'camera_path':'Hold then pan.'},
            'scene_objects':'The backpack drifts in shallow water.'} for i in range(1,31)]
    draft['chapter_script']['shots']=shots
    draft['clips']=[{'shot_indexes':[i], 'request_seconds':8} for i in range(1,31)]
    draft['spatial_plan']['scenes'][0]['shot_indexes']=list(range(1,31))
    before=copy.deepcopy(draft)
    issues=validate(draft,manifest,tmp_path)
    assert len(issues)==60
    for number in range(1,31):
        assert any(f'shot {number} ' in text and 'camera' in text and 'dict' in text and 'string' in text for text in issues)
        assert any(f'shot {number} ' in text and 'scene_objects' in text and 'str' in text and 'list' in text for text in issues)
    assert draft==before


@pytest.mark.parametrize('field', ['scene_id','location','purpose','camera','start_state','event','end_state'])
def test_required_production_text_rejects_objects_with_field_and_type(tmp_path,field):
    draft,manifest=candidate(tmp_path)
    shot=draft['chapter_script']['shots'][0]
    shot[field]={'description':'A complete description is still not a string.'}
    shot['opening_frame']={'image_prompt':'A traveller stands at the entry.',
        'visible_characters':['旅客'], 'reference_subjects':['旅客','入口']}
    issues=validate(draft,manifest,tmp_path)
    assert any('shot 1 ' in text and field in text and 'dict' in text and 'string' in text for text in issues)


@pytest.mark.parametrize('field', ['props','scene_objects'])
@pytest.mark.parametrize('value', ['a whole action sentence', [{'name':'背包'}], {'name':'背包'}])
def test_object_name_fields_reject_prose_or_objects_before_reference_lookup(tmp_path,field,value):
    draft,manifest=candidate(tmp_path);shot=draft['chapter_script']['shots'][0]
    shot[field]=value
    shot['opening_frame']={'image_prompt':'The traveller waits.',
        'visible_characters':['旅客'], 'reference_subjects':['旅客']}
    issues=validate(draft,manifest,tmp_path)
    assert any('shot 1 ' in text and field in text and 'list' in text and type(value).__name__ in text for text in issues)


@pytest.mark.parametrize('field', ['light','scene_time','shot_scale'])
def test_optional_compiler_text_rejects_objects(tmp_path,field):
    draft,manifest=candidate(tmp_path);draft['chapter_script']['shots'][0][field]={'value':'text'}
    assert any(field in text and 'dict' in text and 'string' in text for text in validate(draft,manifest,tmp_path))


@pytest.mark.parametrize('value', ['quiet', ['quiet'], {'music_cue':{'id':'quiet'}}])
def test_audio_plan_remains_a_mapping_with_text_music_cue(tmp_path,value):
    draft,manifest=candidate(tmp_path);draft['chapter_script']['shots'][0]['audio_plan']=value
    assert any('audio_plan' in text and 'shot 1 ' in text for text in validate(draft,manifest,tmp_path))


@pytest.mark.parametrize('clear', [None, []])
def test_empty_props_and_existing_text_array_forms_remain_valid(tmp_path,clear):
    draft,manifest=candidate(tmp_path);shot=draft['chapter_script']['shots'][0]
    shot.update(props=clear,scene_objects=clear,purpose=['Observe the sign.','Keep the route visible.'],
                sfx=['wind','wood creak'],extras='A distant passerby.',light='',scene_time='',
                audio_plan=None)
    before=copy.deepcopy(draft)
    assert validate(draft,manifest,tmp_path)==[]
    assert draft==before
    normalized=normalized_episode(draft,manifest)
    assert normalized['chapter_script']['shots'][0]['purpose']=='Observe the sign.；Keep the route visible.'
    shot.update(purpose='Observe the sign.',sfx='wind',extras=['A distant passerby.'],
                audio_plan={'music_cue':None})
    assert validate(draft,manifest,tmp_path)==[]


def test_empty_frame_does_not_require_an_actor_or_standing_position(tmp_path):
    draft,manifest=candidate(tmp_path)
    for shot in draft['chapter_script']['shots']:
        shot.update(characters=[],in_frame=[],turns=[],props=[],scene_objects=[],
            opening_frame={'image_prompt':'An empty wooden pier.', 'visible_characters':[], 'reference_subjects':[]})
    draft['spatial_plan']['scenes'][0]['placed']=[]
    assert validate(draft,manifest,tmp_path)==[]
