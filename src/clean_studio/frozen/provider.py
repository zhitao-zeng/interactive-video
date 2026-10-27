from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Mapping

import httpx


UNCERTAIN_STATUSES = {500, 502, 504, 520, 521, 522, 523, 524}
NOT_ACCEPTED_STATUSES = {429, 503}
PURGED_STATUSES = {403, 404, 410}


class SubmissionUncertain(RuntimeError):
    pass


class SubmissionRejected(RuntimeError):
    def __init__(
        self, status_code: int, detail: str = "", *, not_accepted: bool = False
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.not_accepted = not_accepted
        super().__init__(f"task submission returned HTTP {status_code}: {detail}")


class ResultPurged(RuntimeError):
    pass


class ProviderTaskFailed(RuntimeError):
    def __init__(self, task_id: str, state: Mapping[str, object]) -> None:
        self.task_id = task_id
        self.state = dict(state)
        super().__init__(f"provider task {task_id} failed: {dict(state)}")


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    download_url: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)


def task_data(payload: dict) -> dict:
    result = payload.get("Result") or payload.get("data") or payload
    return result if isinstance(result, dict) else payload


def submission_exception(error: Exception) -> Exception:
    if isinstance(error, httpx.TransportError):
        return SubmissionUncertain(
            f"task submission unconfirmed ({type(error).__name__}); not sent again"
        )
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        detail = error.response.text.strip().replace("\n", " ")[:1000]
        if status in UNCERTAIN_STATUSES:
            return SubmissionUncertain(
                f"task submission unconfirmed (HTTP {status}); not sent again"
            )
        return SubmissionRejected(
            status, detail, not_accepted=status in NOT_ACCEPTED_STATUSES
        )
    return error


def reference_path(value) -> Path:
    if isinstance(value, Mapping):
        value = value.get("source_path") or value.get("path")
    else:
        value = getattr(value, "source_path", value)
    return Path(value)


def download_file(
    client,
    url: str,
    output: Path,
    *,
    max_bytes: int = 512 * 1024 * 1024,
    attempts: int = 3,
    sleeper=None,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    sleep = sleeper or time.sleep
    for attempt in range(1, attempts + 1):
        partial.unlink(missing_ok=True)
        try:
            with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                total = 0
                with partial.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"remote artifact exceeds {max_bytes} bytes")
                        stream.write(chunk)
            os.replace(partial, output)
            return output
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError):
            partial.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            sleep(3 * attempt)
    raise AssertionError("download attempts exhausted")
