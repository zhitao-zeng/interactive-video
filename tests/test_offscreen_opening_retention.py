import pytest

from novel_manga.story.h3 import first_frame_prompt


@pytest.mark.parametrize('visible,extras,picture',[
    ([],[],None),(['A'],[],1),([],['actor.png'],2)])
def test_retention_matches_actual_offscreen_or_supplied_character(visible,extras,picture):
    clip={'references':[{'role':'character','name':'A','path':'actor.png'},
                        {'role':'location','name':'pool','path':'scene.png'}],
          'first_frame':{'visible_characters':visible,'extra_references':extras}}
    prompt=('subject_definitions:\n<Subject 1> is A in <Picture 1>.\n'
        '<Subject 2> is the setting in <Picture 2>.\n\nsummary:\nScene.\n\n'
        'retention_analysis:\n<Subject 1>: fully_preserved - the identity and outfit design of <Picture 1>.\n'
        '<Subject 2>: fully_preserved - the setting from <Picture 2>.\n\n'
        'detailed_description:\n<Subject 1> says <d>[Chinese] 看这儿。</d>\n\n'
        'overall_soundscape:\nQuiet water.\n\nnon_diegetic_music:\nNone.')
    result=first_frame_prompt(clip,prompt)
    retention=result.split('retention_analysis:\n')[1].split('\n\n')[0].splitlines()[0]
    if picture is None:
        assert 'source speaker identity only' in retention and '<Picture' not in retention
        assert 'source-identified offscreen speaker; voice only.' in result
    else:
        assert f'<Picture {picture}>' in retention and 'identity and outfit' in retention
    assert '<Subject 2>: fully_preserved - the setting from <Picture 1>.' in result
    assert '<d>[Chinese] 看这儿。</d>' in result
