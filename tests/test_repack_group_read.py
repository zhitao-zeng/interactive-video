"""Reuse recut definitions, while changes to history and physical counts stay live."""
from novel_manga.application.repair import repack_budget
from novel_manga.util import atomic_write_json


def record(root='a', child='b'):
    return {'trials': [{'changes': {'groups': [{
        'old': [root], 'new': [child],
        'source_budget': {'roots': {root: {'used': 1, 'limit': 3}},
                          'children': {child: {'roots': [root], 'baseline_count': 0}}}
    }]}}]}


def test_one_history_parse_serves_all_clip_scope_queries(tmp_path, monkeypatch):
    path = tmp_path/'repair_history/history.json'
    atomic_write_json(path, record())
    original = repack_budget.read_json
    calls = []
    def read(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(repack_budget, 'read_json', read)
    with repack_budget.scope_reads(tmp_path):
        assert repack_budget.group_for(tmp_path, 'a')
        assert repack_budget.group_for(tmp_path, 'b')
        assert repack_budget.group_for(tmp_path, 'other') is None
    assert calls == [path]


def test_new_history_revision_and_explicit_unpublished_record_are_visible(tmp_path):
    path = tmp_path/'repair_history/history.json'
    assert repack_budget.group_for(tmp_path, 'a') is None
    atomic_write_json(path, record())
    with repack_budget.scope_reads(tmp_path):
        assert repack_budget.group_for(tmp_path, 'a')
        # The supplied draft is newer than the file and bypasses its snapshot.
        assert repack_budget.group_for(tmp_path, 'c', record=record('c', 'd'))
    atomic_write_json(path, record('c', 'd'))
    assert repack_budget.group_for(tmp_path, 'a') is None
    assert repack_budget.group_for(tmp_path, 'c')


def test_cached_scope_never_caches_take_counts_or_limits(tmp_path):
    atomic_write_json(tmp_path/'repair_history/history.json', record())
    with repack_budget.scope_reads(tmp_path):
        first = repack_budget.balances(tmp_path, 'b', {'b': 0}, {}, {'a': 3})
        assert first['remaining'] == 2
        assert repack_budget.balances(tmp_path, 'b', {'b': 1}, {}, {'a': 3})['remaining'] == 1
        assert repack_budget.balances(tmp_path, 'b', {'b': 1}, {}, {'a': 2})['remaining'] == 0


def test_snapshot_is_released_on_error_and_does_not_cover_other_episode(tmp_path):
    import pytest
    atomic_write_json(tmp_path/'repair_history/history.json', record())
    other = tmp_path/'other'
    atomic_write_json(other/'repair_history/history.json', record('c', 'd'))
    with pytest.raises(RuntimeError):
        with repack_budget.scope_reads(tmp_path):
            assert repack_budget.group_for(tmp_path, 'a')
            assert repack_budget.group_for(other, 'c')
            raise RuntimeError('status failed')
    atomic_write_json(tmp_path/'repair_history/history.json', record('c', 'd'))
    assert repack_budget.group_for(tmp_path, 'a') is None
    assert repack_budget.group_for(tmp_path, 'c')
