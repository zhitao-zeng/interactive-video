"""Scene-card wording follows the location, not the whole book.

Regression basis: backrooms55 EP02-A, 2026-10-22 — the poolrooms-live pack put
"白砖池室/蓝绿池水/白色方柱/池底" into every location card prompt (scene_style,
scene_direction, art_direction and the bible palette), so three outdoor park-edge cards
came back as indoor pool halls and ten clips bound to them were drawn as the pool.
"""
import json
from pathlib import Path

from novel_manga.media.asset_prompts import location_prompt
from novel_manga.media.asset_style import AssetStyle, scene_in_scope
from novel_manga.models.bible import StoryBible

ROOT = Path(__file__).resolve().parents[1]
POOL_WORDS = ('白砖', '池水', '池底', '白色方柱', '白瓷砖', '泳池')


def _bible():
    return StoryBible(novel_title='t', genre='g', visual_style='真人手机探索Vlog，巨大白砖池室、蓝绿色清水',
                      palette='白色瓷砖、蓝绿色池水、温润天光', style_fingerprint='f',
                      characters=[], locations=['x'], props=[])


def _pool_style():
    pack = json.loads((ROOT / 'configs/styles/poolrooms-live.json').read_text())
    return AssetStyle.for_genre({}, style=pack)


def _prompt(style, location):
    in_scope = scene_in_scope(style, location)
    direction = ((style.scene_direction or style.render_direction) if in_scope
                 else (style.scene_neutral_direction or style.render_direction))
    return location_prompt(_bible(), location, family=style.render_family, direction=direction,
                           fingerprint=False, scene_style=style.scene_style, in_scope=in_scope,
                           neutral_style=style.scene_neutral_style)


def test_pool_location_still_gets_pool_wording():
    style = _pool_style()
    prompt = _prompt(style, '池室大柱与亮口：白色主柱东侧')
    assert scene_in_scope(style, '池室大柱与亮口：白色主柱东侧')
    assert '白砖池室' in prompt and '蓝绿' in prompt


def test_outdoor_location_carries_no_pool_vocabulary():
    style = _pool_style()
    for place in ('废弃园区外缘·步道：宽约两人并行的连续路面',
                  '废弃园区外缘·旧摊位与长椅外侧：关闭的旧摊位口',
                  '废弃园区外缘·旧拱口：剥落涂层的旧园区入口'):
        assert not scene_in_scope(style, place)
        prompt = _prompt(style, place)
        # the pack's own stage vocabulary must be gone; the location text stays
        assert place in prompt
        stage_text = prompt.replace(place, '')
        for word in POOL_WORDS:
            assert word not in stage_text, word
        assert '池室' not in stage_text


def test_pack_without_scope_applies_everywhere_as_before():
    style = AssetStyle.for_genre({}, style={'scene_style': '海边小屋真实室内摄影', 'scene_direction': '木纹'})
    assert style.scene_scope == ()
    assert scene_in_scope(style, '随便什么地点') is True
    prompt = _prompt(style, '街道')
    assert '海边小屋真实室内摄影' in prompt


def test_scope_matches_the_name_part_before_the_description():
    style = AssetStyle.for_genre({}, style={'scene_scope': ['池室']})
    # a description that mentions the word does not pull an outdoor place into scope
    assert not scene_in_scope(style, '废弃园区外缘·步道：远处能看到池室的方向')
    assert scene_in_scope(style, '池室·暗廊出口：出口外')
