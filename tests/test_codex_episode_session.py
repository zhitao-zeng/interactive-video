"""Codex login uses the existing episode contract without API credentials or new takes."""
import json
from pathlib import Path
import uuid
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from novel_manga.application.agents.episode import codex_session, session
from novel_manga.application.agents.episode.recovery import completed_idle
from novel_manga.application.agents.sandbox import SandboxRefused


@pytest.fixture
def codex_config(tmp_path, monkeypatch):
    auth = tmp_path/'codex-login'
    auth.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(auth))
    monkeypatch.setattr(codex_session.shutil, 'which', lambda name: '/usr/bin/codex')
    monkeypatch.setattr(session, 'key_for', lambda name: pytest.fail('Codex must not request an API key'))
    return {'backend': 'codex', 'image': 'agent-sandbox:test', 'model': 'gpt-6-luna', 'effort': 'low'}


def cli(monkeypatch, work, *, identity=None, outputs=None, exit_code=0, handoff=False, failed=False):
    identity = identity or str(uuid.uuid4())
    calls = []
    def call(command, *, stdin, stdout, stderr, env):
        calls.append(command)
        assert stdin.read().startswith('You are the whole-episode production Agent.')
        events = [{'type': 'thread.started', 'thread_id': identity}]
        if handoff:
            events.append({'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
                'aggregated_output': json.dumps({'yield': True, 'job_id': 'job-2'})}})
        events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Operation handed to host.'}})
        if failed:
            events.append({'type': 'turn.failed', 'error': {'message': 'Login expired'}})
        elif exit_code == 0:
            events.append({'type': 'turn.completed', 'usage': {}})
        for event in events:
            stdout.write(json.dumps(event)+'\n')
        for name, value in (outputs or {}).items():
            (work/'output'/name).write_text(value)
        return exit_code
    monkeypatch.setattr(codex_session.subprocess, 'call', call)
    return identity, calls


def test_codex_login_dispatch_keeps_sandbox_paths_and_never_asks_api_key(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    identity, calls = cli(monkeypatch, work, outputs={'repair.json': 'candidate'}, handoff=True)
    result = session.run_session(work, 'Continue automatic review', config=codex_config,
                                 enable_tools=True, expected_output='repair.json')
    command = calls[0]
    assert result.ok and result.produced == ['repair.json']
    assert result.record['backend'] == 'codex' and result.record['auth'] == 'saved_codex_login'
    assert result.record['model'] == 'gpt-6-luna' and result.record['session_id'] == identity
    assert '--ignore-user-config' in command and 'ANTHROPIC_AUTH_TOKEN' not in command
    assert command[command.index('--model')+1] == 'gpt-6-luna'
    assert str(tmp_path/'codex-login')+':'+str(tmp_path/'codex-login') in command
    assert str(work)+':/work' in command
    assert any(value.endswith('/src:/opt/novel-code:ro') for value in command)
    assert json.loads((work/session.SESSION_FILE).read_text())['session_id'] == identity
    assert completed_idle(str(result.directory)) is False
    assert (result.directory/'codex-events.jsonl').is_file()


def test_codex_resume_keeps_actual_thread_and_prior_candidates(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    identity, _ = cli(monkeypatch, work, outputs={'requests.json': 'retained'})
    first = session.run_session(work, 'Initial', config=codex_config, enable_tools=True)
    _, calls = cli(monkeypatch, work, identity=identity)
    second = session.run_session(work, 'Continue', config=codex_config, resume=True, enable_tools=True)
    assert calls[0][calls[0].index('resume')+1] == first.record['session_id']
    assert second.record['session_id'] == identity and second.produced == []
    assert (second.directory/'output-before/requests.json').read_text() == 'retained'
    assert (first.directory/'output-after/requests.json').read_text() == 'retained'


def test_codex_cannot_reinterpret_claude_uuid_as_codex_thread(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'; work.mkdir()
    old = json.dumps({'session_id': str(uuid.uuid4())})
    (work/session.SESSION_FILE).write_text(old)
    with pytest.raises(SandboxRefused, match='previous provider'):
        session.run_session(work, 'Continue', config=codex_config, resume=True)
    assert (work/session.SESSION_FILE).read_text() == old


def test_codex_timeout_records_progress_without_claiming_new_output(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    identity, _ = cli(monkeypatch, work, exit_code=124, handoff=True)
    result = session.run_session(work, 'Continue', config=codex_config, timeout=600, enable_tools=True)
    assert not result.ok and result.record['exit'] == 124 and result.produced == []
    assert result.record['timeout_seconds'] == 600
    assert result.record['session_id'] == identity
    assert completed_idle(str(result.directory)) is False


def test_codex_turn_failure_is_not_success_even_when_cli_returns_zero(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    cli(monkeypatch, work, failed=True)
    result = session.run_session(work, 'Continue', config=codex_config)
    assert not result.ok and result.produced == []
    rows = [json.loads(line) for line in (result.directory/'session.jsonl').read_text().splitlines()]
    assert rows[-1]['is_error'] is True and rows[-1]['result'] == 'Login expired'


def test_codex_invocation_does_not_hold_the_unrelated_api_provider_slot(tmp_path, monkeypatch, codex_config):
    from novel_manga.application.agents.episode import turns, bounded_recovery
    monkeypatch.setattr(turns, 'model_slot', lambda *a, **k: pytest.fail('Codex must not hold the API slot'))
    monkeypatch.setattr(bounded_recovery, 'record_proposal', lambda ctx: None)
    attempt = object()
    run = Mock(return_value=attempt)
    monkeypatch.setattr(turns, 'run_session', run)
    ctx = SimpleNamespace(run_dir=tmp_path, work_dir=tmp_path, project_root=tmp_path, input_mode='script')
    state = SimpleNamespace(result={'deadline': time.time()+300, 'agent_seconds': 0},
        deadline=time.monotonic()+300, slice_seconds=200, slice_invocations=0)
    result, limit, remaining = turns.invoke(ctx, Mock(), state, codex_config, 'Continue', False, 300)
    assert result is attempt and limit == 200 and remaining > 200
    assert run.call_args.kwargs['config']['backend'] == 'codex'
