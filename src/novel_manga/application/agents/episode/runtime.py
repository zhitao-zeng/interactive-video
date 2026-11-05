"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


def paused():
    path = os.environ.get('NOVEL_AGENT_PAUSE_FILE')
    return bool(path and Path(path).exists())


@contextmanager
def process_environment(values):
    """This sequential command owns its environment until its host jobs have settled."""
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
