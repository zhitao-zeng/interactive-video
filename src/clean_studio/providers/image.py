from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from clean_studio.compiler import validate_current_references
from clean_studio.frozen.provider import (
    PURGED_STATUSES,
    ResultPurged,
    SubmissionUncertain,
    TaskResult,
    download_file,
    reference_path,
    submission_exception,
    task_data,
)


def reference_base64(path: Path) -> str:
    data = Path(path).read_bytes()
    if len(data) > 2 * 1024 * 1024:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            data = buffer.getvalue()
    return base64.b64encode(data).decode("ascii")


class ImageProvider:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str | None = None,
        client=None,
        timeout: float = 1200.0,
        aspect_ratio: str = "9:16",
        sleeper=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout, trust_env=False)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.sleeper = sleeper

    def payload(self, request) -> dict:
        validate_current_references(request.shot_id, request.references)
        paths = tuple(reference_path(reference) for reference in request.references)
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "aspectRatio": self.aspect_ratio,
            "resolution": "2K",
            "thinking": "high",
        }
        if len(paths) > 1:
            payload["base64Files"] = [reference_base64(path) for path in paths]
        elif paths:
            payload["base64File"] = reference_base64(paths[0])
        return payload

    def submit(self, request) -> str:
        payload = request if isinstance(request, dict) else self.payload(request)
        try:
            response = self.client.post(
                f"{self.base_url}/v3/images/generations",
                headers=self.headers,
                json=payload,
                timeout=min(self.timeout, 120.0),
            )
            response.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            raise submission_exception(error) from error
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("response is not an object")
            task_id = data.get("task_id")
        except (ValueError, TypeError, AttributeError) as error:
            raise SubmissionUncertain(
                "image submission returned unreadable JSON; reconciliation required"
            ) from error
        if not isinstance(task_id, str) or not task_id.strip():
            raise SubmissionUncertain(
                "image submission returned no task ID; reconciliation required"
            )
        return task_id

    def query(self, task_id: str) -> TaskResult:
        response = self.client.get(
            f"{self.base_url}/v3/images/generations/{task_id}",
            headers=self.headers,
        )
        if response.status_code in {404, 410}:
            raise ResultPurged(f"image task result is no longer available: {task_id}")
        response.raise_for_status()
        data = task_data(response.json())
        status = str(data.get("status", "")).lower()
        if status in {"succeeded", "success", "completed"}:
            url = data.get("url")
            if not url:
                raise ValueError("successful image task returned no URL")
            return TaskResult(task_id, "succeeded", str(url), data)
        if status in {"failed", "failure", "error", "cancelled", "canceled"}:
            return TaskResult(task_id, "failed", raw=data)
        return TaskResult(task_id, "pending", raw=data)

    def download(self, result, output: Path) -> Path:
        if isinstance(result, str):
            result = (
                TaskResult("download", "succeeded", result)
                if result.startswith(("http://", "https://"))
                else self.query(result)
            )
        if result.status != "succeeded" or not result.download_url:
            raise ValueError("image task has no completed result to download")
        try:
            return download_file(
                self.client,
                result.download_url,
                Path(output),
                max_bytes=64 * 1024 * 1024,
                sleeper=self.sleeper,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in PURGED_STATUSES:
                raise ResultPurged(
                    f"image task result was purged: {result.task_id}"
                ) from error
            raise


PhanRouterImageProvider = ImageProvider
