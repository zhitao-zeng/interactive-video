from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


class SandboxRefused(ValueError):
    pass


@dataclass
class Attempt:
    run: str
    directory: Path
    exit_code: int
    seconds: int
    produced: list[str] = field(default_factory=list)
    kept: int = 0
    record: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + f".{os.getpid()}.partial")
    partial.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(partial, path)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _snapshot(output: Path) -> dict:
    return {
        path.relative_to(output): (
            path.stat().st_mtime_ns,
            path.stat().st_size,
            path.read_bytes(),
        )
        for path in output.rglob("*")
        if path.is_file()
    }


def run_structured_session(
    work_dir,
    prompt_text,
    *,
    model,
    effort="low",
    image,
    timeout=1200,
    resume=False,
    sandbox=None,
):
    if not prompt_text.strip() or timeout <= 0:
        raise SandboxRefused("Codex needs a nonempty prompt and positive timeout")
    binary = shutil.which("codex")
    if not binary:
        installed = Path.home() / ".local/bin/codex"
        if installed.is_file() and os.access(installed, os.X_OK):
            binary = str(installed)
    if not binary:
        raise SandboxRefused("Codex CLI is not installed")
    binary = Path(binary).resolve()
    auth_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    if not auth_home.is_dir():
        raise SandboxRefused("No saved Codex login directory")

    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "codex-session.json"
    state = _read_json(state_path, {})
    if resume:
        if state.get("backend") != "codex":
            raise SandboxRefused("No saved Codex session to resume")
        try:
            uuid.UUID(state.get("session_id", ""))
        except (ValueError, TypeError, AttributeError) as error:
            raise SandboxRefused("No valid saved Codex session to resume") from error
    elif state_path.exists():
        raise SandboxRefused("This workspace already has a session; use resume=True")

    output = work / "output"
    output.mkdir(parents=True, exist_ok=True)
    attempts = work / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    attempt = attempts / (
        f"{time.strftime('%Y%m%d-%H%M%S')}-json-{uuid.uuid4().hex[:8]}"
    )
    attempt.mkdir()
    before = _snapshot(output)
    shutil.copytree(output, attempt / "output-before")
    role = (
        "Answer this one structured model request from its supplied text and actual "
        "attached images. Use no tools, make no file changes, and output only the "
        "final JSON object.\n\n"
    )
    (attempt / "prompt.txt").write_text(role + prompt_text, encoding="utf-8")

    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "host",
        "--name",
        f"clean-codex-{uuid.uuid4()}",
        "--user",
        "1001:1001",
        "-e",
        f"HOME={Path.home()}",
        "-v",
        f"{auth_home}:{auth_home}",
        "-v",
        f"{binary}:/usr/local/bin/codex:ro",
        "-v",
        f"{work}:/work",
        "-w",
        "/work",
        "--tmpfs",
        "/work/attempts:ro,mode=755",
    ]
    environment = dict(os.environ)
    sandbox = dict(sandbox or {})
    if sandbox.get("codex_proxy_url"):
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            environment[name] = sandbox["codex_proxy_url"]
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "CODEX_HOME",
    ):
        if environment.get(name):
            command.extend(["-e", name])
    command.extend(
        [
            image,
            "timeout",
            str(timeout),
            "codex",
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--json",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
            "-c",
            f"model_reasoning_effort={json.dumps(effort)}",
            "--model",
            model,
            "--enable",
            "skip_host_skill_discovery",
            "-c",
            "project_doc_max_bytes=0",
        ]
    )
    if sandbox.get("codex_transport") == "http":
        for value in (
            'model_provider="codex-http"',
            'model_providers.codex-http.name="Codex saved login HTTP"',
            'model_providers.codex-http.base_url="https://chatgpt.com/backend-api/codex"',
            'model_providers.codex-http.wire_api="responses"',
            'model_providers.codex-http.requires_openai_auth=true',
            'model_providers.codex-http.supports_websockets=false',
        ):
            command.extend(["-c", value])
    if (work / "input/schema.json").is_file():
        command.extend(["--output-schema", "/work/input/schema.json"])
    command.extend(["--output-last-message", "/work/output/response.json"])
    attached = []
    if not resume:
        for path in (work / "input/images.json").exists() and _read_json(
            work / "input/images.json", []
        ) or []:
            command.extend(["--image", "/work/" + path])
            attached.append({"path": path, "bytes": (work / path).stat().st_size})
    if resume:
        command.extend(["resume", state["session_id"]])
    command.append("-")
    _atomic_write_json(attempt / "attached-images.json", attached)

    started = time.monotonic()
    with (
        (attempt / "codex-events.jsonl").open("w") as stream,
        (attempt / "stderr.log").open("w") as errors,
        (attempt / "prompt.txt").open() as prompt,
    ):
        try:
            code = subprocess.call(
                command, stdin=prompt, stdout=stream, stderr=errors, env=environment
            )
        except OSError as error:
            errors.write(str(error) + "\n")
            code = 127

    events = []
    for line in (attempt / "codex-events.jsonl").read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    thread = next(
        (event["thread_id"] for event in events if event.get("type") == "thread.started"),
        None,
    )
    if thread:
        if resume and thread != state["session_id"]:
            raise SandboxRefused("Codex resumed a different session")
        state.update(
            backend="codex",
            session_id=thread,
            last_attempt=attempt.name,
        )
        _atomic_write_json(state_path, state)
    elif code == 0:
        code = 1

    files = [path for path in output.rglob("*") if path.is_file()]
    produced = sorted(
        str(path.relative_to(output))
        for path in files
        if before.get(path.relative_to(output))
        != (path.stat().st_mtime_ns, path.stat().st_size, path.read_bytes())
    )
    shutil.copytree(output, attempt / "output-after")
    failed = next(
        (event for event in reversed(events) if event.get("type") == "turn.failed"),
        None,
    )
    if failed is not None and code == 0:
        code = 1
    native_completion = next(
        (event for event in reversed(events) if event.get("type") == "turn.completed"),
        {},
    )
    record = {
        "run": work.name,
        "attempt": attempt.name,
        "session_id": thread or state.get("session_id"),
        "backend": "codex",
        "auth": "saved_codex_login",
        "model": model,
        "image": image,
        "resume": resume,
        "phase": "model_json",
        "usage": native_completion.get("usage", {}),
        "timeout_seconds": timeout,
        "seconds": round(time.monotonic() - started),
        "exit": code,
        "produced": produced,
        "kept_from_earlier_attempts": len(files) - len(produced),
        "expected_output": "response.json",
        "expected_output_produced": "response.json" in produced,
    }
    _atomic_write_json(attempt / "attempt.json", record)
    return Attempt(
        run=work.name,
        directory=attempt,
        exit_code=code,
        seconds=record["seconds"],
        produced=produced,
        kept=record["kept_from_earlier_attempts"],
        record=record,
    )
