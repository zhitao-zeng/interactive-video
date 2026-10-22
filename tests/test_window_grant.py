"""User-authorized time-window renewal via run_dir/window-grant.json."""
import json

import pytest

from novel_manga.application.agents.episode import lifecycle
from novel_manga.util import atomic_write_json


def ctx_of(tmp_path):
    from types import SimpleNamespace
    run = tmp_path/'run'; run.mkdir(exist_ok=True)
    return SimpleNamespace(run_dir=run, episode_dir=tmp_path/'ep')


def test_grant_renews_once_and_unblocks(tmp_path):
    ctx = ctx_of(tmp_path)
    atomic_write_json(ctx.run_dir/'window-grant.json',
                      {'seconds': 900, 'authorized_by': 'user 2026-10-22', 'granted_at': 'x'})
    saved = {'status': 'blocked', 'reason': 'episode operation timeout exhausted', 'remaining_seconds': 0}
    renewed = lifecycle._consume_window_grant(ctx, saved)
    assert renewed['remaining_seconds'] == 900
    assert renewed['status'] == 'deferred' and renewed['reason'] is None
    assert renewed['window_grant']['authorized_by'] == 'user 2026-10-22'
    consumed = json.loads((ctx.run_dir/'window-grant.json').read_text())
    assert consumed['consumed_at']
    # Second start must not double-consume; the earlier renewal survives as-is.
    again = lifecycle._consume_window_grant(ctx, {**renewed, 'remaining_seconds': 812})
    assert again['remaining_seconds'] == 812 and 'window_grant' in again


def test_grant_stacks_remaining_and_ignores_finished_runs(tmp_path):
    ctx = ctx_of(tmp_path)
    atomic_write_json(ctx.run_dir/'window-grant.json', {'seconds': 300, 'authorized_by': 'user'})
    saved = {'status': 'deferred', 'remaining_seconds': 470}
    renewed = lifecycle._consume_window_grant(ctx, saved)
    assert renewed['remaining_seconds'] == 770 and renewed['status'] == 'deferred'
    # A passed or upstream-recovery run never takes a window grant.
    assert lifecycle._consume_window_grant(ctx, {'status': 'deferred', 'remaining_seconds': 5, 'passed': True})['remaining_seconds'] == 5
