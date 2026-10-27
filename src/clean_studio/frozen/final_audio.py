from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import struct
import subprocess

AUDIO_POLICY = "eureka-final-movie-audio-review-v2-material-defects"
WINDOW_SECONDS = 30
OVERLAP_SECONDS = 2
SAMPLE_RATE = 16000


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(_canonical(value), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_state(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _file_hash(path),
    }


def _model_state(path):
    path = Path(path).resolve()
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            stat = item.stat()
            files.append([str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns])
    return {"path": str(path), "files": files}


def runtime_identity(runtime):
    keys = ("python", "audio_source_root", "audio_model_dir")
    if not isinstance(runtime, Mapping) or any(
        not isinstance(runtime.get(key), str) or not runtime[key] for key in keys
    ):
        raise ValueError("Eureka runtime requires python, audio_source_root and audio_model_dir")
    model_dir = Path(runtime["audio_model_dir"])
    source_root = Path(runtime["audio_source_root"])
    if not model_dir.is_dir() or not source_root.is_dir():
        raise ValueError("configured Eureka model or source package directory is missing")
    return {
        "python": runtime["python"],
        "audio_source_root": str(source_root.resolve()),
        "model": _model_state(model_dir),
    }


def _write_float_wav(path, samples):
    count = len(samples) // 4
    data_size = count * 4
    with Path(path).open("wb") as stream:
        stream.write(b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE")
        stream.write(b"fmt ")
        stream.write(
            struct.pack(
                "<IHHIIHH",
                16,
                3,
                1,
                SAMPLE_RATE,
                SAMPLE_RATE * 4,
                4,
                32,
            )
        )
        stream.write(b"data" + struct.pack("<I", data_size))
        stream.write(samples)


def _duration(movie, *, ffprobe, runner):
    result = runner(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(movie)],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if duration <= 0:
        raise ValueError("final movie has no positive duration")
    return duration


def _decode(movie, directory, identity, duration, *, ffmpeg, runner):
    pcm = directory / "decoded-f32le.pcm"
    stamp = directory / "decode.json"
    if _read_json(stamp, {}).get("identity") != identity or not pcm.is_file():
        pcm.unlink(missing_ok=True)
        environment = {
            **os.environ,
            "CUDA_VISIBLE_DEVICES": "",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        runner(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(movie),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "f32le",
                str(pcm),
            ],
            check=True,
            env=environment,
        )
        _write_json(
            stamp,
            {
                "identity": identity,
                "sample_rate": SAMPLE_RATE,
                "samples": pcm.stat().st_size // 4,
                "duration": duration,
            },
        )
    samples = pcm.stat().st_size // 4
    expected = round(duration * SAMPLE_RATE)
    if samples < expected - 1:
        raise ValueError("decoded audio is shorter than the final movie duration")
    return pcm, samples


def _windows(pcm, samples, duration, directory):
    max_samples = WINDOW_SECONDS * SAMPLE_RATE
    stride = (WINDOW_SECONDS - OVERLAP_SECONDS) * SAMPLE_RATE
    rows = []
    start_sample = 0
    with Path(pcm).open("rb") as source:
        while start_sample < samples:
            end_sample = min(start_sample + max_samples, samples)
            start = start_sample / SAMPLE_RATE
            end = min(end_sample / SAMPLE_RATE, duration)
            path = directory / f"window-{len(rows):04d}.wav"
            source.seek(start_sample * 4)
            _write_float_wav(path, source.read((end_sample - start_sample) * 4))
            rows.append(
                {
                    "index": len(rows),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "path": str(path),
                    "output": str(directory / f"window-{len(rows):04d}.json"),
                }
            )
            if end_sample == samples:
                break
            start_sample += stride
    if not rows or rows[0]["start"] != 0 or rows[-1]["end"] < duration - 0.02:
        raise ValueError("audio windows do not cover the final movie")
    for left, right in zip(rows, rows[1:]):
        if (
            right["start"] > left["end"]
            or right["end"] - right["start"] > WINDOW_SECONDS + 0.001
        ):
            raise ValueError("audio window coverage has a gap or exceeds 30 seconds")
    return rows


def _run_worker(runtime, job_path, worker_path, runner):
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "OMP_NUM_THREADS": "8",
        "MKL_NUM_THREADS": "8",
    }
    return runner(
        [runtime["python"], str(worker_path), str(job_path)],
        env=environment,
        timeout=3600,
        capture_output=True,
        text=True,
    )


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def judge_audio(client, facts, windows):
    prompt = (
        "按本集原稿、镜头与成片时间线、配乐/声景意图和下列整片音频窗口原始Eureka响应，"
        "判断有证据确认、实质影响对白理解、剧情必要声音信息或基本收听体验的音频问题。"
        "issues只列这样的阻断问题；每项必须有可核对的电影全局秒数、明确证据与原稿依据。"
        "sfx或音效计划未列出某种声音，不等于禁止该自然声音。轻微环境声或拟音的缺失、强弱和音色偏差，"
        "以及近似金属声、门声的差异，未造成上述实质影响时不进issues，可在summary说明，不为补齐音效清单要求重拍。"
        "声音模型的声源标签不是具体画面动作的证明；把声音称为门响、机械声或金属声，不能据此断言门已开关或发生了额外动作。"
        "窗口start/end是覆盖范围；响应中估计的时间点可能不可靠，不能仅靠该时间猜测声画错位或动作先后。"
        "只有与原稿和实际时间线能明确对应的证据才支持具体动作或时间硬伤；证据不足不编造冲突。"
        "保留可证的对白丢失、对白不可懂、音轨损坏、明显破音或持续严重刺耳干扰等真实问题；"
        "剧情必须依赖的声音线索明确缺失并妨碍理解时也应报告，不能以轻微拟音为由放过。"
        "没有配乐、原稿要求安静、静音比例偏高或声音模型未提及某事，都不能单独算错；"
        "不得推断声音真实/AI来源，不要把模型自然语言直接当成通过或失败。"
        "无上述可证实质问题时issues为空。该模型分析不等于人工听审。\n"
        + json.dumps({"episode_facts": facts, "audio_windows": windows}, ensure_ascii=False)
    )
    issue = _object(
        {
            "start": {"type": "number"},
            "end": {"type": "number"},
            "problem": {"type": "string"},
            "evidence": {"type": "string"},
            "source_basis": {"type": "string"},
        }
    )
    schema = _object(
        {
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": issue},
        }
    )
    return client.ask_json(
        [{"type": "text", "text": prompt}],
        schema,
        name="final_movie_audio_review",
        max_tokens=3000,
        timeout=240,
        retry_truncated=False,
    )


def _validate_judgment(judgment, facts, duration):
    if (
        not isinstance(judgment, dict)
        or not isinstance(judgment.get("summary"), str)
        or not isinstance(judgment.get("issues"), list)
    ):
        raise ValueError("final audio text judge returned a malformed response")
    timeline = facts.get("timeline") or []
    issues = []
    for issue in judgment["issues"]:
        if (
            not isinstance(issue, dict)
            or type(issue.get("start")) not in (int, float)
            or type(issue.get("end")) not in (int, float)
            or not math.isfinite(issue["start"])
            or not math.isfinite(issue["end"])
            or any(
                not isinstance(issue.get(field), str) or not issue[field].strip()
                for field in ("problem", "evidence", "source_basis")
            )
            or issue["start"] < 0
            or issue["end"] <= issue["start"]
            or issue["end"] > duration + 0.02
        ):
            raise ValueError("final audio text judge returned an unbound or malformed issue")
        overlaps = []
        for row in timeline:
            start, end = row.get("start"), row.get("end")
            if (
                type(start) in (int, float)
                and type(end) in (int, float)
                and start < issue["end"]
                and end > issue["start"]
            ):
                overlaps.append(row.get("clip_id") or row.get("shot_id"))
        clip_ids = list(dict.fromkeys(value for value in overlaps if value))
        issues.append(
            {
                **issue,
                "start": round(issue["start"], 3),
                "end": round(issue["end"], 3),
                "clip_ids": clip_ids,
                **({} if clip_ids else {"scope": "final_movie_global"}),
            }
        )
    return issues


def run_audio_review(
    movie,
    facts,
    judge,
    *,
    runtime,
    work_dir,
    worker_path=None,
    worker_runner=None,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
):
    movie = Path(movie).resolve()
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    run = runner or subprocess.run
    worker = worker_runner or subprocess.run
    worker_path = Path(worker_path or Path(__file__).with_name("eureka_worker.py"))
    result = {
        "movie": str(movie),
        "complete": False,
        "passed": False,
        "issues": [],
        "errors": [],
        "coverage": [],
        "model": "Eureka-Audio-Instruct",
        "policy": AUDIO_POLICY,
        "scope": "CPU model-assisted full final-movie audio review; not human listening",
    }
    try:
        if not movie.is_file():
            raise ValueError("final movie is unavailable")
        keys = ("python", "audio_source_root", "audio_model_dir")
        if not isinstance(runtime, Mapping) or any(
            not isinstance(runtime.get(key), str) or not runtime[key] for key in keys
        ):
            raise ValueError("Eureka runtime requires python, audio_source_root and audio_model_dir")
        runtime = {key: runtime[key] for key in keys}
        model_dir = Path(runtime["audio_model_dir"])
        source_root = Path(runtime["audio_source_root"])
        if not model_dir.is_dir() or not source_root.is_dir():
            raise ValueError("configured Eureka model or source package directory is missing")
        identity = {
            "movie": _file_state(movie),
            "model": _model_state(model_dir),
            "audio_source_root": str(source_root.resolve()),
        }
        result["identity"] = identity
        window_identity = directory / "window-identity.json"
        if _read_json(window_identity, {}).get("identity") != identity:
            for pattern in ("window-[0-9][0-9][0-9][0-9].json", "window-[0-9][0-9][0-9][0-9].wav"):
                for stale in directory.glob(pattern):
                    stale.unlink()
            _write_json(window_identity, {"identity": identity})
        duration = _duration(movie, ffprobe=ffprobe, runner=run)
        pcm, sample_count = _decode(
            movie,
            directory,
            identity,
            duration,
            ffmpeg=ffmpeg,
            runner=run,
        )
        result["decode"] = {
            "fully_decoded": True,
            "samples": sample_count,
            "sample_rate": SAMPLE_RATE,
            "duration_seconds": duration,
        }
        rows = _windows(pcm, sample_count, duration, directory)
        result["coverage"] = [
            {"start": row["start"], "end": row["end"]} for row in rows
        ]
        jobs = []
        for row in rows:
            if not Path(row["output"]).is_file():
                jobs.append(
                    {
                        "id": f"final-audio-{row['index']:04d}",
                        "audio": str(Path(row["path"]).resolve()),
                        "output": row["output"],
                        "question": (
                            "只依据实际音频，简要报告语言和一段简短可辨原话（或说明听不清）、"
                            "说话人数与音色变化、环境声和音乐、背景声是否遮挡对白，以及突兀剪辑、爆音、失真、断音或异常静音。"
                            "有问题时说明片段内的大致秒数；没有音乐或静音也可能是有意安排。不要推测剧情、声源身份或是否AI。"
                        ),
                        "source_windows": [{"start": row["start"], "end": row["end"]}],
                        "scope": "final delivered movie window; not human listening",
                        "max_new_tokens": 160,
                    }
                )
        if jobs:
            job_path = directory / "worker-job.json"
            _write_json(job_path, {"action": "audio", "runtime": runtime, "jobs": jobs})
            done = _run_worker(runtime, job_path, worker_path, worker)
            if done.returncode:
                _write_json(
                    directory / "worker-error.json",
                    {
                        "returncode": done.returncode,
                        "stdout": done.stdout,
                        "stderr": done.stderr,
                    },
                )
                raise RuntimeError(
                    "Eureka audio worker failed; raw completed windows and worker-error.json retained"
                )
        parsed = []
        for row in rows:
            output = Path(row["output"])
            if not output.is_file():
                raise RuntimeError(f"Eureka window {row['index']} is missing")
            item = _read_json(output, {})
            if not isinstance(item, dict) or not isinstance(item.get("response"), str) or not item["response"].strip():
                raise RuntimeError(f"Eureka window {row['index']} response is missing or malformed")
            parsed.append(
                {"start": row["start"], "end": row["end"], "response": item["response"]}
            )
        judgment = judge_audio(judge, facts, parsed) if hasattr(judge, "ask_json") else judge(facts, parsed)
        result["raw_judgment"] = judgment
        issues = _validate_judgment(judgment, facts, duration)
        result.update(
            complete=True,
            passed=not issues,
            issues=issues,
            summary=judgment["summary"],
            windows=parsed,
            coverage_seconds=duration,
        )
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
        result["complete"] = False
        result["passed"] = False
    _write_json(directory / "last-result.json", result)
    return result
