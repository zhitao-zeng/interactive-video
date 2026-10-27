from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import httpx

from clean_studio.compiler import validate_current_references
from clean_studio.frozen.h3 import h3_payload, validate_duration
from clean_studio.frozen.provider import (
    PURGED_STATUSES,
    ResultPurged,
    SubmissionUncertain,
    TaskResult,
    download_file,
    reference_path,
    submission_exception,
)


class H3Provider:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        client=None,
        timeout: float = 1200.0,
        sleeper=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout, trust_env=False)
        self.sleeper = sleeper

    def payload(self, request, first_frame: Path | None, *, seed: int = 0) -> dict:
        validate_duration(request.duration_seconds)
        validate_current_references(request.shot_id, request.references)
        if first_frame is None:
            if request.accepted_frame_hash:
                raise ValueError("reference-mode request must not carry an accepted frame hash")
            frame = None
        else:
            first_frame = Path(first_frame)
            actual_hash = sha256(first_frame.read_bytes()).hexdigest()
            if actual_hash != request.accepted_frame_hash:
                raise ValueError("first frame bytes do not match the accepted frame hash")
            frame = first_frame
        references = tuple(reference_path(reference) for reference in request.references)
        return h3_payload(
            model=self.model,
            prompt=request.prompt,
            duration_seconds=request.duration_seconds,
            first_frame=frame,
            references=references,
            seed=seed,
        )

    def submit(self, payload: dict) -> str:
        try:
            response = self.client.post(
                f"{self.base_url}/v1/videos",
                json=payload,
                timeout=min(self.timeout, 300.0),
            )
            response.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            raise submission_exception(error) from error
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("response is not an object")
            task_id = data.get("id")
        except (ValueError, TypeError, AttributeError) as error:
            raise SubmissionUncertain(
                "H3 submission returned unreadable JSON; reconciliation required"
            ) from error
        if not isinstance(task_id, str) or not task_id.strip():
            raise SubmissionUncertain(
                "H3 submission returned no task ID; reconciliation required"
            )
        return task_id

    def query(self, task_id: str) -> TaskResult:
        response = self.client.get(f"{self.base_url}/v1/videos/{task_id}")
        if response.status_code in {404, 410}:
            raise ResultPurged(f"H3 task result is no longer available: {task_id}")
        response.raise_for_status()
        data = response.json()
        status = str(data.get("status", "")).lower()
        if status in {"completed", "succeeded", "success"}:
            return TaskResult(
                task_id,
                "succeeded",
                f"{self.base_url}/v1/videos/{task_id}/content",
                data,
            )
        if status in {"failed", "failure", "error", "cancelled", "canceled"}:
            return TaskResult(task_id, "failed", raw=data)
        return TaskResult(task_id, "pending", raw=data)

    def download(self, result, output: Path) -> Path:
        task_id = result if isinstance(result, str) else result.task_id
        if not isinstance(result, str) and result.status != "succeeded":
            raise ValueError("H3 task has no completed result to download")
        try:
            return download_file(
                self.client,
                f"{self.base_url}/v1/videos/{task_id}/content",
                Path(output),
                sleeper=self.sleeper,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in PURGED_STATUSES:
                raise ResultPurged(f"H3 task result was purged: {task_id}") from error
            raise


LocalH3Provider = H3Provider
