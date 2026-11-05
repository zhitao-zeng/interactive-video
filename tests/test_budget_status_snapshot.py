"""A read-only status reuses its actual-take scan without changing budget semantics."""
from novel_manga.application.repair import history
import pytest


@pytest.mark.parametrize('grants', [
    {}, {'clip_01': {'limit': 3, 'reason': 'original limit'}},
    {'*': {'limit': 2, 'reason': 'bounded episode'}},
    {'clip_01': {'conditional_extra': {'authorization': 'user', 'limit': 4}}},
    {'clip_01': {'conditional_extra': {'reason': 'missing authorization', 'limit': 4}}},
])
def test_no_authorized_extra_does_not_read_take_history(tmp_path, monkeypatch, grants):
    monkeypatch.setattr(history, 'source_budget', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('no authorized extra exists on any source root')))
    assert history.conditional_extra(tmp_path, 'clip_01', grants=grants) == {}


def test_authorized_source_root_still_uses_current_scope_and_count(tmp_path, monkeypatch):
    grants = {'clip_01': {'conditional_extra': {
        'authorization': 'user', 'reason': 'one reviewed recovery',
        'limit': 4, 'prepared_clips': ['clip_02']}}}
    monkeypatch.setattr(history, 'source_budget', lambda *a, **k: {
        'roots': ['clip_01'], 'remaining': 1})
    monkeypatch.setattr(history, 'generation_counts', lambda *_: {'clip_02': 2})
    extra = history.conditional_extra(tmp_path, 'clip_02', grants=grants)
    assert extra['source_limit'] == 4
    assert extra['limit'] == 3
    assert extra['scope_roots'] == ['clip_01']
    assert extra['prepared'] is True


def test_unrepacked_scope_does_not_rescan_owned_media(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'generation_counts', lambda *_: (_ for _ in ()).throw(
        AssertionError('no source budget exists to calculate')))
    assert history.source_budget(tmp_path, 'clip_01') == {
        'roots': [], 'constraints': {}, 'remaining': None}


def test_status_counts_are_call_scoped_and_default_generation_sees_new_take(tmp_path, monkeypatch):
    calls = []
    def current(directory):
        calls.append(directory)
        return {'clip_01': 3}
    monkeypatch.setattr(history, 'generation_counts', current)
    assert history.remaining_takes(tmp_path, 'clip_01', preparation=True,
                                   counts={'clip_01': 2}) == 1
    assert calls == []
    assert history.remaining_takes(tmp_path, 'clip_01') == 0
    assert calls == [tmp_path]


def test_explicit_empty_snapshot_is_not_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'generation_counts', lambda *_: (_ for _ in ()).throw(
        AssertionError('status already supplied the snapshot')))
    assert history.remaining_takes(tmp_path, 'clip_01', preparation=True, counts={}) == 3
