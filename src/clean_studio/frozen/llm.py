from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import time
import uuid

from PIL import Image

from .codex import completion


@dataclass(frozen=True)
class JsonEndpoint:
    model: str
    endpoints: tuple[str, ...]
    key: str = field(default="", repr=False)
    stream: bool = False
    reasoning: str = "low"
    min_max_tokens: int = 0

    @classmethod
    def from_env(cls, overrides=None):
        env = {**os.environ, **(overrides or {})}
        bases = env.get("QWEN38_LOCAL_BASE_URL", "codex://local")
        return cls(
            env.get("QWEN38_LOCAL_MODEL", "gpt-5.6-luna"),
            tuple(item.strip().rstrip("/") for item in bases.split(",") if item.strip()),
            reasoning=env.get("QWEN38_LOCAL_REASONING", "low"),
        )


class ModelOutputTruncated(ValueError):
    def __init__(self, name, max_tokens):
        self.name = name
        self.max_tokens = max_tokens
        super().__init__(f"{name}: JSON truncated at {max_tokens} output tokens")


class ModelResponseInvalid(ValueError):
    pass


def image_part(path: Path, max_side: int) -> dict:
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = min(1.0, max_side / max(image.size))
        if scale < 1.0:
            image = image.resize(
                (round(image.width * scale), round(image.height * scale))
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii")
        },
    }


def _new_work_dir() -> Path:
    root = Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[3]
    ).resolve()
    return root / ".runtime/clean-codex" / f"json-{uuid.uuid4()}"


def ask_json(
    parts: list[dict],
    schema: dict,
    *,
    name: str,
    max_tokens: int = 700,
    timeout: float = 600.0,
    retry_truncated: bool = True,
    settings: JsonEndpoint | None = None,
    json_object: bool = False,
    work_dir=None,
    resume: bool = False,
    sandbox=None,
) -> dict:
    settings = settings or JsonEndpoint.from_env()
    if list(settings.endpoints) != ["codex://local"]:
        raise ValueError("Clean frozen Task 3 adapter only supports codex://local")
    payload = {
        "model": settings.model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_effort": settings.reasoning,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
        "messages": [{"role": "user", "content": parts}],
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] = [
            *parts,
            {
                "type": "text",
                "text": "Return a JSON object matching this schema: "
                + json.dumps(schema, ensure_ascii=False),
            },
        ]

    deadline = time.monotonic() + timeout
    request_work_dir = Path(work_dir) if work_dir is not None else _new_work_dir()
    for retry in range(3 if retry_truncated else 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{name}: request budget exhausted")
        body = completion(
            payload,
            timeout=remaining,
            work_dir=request_work_dir,
            resume=resume or retry > 0,
            sandbox=sandbox,
        )
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            empty = not content
            if retry == 0 and retry_truncated and payload["max_tokens"] < 16000:
                payload["max_tokens"] = min(16000, payload["max_tokens"] * 2)
                continue
            if empty and retry == 1 and retry_truncated:
                payload["max_tokens"] = max(700, payload["max_tokens"] // 2)
                continue
            raise ModelOutputTruncated(name, payload["max_tokens"])
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            raise ModelResponseInvalid(f"{name}: response is not complete JSON") from error
        if not isinstance(result, dict):
            raise ModelResponseInvalid(f"{name}: response must be a JSON object")
        return result
    raise ModelOutputTruncated(name, payload["max_tokens"])


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }
