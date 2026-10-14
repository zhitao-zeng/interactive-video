from dataclasses import replace
from novel_manga.application.packing.assets import build_references
from novel_manga.application.packing.context import compiler_options
from novel_manga.models.bible import StoryBible
from novel_manga.story.h3 import voice_lines


def test_unseen_narrator_keeps_voice_without_inventing_visible_character():
    bible=StoryBible(novel_title='探险',genre='现代',visual_style='实拍',palette='自然',style_fingerprint='v1')
    options=replace(compiler_options(),voices={'林野':'series_assets/voices/林野.wav'})
    refs,_,_=build_references([],None,bible,{},speakers=('林野',),settings=options)
    assert refs==[{'tag':'@音频1','role':'voice','name':'林野','path':'series_assets/voices/林野.wav'}]
    lines=voice_lines({'references':refs},{},{'林野':'(S1)'})
    assert len(lines)==1 and '<Audio 1>' in lines[0] and '(S1)' in lines[0]
    assert '<Subject' not in lines[0]
