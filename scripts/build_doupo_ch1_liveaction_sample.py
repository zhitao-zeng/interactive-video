#!/usr/bin/env python3
"""Build the user-supplied 10-shot live-action chapter-one sample."""

from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.render import Renderer, TimedSubtitle
from novel_manga.util import atomic_write_json, media_duration


BASE_LOCK = """
原创真人化东方玄幻短剧关键帧，竖屏9:16，写实电影摄影；不复刻现有演员、动画角色脸或影视服装。
固定场景为家族日常使用的石质测验庭院：三面木石回廊、一面高墙，中央是两人高的黑色玄武岩测验石碑，
石碑下有低矮石台，十几名穿朴素练功服的少年族人自然站立。空间尺度真实，前中后景清楚。
傍晚自然光从院墙一侧进入，庭院中央较亮，回廊下保留真实阴影；材质哑光克制，不做游戏CG高反光。
摄影机像站在人群中的观察者，允许衣袖、肩膀和廊柱形成自然前景遮挡。
禁止仙侠宣传海报、英雄站姿、人物集体看镜头、对称大合照、仙雾、浮空山、漂浮火花、彩色雾气、
霓虹能量、无来源轮廓光、塑料皮肤、蜡质脸、过度浅景深、商业广告打光、乱码文字和水印。
测验结果区域保持干净，文字全部留给后期合成。
""".strip()


CHARACTER_PROMPTS = {
    "xiaoyan": """
单人角色定妆参考图，只出现一名15岁东亚少年萧炎。身形偏瘦，肩膀尚未完全长开，清秀但疲惫，
黑色眼睛，眼下轻微睡眠不足，黑发半束、其余自然落下。深灰黑旧练功袍，窄袖哑光布料，袖口正常磨损，
普通黑色布腰带；没有铠甲、复杂金属肩饰、发光纹路、英雄披风或古戒特写。克制沉默，不直视镜头。
全身三分之二侧身站姿，双手自然可见，真实年龄与自然人体比例，背景为简洁中性灰色摄影棚，无文字无拼图。
""",
    "xuner": """
单人角色定妆参考图，只出现一名14岁左右东亚少女萧薰儿。年龄感自然明确，不做成人化妆容；
黑色长发简单束起，低饱和紫色衣裙，仅少量黑金包边，衣着端正朴素。姿态稳定，表情平静，
不做商业古装模特姿势，不看镜头。全身三分之二侧身站姿，双手自然可见，真实人体比例，
背景为简洁中性灰色摄影棚，无文字无拼图。
""",
    "xiaomei": """
单人角色定妆参考图，只出现一名14岁左右东亚少女萧媚。暗酒红色简洁练功服，黑色长发自然束起，
神态自信但不是反派脸，保留一丝复杂和犹豫；不做成熟妆容、冷笑或商业摆拍，不看镜头。
全身三分之二侧身站姿，双手自然可见，真实年龄与人体比例，背景为简洁中性灰色摄影棚，无文字无拼图。
""",
    "courtyard": """
空场景空间母版，不出现人物。萧家内部石质测验庭院，三面木石回廊、一面朴素高墙，
中央一块约两人高的黑色玄武岩测验石碑，底部低矮石台；右后方回廊柱附近有可供人物退入的阴影区域，
庭院出口方向明确。傍晚自然光从一侧斜入，中央较亮、廊檐下真实偏暗。真实电影场景勘景照，
竖屏9:16，24毫米镜头，人眼高度，无人、无字、无雾、无金色法阵、无巨型宫殿。
""",
}


SHOT_ROWS = [
    {
        "duration": 5,
        "refs": ("xiaoyan",),
        "delta": "极近景。萧炎的手贴在黑色石碑上，最后一点微弱暖白光停住，手指缓慢离开石面。重点是手、石材和失去亮度的能量；石碑文字区保持干净。",
        "motion": "固定极近景，石碑低频震动后微光衰减，萧炎手指缓慢离开石面；动作克制，不重绘手指。",
        "lines": [("测验员", "萧炎，斗之力三段。评定，低级。")],
    },
    {
        "duration": 6,
        "refs": ("xiaoyan",),
        "delta": "摄影机藏在两名族人的肩膀之间，前景只露局部肩背和衣袖。萧炎在画面远处，被空间压缩和前景切割；几名族人交换眼神后克制低笑。",
        "motion": "轻微手持漂移，前景肩膀自然晃动；远处萧炎保持克制，族人先互看再低笑，不做夸张群演表情。",
        "lines": [("族人甲", "三年了，还是三段。"), ("族人乙", "他真以为自己还是以前那个天才？")],
    },
    {
        "duration": 7,
        "refs": ("xiaoyan",),
        "delta": "萧炎掌心真实近景。指甲压出红痕，只有极少量血迹；他察觉后立即松手，用旧练功袍袖口遮住掌心。不拍夸张痛苦表情。",
        "motion": "手指先收紧后立刻松开，旧袖口覆盖掌心；镜头稳定，仅有自然呼吸造成的轻微位移。",
        "lines": [("萧炎内心", "每一次聚起的斗气，都会从身体里消失。")],
    },
    {
        "duration": 9,
        "refs": ("xiaoyan",),
        "delta": "同一庭院数年前的记忆。约十一岁的萧炎，与定妆角色保持可辨认的同一身份但更年幼、衣服更干净；他站在同一石碑前，腹部附近形成克制半透明斗气旋涡，成年人从远处靠近祝贺。画面略暖但仍是真实自然光，无神圣光环。",
        "motion": "一次短促而克制的慢推；半透明斗气旋涡稳定形成，成年人自然靠近祝贺，年幼萧炎没有英雄摆拍。",
        "lines": [("萧炎旁白", "四岁开始修炼，十岁踏入九段，十一岁凝成斗气之旋。"), ("萧炎旁白", "那时候，他们都认识我。")],
    },
    {
        "duration": 7,
        "refs": ("xiaoyan",),
        "delta": "记忆中的斗气旋涡正在失稳并消散。年幼萧炎低头看双手；周围祝贺者的轮廓通过匹配剪辑逐渐与现实嘲笑人群重叠，表现荣耀骤然转为羞辱，人物脸仍稳定真实。",
        "motion": "斗气旋涡由稳定到断裂消散，年幼萧炎低头；过去鼓掌动作与现实低笑人群做克制匹配转场，不做能量爆炸。",
        "lines": [("萧炎旁白", "他们用一天叫我天才，也用了三年，把我叫成废物。")],
    },
    {
        "duration": 8,
        "refs": ("xiaomei", "xiaoyan"),
        "delta": "回到现实。萧媚在石碑前完成测验，暖白光明显强于萧炎；人群集中看她。萧炎位于右后方回廊阴影中，只见稳定侧脸和半身，空间关注方向明确离开他。",
        "motion": "萧媚把手放上石碑，暖白光稳定增强；人群转向她并响起祝贺，阴影中的萧炎只做一次很轻的呼吸。",
        "lines": [("测验员", "萧媚，七段，高级。")],
    },
    {
        "duration": 7,
        "refs": ("xiaomei", "xiaoyan"),
        "delta": "萧媚在人群中央接受祝贺。她越过几个人看见远处阴影中的萧炎，表情短暂停顿，像想走过去；随后被同伴自然挡住，她最终移开视线。不要冷笑，不要反派化。",
        "motion": "固定机位；萧媚先越过人群看向萧炎，停顿半秒，同伴靠近挡住视线，她缓慢转回人群。只有衣料摩擦、祝贺声和萧炎轻微呼吸，无明确对白。",
        "lines": [],
    },
    {
        "duration": 8,
        "refs": ("xuner",),
        "delta": "萧薰儿从回廊进入测验庭院。摄影机在人群正常肩线高度，人群自然让出一条狭窄道路；她平静走向石碑，不看镜头，不做慢动作裙摆或偶像式出场。",
        "motion": "肩线高度短跟拍；人群先安静再自然让路，萧薰儿以正常速度走到石碑前，衣裙运动符合真实惯性。",
        "lines": [("测验员", "下一个，萧薰儿。")],
    },
    {
        "duration": 10,
        "refs": ("xuner", "xiaoyan"),
        "delta": "萧薰儿刚完成测验，石碑亮起稳定暖白光。人群向她靠近，但她身体仍朝石碑，只转动头部和目光越过人群寻找阴影中的萧炎。不要在图中生成测验文字。",
        "motion": "石碑暖白光稳定亮起，人群向前半步；萧薰儿不看祝贺者，只缓慢转头寻找萧炎，动作克制自然。",
        "lines": [("测验员", "九段，高级。"), ("族人", "九段？"), ("族人", "她很快就能成为斗者了。")],
    },
    {
        "duration": 11,
        "refs": ("xuner", "xiaoyan"),
        "delta": "萧薰儿走到萧炎身边，两人处在同一片回廊阴影里，随后并肩向庭院出口离开。摄影机留在原地，透过人群缝隙观察两人背影逐渐缩小；二人不回头、不摆英雄姿态，其他人仍留在亮处。",
        "motion": "固定机位。萧薰儿逆着人群走入阴影，在萧炎身旁停住完成短对话；随后两人正常并肩走向出口，背影逐渐缩小，结尾只剩脚步声。",
        "lines": [("萧薰儿", "萧炎哥哥。"), ("萧炎", "现在跟着我，会被他们笑。"), ("萧薰儿", "那就让他们笑。你只是还没找到答案。"), ("萧炎内心", "我会找到它。")],
    },
]


MOTION_LOCK = """
保持首帧中人物面部身份、年龄、发型、服装结构、身体比例、石碑和庭院拓扑不变，只执行指定轻微动作。
动作符合真实惯性，呼吸、眨眼和衣料运动克制自然。禁止脸部重绘、五官漂移、头发突然变长、服装换色、
额外手指、背景人物复制、镜头无故环绕、快速推拉、夸张能量爆炸、慢动作摆拍、画面文字和水印。
""".strip()


def _settings(output_root: Path) -> Settings:
    return Settings.from_env(
        provider="phanrouter",
        output_root=output_root,
        admission_mode="preview",
    )


def _provider(settings: Settings) -> PhanRouterMediaProvider:
    return PhanRouterMediaProvider(settings)


def _image_result(path: Path) -> ImageResult:
    if not path.is_file():
        raise FileNotFoundError(path)
    return ImageResult(path=path)


def _valid_image(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _preserve_invalid_image(path: Path) -> None:
    if not path.exists() or _valid_image(path):
        return
    target = path.with_name(path.name + ".invalid-download")
    if not target.exists():
        path.rename(target)
    else:
        path.unlink()


def _valid_video(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return False
    try:
        streams = json.loads(probe.stdout).get("streams", [])
    except json.JSONDecodeError:
        return False
    return bool(streams and streams[0].get("width") and streams[0].get("height"))


def _preserve_invalid_video(path: Path) -> None:
    if not path.exists() or _valid_video(path):
        return
    target = path.with_name(path.name + ".invalid-download")
    if not target.exists():
        path.rename(target)
    else:
        path.unlink()


def _ledger(root: Path, stage: str, rows: list[dict]) -> None:
    atomic_write_json(
        root / "task_ledger.json",
        {
            "production": root.name,
            "stage": stage,
            "declared_task_counts": {
                "reference_images": 4,
                "keyframes": 10,
                "video_clips": 10,
            },
            "tasks": rows,
        },
    )


def _run_parallel(items, worker, max_workers: int):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            results[item] = future.result()
    return results


def generate_references(root: Path, settings: Settings, workers: int) -> dict[str, Path]:
    reference_root = root / "references"
    reference_root.mkdir(parents=True, exist_ok=True)
    paths = {name: reference_root / f"{name}.jpeg" for name in CHARACTER_PROMPTS}

    def build(name: str) -> Path:
        output = paths[name]
        if _valid_image(output):
            return output
        _preserve_invalid_image(output)
        prompt = BASE_LOCK + "\n\n" + CHARACTER_PROMPTS[name]
        _provider(settings).create_image(prompt, output)
        return output

    _run_parallel(list(paths), build, workers)
    _ledger(
        root,
        "references_ready",
        [
            {"task": f"reference:{name}", "purpose": "identity_or_location_lock", "path": str(path)}
            for name, path in paths.items()
        ],
    )
    return paths


def generate_keyframes(
    root: Path,
    settings: Settings,
    references: dict[str, Path],
    workers: int,
) -> list[Path]:
    keyframe_root = root / "keyframes"
    keyframe_root.mkdir(parents=True, exist_ok=True)
    indexes = list(range(1, len(SHOT_ROWS) + 1))

    def build(index: int) -> Path:
        row = SHOT_ROWS[index - 1]
        output = keyframe_root / f"shot_{index:02d}.jpeg"
        if _valid_image(output):
            return output
        _preserve_invalid_image(output)
        ref_names = row["refs"]
        prompt = (
            BASE_LOCK
            + "\n\n参考图规则：图1只锁定庭院空间、石碑、回廊、自然光和材质；后续图片只锁定对应人物的脸、年龄、发型、服装和体型。"
            + "不得复制参考图构图，不得增加未列出角色。\n\n"
            + f"SHOT {index:02d}\n"
            + row["delta"]
        )
        _provider(settings).create_image(
            prompt,
            output,
            reference=references["courtyard"],
            additional_references=tuple(references[name] for name in ref_names),
        )
        return output

    generated = _run_parallel(indexes, build, workers)
    paths = [generated[index] for index in indexes]
    _ledger(
        root,
        "keyframes_ready",
        [
            {"task": f"reference:{name}", "purpose": "identity_or_location_lock", "path": str(path)}
            for name, path in references.items()
        ]
        + [
            {"task": f"keyframe:shot_{index:02d}", "purpose": SHOT_ROWS[index - 1]["delta"], "path": str(path)}
            for index, path in enumerate(paths, start=1)
        ],
    )
    return paths


def make_contact_sheet(paths: list[Path], output: Path, columns: int = 5) -> Path:
    width, height = 216, 384
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * (height + 28)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path).convert("RGB") as source:
            frame = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS)
        x = (index % columns) * width
        y = (index // columns) * (height + 28)
        sheet.paste(frame, (x, y))
        draw.text((x + 8, y + height + 6), f"SHOT {index + 1:02d}", fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "JPEG", quality=92)
    return output


def generate_videos(
    root: Path,
    settings: Settings,
    keyframes: list[Path],
    workers: int,
) -> list[Path]:
    clip_root = root / "clips"
    clip_root.mkdir(parents=True, exist_ok=True)
    indexes = list(range(1, len(SHOT_ROWS) + 1))

    def build(index: int) -> Path:
        row = SHOT_ROWS[index - 1]
        output = clip_root / f"shot_{index:02d}.mp4"
        if _valid_video(output):
            return output
        _preserve_invalid_video(output)
        lines = row["lines"]
        audio = (
            "本镜无明确对白，只保留真实庭院环境声、衣料摩擦和克制人群声。"
            if not lines
            else "严格按角色顺序说出以下中文台词，不增字、不改字、不交换说话人；其余可听人物保持闭嘴：\n"
            + "\n".join(f"{speaker}：{text}" for speaker, text in lines)
        )
        prompt = (
            MOTION_LOCK
            + "\n\n镜头动作："
            + row["motion"]
            + "\n\n声音要求："
            + audio
        )
        return _provider(settings).create_video(
            prompt,
            _image_result(keyframes[index - 1]),
            output,
            float(row["duration"]),
        )

    generated = _run_parallel(indexes, build, workers)
    paths = [generated[index] for index in indexes]
    prior = json.loads((root / "task_ledger.json").read_text(encoding="utf-8"))["tasks"]
    _ledger(
        root,
        "clips_ready",
        prior
        + [
            {"task": f"video:shot_{index:02d}", "purpose": SHOT_ROWS[index - 1]["motion"], "path": str(path)}
            for index, path in enumerate(paths, start=1)
        ],
    )
    return paths


def _normalize_clip(source: Path, output: Path, settings: Settings) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-vf",
            (
                f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
                f"crop={settings.width}:{settings.height},fps={settings.fps},format=yuv420p"
            ),
            "-af", "aresample=48000,aformat=channel_layouts=stereo",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(output),
        ],
        check=True,
    )
    return output


def assemble(
    root: Path,
    settings: Settings,
    keyframes: list[Path],
    clips: list[Path],
) -> Path:
    renderer = Renderer(settings)
    normalized_root = root / "normalized_clips"
    normalized = [
        _normalize_clip(path, normalized_root / path.name, settings)
        for path in clips
    ]
    durations = [media_duration(path) for path in normalized]
    joined = root / "joined.mp4"
    offsets = renderer._join_with_crossfade(
        normalized,
        durations,
        joined,
        crossfade_seconds=0.15,
    )
    subtitles = []
    for index, (row, start, duration) in enumerate(zip(SHOT_ROWS, offsets, durations, strict=True), start=1):
        lines = row["lines"]
        if not lines:
            continue
        usable_start = start + 0.35
        usable_end = start + max(0.8, duration - 0.35)
        span = max(0.5, (usable_end - usable_start) / len(lines))
        for line_index, (speaker, text) in enumerate(lines):
            line_start = usable_start + line_index * span
            line_end = min(usable_end, line_start + span - 0.05)
            subtitles.append(TimedSubtitle(line_start, line_end, text, speaker))
    ass = renderer.write_ass_pages(root / "subtitles.ass", subtitles)
    final = root / f"{root.name}.mp4"
    escaped_ass = str(ass.resolve()).replace("'", r"\'").replace(":", r"\:")
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(joined),
            "-vf", f"ass='{escaped_ass}'",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", str(settings.fps), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(final),
        ],
        check=True,
    )
    renderer.make_cover(
        keyframes[0],
        root / f"{root.name}_cover.jpeg",
        novel_title="斗破苍穹",
        art_title="陨落的天才",
        episode_label="第1集",
    )
    renderer.make_card(
        keyframes[-1],
        root / f"{root.name}_ending.jpeg",
        "斗破苍穹",
        "未完待续",
        "他的斗气为什么会消失？",
    )
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_name,width,height,r_frame_rate",
            "-of", "json", str(final),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    atomic_write_json(root / "technical_qc.json", json.loads(probe.stdout))
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/doupo-ch1-liveaction-v1"),
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
    settings = _settings(root.parent)

    references = generate_references(root, settings, args.workers)
    if args.stage == "references":
        return 0
    keyframes = generate_keyframes(root, settings, references, args.workers)
    make_contact_sheet(keyframes, root / "keyframes_contact_sheet.jpeg")
    if args.stage == "keyframes":
        return 0
    clips = generate_videos(root, settings, keyframes, args.workers)
    if args.stage == "videos":
        return 0
    final = assemble(root, settings, keyframes, clips)
    print(json.dumps({"video": str(final)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
