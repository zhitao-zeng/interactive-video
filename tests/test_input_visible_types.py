import pytest
from novel_manga.application.agents.episode.input_candidate import validate_visible_types
from novel_manga.application.agents.episode.arguments import RepairArgumentError


def test_known_object_and_location_report_exact_source_field():
    shots={29:{'in_frame':['fox procession','foxfire','cabin'],
               'scene_objects':['foxfire'],'location':'cabin'}}
    bible={'characters':[{'name':'fox procession'}],'locations':['cabin：wooden'],'props':[]}
    with pytest.raises(RepairArgumentError) as error:
        validate_visible_types(shots,{29},bible)
    assert 'shot 29.in_frame: foxfire is a known scene_objects' in str(error.value)
    assert 'shot 29.in_frame: cabin is a known location' in str(error.value)


def test_existing_or_source_candidate_character_is_not_rejected_as_prop():
    shots={1:{'in_frame':['existing','new source actor'],'scene_objects':['existing','new source actor']}}
    validate_visible_types(shots,{1},{'characters':[{'name':'existing'}]},['new source actor'])


def test_unregistered_actor_without_known_noncast_type_is_left_to_source_review():
    validate_visible_types({1:{'in_frame':['new animal']}},{1},{'characters':[]})
