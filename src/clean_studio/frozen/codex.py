from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import uuid

from .codex_session import _atomic_write_json, run_structured_session


def _project_root() -> Path:
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[3]
    ).resolve()


def completion(request, *, timeout, work_dir=None, resume=False, sandbox=None):
    if timeout <= 0:
        raise TimeoutError("Codex model request budget exhausted")
    root = _project_root()
    work = Path(
        work_dir or root / ".runtime/clean-codex" / f"json-{uuid.uuid4()}"
    ).resolve()
    (work / "input").mkdir(parents=True, exist_ok=True)
    (work / "output").mkdir(parents=True, exist_ok=True)
    owner = work / "owner"
    if not owner.exists():
        owner.write_text(
            "Clean Studio structured Codex JSON/vision request; saved login; no media generation\n",
            encoding="utf-8",
        )

    payload = copy.deepcopy(request)
    images = []
    image_bytes = {}
    for message in payload.get("messages", []):
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Codex message parts must be objects")
            if part.get("type") == "video_url":
                raise ValueError("Codex JSON/image adapter cannot inspect raw video")
            if part.get("type") != "image_url":
                continue
            url = part.get("image_url", {}).get("url", "")
            if not url.startswith("data:image/") or ";base64," not in url:
                raise ValueError("Codex vision requires inline image bytes")
            try:
                data = base64.b64decode(url.split(";base64,", 1)[1], validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("Codex vision image bytes are not valid base64") from error
            if not data:
                raise ValueError("Codex vision image bytes must be nonempty")
            relative = f"input/image-{len(images) + 1:03d}.jpg"
            path = work / relative
            if resume:
                if not path.is_file() or path.read_bytes() != data:
                    raise ValueError("a resumed session cannot attach different images")
            else:
                path.write_bytes(data)
            images.append(relative)
            image_bytes[relative] = data
            part.clear()
            part.update(
                type="text",
                text=f"Actual attached image {len(images)}: {relative}",
            )

    if resume and image_bytes:
        previous = (work / "input/images.json").exists() and json.loads(
            (work / "input/images.json").read_text(encoding="utf-8")
        ) or []
        if list(image_bytes) != list(previous):
            raise ValueError("a resumed session must preserve its original attachments")
    if not resume or image_bytes:
        _atomic_write_json(work / "input/images.json", images)
    _atomic_write_json(work / "input/manifest.json", {"phase": "model_json"})
    _atomic_write_json(work / "input/request.json", payload)
    schema = (request.get("response_format", {}).get("json_schema") or {}).get("schema")
    if schema:
        _atomic_write_json(work / "input/schema.json", schema)

    sandbox = dict(sandbox or {})
    image = sandbox.get("image") or os.environ.get(
        "CLEAN_CODEX_IMAGE", "agent-sandbox:0918"
    )
    prompt = (
        "Return the final JSON response for this structured request. Preserve all "
        "role/source constraints and use the actual attached images in their listed order.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    attempt = run_structured_session(
        work,
        prompt,
        model=request["model"],
        effort=request.get("reasoning_effort", "low"),
        image=image,
        timeout=max(1, int(timeout)),
        resume=resume,
        sandbox=sandbox,
    )
    if not attempt.ok or "response.json" not in attempt.produced:
        events = Path(attempt.directory) / "codex-events.jsonl"
        if events.is_file():
            for line in events.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                message = (
                    (event.get("error") or {}).get("message")
                    if event.get("type") == "turn.failed"
                    else event.get("message")
                )
                if event.get("type") in {"error", "turn.failed"} and (
                    "Selected model is at capacity" in str(message)
                ):
                    raise ConnectionError(
                        "Selected model is at capacity; preserved evidence: "
                        + str(attempt.directory)
                    )
        raise RuntimeError(
            "Codex request did not complete; preserved evidence: "
            + str(attempt.directory)
        )
    try:
        answer = json.loads(
            (work / "output/response.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Codex response is not complete UTF-8 JSON") from error
    if not isinstance(answer, dict):
        raise ValueError("Codex response must be a JSON object")
    return {
        "choices": [
            {
                "message": {"content": json.dumps(answer, ensure_ascii=False)},
                "finish_reason": "stop",
            }
        ],
        "usage": attempt.record.get("usage", {}),
        "codex_execution": {
            "seconds": attempt.record.get("seconds", 0),
            "attempt": str(attempt.directory),
            "session_id": attempt.record.get("session_id"),
        },
    }
