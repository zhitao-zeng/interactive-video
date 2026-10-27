from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import uuid

from clean_studio.frozen.llm import JsonEndpoint, ask_json as frozen_ask_json


@dataclass
class CodexSession:
    work_dir: Path
    started: bool = False


class CodexClient:
    def __init__(
        self,
        *,
        root=None,
        model="gpt-5.6-luna",
        effort="low",
        sandbox=None,
    ):
        self.root = Path(
            root
            or os.environ.get("NOVEL_PROJECT_ROOT")
            or Path(__file__).resolve().parents[3]
        ).resolve()
        self.model = model
        self.effort = effort
        self.sandbox = dict(sandbox or {})

    def create_session(self, prefix="json"):
        safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", prefix).strip("-") or "json"
        work_dir = (
            self.root
            / ".runtime/clean-codex"
            / f"{safe_prefix}-{uuid.uuid4()}"
        )
        return CodexSession(work_dir=work_dir)

    def ask_json(
        self,
        parts,
        schema,
        *,
        name,
        max_tokens=16000,
        timeout=1200,
        session=None,
        retry_truncated=True,
        json_object=False,
    ):
        session = session or self.create_session("json")
        settings = JsonEndpoint(
            self.model,
            ("codex://local",),
            reasoning=self.effort,
            min_max_tokens=0,
        )
        try:
            result = frozen_ask_json(
                parts,
                schema,
                name=name,
                max_tokens=max_tokens,
                timeout=timeout,
                retry_truncated=retry_truncated,
                settings=settings,
                json_object=json_object,
                work_dir=session.work_dir,
                resume=session.started,
                sandbox=self.sandbox,
            )
        except Exception:
            session.started = session.started or (
                session.work_dir / "codex-session.json"
            ).is_file()
            raise
        session.started = True
        return result
