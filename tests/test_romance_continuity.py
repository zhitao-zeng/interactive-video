import copy

from novel_manga.story.compilation import ClipCompiler
from novel_manga.interactive.continuity import repair_room_shots
from novel_manga.models.directing import HandoffState
from tests.test_scene_compilation import options, stage
from novel_manga.models.bible import Character, StoryBible


def test_english_word_boundaries_survive_compiled_picture_action_and_end():
    shot = stage()
    shot.update(visual_prompt='A woman stands by the wooden door.',
                motion_prompt='She opens the door and parks her suitcase.',
                end_state='The suitcase stays by the north wall.', turns=[])
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='真人', palette='青',
                       characters=[Character(name='甲', appearance='黑发', wardrobe='青衣')],
                       locations=['庭院'], style_fingerprint='test')
    prompt = ClipCompiler(options(15)).compile_prompt(
        {'shots': [shot], 'request_seconds': 5}, bible, ['甲'], [], '庭院')
    assert 'A woman stands by the wooden door' in prompt
    assert 'She opens the door and parks her suitcase' in prompt
    assert 'The suitcase stays by the north wall' in prompt


def test_room_occupants_and_parked_luggage_survive_a_single_person_cut():
    original = {'shots': [{**stage(), 'in_frame': ['甲'], 'props': [], 'source_quote': '甲回应乙。'}]}
    before = copy.deepcopy(original)
    room = HandoffState(physical={'甲': '坐在西北椅', '乙': '坐在东北椅',
                                  '嘉宾行李箱': '两只箱子停在北墙，未被移动'})
    edits = {1: {'start': room, 'end': room, 'event': '甲看向乙回应',
                 'visible': ['甲'], 'props': ['嘉宾行李箱']}}
    repaired = repair_room_shots(original, edits)
    shot = repaired['shots'][0]
    assert shot['characters'] == ['甲', '乙']
    assert shot['in_frame'] == ['甲']
    assert '乙坐在东北椅' in shot['visual_prompt']
    assert '两只箱子停在北墙' in shot['end_state']
    assert shot['source_quote'] == before['shots'][0]['source_quote']
    assert shot['turns'] == before['shots'][0]['turns']
    assert original == before


def test_arrival_does_not_start_with_the_newcomer_already_seated():
    original = {'shots': [{**stage(), 'in_frame': ['甲'], 'props': [], 'source_quote': '甲坐下。'}]}
    start = HandoffState(physical={'甲': '站在西南椅旁', '乙': '坐在东北椅'})
    end = HandoffState(physical={'甲': '坐在西南椅', '乙': '坐在东北椅'})
    shot = repair_room_shots(original, {1: {'start': start, 'end': end,
        'event': '甲坐进西南椅', 'visible': ['甲', '乙'], 'props': []}})['shots'][0]
    assert '甲站在西南椅旁' in shot['visual_prompt']
    assert '甲坐在西南椅' not in shot['visual_prompt']
    assert '甲坐在西南椅' in shot['end_state']
