from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1920
FPS = 25
END_CARD_SECONDS = 3


def _font(path: Path, text: str, draw: ImageDraw.ImageDraw, size: int, width: int):
    current = size
    while current > 28:
        font = ImageFont.truetype(str(path), current)
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if bounds[2] - bounds[0] <= width:
            return font
        current -= 2
    font = ImageFont.truetype(str(path), 28)
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    if bounds[2] - bounds[0] > width:
        raise ValueError("fixed end-card text does not fit without rewriting or truncation")
    return font


def _centered(draw, text, y, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    x = (WIDTH - (bounds[2] - bounds[0])) / 2
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=2,
        stroke_fill=(12, 16, 28),
    )


def make_end_card(episode, output: str | Path, *, font_path: str | Path) -> Path:
    font_path = Path(font_path)
    if not font_path.is_file():
        raise ValueError("subtitle/card font is missing")
    if not episode.fixed_question or not episode.fixed_options:
        raise ValueError("fixed end-card question and options are required")
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / (HEIGHT - 1)
        color = (
            round(28 + 22 * ratio),
            round(40 + 8 * ratio),
            round(72 + 20 * ratio),
        )
        draw.line((0, y, WIDTH, y), fill=color)
    draw.rectangle((54, 52, WIDTH - 54, HEIGHT - 52), outline=(196, 160, 88), width=3)
    _centered(
        draw,
        episode.title,
        210,
        _font(font_path, episode.title, draw, 72, 900),
        (246, 222, 165),
    )
    draw.line((190, 410, 890, 410), fill=(220, 184, 105), width=3)
    _centered(
        draw,
        episode.fixed_question,
        535,
        _font(font_path, episode.fixed_question, draw, 68, 920),
        (255, 248, 228),
    )
    y = 900
    for option in episode.fixed_options:
        label_title = f"{option.label}. {option.title}"
        _centered(
            draw,
            label_title,
            y,
            _font(font_path, label_title, draw, 62, 900),
            (246, 211, 126),
        )
        y += 220
    if y > 1650:
        raise ValueError("fixed end-card options exceed the host layout")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "JPEG", quality=96, subsampling=0)
    return output


@dataclass(frozen=True)
class TimedSubtitle:
    start: float
    end: float
    text: str
    role: str
    subtitle_source: str
    shot_id: str


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def write_ass(path: str | Path, subtitles, *, width=WIDTH, height=HEIGHT) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,WenQuanYi Micro Hei,58,&H00FFFFFF,&H000000FF,&H00111111,&H00000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for item in subtitles:
        text = (
            item.text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("{", "\\{")
            .replace("}", "\\}")
            .replace("\n", r"\N")
        )
        role = item.role.replace(",", "/")
        rows.append(
            f"Dialogue: 0,{_ass_time(item.start)},{_ass_time(item.end)},"
            f"Default,{role},0,0,0,,{text}"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def render_command(
    videos,
    durations,
    end_card,
    music,
    subtitles,
    output,
    *,
    preset="veryfast",
) -> list[str]:
    if not videos or len(videos) != len(durations):
        raise ValueError("render requires matching nonempty videos and durations")
    command = ["-nostdin", "-y", "-v", "error", "-xerror"]
    for video in videos:
        command += ["-i", str(video)]
    end_index = len(videos)
    command += ["-loop", "1", "-framerate", str(FPS), "-i", str(end_card)]
    for row in music:
        command += ["-i", str(row["audio_path"])]
    filters = []
    concat_inputs = []
    for index, seconds in enumerate(durations):
        filters.append(
            f"[{index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={seconds:.6f},"
            f"trim=duration={seconds:.6f},setpts=PTS-STARTPTS,format=yuv420p[v{index}]"
        )
        filters.append(
            f"[{index}:a]aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad=whole_dur={seconds:.6f},atrim=duration={seconds:.6f},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filters.append(
        f"[{end_index}:v]scale={WIDTH}:{HEIGHT},fps={FPS},"
        f"trim=duration={END_CARD_SECONDS:.6f},setpts=PTS-STARTPTS,"
        f"format=yuv420p[v{end_index}]"
    )
    filters.append(
        f"anullsrc=r=48000:cl=stereo,atrim=duration={END_CARD_SECONDS:.6f},"
        f"asetpts=PTS-STARTPTS[a{end_index}]"
    )
    concat_inputs.append(f"[v{end_index}][a{end_index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(durations) + 1}:v=1:a=1[joinedv][joineda]"
    )
    escaped_ass = (
        str(Path(subtitles).resolve())
        .replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
    )
    filters.append(f"[joinedv]ass='{escaped_ass}'[vout]")
    music_labels = []
    for index, row in enumerate(music):
        input_index = end_index + 1 + index
        length = row["end_seconds"] - row["start_seconds"]
        chain = (
            f"[{input_index}:a]atrim=start={row['source_start_seconds']:.6f}:"
            f"duration={row['source_seconds']:.6f},asetpts=PTS-STARTPTS,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        )
        if row["loop"]:
            chain += f",aloop=loop=-1:size={max(1, math.ceil(row['source_seconds'] * 48000))}"
        chain += (
            f",atrim=duration={length:.6f},asetpts=PTS-STARTPTS,"
            f"volume={row['gain_db']:g}dB"
        )
        if row["fade_in_seconds"]:
            chain += f",afade=t=in:st=0:d={row['fade_in_seconds']:.6f}"
        if row["fade_out_seconds"]:
            chain += (
                f",afade=t=out:st={length - row['fade_out_seconds']:.6f}:"
                f"d={row['fade_out_seconds']:.6f}"
            )
        chain += f",adelay={round(row['start_seconds'] * 1000)}:all=1[cue{index}]"
        filters.append(chain)
        music_labels.append(f"[cue{index}]")
    total = sum(durations) + END_CARD_SECONDS
    if music_labels:
        filters.append(
            "[joineda]"
            + "".join(music_labels)
            + f"amix=inputs={len(music_labels) + 1}:duration=first:"
            "dropout_transition=0:normalize=0[mixed]"
        )
        filters.append(f"[mixed]atrim=duration={total:.6f},asetpts=PTS-STARTPTS[aout]")
    else:
        filters.append(f"[joineda]atrim=duration={total:.6f},asetpts=PTS-STARTPTS[aout]")
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        "20",
        "-r",
        str(FPS),
        "-vsync",
        "cfr",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        f"{total:.6f}",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command
