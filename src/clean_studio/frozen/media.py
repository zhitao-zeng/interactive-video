from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess


def _run(runner, command):
    return runner(command, capture_output=True, text=True, check=False)


def media_duration(path: Path, *, ffprobe="ffprobe", runner=None) -> float:
    run = runner or subprocess.run
    result = _run(
        run,
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-400:]}")
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def probe_video(
    path: Path,
    *,
    expected_duration: int,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
    runner=None,
) -> dict:
    path = Path(path)
    run = runner or subprocess.run
    probe_command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    probe = _run(run, probe_command)
    payload = {}
    probe_error = None
    if probe.returncode == 0:
        try:
            payload = json.loads(probe.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            probe_error = f"invalid ffprobe JSON: {error}"
    else:
        probe_error = probe.stderr[-400:]

    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    duration = None
    try:
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        pass

    decode_command = [
        ffmpeg,
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
    ]
    decode = _run(run, decode_command)
    duration_ok = (
        duration is not None
        and max(3.7, float(expected_duration) - 0.35)
        <= duration
        <= float(expected_duration) + 0.75
    )
    fully_decoded = decode.returncode == 0
    passed = (
        probe.returncode == 0
        and probe_error is None
        and video is not None
        and audio is not None
        and duration_ok
        and fully_decoded
    )
    return {
        "passed": passed,
        "video_sha256": sha256(path.read_bytes()).hexdigest(),
        "duration_seconds": duration,
        "expected_duration_seconds": expected_duration,
        "duration_ok": duration_ok,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "native_audio": audio is not None,
        "probe_returncode": probe.returncode,
        "probe_error": probe_error,
        "decode_attempted": True,
        "fully_decoded": fully_decoded,
        "decode_returncode": decode.returncode,
        "decode_error": decode.stderr[-400:] if decode.returncode else None,
        "content_accepted": False,
        "final_audio_accepted": False,
        "evidence_scope": "technical_only",
    }


def extract_native_audio(
    video: Path,
    output: Path,
    *,
    ffmpeg="ffmpeg",
    runner=None,
) -> Path:
    run = runner or subprocess.run
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial.wav")
    partial.unlink(missing_ok=True)
    run(
        [
            ffmpeg,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(video),
            "-vn",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(partial),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    os.replace(partial, output)
    return output
