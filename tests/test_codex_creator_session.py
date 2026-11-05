"""Codex login uses the existing episode contract without API credentials or new takes."""
import json
from pathlib import Path
import uuid
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from novel_manga.application.agents.episode import session
from novel_manga.application.agents.episode import codex_session
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
        delivered = stdin.read()
        assert delivered.startswith('For text reads, including SKILL.md and the manifest, use python3')
        assert '/work/.claude/skills/episode-production/scripts/read-text.py PATH' in delivered
        assert any(role in delivered for role in ('You are the whole-episode production Agent.',
                                                  'You are the single creative author',
                                                  'You are the same author compiling'))
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
    result = codex_session.run_session(work, 'Continue automatic review', config=codex_config,
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


def test_daemon_without_user_path_uses_installed_user_codex(tmp_path,monkeypatch,codex_config):
    binary=tmp_path/'.local/bin/codex';binary.parent.mkdir(parents=True);binary.write_text('binary');binary.chmod(0o755)
    monkeypatch.setattr(codex_session.shutil,'which',lambda name:None)
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    work=tmp_path/'episode';_,calls=cli(monkeypatch,work)
    codex_session.run_session(work,'Initial',config=codex_config)
    assert f'{binary}:/usr/local/bin/codex:ro' in calls[0]


def test_codex_resume_keeps_actual_thread_and_prior_candidates(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    identity, _ = cli(monkeypatch, work, outputs={'requests.json': 'retained'})
    first = codex_session.run_session(work, 'Initial', config=codex_config, enable_tools=True)
    _, calls = cli(monkeypatch, work, identity=identity)
    second = codex_session.run_session(work, 'Continue', config=codex_config, resume=True, enable_tools=True)
    assert calls[0][calls[0].index('resume')+1] == first.record['session_id']
    assert second.record['session_id'] == identity and second.produced == []
    assert (second.directory/'output-before/requests.json').read_text() == 'retained'
    assert (first.directory/'output-after/requests.json').read_text() == 'retained'


def test_native_creator_attaches_original_images_without_host_media_tools(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'creator'; (work/'input').mkdir(parents=True)
    (work/'input/portrait.png').write_bytes(b'original-reference')
    (work/'input/manifest.json').write_text(json.dumps({'phase':'creation_only',
        'asset_catalog':[{'path':'input/portrait.png'}]}))
    _, calls = cli(monkeypatch, work, outputs={'episode.json':'candidate'})
    result = codex_session.run_session(work, 'Write the full creative draft', config=codex_config,
                                      expected_output='episode.json', enable_tools=False)
    command = calls[0]
    assert command[command.index('--image')+1] == '/work/input/portrait.png'
    assert not any('/opt/novel-code' in part for part in command)
    assert result.record['creation_only'] is True
    assert result.record['host_media_tools_installed'] is False
    assert json.loads((result.directory/'attached-images.json').read_text())[0]['bytes'] == 18


def test_codex_cannot_reinterpret_claude_uuid_as_codex_thread(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'; work.mkdir()
    old = json.dumps({'session_id': str(uuid.uuid4())})
    (work/session.SESSION_FILE).write_text(old)
    with pytest.raises(SandboxRefused, match='previous provider'):
        codex_session.run_session(work, 'Continue', config=codex_config, resume=True)
    assert (work/session.SESSION_FILE).read_text() == old


def test_locked_script_preparation_is_file_only_and_preserves_native_session(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'preparation'; (work/'input').mkdir(parents=True)
    (work/'input/manifest.json').write_text(json.dumps({'phase':'script_preparation'}))
    identity, calls = cli(monkeypatch, work, outputs={'requests.json':'candidate'})
    (work/session.SESSION_FILE).write_text(json.dumps({'backend':'codex', 'session_id':identity}))
    result = codex_session.run_session(work, 'Compile approved screenplay', config=codex_config,
        resume=True, expected_output='requests.json', enable_tools=False)
    assert result.ok and result.record['phase'] == 'script_preparation'
    assert 'danger-full-access' in calls[0]
    assert not any('/opt/novel-code' in part for part in calls[0])
    assert 'same author compiling an approved screenplay' in (result.directory/'prompt.txt').read_text()


def test_codex_timeout_records_progress_without_claiming_new_output(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    identity, _ = cli(monkeypatch, work, exit_code=124, handoff=True)
    result = codex_session.run_session(work, 'Continue', config=codex_config, timeout=600, enable_tools=True)
    assert not result.ok and result.record['exit'] == 124 and result.produced == []
    assert result.record['timeout_seconds'] == 600
    assert result.record['session_id'] == identity
    assert completed_idle(str(result.directory)) is False


def test_codex_turn_failure_is_not_success_even_when_cli_returns_zero(tmp_path, monkeypatch, codex_config):
    work = tmp_path/'episode'
    cli(monkeypatch, work, failed=True)
    result = codex_session.run_session(work, 'Continue', config=codex_config)
    assert not result.ok and result.produced == []
    rows = [json.loads(line) for line in (result.directory/'session.jsonl').read_text().splitlines()]
    assert rows[-1]['is_error'] is True and rows[-1]['result'] == 'Login expired'

