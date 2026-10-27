from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path

from clean_studio.frozen.final_audio import AUDIO_POLICY
from clean_studio.frozen.final_review import (
    FRAME_GROUP_SIZE,
    VISUAL_POLICY,
    blocking_issues,
    validate_group,
)


class ReviewContractError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewDecision:
    accepted: bool
    verdict: str
    issues: tuple[Mapping[str, object], ...]
    blocking: tuple[Mapping[str, object], ...]


def _expected(export, name, alias=None, *, required=True):
    if isinstance(export, Mapping):
        value = export.get(name)
        if value is None and alias is not None:
            value = export.get(alias)
    else:
        value = getattr(export, name, None)
        if value is None and alias is not None:
            value = getattr(export, alias, None)
    if required and value is None:
        raise ReviewContractError(f"current export bundle lacks {name}")
    return value


def _common_review(evidence, export, kind, policy):
    if not isinstance(evidence, Mapping):
        raise ReviewContractError(f"{kind} requires structured evidence")
    if evidence.get("policy") != policy:
        raise ReviewContractError(f"{kind} evidence uses an old or unknown review policy")
    if not isinstance(evidence.get("review_id"), str) or not evidence["review_id"]:
        raise ReviewContractError(f"{kind} evidence lacks a review identity")
    if evidence.get("complete") is not True or evidence.get("errors"):
        raise ReviewContractError(f"{kind} review is incomplete or has service errors")
    bundle_hash = _expected(export, "bundle_hash")
    video_hash = _expected(export, "video_sha256", "sha256")
    if evidence.get("bundle_hash") != bundle_hash:
        raise ReviewContractError(f"{kind} is not bound to the current export bundle")
    for field in ("content_hash", "input_hash"):
        if evidence.get(field) != bundle_hash:
            raise ReviewContractError(f"{kind} {field} is not the current bundle hash")
    for field in ("export_sha256", "video_sha256"):
        if evidence.get(field) != video_hash:
            raise ReviewContractError(f"{kind} {field} is not the current video hash")
    if evidence.get("verdict") not in {"accepted", "rejected"}:
        raise ReviewContractError(f"{kind} has no valid content verdict")
    if not isinstance(evidence.get("passed"), bool):
        raise ReviewContractError(f"{kind} lacks an explicit passage result")
    return bundle_hash, video_hash


def flatten_visual_issues(groups):
    if not isinstance(groups, list):
        raise ReviewContractError("final visual groups must be an array")
    flattened = []
    for group in groups:
        if not isinstance(group, Mapping) or not isinstance(group.get("issues"), list):
            raise ReviewContractError("final visual group issues must be an array")
        for issue in group["issues"]:
            if not isinstance(issue, Mapping):
                raise ReviewContractError("final visual issues must be structured objects")
            flattened.append(issue)
    return flattened


def validate_visual_review(evidence, export):
    _common_review(evidence, export, "final_visual_review", VISUAL_POLICY)
    if evidence.get("cover_sha256") != _expected(export, "cover_sha256"):
        raise ReviewContractError("final visual review is not bound to the current cover SHA-256")
    frames = evidence.get("frames")
    groups = evidence.get("groups")
    issues = evidence.get("issues")
    if (
        not isinstance(frames, list)
        or not frames
        or not isinstance(groups, list)
        or len(groups) != (len(frames) + FRAME_GROUP_SIZE - 1) // FRAME_GROUP_SIZE
        or not isinstance(issues, list)
    ):
        raise ReviewContractError("formal visual evidence lacks complete extracted-frame groups")
    for index, answer in enumerate(groups):
        selected = frames[index * FRAME_GROUP_SIZE:(index + 1) * FRAME_GROUP_SIZE]
        try:
            validate_group(answer, selected)
        except (KeyError, TypeError, ValueError) as error:
            raise ReviewContractError(f"formal visual group is malformed: {error}") from error
    aggregated = flatten_visual_issues(groups)
    if issues != aggregated:
        raise ReviewContractError("top-level visual issues must exactly match group aggregation")
    if "findings" in evidence and evidence["findings"] != issues:
        raise ReviewContractError("visual findings must exactly match aggregated issues")
    blocking = tuple(blocking_issues(aggregated))
    verdict = "rejected" if blocking else "accepted"
    passed = not blocking
    if evidence["verdict"] != verdict or evidence["passed"] is not passed:
        raise ReviewContractError("formal visual verdict conflicts with aggregated blocking issues")
    return ReviewDecision(
        accepted=not blocking,
        verdict=verdict,
        issues=tuple(aggregated),
        blocking=blocking,
    )


def _expected_coverage(duration):
    rows = []
    start = 0.0
    while start < duration:
        end = min(start + 30.0, duration)
        rows.append((round(start, 3), round(end, 3)))
        if end >= duration:
            break
        start += 28.0
    return rows


def validate_audio_review(evidence, export):
    _, video_hash = _common_review(evidence, export, "final_audio_review", AUDIO_POLICY)
    expected_movie = _expected(export, "movie", "path", required=False)
    if expected_movie is not None and Path(str(evidence.get("movie") or "")).resolve() != Path(expected_movie).resolve():
        raise ReviewContractError("final audio review is not for the current export movie")
    expected_duration = _expected(export, "expected_duration_seconds")
    duration = evidence.get("coverage_seconds")
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or duration <= 0
        or abs(duration - expected_duration) > 0.04
    ):
        raise ReviewContractError("final audio coverage is not the complete current export duration")
    decode = evidence.get("decode")
    if (
        not isinstance(decode, Mapping)
        or decode.get("fully_decoded") is not True
        or decode.get("sample_rate") != 16000
        or type(decode.get("samples")) is not int
        or decode["samples"] < round(duration * 16000) - 1
        or decode.get("export_sha256") != video_hash
    ):
        raise ReviewContractError("final audio was not fully decoded from the current video hash")
    coverage = evidence.get("coverage")
    windows = evidence.get("windows")
    if not isinstance(coverage, list) or not isinstance(windows, list) or len(coverage) != len(windows):
        raise ReviewContractError("final audio window coverage is missing or malformed")
    expected = _expected_coverage(duration)
    if len(coverage) != len(expected):
        raise ReviewContractError("final audio window coverage or 2-second overlap is incomplete")
    for index, (row, window, (start, end)) in enumerate(zip(coverage, windows, expected)):
        if (
            not isinstance(row, Mapping)
            or not isinstance(window, Mapping)
            or type(row.get("start")) not in (int, float)
            or type(row.get("end")) not in (int, float)
            or abs(row["start"] - start) > 0.02
            or abs(row["end"] - end) > 0.02
            or row["end"] - row["start"] > 30.001
            or window.get("start") != row.get("start")
            or window.get("end") != row.get("end")
            or not isinstance(window.get("response"), str)
            or not window["response"].strip()
        ):
            raise ReviewContractError(f"final audio window {index} is missing, stale, or malformed")
    issues = evidence.get("issues")
    if not isinstance(issues, list):
        raise ReviewContractError("final audio issues are malformed")
    for issue in issues:
        if (
            not isinstance(issue, Mapping)
            or type(issue.get("start")) not in (int, float)
            or type(issue.get("end")) not in (int, float)
            or issue["start"] < 0
            or issue["end"] <= issue["start"]
            or issue["end"] > duration + 0.02
            or any(
                not isinstance(issue.get(field), str) or not issue[field].strip()
                for field in ("problem", "evidence", "source_basis")
            )
            or not isinstance(issue.get("clip_ids"), list)
        ):
            raise ReviewContractError("final audio text judgment contains a malformed issue")
    if "findings" in evidence and evidence["findings"] != issues:
        raise ReviewContractError("audio findings must exactly match complete-judgment issues")
    verdict = "rejected" if issues else "accepted"
    passed = not issues
    if evidence["verdict"] != verdict or evidence["passed"] is not passed:
        raise ReviewContractError("final audio verdict conflicts with its complete text judgment")
    return ReviewDecision(
        accepted=not issues,
        verdict=verdict,
        issues=tuple(issues),
        blocking=tuple(issues),
    )


def validate_final_review(kind, evidence, export):
    if kind == "final_visual_review":
        return validate_visual_review(evidence, export)
    if kind == "final_audio_review":
        return validate_audio_review(evidence, export)
    raise ValueError(f"not a final export review kind: {kind}")
