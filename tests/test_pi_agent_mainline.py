from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'examples/pi-video-agent'


def load_starter():
    spec = importlib.util.spec_from_file_location('mainline_pi_starter', KIT / 'starter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_entrypoint_help_does_not_start_production():
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/pi_video_agent.py'), '--help'],
        cwd=ROOT, text=True, capture_output=True, check=False,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    assert result.returncode == 0, result.stderr
    assert 'init' in result.stdout and 'run' in result.stdout


def test_init_uses_formal_dotenv_loader_not_direct_secret_file_read(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    starter = load_starter()
    import novel_manga.util

    monkeypatch.setattr(novel_manga.util, 'load_dotenv', lambda path: None)
    original = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == '.env':
            raise AssertionError('dotenv must only be read by the formal loader')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', guarded)
    original_is_file = Path.is_file

    def present_dotenv(self):
        if self.name == '.env':
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, 'is_file', present_dotenv)
    (tmp_path / 'input').mkdir()
    workspace = starter.initialize(
        tmp_path / 'work', ROOT,
        '/mnt/disk1/zengzhitao/interactive-video-worktrees/pi-video-model-ab-20261027/.runtime/pi-full-episode-20261028/code/node_modules',
        tmp_path / 'input',
    )
    assert (workspace / 'CAPABILITIES.json').is_file()


def test_provenance_and_package_exclude_runtime_and_secrets():
    provenance = KIT / 'PROVENANCE.json'
    assert provenance.is_file()
    recorded = json.loads(provenance.read_text())
    assert recorded['source'].endswith('experiments/pi-agent-kit-v2-20261028')
    assert recorded['files']
    expected = {row['path'] for row in recorded['files']}
    actual = {str(path.relative_to(KIT)) for path in KIT.rglob('*')
              if path.is_file() and path.name != 'PROVENANCE.json'}
    assert expected == actual
    for row in recorded['files']:
        assert hashlib.sha256((KIT / row['path']).read_bytes()).hexdigest() == row['merged_sha256']
    forbidden = {'.env', '.pyc'}
    for path in KIT.rglob('*'):
        assert path.name not in forbidden
        assert '__pycache__' not in path.parts
        assert path.suffix.lower() not in {'.mp4', '.png', '.jpg', '.jpeg'}
        if path.is_file():
            assert not path.is_symlink()


def test_legacy_cli_is_not_used_for_new_pi_work(tmp_path):
    legacy = ROOT / 'scripts/interactive_video.py'
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/pi_video_agent.py'), 'init', str(tmp_path / 'new')],
        cwd=ROOT, text=True, capture_output=True, check=False,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    assert result.returncode != 0
    assert '--sdk-root' in result.stderr
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before
    assert not (tmp_path / 'new').exists()
