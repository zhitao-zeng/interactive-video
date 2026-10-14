from types import SimpleNamespace
from novel_manga.media.asset_repair import repair_privacy_cards, repair_rejected_reference


def test_photo_profile_does_not_redraw_reference_as_animation(tmp_path):
    ctx=SimpleNamespace(asset_style=SimpleNamespace(render_family='photo'),novel_dir=tmp_path,
                        state=SimpleNamespace(ok_assets=set()))
    clip={'clip_id':'clip_01','references':[{'role':'character','asset_id':'character_001','path':'card.jpeg'}]}
    (tmp_path/'card.jpeg').write_bytes(b'original reference')
    assert repair_privacy_cards(ctx,clip)==[]
    assert repair_rejected_reference(ctx,clip,0)==[]
    assert (tmp_path/'card.jpeg').read_bytes()==b'original reference'


def test_repair_compilation_does_not_reintroduce_animation_target_for_live_action():
    from novel_manga.story.compilation import ClipCompiler
    from novel_manga.application.packing.context import compiler_options
    from novel_manga.models.bible import StoryBible
    shot={'index':1,'characters':[],'in_frame':[],'extras':[],
          'visual_prompt':'手机放在桌上','motion_prompt':'手机保持原位','end_state':'手机仍在桌上',
          'shot_scale':'特写','turns':[]}
    bible=StoryBible(novel_title='t',genre='generic',visual_style='现代真人电影摄影',palette='灰',style_fingerprint='photo')
    prompt=ClipCompiler(compiler_options()).compile_prompt({'shots':[shot],'request_seconds':4},bible,[],[],'房间')
    assert '中国国漫短剧片段' not in prompt
    assert '现代真人电影摄影' in prompt
