from __future__ import annotations

import base64
import io
from pathlib import Path
import re

from PIL import Image, ImageOps


PICTURE_TAG = re.compile(r"@图片(\d+)")
MIN_SECONDS = 4
MAX_SECONDS = 15
SHORT_EDGE = 768


def validate_duration(duration_seconds: int) -> int:
    if type(duration_seconds) is not int or not MIN_SECONDS <= duration_seconds <= MAX_SECONDS:
        raise ValueError("H3 duration_seconds must be an integer from 4 through 15")
    return duration_seconds


def raw_image_data_url(path: Path) -> str:
    path = Path(path)
    with Image.open(path) as source:
        mime = Image.MIME.get(source.format, "image/png")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def image_data_url(path: Path, max_side: int = 1280) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if max(image.size) > max_side:
            scale = max_side / max(image.size)
            image = image.resize(
                (round(image.width * scale), round(image.height * scale))
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def h3_payload(
    *,
    model: str,
    prompt: str,
    duration_seconds: int,
    first_frame: Path | None,
    references: tuple[Path, ...],
    seed: int,
) -> dict:
    seconds = validate_duration(duration_seconds)
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("H3 seed must be an integer in [0, 2**31)")
    paths = tuple(Path(path) for path in references)
    if first_frame is not None:
        first_frame = Path(first_frame)
        paths = (first_frame, *paths)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    conditions = [
        {
            "type": "image",
            "role": "reference",
            "uri": raw_image_data_url(path)
            if first_frame is not None and index == 0
            else image_data_url(path),
        }
        for index, path in enumerate(paths)
    ]
    if first_frame is not None:
        conditions.append(
            {
                "type": "image",
                "role": "keyframe",
                "frame_index": 0,
                "uri": raw_image_data_url(first_frame),
            }
        )
    return {
        "model": model,
        "task": "ref2va",
        "prompt": PICTURE_TAG.sub(r"<Picture \1>", prompt),
        "seconds": seconds,
        "conditions": conditions,
        "target": {
            "short_edge": SHORT_EDGE,
            "aspect_ratio": "9:16",
            "duration_seconds": float(seconds),
        },
        "num_outputs_per_prompt": 1,
        "num_inference_steps": 9,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
        "seed": seed,
    }
