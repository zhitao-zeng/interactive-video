"""FastH3-Omni UniServe video generation with explicit task recovery."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from ..config import Settings
from ..util import atomic_write_json, read_json


NONTERMINAL_STATUSES = {"queued", "pending", "running", "processing", "in_progress"}
SUCCESS_STATUSES = {"completed", "succeeded", "success"}
FAILURE_STATUSES = {"failed", "error", "cancelled", "canceled"}


class TaskExpiredError(RuntimeError):
    pass


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _image_uri(path: Path) -> str:
    with Image.open(path) as image:
        mime = Image.MIME.get(image.format, "image/png")
    return _data_uri(path, mime)


def _audio_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _data_uri(path, mime)


def normalize_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint or endpoint == "pool" or endpoint.startswith("pool:"):
        raise ValueError("FastH3-Omni requires an explicit UniServe endpoint, not a pool or machine default")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("FastH3-Omni endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("FastH3-Omni endpoint must not contain credentials, query or fragment")
    return endpoint


class FastH3MediaProvider:
    def __init__(self, settings: Settings, base_url: str, ratio: str = "16:9") -> None:
        self.settings = settings
        self.base_url = normalize_endpoint(base_url)
        self.ratio = ratio
        self.fast_client = httpx.Client(timeout=settings.request_timeout, trust_env=False)

    def close(self) -> None:
        self.fast_client.close()

    def _payload(self, prompt: str, images: list[Path], audios: tuple[Path, ...], duration: float,
                 *, seed: int | None = None, short_edge: int = 768) -> dict:
        for path in (*images, *audios):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        target = {"short_edge": int(short_edge), "aspect_ratio": self.ratio,
                  "duration_seconds": float(duration)}
        payload = {
            "model": "FastH3-Omni",
            "task": "ref2va",
            "prompt": prompt,
            "conditions": [
                *({"type": "image", "role": "reference", "uri": _image_uri(path)} for path in images),
                *({"type": "audio", "role": "reference", "uri": _audio_uri(path)} for path in audios),
            ],
            "target": target,
        }
        if seed is None:
            seed = 42
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2 ** 31:
            raise ValueError("FastH3 seed must be an integer in [0, 2**31)")
        payload["seed"] = seed
        return payload

    @staticmethod
    def _request_digest(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _body(response: httpx.Response):
        try:
            return response.json()
        except ValueError:
            return response.text

    def _http_error(self, output: Path, response: httpx.Response) -> RuntimeError:
        body = self._body(response)
        atomic_write_json(output.with_suffix(".http-error.json"),
                          {"status": response.status_code, "body": body})
        detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        return RuntimeError(f"FastH3 HTTP {response.status_code}: {detail[:4000]}")

    def _json_object(self, output: Path, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as error:
            atomic_write_json(output.with_suffix(".http-error.json"),
                              {"status": response.status_code, "body": response.text})
            raise RuntimeError(f"FastH3 expected a JSON object, got HTTP {response.status_code}") from error
        if not isinstance(body, dict):
            atomic_write_json(output.with_suffix(".http-error.json"),
                              {"status": response.status_code, "body": body})
            raise RuntimeError("FastH3 expected a JSON object")
        return body

    def _submit(self, output: Path, payload: dict) -> tuple[str, dict]:
        response = self.fast_client.post(f"{self.base_url}/v1/videos", json=payload)
        if not 200 <= response.status_code < 300:
            raise self._http_error(output, response)
        body = self._json_object(output, response)
        task_id = body.get("id") or body.get("task_id")
        if not task_id:
            atomic_write_json(output.with_suffix(".http-error.json"),
                              {"status": response.status_code, "body": body})
            raise ValueError("FastH3 submission returned no task id")
        return str(task_id), body

    def _state(self, output: Path, task_id: str, endpoint: str) -> dict:
        response = self.fast_client.get(f"{endpoint}/v1/videos/{task_id}")
        if response.status_code == 404:
            body = self._body(response)
            atomic_write_json(output.with_suffix(".http-error.json"),
                              {"status": 404, "body": body, "task_id": task_id,
                               "expired": True})
            raise TaskExpiredError(f"FastH3 task {task_id} expired or is unavailable after a service restart")
        if not 200 <= response.status_code < 300:
            raise self._http_error(output, response)
        return self._json_object(output, response)

    def _download(self, output: Path, task_id: str, endpoint: str) -> None:
        response = self.fast_client.get(f"{endpoint}/v1/videos/{task_id}/content")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if response.status_code != 200 or content_type != "video/mp4" or not response.content:
            body = self._body(response)
            atomic_write_json(output.with_suffix(".http-error.json"),
                              {"status": response.status_code, "body": body,
                               "content_type": content_type, "task_id": task_id})
            detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            raise RuntimeError(
                f"FastH3 download requires HTTP 200 video/mp4 with nonempty bytes; got "
                f"{response.status_code} {content_type or 'no-content-type'} {len(response.content)} bytes: {detail[:4000]}")
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_bytes(response.content)
        os.replace(partial, output)

    def create_video(self, prompt: str, image, output: Path, duration: float,
                     additional_images: tuple[Path, ...] = (),
                     reference_audios: tuple[Path, ...] = (),
                     seed: int | None = None, short_edge: int = 768) -> Path:
        started = time.monotonic()
        output = Path(output)
        images = ([Path(image.path)] if image is not None else []) + [Path(path) for path in additional_images]
        payload = self._payload(prompt, images, tuple(reference_audios), duration,
                                seed=seed, short_edge=short_edge)
        request_sha256 = self._request_digest(payload)
        task_path = output.with_suffix(output.suffix + ".task.json")
        cached = read_json(task_path, {})
        endpoint = self.base_url
        task_id = None
        resumed = False
        if cached:
            if cached.get("request_sha256") != request_sha256:
                raise ValueError("Output already belongs to a different FastH3 request; use a new output filename")
            task_id = cached.get("task_id")
            endpoint = str(cached.get("endpoint") or self.base_url).removesuffix("/v1/videos")
            resumed = True
        receipt = dict(cached) if cached else {
            "kind": "fasth3",
            "model": "FastH3-Omni",
            "endpoint": f"{endpoint}/v1/videos",
            "request_sha256": request_sha256,
            "seed": payload["seed"],
            "target": payload["target"],
            "attachments": [
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "media_type": media_type}
                for path, media_type in [*( (path, "image") for path in images),
                                         *( (path, "audio") for path in reference_audios)]
            ],
            "status": "submitting",
            "task_id": None,
            "status_history": [],
        }
        if not task_id:
            wire_request = read_json(output.with_suffix(output.suffix + ".wire") / "request.json", {})
            if wire_request:
                expected_endpoint = f"{endpoint}/v1/videos"
                if (wire_request.get("kind") != "fasth3"
                        or wire_request.get("request_sha256") != request_sha256
                        or wire_request.get("endpoint") != expected_endpoint):
                    raise RuntimeError("FastH3 wire submission does not match this output; do not repost")
                wire_response = read_json(output.with_suffix(output.suffix + ".wire") / "response.json", {})
                task_id = wire_response.get("task_id")
                if not task_id or not 200 <= int(wire_response.get("http_status", 0)) < 300:
                    raise RuntimeError("FastH3 submission is uncertain and has no retained task id; do not repost")
                receipt.update(task_id=task_id, status="accepted")
                resumed = True
                atomic_write_json(task_path, receipt)
            elif cached:
                raise RuntimeError("FastH3 submission is uncertain and has no retained task id; do not repost")
            else:
                atomic_write_json(task_path, receipt)
                task_id, _ = self._submit(output, payload)
                receipt.update(task_id=task_id, status="accepted")
                atomic_write_json(task_path, receipt)

        def capture_server(state):
            actual_id = state.get("id") or state.get("task_id")
            if actual_id and str(actual_id) != task_id:
                receipt.update(server_response=state, server_task_id=actual_id)
                atomic_write_json(task_path, receipt)
                raise RuntimeError(f"FastH3 returned task id {actual_id} instead of {task_id}")
            actual_seconds = state.get("actual_seconds")
            if actual_seconds is None:
                actual_seconds = state.get("seconds")
            receipt.update(
                server_response=state,
                server_task_id=actual_id or task_id,
                server_model=state.get("model") or state.get("served_model_name"),
                actual_seconds=actual_seconds,
                size=state.get("size"),
                expires_at=state.get("expires_at"),
                num_frames=state.get("num_frames"),
                completed_at=state.get("completed_at"),
            )

        last_status = None
        deadline = time.monotonic() + self.settings.poll_timeout
        while True:
            state = self._state(output, task_id, endpoint)
            status = str(state.get("status") or "").lower()
            if status != last_status:
                history = list(receipt.get("status_history", []))
                history.append({"at": time.time(), "status": status,
                                "phase": state.get("phase"),
                                "completed_steps": state.get("completed_steps")})
                receipt["status_history"] = history
                last_status = status
            receipt.update(task_id=task_id, status=status,
                           elapsed_seconds=round(time.monotonic() - started, 6), resumed=resumed)
            atomic_write_json(task_path, receipt)
            if status in SUCCESS_STATUSES:
                self._download(output, task_id, endpoint)
                capture_server(state)
                receipt.update(status=status, elapsed_seconds=round(time.monotonic() - started, 6),
                               inference_time_s=state.get("inference_time_s"))
                atomic_write_json(task_path, receipt)
                return output
            if status in FAILURE_STATUSES:
                capture_server(state)
                atomic_write_json(task_path, receipt)
                raise RuntimeError(f"FastH3 task {task_id} {status}: {state.get('error') or state}")
            if status not in NONTERMINAL_STATUSES:
                raise RuntimeError(f"FastH3 task {task_id} returned unknown status {status!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"FastH3 task {task_id} remains {status}; no implicit repost")
            time.sleep(0.25)
