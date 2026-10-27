from __future__ import annotations

import re

from PIL import Image


def frame_rate(value: str) -> float:
    numerator, denominator = str(value).split("/")
    return float(numerator) / max(1.0, float(denominator))


def jpeg_check(path, *, width=1080, height=1920) -> dict:
    try:
        with Image.open(path) as image:
            image.load()
            passed = image.format == "JPEG" and image.size == (width, height)
            detail = f"{image.format} {image.width}x{image.height}"
    except Exception as error:
        passed, detail = False, str(error)
    return {"passed": passed, "detail": detail}


def black_intervals(stderr: str) -> list[dict[str, float]]:
    return [
        {"start": float(start), "end": float(end), "seconds": float(seconds)}
        for start, end, seconds in re.findall(
            r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",
            stderr or "",
        )
    ]


def stream_checks(probe, decode, *, width=1080, height=1920, fps=25) -> dict:
    streams = probe.get("streams", [])
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    video = video or {}
    audio = audio or {}
    try:
        actual_fps = frame_rate(video.get("avg_frame_rate", "0/1"))
    except (TypeError, ValueError, ZeroDivisionError):
        actual_fps = 0.0
    try:
        duration = float(video.get("duration") or probe["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        duration = None
    try:
        frames = int(video["nb_frames"])
    except (KeyError, TypeError, ValueError):
        frames = None
    return {
        "streams": {
            "passed": bool(video) and bool(audio),
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
        },
        "resolution": {
            "passed": video.get("width") == width and video.get("height") == height,
            "width": video.get("width"),
            "height": video.get("height"),
        },
        "fps": {"passed": abs(actual_fps - fps) < 0.001, "value": actual_fps},
        "video_codec": {
            "passed": video.get("codec_name") == "h264",
            "value": video.get("codec_name"),
        },
        "audio_codec": {
            "passed": audio.get("codec_name") == "aac",
            "value": audio.get("codec_name"),
        },
        "duration": {"passed": duration is not None, "seconds": duration},
        "frames": {"passed": frames is not None, "count": frames},
        "decode": {
            "passed": decode.returncode == 0,
            "fully_decoded": decode.returncode == 0,
            "returncode": decode.returncode,
            "error": (decode.stderr or "")[-600:] if decode.returncode else None,
        },
    }
