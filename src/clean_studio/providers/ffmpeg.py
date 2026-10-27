from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


class FFmpeg:
    def __init__(
        self,
        *,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        runner=None,
    ) -> None:
        self.ffmpeg = str(ffmpeg)
        self.ffprobe = str(ffprobe)
        self.runner = runner or subprocess.run

    def execute(self, binary: str, arguments, *, check=True):
        command = [binary, *map(str, arguments)]
        result = self.runner(command, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"{Path(binary).name} failed ({result.returncode}): "
                f"{(result.stderr or '')[-600:]}"
            )
        return result

    def run(self, arguments, *, check=True):
        return self.execute(self.ffmpeg, arguments, check=check)

    def probe(self, path: str | Path) -> dict:
        result = self.execute(
            self.ffprobe,
            [
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
        )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("ffprobe returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("ffprobe returned a non-object")
        return payload

    def decode(self, path: str | Path):
        return self.run(
            [
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            check=False,
        )

    def blackdetect(self, path: str | Path, *, start: float, seconds: float):
        return self.run(
            [
                "-nostdin",
                "-v",
                "info",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(path),
                "-t",
                f"{seconds:.6f}",
                "-vf",
                "blackdetect=d=0.5:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
        )

    def extract_cover(self, source: str | Path, output: str | Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.stem + ".partial" + output.suffix)
        partial.unlink(missing_ok=True)
        self.run(
            [
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920",
                "-q:v",
                "2",
                str(partial),
            ]
        )
        if not partial.is_file():
            raise RuntimeError("FFmpeg did not create the JPEG cover")
        os.replace(partial, output)
        return output
