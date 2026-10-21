"""Verify the actual suspense production entry and retain the adopted packing path."""
import pytest
from types import SimpleNamespace
from novel_manga.interactive import production


@pytest.mark.parametrize('lane', ['h3', 'sd25'])
def test_suspense_edition_dispatches_to_agent_and_preserves_project_scope(tmp_path, monkeypatch, lane):
    from novel_manga.interactive import agent
    from novel_manga.interactive import delivery
    name = 'suspense-trial'
    episode = tmp_path/'outputs'/name/(name+'_1')
    episode.mkdir(parents=True)
    (episode/'clip_plan.json').write_text('{"clips":[]}')
    calls = []
    runner = SimpleNamespace()
    def runner_for(root, novel, lane, **kwargs):
        calls.append(('runner', lane, kwargs))
        return runner
    def run(actual, lane, *, resume=False, destination=None, config=None):
        assert actual is runner
        assert destination==tmp_path/'delivery/suspense/trial'
        assert config is None  # no task-level agent config in this dispatch
        calls.append(('agent', lane, resume))
        return {'status':'candidate'}
    monkeypatch.setattr(production, 'runner_for', runner_for)
    monkeypatch.setattr(agent, 'run', run)
    monkeypatch.setattr(delivery, 'deliver', lambda *args: {'status':'candidate'})
    production.run_episode(tmp_path, lane, project='suspense', edition=name, resume=True)
    expected = [('runner', lane, {'project':'suspense'})]
    if lane == 'h3':
        expected += [('runner', lane, {'project':'suspense'})]
    assert calls == expected + [('agent', lane, True)]


def test_suspense_groups_keep_the_existing_suspense_packer(tmp_path, monkeypatch):
    from novel_manga.interactive import suspense
    (tmp_path/'suspense_groups.json').write_text('[]')
    (tmp_path/'authored_groups.json').write_text('[]')
    monkeypatch.setattr(suspense, 'pack_suspense', lambda path: {'selected':str(path)})
    assert production.pack(tmp_path) == {'selected':str(tmp_path)}
