#!/usr/bin/env python3
"""Copy a modifiable starting kit, or launch the workspace's own Pi code."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def initialize(workspace, sdk_root, dependencies, inputs=None, skills=None):
    workspace, sdk_root = Path(workspace).resolve(), Path(sdk_root).resolve()
    # Init is deliberately separate from resume: never replace an Agent's edits.
    workspace.mkdir(parents=True, exist_ok=False)
    template = Path(__file__).resolve().parent / 'template'
    shutil.copytree(template, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (workspace / 'tools/node_modules').symlink_to(Path(dependencies).resolve(), target_is_directory=True)
    target = workspace / 'skills/episode-production'
    shutil.copytree(skills or sdk_root / 'skills/episode-production', target)
    (target / 'host-workflow-background.md').write_text((target / 'SKILL.md').read_text())
    (target / 'SKILL.md').write_text((workspace / 'SKILL.md').read_text())
    (workspace / 'SKILL.md').unlink()
    sys.path.insert(0, str(sdk_root / 'src'))
    from novel_manga.planning.methods import METHODS
    (target / 'creation-methods.json').write_text(json.dumps(
        [asdict(method) for method in METHODS.values()], ensure_ascii=False, indent=2) + '\n')
    if inputs:
        shutil.copytree(Path(inputs).resolve(), workspace / 'input')
    else:
        (workspace / 'input').mkdir()
    (workspace / 'output').mkdir()
    config = {'sdk_root': str(sdk_root), 'python': sys.executable,
              'context_window': 1048576, 'max_output_tokens': 128000}
    (workspace / 'local.json').write_text(json.dumps(config, indent=2) + '\n')
    shutil.copy2(Path(__file__), workspace / 'starter.py')
    (workspace / 'owner').write_text('Pi editable starter / user-owned experiment; no generation launched by init.\n')
    return workspace


def run(workspace, endpoint=None, resume=False, prompt=None):
    workspace = Path(workspace).resolve()
    config = json.loads((workspace / 'local.json').read_text())
    root = Path(config['sdk_root'])
    sys.path.insert(0, str(root / 'src'))
    from novel_manga.util import load_dotenv
    load_dotenv(root / '.env')
    env = dict(os.environ, PI_RUN_DIR=str(workspace), PI_PROJECT_ROOT=str(root))
    if endpoint:
        from novel_manga.llm.config import endpoint_settings
        selected = endpoint_settings(endpoint)
        env.update(PI_PROBE_MODEL=selected.model, PI_PROBE_BASE=selected.endpoints[0],
                   PI_PROBE_API_KEY=selected.key)
    for name in ('PI_PROBE_MODEL', 'PI_PROBE_BASE', 'PI_PROBE_API_KEY'):
        if not env.get(name):
            raise ValueError(f'Set {name}, or choose an existing --endpoint.')
    # Execute this workspace's copy. No template code is imported on resume.
    cmd = ['bun', str(workspace / 'tools/agent.mjs')]
    if resume:
        cmd.append('--resume')
    if prompt:
        cmd += ['--prompt', prompt]
    return subprocess.call(cmd, cwd=workspace, env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('workspace', type=Path)
    init.add_argument('--sdk-root', type=Path, required=True)
    init.add_argument('--dependencies', type=Path, required=True, help='Existing Pi node_modules; not copied or installed')
    init.add_argument('--input', type=Path)
    init.add_argument('--skills', type=Path, help='Complete Skill directory; defaults to SDK root skills/episode-production')
    launch = sub.add_parser('run')
    launch.add_argument('workspace', type=Path)
    launch.add_argument('--endpoint')
    launch.add_argument('--resume', action='store_true')
    launch.add_argument('--prompt', help='Optional instruction for this turn; not a new production mode')
    args = parser.parse_args()
    if args.command == 'init':
        print(initialize(args.workspace, args.sdk_root, args.dependencies, args.input, args.skills))
    else:
        raise SystemExit(run(args.workspace, args.endpoint, args.resume, args.prompt))


if __name__ == '__main__':
    main()
