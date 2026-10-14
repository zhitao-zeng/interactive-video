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
