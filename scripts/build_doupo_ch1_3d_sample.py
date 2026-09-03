#!/usr/bin/env python3
"""Build the user-supplied 10-shot chapter-one sample in locked 3D donghua style."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.providers.phanrouter import PhanRouterMediaProvider


BASE_SCRIPT = Path(__file__).with_name("build_doupo_ch1_liveaction_sample.py")
spec = importlib.util.spec_from_file_location("doupo_sample_base", BASE_SCRIPT)
assert spec is not None and spec.loader is not None
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


THREED_BASE_LOCK = """
原创高品质中国3D国漫短剧正片截图，竖屏9:16，明显非真人摄影。
固定美术：简化雕塑式东方面部结构，自然青少年人体比例，哑光无毛孔皮肤，束状设计发丝，
克制Toon-PBR布料、木石和金属，电影化但清楚的傍晚体积光；同一集写实程度完全一致。
固定场景：家族日常使用的石质测验庭院，三面木石回廊、一面高墙，中央两人高黑色玄武岩测验石碑，
底部低矮石台，右后回廊有阴影，出口方向明确；十几名朴素练功服少年族人自然站立。
摄影机像藏在人群中的观察者，允许衣袖、肩膀、廊柱形成前景遮挡；庭院中央较亮，回廊阴影可信。
禁止真人照片、真人毛孔、半写实厚涂、塑料玩偶、欧美卡通、二维线稿、平面赛璐璐、游戏角色创建界面、
仙侠宣传海报、英雄站姿、集体看镜头、巨型宫殿、浮空山、仙雾、霓虹能量、金色法阵、过量粒子、乱码和水印。
石碑结果区域保持干净，文字全部后期合成。
""".strip()


THREED_CHARACTER_PROMPTS = {
    "xiaoyan": """
3D国漫角色定妆卡，只出现15岁萧炎一人。东方面部雕塑结构，偏瘦，肩膀尚未完全长开，清秀疲惫，
黑色眼睛，黑发半束其余自然落下；深灰黑旧练功袍、窄袖、哑光布料、袖口正常磨损、普通黑布腰带。
没有铠甲、金属肩饰、发光纹路、英雄披风或醒目戒指。克制沉默，不看镜头。全身三分之二侧身，双手自然可见，
单色中性背景，无文字、无拼图；严格沿用风格母版的3D面部、皮肤、发丝和材质语言。
""",
    "xuner": """
3D国漫角色定妆卡，只出现14岁萧薰儿一人。年龄感自然，不成人化；东方面部雕塑结构，黑色长发简单高束，
低饱和紫色衣裙、极少黑金包边，端正朴素。姿态稳定安静，不做广告摆拍，不看镜头。
全身三分之二侧身，双手自然可见，单色中性背景，无文字、无拼图；严格沿用风格母版的3D渲染。
""",
    "xiaomei": """
3D国漫角色定妆卡，只出现14岁萧媚一人。东方面部雕塑结构，黑色长发自然束起，暗酒红色简洁交领练功服、
暗酒红下装、黑靴；自信但不是反派脸，保留一丝复杂与犹豫，不冷笑、不看镜头。
全身三分之二侧身，双手自然可见，单色中性背景，无文字、无拼图；严格沿用风格母版的3D渲染。
""",
    "courtyard": """
3D国漫空场景空间母版，不出现人物。萧家石质测验庭院，三面木石回廊、一面朴素高墙，中央两人高黑色玄武岩石碑，
底部低矮石台，右后回廊阴影与庭院出口清楚。傍晚自然侧光，中央较亮、廊檐下偏暗。
24毫米镜头、人眼高度、竖屏9:16；严格沿用风格母版的Toon-PBR木石材质与光线，无字、无雾、无法阵。
""",
}


THREED_MOTION_LOCK = """
高品质中国3D国漫动画。保持首帧人物3D脸型、年龄、发型、服装结构、自然比例、石碑和庭院拓扑不变，
只执行指定轻微动作；呼吸、眨眼、束状发丝和衣料运动克制自然。禁止转真人、脸部重绘、五官漂移、塑料皮肤、
头发突然变长、服装换色、额外手指、背景人物复制、镜头无故环绕、快速推拉、能量爆炸、慢动作摆拍、文字和水印。
""".strip()


def settings_for(root: Path) -> Settings:
    return Settings.from_env(
        provider="phanrouter",
        output_root=root.parent,
        admission_mode="preview",
    )


def generate_references(
    root: Path,
    settings: Settings,
    style_anchor: Path,
    workers: int,
) -> dict[str, Path]:
    reference_root = root / "references"
    reference_root.mkdir(parents=True, exist_ok=True)
    paths = {
        name: reference_root / f"{name}.jpeg"
        for name in THREED_CHARACTER_PROMPTS
    }

    def build(name: str) -> Path:
        output = paths[name]
        if base._valid_image(output):
            return output
        base._preserve_invalid_image(output)
        PhanRouterMediaProvider(settings).create_image(
            THREED_BASE_LOCK + "\n\n" + THREED_CHARACTER_PROMPTS[name],
            output,
            reference=style_anchor,
        )
        return output

    base._run_parallel(list(paths), build, workers)
    base._ledger(
        root,
        "references_ready",
        [
            {
                "task": f"reference:{name}",
                "purpose": "3d_identity_or_location_lock",
                "path": str(path),
            }
            for name, path in paths.items()
        ],
    )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/doupo-ch1-3d-v1"),
    )
    parser.add_argument(
        "--stage",
        choices=("references", "keyframes", "videos", "assemble", "all"),
        default="all",
    )
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    style_anchor = root / "style_anchor.png"
    if not style_anchor.is_file():
        raise FileNotFoundError(style_anchor)
    settings = settings_for(root)

    base.BASE_LOCK = THREED_BASE_LOCK
    base.CHARACTER_PROMPTS = THREED_CHARACTER_PROMPTS
    base.MOTION_LOCK = THREED_MOTION_LOCK

    references = generate_references(
        root, settings, style_anchor, args.workers
    )
    if args.stage == "references":
        return 0
    keyframes = base.generate_keyframes(
        root, settings, references, args.workers
    )
    base.make_contact_sheet(
        keyframes, root / "keyframes_contact_sheet.jpeg"
    )
    if args.stage == "keyframes":
        return 0
    clips = base.generate_videos(root, settings, keyframes, args.workers)
    if args.stage == "videos":
        return 0
    final = base.assemble(root, settings, keyframes, clips)
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
