from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .util import atomic_write_json


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
TASK_SUFFIX = ".task.json"
GROUP_PATTERN = re.compile(r"visual_\d{3}")
ATTEMPT_PATTERN = re.compile(r"attempt_\d+(?:\.[^/]+)?")


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _purpose(path: Path, root: Path) -> str:
    relative = _relative(path, root)
    if relative.startswith("series_assets/characters/"):
        return "series_character_asset"
    if relative.startswith("series_assets/locations/"):
        return "series_location_asset"
    if "/work/reference_boards/" in relative:
        return "local_reference_board"
    if "/work/keyframes/" in relative:
        return "turn_trace_keyframe"
    if "/work/visual_group_keyframes/" in relative:
        return "canonical_group_keyframe"
    if "/work/visual_group_video/" in relative:
        return "canonical_group_video"
    if "/work/visual_attempts/" in relative:
        if path.suffix.lower() == ".mp4":
            return "video_attempt"
        if "privacy_safe" in path.name:
            return "privacy_repair_keyframe"
        return "keyframe_attempt"
    if path.name == "cover_art_source.jpeg":
        return "cover_art_source"
    if "cover" in path.stem:
        return "cover"
    if "ending" in path.stem:
        return "ending"
    if path.name == "series_intro.jpeg":
        return "series_intro"
    if "/work/vlm_review/" in relative:
        return "vlm_review_evidence"
    return "other"


def _episode_id(path: Path, root: Path) -> str | None:
    relative = path.resolve().relative_to(root.resolve())
    return relative.parts[0] if relative.parts and relative.parts[0] != "series_assets" else None


def _group_id(path: Path) -> str | None:
    return next(
        (match.group(0) for part in path.parts if (match := GROUP_PATTERN.search(part))),
        None,
    )


def _attempt(path: Path) -> str | None:
    return next(
        (match.group(0) for part in path.parts if (match := ATTEMPT_PATTERN.search(part))),
        None,
    )


def _referenced_artifacts(root: Path) -> set[str]:
    referenced: set[str] = set()
    manifest_path = root / "series_assets" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for row in [*manifest.get("characters", []), *manifest.get("locations", [])]:
            for key in ("primary_image", "secondary_image", "spec_path"):
                if row.get(key):
                    referenced.add(str(row[key]))
    for episode_dir in root.iterdir() if root.is_dir() else ():
        if not episode_dir.is_dir() or episode_dir.name == "series_assets":
            continue
        plan_path = episode_dir / "production_plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            for row in [*plan.get("units", []), *plan.get("visual_groups", [])]:
                for key in ("keyframe_path", "raw_video_path", "segment_path"):
                    if row.get(key):
                        referenced.add(
                            f"{episode_dir.name}/{str(row[key]).lstrip('/')}"
                        )
        for suffix in ("_cover.jpeg", "_ending.jpeg", ".mp4"):
            candidate = episode_dir / f"{episode_dir.name}{suffix}"
            if candidate.is_file():
                referenced.add(_relative(candidate, root))
    return referenced


def build_generation_task_ledger(
    novel_dir: Path,
    output: Path | None = None,
) -> dict:
    root = novel_dir.resolve()
    referenced = _referenced_artifacts(root)
    task_rows: dict[str, dict] = {}
    task_metadata_files = 0
    for metadata_path in root.rglob(f"*{TASK_SUFFIX}"):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        task_metadata_files += 1
        target = Path(str(metadata_path)[: -len(TASK_SUFFIX)])
        task_id = str(data.get("task_id") or "")
        request_sha256 = str(data.get("request_sha256") or "")
        identity = (
            f"remote:{task_id}"
            if task_id
            else f"local:{request_sha256}:{_relative(target, root)}"
        )
        row = task_rows.setdefault(
            identity,
            {
                "task_id": task_id or None,
                "kind": data.get("kind"),
                "model": data.get("model"),
                "endpoint": data.get("endpoint"),
                "request_sha256s": set(),
                "output_paths": set(),
                "metadata_paths": set(),
                "purposes": set(),
                "episode_ids": set(),
                "group_ids": set(),
                "attempts": set(),
            },
        )
        if request_sha256:
            row["request_sha256s"].add(request_sha256)
        target_relative = _relative(target, root)
        row["output_paths"].add(target_relative)
        row["metadata_paths"].add(_relative(metadata_path, root))
        row["purposes"].add(_purpose(target, root))
        episode_id = _episode_id(target, root)
        if episode_id:
            row["episode_ids"].add(episode_id)
        group_id = _group_id(target)
        if group_id:
            row["group_ids"].add(group_id)
        attempt = _attempt(target)
        if attempt:
            row["attempts"].add(attempt)

    serialized_tasks = []
    remote_by_kind: Counter[str] = Counter()
    for identity, row in sorted(task_rows.items()):
        output_paths = sorted(row["output_paths"])
        canonical = any(path in referenced for path in output_paths)
        stale = all("stale" in path.casefold() for path in output_paths)
        if row["task_id"]:
            remote_by_kind[str(row["kind"] or "unknown")] += 1
        serialized_tasks.append(
            {
                "identity": identity,
                "task_id": row["task_id"],
                "kind": row["kind"],
                "model": row["model"],
                "endpoint": row["endpoint"],
                "request_sha256s": sorted(row["request_sha256s"]),
                "output_paths": output_paths,
                "metadata_paths": sorted(row["metadata_paths"]),
                "purposes": sorted(row["purposes"]),
                "episode_ids": sorted(row["episode_ids"]),
                "group_ids": sorted(row["group_ids"]),
                "attempts": sorted(row["attempts"]),
                "canonical": canonical,
                "retention_state": (
                    "canonical" if canonical else "stale" if stale else "retained_evidence"
                ),
            }
        )

    artifact_counts: Counter[str] = Counter()
    artifact_rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        relative = _relative(path, root)
        purpose = _purpose(path, root)
        artifact_counts[purpose] += 1
        artifact_rows.append(
            {
                "path": relative,
                "purpose": purpose,
                "bytes": path.stat().st_size,
                "episode_id": _episode_id(path, root),
                "group_id": _group_id(path),
                "attempt": _attempt(path),
                "canonical": relative in referenced,
                "stale": "stale" in relative.casefold(),
            }
        )

    ledger = {
        "schema_version": 1,
        "policy": "novel-generation-ledger-v1",
        "novel_id": root.name,
        "summary": {
            "task_metadata_files": task_metadata_files,
            "unique_task_records": len(serialized_tasks),
            "unique_remote_tasks": sum(remote_by_kind.values()),
            "remote_tasks_by_kind": dict(sorted(remote_by_kind.items())),
            "image_file_count": len(artifact_rows),
            "image_files_by_purpose": dict(sorted(artifact_counts.items())),
            "canonical_image_files": sum(row["canonical"] for row in artifact_rows),
            "stale_image_files": sum(row["stale"] for row in artifact_rows),
        },
        "tasks": serialized_tasks,
        "image_artifacts": artifact_rows,
    }
    atomic_write_json(output or root / "generation_task_ledger.json", ledger)
    return ledger
