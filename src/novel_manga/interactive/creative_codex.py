"""Run the same episode tools with Codex's saved login inside the existing sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from novel_manga.application.agents.sandbox import Attempt, SandboxRefused, unique_attempt
from novel_manga.util import atomic_write_json, read_json

SKILL_SOURCE = Path(__file__).resolve().parents[3] / 'skills/episode-production'
SESSION_FILE = 'episode-session.json'


def normalized_events(events):
    """Keep native events as evidence and expose existing recovery's text/handoff contract."""
    last_text = ''
    for event in events:
        kind = event.get('type')
        if kind == 'thread.started':
            yield {'type': 'system', 'subtype': 'init', 'session_id': event['thread_id']}
        elif kind == 'item.completed':
            item = event.get('item') or {}
            if item.get('type') == 'agent_message':
                last_text = str(item.get('text') or '')
                yield {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': last_text}]}}
            elif item.get('type') == 'command_execution':
                yield {'type': 'user', 'message': {'content': [{'type': 'tool_result',
                    'is_error': item.get('exit_code', 0) != 0,
                    'content': str(item.get('aggregated_output') or '')}]}}
        elif kind in {'turn.completed', 'turn.failed'}:
            failed = kind == 'turn.failed'
            error = event.get('error') or {}
            yield {'type': 'result', 'subtype': 'error' if failed else 'success',
                'is_error': failed, 'result': str(error.get('message') or last_text)}


def run_session(work_dir, prompt_text, *, resume=False, timeout=1200, config,
                expected_output=None, enable_tools=False, project_root=None):
    from novel_manga.llm.policy import session_policy, session_role
    config = session_policy(config, work_dir, resume=resume,
                            role=session_role(work_dir, config, expected_output))
    if not prompt_text.strip() or timeout <= 0:
        raise SandboxRefused('Codex needs a nonempty episode prompt and positive timeout.')
    expected_output = expected_output or ('repair.json' if resume and not enable_tools else 'episode.json')
    if expected_output not in {'episode.json', 'requests.json', 'repair.json'}:
        raise SandboxRefused('Expected output must be episode.json, requests.json or repair.json.')
    binary = shutil.which('codex')
    if not binary:
        raise SandboxRefused('Codex CLI is not installed.')
    binary = Path(binary).resolve()
    auth_home = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').resolve()
    if not auth_home.is_dir():
        raise SandboxRefused('No saved Codex login directory; run codex login first.')
    work = Path(work_dir).resolve()
    state_path = work/SESSION_FILE
    state = read_json(state_path, {})
    if resume:
        if state.get('backend') != 'codex':
            raise SandboxRefused('Archive the previous provider session through normal retry before using Codex.')
        try:
            uuid.UUID(state.get('session_id', ''))
        except (ValueError, TypeError, AttributeError) as error:
            raise SandboxRefused('No valid saved Codex episode session to resume.') from error
    elif state_path.exists():
        raise SandboxRefused('This episode already has a session; use resume=True.')
    skill = work/'.claude/skills/episode-production'
    if skill.parent.exists() and any(p.name != 'episode-production' for p in skill.parent.iterdir()):
        raise SandboxRefused('This workspace contains other skills; use a dedicated episode workspace.')
    output = work/'output'
    output.mkdir(parents=True, exist_ok=True)
    manifest = read_json(work/'input/manifest.json', {})
    creation_only = manifest.get('phase') == 'creation_only'
    script_preparation = manifest.get('phase') == 'script_preparation'
    attempt = unique_attempt(work/'attempts', f'{time.strftime("%Y%m%d-%H%M%S")}-episode')
    if skill.exists():
        shutil.copytree(skill, attempt/'skill-before')
    shutil.copytree(SKILL_SOURCE, skill, dirs_exist_ok=True)
    shutil.copytree(skill, attempt/'skill-current')
    role = ('You are the whole-episode production Agent. Read '
        '/work/.claude/skills/episode-production/SKILL.md and only the relevant references. '
        'Use /work/tools/episode.py for host workflow actions. After yield=true, end this turn '
        'so the host can execute and resume this same session. Only output/ is for authored '
        'candidates; input/ contains host evidence. Source code under /opt/novel-code is read-only. '
        'The host owns generation limits and independent acceptance. Do not edit host verdicts '
        'or history, install software, or modify the saved Codex login.\n\n')
    if creation_only:
        role = ('You are the single creative author in an isolated text-only workspace. '
            'Read /work/.claude/skills/episode-production/SKILL.md, the creation-only contract '
            'and required original sources on the first turn. On resume, retain that context, '
            'read current feedback and refreshed guidance, and verify relevant source passages. '
            'Use file, image and local shell operations only '
            'for reading supplied material and writing the complete creative draft under output/. '
            'No host workflow or media-generation tools are installed. Do not invoke models, '
            'media services, rendering, production adoption, or modify inputs/history/authentication. '
            'Use explicit literal file paths in read commands so the host can trace resource use. '
            'Prefer available Python3 for structured JSON edits; use simple shell file writers '
            'if a runtime or apply_patch is absent, without relying on optional Perl/Node modules. '
            'Save substantial edit scripts under output before running them so a syntax repair '
            'can continue the saved work instead of retyping the complete draft. '
            'the host validates JSON after the turn, so installing tools is unnecessary. '
            'Original reference images are attached on the first turn.\n\n')
    elif script_preparation:
        role = ('You are the same author compiling an approved screenplay into final H3 requests. '
            'Read /work/.claude/skills/episode-production/SKILL.md and the script-mode output contract. '
            'Keep the approved shots, dialogue, ownership, camera, groups, timing and supplied references. '
            'Read supplied files and inspect the actual reference images using local file/image tools. '
            'Write output/requests.json only, plus an asset-binding explanation when requested. '
            'No host workflow or media-generation tools are installed. Do not invoke models, media '
            'services, production adoption, or modify inputs, history or authentication. '
            'The host composes and checks the final syntax and executes the complete pipeline.\n\n')
    (attempt/'prompt.txt').write_text(role+prompt_text, encoding='utf-8')
    before = {p.relative_to(output): (p.stat().st_mtime_ns, p.stat().st_size)
              for p in output.rglob('*') if p.is_file()}
    shutil.copytree(output, attempt/'output-before')
    command = ['docker', 'run', '--rm', '-i', '--network', 'host',
        '--name', f'episode-codex-{uuid.uuid4()}', '--user', '1001:1001',
        '-e', f'HOME={Path.home()}', '-v', f'{auth_home}:{auth_home}',
        '-v', f'{binary}:/usr/local/bin/codex:ro', '-v', f'{work}:/work', '-w', '/work']
    environment = dict(os.environ)
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'CODEX_HOME'):
        if environment.get(name):
            command.extend(['-e', name])
    if enable_tools:
        command.extend(['-v', f'{SKILL_SOURCE.parents[1]/"src"}:/opt/novel-code:ro'])
    command.extend([config['image'], 'timeout', str(timeout), 'codex', 'exec',
        '--ignore-user-config', '--skip-git-repo-check', '--json',
        '--sandbox', 'danger-full-access' if enable_tools or creation_only or script_preparation else 'workspace-write',
        '-c', 'approval_policy="never"', '-c', f'model_reasoning_effort={json.dumps(config.get("effort", "medium"))}',
        '--model', config['model']])
    attached = []
    if creation_only and not resume:
        for row in manifest.get('asset_catalog', []):
            command.extend(['--image', '/work/'+row['path']])
            attached.append({'path':row['path'], 'bytes':(work/row['path']).stat().st_size})
    atomic_write_json(attempt/'attached-images.json', attached)
    if resume:
        command.extend(['resume', state['session_id']])
    command.append('-')
    started = time.monotonic()
    with (attempt/'codex-events.jsonl').open('w') as stream, (attempt/'stderr.log').open('w') as errors, \
            (attempt/'prompt.txt').open() as prompt:
        try:
            code = subprocess.call(command, stdin=prompt, stdout=stream, stderr=errors, env=environment)
        except OSError as error:
            errors.write(str(error)+'\n')
            code = 127
    events = []
    for line in (attempt/'codex-events.jsonl').read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    normalized = list(normalized_events(events))
    with (attempt/'session.jsonl').open('w') as stream:
        for event in normalized:
            stream.write(json.dumps(event, ensure_ascii=False)+'\n')
    thread = next((e['thread_id'] for e in events if e.get('type') == 'thread.started'), None)
    if thread:
        if resume and thread != state['session_id']:
            raise SandboxRefused('Codex resumed a different episode session.')
        state.update(backend='codex', session_id=thread, current_task_policy=1, last_attempt=attempt.name)
        atomic_write_json(state_path, state)
    elif code == 0:
        code = 1
    files = [p for p in output.rglob('*') if p.is_file()]
    produced = sorted(str(p.relative_to(output)) for p in files
        if before.get(p.relative_to(output)) != (p.stat().st_mtime_ns, p.stat().st_size)
        or (attempt/'output-before'/p.relative_to(output)).read_bytes() != p.read_bytes())
    shutil.copytree(output, attempt/'output-after')
    completion = next((e for e in reversed(normalized) if e.get('type') == 'result'), {})
    native_completion = next((e for e in reversed(events) if e.get('type') == 'turn.completed'), {})
    if completion.get('is_error') and code == 0:
        code = 1
    record = {'run': work.name, 'attempt': attempt.name, 'session_id': thread or state.get('session_id'),
        'backend': 'codex', 'auth': 'saved_codex_login', 'model': config['model'], 'image': config['image'],
        'resume': resume, 'enable_tools': enable_tools, 'skills': 'episode-production',
        'creation_only': creation_only, 'host_media_tools_installed': bool(enable_tools),
        'phase': manifest.get('phase', 'production'),
        'usage': native_completion.get('usage', {}),
        'timeout_seconds': timeout, 'seconds': round(time.monotonic()-started), 'exit': code,
        'produced': produced, 'kept_from_earlier_attempts': len(files)-len(produced),
        'expected_output': expected_output, 'expected_output_produced': expected_output in produced,
        'turn_limit_reached': False, 'summary_only': False, 'refusal': ''}
    atomic_write_json(attempt/'attempt.json', record)
    return Attempt(run=work.name, directory=attempt, exit_code=code, seconds=record['seconds'],
        produced=produced, kept=record['kept_from_earlier_attempts'], record=record)
