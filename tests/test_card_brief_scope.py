from novel_manga.media.asset_prompts import character_prompt
from novel_manga.models.bible import StoryBible


def test_group_and_carried_design_not_contradicted_when_archetype_is_empty():
    bible=StoryBible(novel_title='test',genre='generic',visual_style='二维卡通',palette='warm',style_fingerprint='test')
    appearance='A procession of nine-tailed foxes carrying foxfire'
    prompt=character_prompt(bible,'fox procession',appearance,'fur')
    assert appearance in prompt
    assert '只画一个人物' not in prompt and '不拿食物' not in prompt
    assert '群体' in prompt and '固定持物' in prompt
    assert '自然人体比例' not in prompt


def test_untyped_single_actor_keeps_single_subject_default():
    bible=StoryBible(novel_title='test',genre='generic',visual_style='二维卡通',palette='warm',style_fingerprint='test')
    prompt=character_prompt(bible,'traveller','young woman','red jacket')
    assert '未声明群体时只呈现一个主体' in prompt
    assert 'red jacket' in prompt
