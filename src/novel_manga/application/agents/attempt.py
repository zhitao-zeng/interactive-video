"""Shared agent-attempt primitives. Leaf module: sandbox and the codex session
driver both use these, so they live here to keep the import direction one-way."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class SandboxRefused(ValueError):
    """The run cannot start as asked - a missing prompt, an unknown skill, no credential."""


@dataclass
class Attempt:
    """One execution of a run: where its record is, and what it actually produced."""
    run: str
    directory: Path
    exit_code: int
    seconds: int
    produced: list[str] = field(default_factory=list)
    kept: int = 0
    record: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The process finished cleanly.  Whether what it wrote is usable is a separate question, and
        the caller has to ask it: exit=0 does not mean there is a storyboard worth binding."""
        return self.exit_code == 0


def unique_attempt(attempts: Path, name: str) -> Path:
    """A directory no attempt has had before.  The stamp is to the second, and two attempts inside one
    second sharing a directory is the very thing an attempt id exists to prevent."""
    attempts.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        attempt = attempts / (name + suffix)
        try:
            attempt.mkdir()
            return attempt
        except FileExistsError:
            continue
    raise SandboxRefused(f"attempts/{name} 这一秒里已经有 99 次尝试了")
