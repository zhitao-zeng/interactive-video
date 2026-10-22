"""The episode CLI must continue an edition's EXISTING agent run, not open a
parallel state directory under a different naming convention (2026-10-22 #12);
and the default author config carries the fast non-thinking profile verified
by repair agents (2026-10-22 session-degradation fix)."""
import json
from pathlib import Path

from novel_manga.application.agents.episode.context import create_context
from novel_manga.application.agents.sandbox import thinking_budget, provider_container_args
from novel_manga.util import atomic_write_json


def source_book(tmp_path):
    book = tmp_path/'book'; book.mkdir()
    (book/'novel.json').write_text(json.dumps({'novel_id': 'book'}))
    episode = book/'book_1'; episode.mkdir()
    atomic_write_json(episode/'chapter_script.json', {'shots': []})
    return book


def test_cli_resolution_continues_an_existing_firstframes_run(tmp_path, monkeypatch):
    book = source_book(tmp_path)
    monkeypatch.setattr('novel_manga.application.agents.episode.context.configured_root', lambda: tmp_path)
    # produce's first-frame entry created the real state here:
    real = tmp_path/'outputs/episode-agent-firstframes/book/book_1-script'
    (real/'sandbox').mkdir(parents=True)
    atomic_write_json(real/'agent-run.json', {'status': 'blocked'})
    ctx = create_context(book, 'book_1', input_mode='script')  # no output_root: CLI path
    assert ctx.run_dir == real
    # An explicit output_root stays authoritative.
    explicit = create_context(book, 'book_1', output_root=tmp_path/'elsewhere', input_mode='script')
    assert explicit.run_dir == tmp_path/'elsewhere/book/book_1-script'
    # With no existing run anywhere, the default convention applies.
    (real/'agent-run.json').unlink(); (real/'sandbox').rmdir()
    fresh = create_context(book, 'book_1', input_mode='script')
    assert fresh.run_dir == tmp_path/'outputs/episode-agent/book/book_1-script'


def test_default_author_config_carries_the_fast_non_thinking_profile():
    config = json.loads((Path(__file__).parent.parent/'configs/agent_sandbox.json').read_text())
    assert ((config.get('extra_body') or {}).get('thinking') or {}).get('type') == 'disabled'
    assert config.get('effort') == 'low'
    assert thinking_budget(config) == 0
    args = provider_container_args(config)
    assert 'MAX_THINKING_TOKENS=0' in args and 'CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1' in args
    body = next(a for a in args if a.startswith('CLAUDE_CODE_EXTRA_BODY='))
    assert '"thinking": {"type": "disabled"}' in body.replace("'", '"') or 'thinking' in body
