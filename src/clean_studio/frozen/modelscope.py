from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath


def source(path):
    path = Path(path).resolve()
    stat = path.stat()
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def inventory(api, repository):
    rows = api.list_repo_files(repository, "dataset")
    result = {}
    for row in rows:
        get = row.get if isinstance(row, Mapping) else lambda name, default=None: getattr(row, name, default)
        path = get("path", "")
        if not path:
            continue
        result[path] = {
            "size": int(get("size", 0) or 0),
            "sha256": get("sha256"),
        }
    return result


@contextmanager
def single_writer(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def remote_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("remote object path must be a clean relative path")
    return path


def stage_hardlink(stage, path, original):
    relative = remote_path(path)
    original = Path(original).resolve()
    target = Path(stage) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.samefile(original):
        return target
    target.unlink(missing_ok=True)
    try:
        target.hardlink_to(original)
    except OSError:
        temporary = target.with_suffix(target.suffix + ".partial")
        temporary.unlink(missing_ok=True)
        with original.open("rb") as source_stream, temporary.open("wb") as target_stream:
            for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                target_stream.write(block)
        os.replace(temporary, target)
    return target


def episode_index(
    accepted,
    repository,
    remote_object,
    cover_object,
    video_source,
    cover_source,
):
    row = {
        "episode": accepted.episode_id,
        "title": accepted.title,
        "seconds": accepted.export.expected_duration_seconds,
        "bundle_hash": accepted.bundle_hash,
        "file": remote_object,
        "cover": cover_object,
        "video_sha256": video_source["sha256"],
        "cover_sha256": cover_source["sha256"],
        "video_bytes": video_source["bytes"],
        "cover_bytes": cover_source["bytes"],
    }
    return {
        "schema": "clean-studio-modelscope-index-v1",
        "repository": repository,
        "title": accepted.title,
        "total_episodes": 1,
        "uploaded_episodes": 1,
        "episodes": [row],
    }
