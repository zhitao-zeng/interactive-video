from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
import subprocess

VISUAL_POLICY = "final-movie-v3-watchable-severity-approved-postproduction-caption-origin"
MAX_IMAGES = 8
FRAME_GROUP_SIZE = MAX_IMAGES - 2
WATCHABLE_CRITERIA = (
    "验收以观众能顺畅看懂剧情为准，允许正常剪辑、表演简化和不改变关键因果的合理改编。"
    "不影响人物识别或关键事件的服饰、配饰、发色、外貌细节、口型、站坐、手脚摆放和动作幅度差异，"
    "非关键动作省略、背景布置、无关背景人物增减和道具局部形状差异，只记提示，不要求重拍。"
    "旧分镜或提示词写得精确，不等于这些细节本身决定剧情；背景多一名旁观者不等于关键人物错误。"
    "服饰或物件承担关键身份、防护、交接或功能，人数决定事件，或动作差异改变生死、行动能力与攻击防御时，仍须修复。"
    "关键对白错漏或意思反转、明显错人或性别物种错误、人物分身、关键角色缺席、动作归属错误、关键事件缺失、"
    "剧情必需群体完全未出现、额外肢体、严重头身或肢体比例失真、持续严重抖动，仍必须修复。"
    "不能只凭与原文或旧请求有差别判失败，必须指出观众会误认的关键人物、误解的事件或可见的严重画面缺陷。"
    "无法判断或审核未完成不能记通过；一项轻微差异不能清除其他独立失败。"
)


def blocking_issues(issues):
    return [issue for issue in issues if issue.get("severity") != "minor"]


def _nonproblem_issue(issue):
    return bool(
        re.match(
            r"^(?:无问题(?:[：:，,；;。.!！\s]|$)|未发现(?:明确)?(?:违规|违反))",
            issue.get("problem", "").strip(),
        )
    )


def validate_group(answer, selected):
    if not isinstance(answer, Mapping):
        raise ValueError("final reviewer response must be an object")
    if (
        not isinstance(answer.get("passed"), bool)
        or not isinstance(answer.get("observations"), list)
        or not answer["observations"]
        or any(not isinstance(row, str) or not row.strip() for row in answer["observations"])
        or not isinstance(answer.get("issues"), list)
        or answer["passed"] != (not blocking_issues(answer["issues"]))
    ):
        raise ValueError("final reviewer omitted observations or returned conflicting passage")
    allowed = {row["clip_id"] for row in selected}
    for issue in answer["issues"]:
        if not isinstance(issue, Mapping):
            raise ValueError("final reviewer issue must be an object")
        if issue.get("severity") not in ("must_fix", "minor"):
            raise ValueError("final reviewer issue lacks a must_fix/minor severity grade")
        if (
            not issue.get("clip_ids")
            or not isinstance(issue["clip_ids"], list)
            or not set(issue["clip_ids"]) <= allowed
            or not issue.get("frame_numbers")
            or not isinstance(issue["frame_numbers"], list)
            or not issue.get("problem")
            or not issue.get("evidence")
            or not issue.get("source_basis")
        ):
            raise ValueError("final reviewer did not bind the issue to visible clips/source evidence")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (issue["problem"], issue["evidence"], issue["source_basis"])
        ):
            raise ValueError("final reviewer issue evidence must be nonempty text")
        if _nonproblem_issue(issue):
            raise ValueError(
                "final reviewer placed an explicit non-problem in issues; correct the judgment/list protocol"
            )
        if any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= len(selected)
            for number in issue["frame_numbers"]
        ):
            raise ValueError("final reviewer cited an unavailable frame")
        cited = {selected[number - 1]["clip_id"] for number in issue["frame_numbers"]}
        if not set(issue["clip_ids"]) <= cited:
            raise ValueError("final reviewer clip has no cited visible evidence")


def caption_cues(path):
    if not path or not Path(path).is_file():
        return []

    def seconds(text):
        hours, minutes, last = text.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(last)

    result = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        row = line.split(",", 9)
        if len(row) == 10:
            result.append(
                {
                    "start": seconds(row[1]),
                    "end": seconds(row[2]),
                    "text": row[9].replace("\\N", "\n"),
                }
            )
    return result


def movie_duration(movie, *, ffprobe="ffprobe", runner=None):
    run = runner or subprocess.run
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(movie),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if duration <= 0:
        raise ValueError("final movie has no positive duration")
    return duration


def extract_frames(
    movie,
    spans,
    *,
    duration,
    body_seconds,
    work_dir,
    ffmpeg="ffmpeg",
    runner=None,
):
    run = runner or subprocess.run
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    frames = []

    def frame(clip_id, second, label, shot_id=None):
        second = min(max(0.0, float(second)), duration - 0.08)
        path = directory / f"frame-{len(frames):03d}.jpg"
        run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-ss",
                str(second),
                "-i",
                str(movie),
                "-frames:v",
                "1",
                "-vf",
                "scale=432:768:force_original_aspect_ratio=decrease",
                str(path),
            ],
            check=True,
        )
        if not path.is_file():
            raise RuntimeError("FFmpeg did not create a final-review frame")
        frames.append(
            {
                "clip_id": clip_id,
                "shot_id": shot_id,
                "seconds": round(second, 3),
                "label": label,
                "path": str(path),
            }
        )

    for span in spans:
        start = float(span["start"])
        end = float(span["end"])
        clip_id = span["clip_id"]
        shot_id = span.get("shot_id")
        for label, second in (
            ("start", start + 0.15),
            ("middle", (start + end) / 2),
            ("end", end - 0.20),
        ):
            frame(clip_id, second, label, shot_id)
    frame(
        "end_card",
        float(body_seconds) + min(1.5, max(0.0, duration - float(body_seconds)) / 2),
        "host-owned fixed end card",
    )
    return frames


def group_schema(selected):
    ids = sorted({row["clip_id"] for row in selected})
    issue = {
        "type": "object",
        "properties": {
            "clip_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": ids},
            },
            "frame_numbers": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": len(selected),
                },
            },
            "severity": {"type": "string", "enum": ["must_fix", "minor"]},
            "problem": {"type": "string"},
            "evidence": {"type": "string"},
            "source_basis": {"type": "string"},
        },
        "required": [
            "clip_ids",
            "frame_numbers",
            "severity",
            "problem",
            "evidence",
            "source_basis",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "observations": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "issues": {"type": "array", "items": issue},
        },
        "required": ["passed", "observations", "issues"],
        "additionalProperties": False,
    }
