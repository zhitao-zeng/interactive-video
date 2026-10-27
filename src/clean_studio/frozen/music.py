from __future__ import annotations

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit


def index_running(config, *, connector=None) -> bool:
    address = urlsplit(config["neo4j_uri"])
    connect = connector or socket.create_connection
    try:
        with connect((address.hostname, address.port or 7687), timeout=1):
            return True
    except OSError:
        return False


def _loopback(hostname) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@contextmanager
def local_index(
    config,
    directory,
    *,
    runner=None,
    environment=None,
    connector=None,
    clock=None,
    sleep=None,
):
    home = config.get("neo4j_home")
    if not home:
        if not index_running(config, connector=connector):
            raise RuntimeError(
                "Configured SoulTuner graph service is unavailable; startup paths are incomplete"
            )
        yield
        return

    run = runner or subprocess.run
    now = clock or time.monotonic
    pause = sleep or time.sleep
    home = Path(home)
    lock = home / "run/studio-music-selection.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **(environment or {})}
    if config.get("java_home"):
        env["JAVA_HOME"] = config["java_home"]
    with lock.open("a") as handle, (Path(directory) / "index.log").open(
        "a", encoding="utf-8"
    ) as log:
        fcntl.flock(handle, fcntl.LOCK_EX)
        started = False
        try:
            if not index_running(config, connector=connector):
                if not config.get("java_home"):
                    raise RuntimeError(
                        "SoulTuner on-demand start requires both neo4j_home and java_home"
                    )
                if not _loopback(urlsplit(config["neo4j_uri"]).hostname):
                    raise RuntimeError("Only a loopback SoulTuner index can be started")
                done = run(
                    [str(home / "bin/neo4j"), "start"],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=45,
                )
                if done.returncode:
                    raise RuntimeError(
                        "SoulTuner local graph could not start; retained index.log"
                    )
                started = True
                deadline = now() + 20
                while not index_running(config, connector=connector):
                    if now() >= deadline:
                        raise RuntimeError("SoulTuner local graph did not become ready")
                    pause(0.2)
            yield
        finally:
            if started:
                stopped = run(
                    [str(home / "bin/neo4j"), "stop"],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=45,
                )
                if stopped.returncode:
                    raise RuntimeError(
                        "SoulTuner local index cleanup failed; retained index.log"
                    )


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def recommended_catalogue_rows(raw, catalogue_rows, *, limit: int):
    if not isinstance(raw, dict) or raw.get("success") is not True:
        raise ValueError("SoulTuner search failed; no candidates adopted")
    recommendations = raw.get("recommendations")
    if not isinstance(recommendations, dict) or recommendations.get("success") is not True:
        raise ValueError("SoulTuner recommendations failed; no candidates adopted")
    rows = recommendations.get("data")
    if not isinstance(rows, list):
        raise ValueError("SoulTuner candidate response is malformed")
    catalogue = {
        row.get("song_id"): row
        for row in catalogue_rows
        if isinstance(row, dict) and isinstance(row.get("song_id"), str)
    }
    selected = []
    unavailable = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("SoulTuner candidate row is malformed")
        song = row.get("song")
        candidate_id = song.get("music_id") if isinstance(song, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("SoulTuner candidate has no original music_id")
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        entry = catalogue.get(candidate_id)
        audio = entry.get("local_audio") if entry else None
        if not entry or not isinstance(audio, str) or not Path(audio).is_file():
            unavailable.append(candidate_id)
            continue
        if len(selected) >= limit:
            continue
        selected.append((entry, str(row.get("reason") or "")))
    return selected, unavailable, len(rows)
