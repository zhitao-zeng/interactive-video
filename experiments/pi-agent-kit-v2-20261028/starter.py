#!/usr/bin/env python3
"""Copy a modifiable starting kit, or launch the workspace's own Pi code."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _seed_assets(workspace, cache):
    """Copy recurring character files a new episode input may omit; never overwrite an episode's own."""
    cache = Path(cache).resolve()
    if not cache.is_dir():
        return []
    seeded = []
    for source in sorted(cache.iterdir()):
        if not source.is_file() or source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.json'}:
            continue
        target = workspace / 'input' / source.name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        seeded.append(source.name)
    return seeded


def _asset_publish_dir(sdk_root):
    """The directory the public asset base actually serves; the SDK worktree may predate this config."""
    configured = os.environ.get('NOVEL_ASSET_PUBLISH_DIR', '').strip()
    if configured:
        return configured
    def find(node):
        if isinstance(node, dict):
            if node.get('asset_publish_dir'):
                return str(node['asset_publish_dir'])
            for value in node.values():
                found = find(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return ''
    candidates = [Path(sdk_root) / 'configs/pipeline.json',
                  Path('/mnt/disk1/zengzhitao/novel/novel-manga-video/configs/pipeline.json')]
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        found = find(data.get('resources') or {})
        if found:
            return found
    return ''


def _expose_previous(workspace, previous):
    """Link a same-episode earlier attempt read-only and index its media for deliberate reuse."""
    previous = Path(previous).resolve()
    if not previous.is_dir():
        raise ValueError(f'previous attempt is not a directory: {previous}')
    target = workspace / 'input' / 'previous'
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.symlink_to(previous, target_is_directory=True)
    media = sorted(
        (p for pattern in ('*.mp4', '*.jpg', '*.jpeg', '*.png')
         for p in previous.glob('media/**/' + pattern)))
    rows = ['relative_path\tbytes\tmodified']
    for path in media:
        stat = path.stat()
        rows.append(f'{path.relative_to(previous)}\t{stat.st_size}\t'
                    f'{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}')
    (workspace / 'input' / 'PREVIOUS-MEDIA.tsv').write_text('\n'.join(rows) + '\n')
    script = previous / 'output' / 'script.md'
    lines = [
        '# 同一集上一次尝试（可复用，需逐条核验）',
        '',
        f'- 来源：{previous}',
        f'- 分镜/工作记录：{"input/previous/output/script.md" if script.is_file() else "未找到 output/script.md"}',
        f'- 媒体索引：input/PREVIOUS-MEDIA.tsv（{len(media)} 个文件）',
        '',
        '只复用于同一集：逐条看 contact sheet、读实际音频，确认人物/服装/场景与本次参考一致、',
        '没有影响观看的问题。采用时在 output/script.md 记录来源。不同集/不同 W 的媒体不要用。',
        '复用不等于免检；原文件只读，不要覆盖。',
    ]
    (workspace / 'input' / 'PREVIOUS.md').write_text('\n'.join(lines) + '\n')
    return len(media)


def initialize(workspace, sdk_root, dependencies, inputs=None, skills=None, previous=None, asset_cache=None,
               lane=None, first_frame='selective', interaction_page='required'):
    workspace, sdk_root = Path(workspace).resolve(), Path(sdk_root).resolve()
    # Init is deliberately separate from resume: never replace an Agent's edits.
    workspace.mkdir(parents=True, exist_ok=False)
    template = Path(__file__).resolve().parent / 'template'
    shutil.copytree(template, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (workspace / 'tools/node_modules').symlink_to(Path(dependencies).resolve(), target_is_directory=True)
    target = workspace / 'skills/episode-production'
    shutil.copytree(skills or sdk_root / 'skills/episode-production', target)
    (target / 'host-workflow-background.md').write_text((target / 'SKILL.md').read_text())
    extra = template / 'skill-extra'
    if extra.is_dir():
        shutil.copytree(extra, target, dirs_exist_ok=True)
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
    if asset_cache is not None:
        _seed_assets(workspace, asset_cache)
    if previous:
        _expose_previous(workspace, previous)
    (workspace / 'output').mkdir()
    audio = template / 'audio-runtime.example.json'
    if audio.is_file():
        shutil.copy2(audio, workspace / 'audio-runtime.json')
    config = {'sdk_root': str(sdk_root), 'python': sys.executable,
              'context_window': 1048576, 'max_output_tokens': 128000,
              'audio_service_url': os.environ.get('AUDIO_SERVICE_URL', ''),
              'novel_asset_publish_dir': _asset_publish_dir(sdk_root),
              'media_lane': lane or '',
              'first_frame_policy': first_frame,
              'interaction_page': interaction_page,
              'max_request_bytes': 10485760, 'keep_recent_images': 4}
    (workspace / 'local.json').write_text(json.dumps(config, indent=2) + '\n')
    (workspace / 'PRODUCTION.md').write_text(
        '# 生产选项（启动时由用户选择）\n\n'
        f'- 视频路线：{lane or "未锁定（agent 可自行选择）"}\n'
        f'- 首帧策略：{first_frame}\n'
        f'- 片尾选项页：{interaction_page}\n\n'
        '这些是用户选项，不由 agent 自行切换；`tools/media.py` 会拒绝与路线/首帧策略不符的请求。\n'
        '- `none`：不允许首帧；`selective`：由 agent 决定哪些镜头需要首帧；`required`：每个视频请求都必须带首帧。\n'
        '- 路线 `sd2.0-fast` 只接受 `action=video`；路线 `MiniMax-H3` 只接受 `action=h3`。\n'
        '- `interaction_page=required`：成片最后必须用 `tools/interaction.py` 追加一个正好 3 秒的 A/B/C 选项页，'
        '并保持到最后一帧；`none`：不做独立选项页。\n'
    )
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
    # The provider publishes reference copies under NOVEL_ASSET_PUBLISH_DIR, which must be the
    # directory the public base actually serves; keep it with the run so a resume cannot drift.
    if config.get('novel_asset_publish_dir') and not env.get('NOVEL_ASSET_PUBLISH_DIR'):
        env['NOVEL_ASSET_PUBLISH_DIR'] = config['novel_asset_publish_dir']
    if endpoint:
        from novel_manga.llm.config import endpoint_settings
        selected = endpoint_settings(endpoint)
        env.update(PI_PROBE_MODEL=selected.model, PI_PROBE_BASE=selected.endpoints[0],
                   PI_PROBE_API_KEY=selected.key)
    for name in ('PI_PROBE_MODEL', 'PI_PROBE_BASE', 'PI_PROBE_API_KEY'):
        if not env.get(name):
            raise ValueError(f'Set {name}, or choose an existing --endpoint.')
    # The session's outbound proxy is for Codex/GitHub, not the PhanRouter endpoint; the kit's
    # launcher strips it for the child, and this editor's run does the same.
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(name, None)
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
    init.add_argument('--previous', type=Path,
                      help='Same-episode earlier attempt; linked read-only under input/previous with a media index')
    init.add_argument('--asset-cache', type=Path,
                      help='Series-specific recurring assets to seed into input/ when absent; no default. '
                           'For the zhiyao series pass series-assets/zhiyao')
    init.add_argument('--lane', choices=('sd2.0-fast', 'MiniMax-H3'),
                      help='Pinned video lane from the start; media.py rejects the other lane')
    init.add_argument('--first-frame', choices=('none', 'selective', 'required'), default='selective',
                      help='First-frame policy: none, agent-selective, or required for every video request')
    init.add_argument('--interaction-page', choices=('required', 'none'), default='required',
                      help='Append the fixed 3-second A/B/C page at the end (required for this series by default)')
    launch = sub.add_parser('run')
    launch.add_argument('workspace', type=Path)
    launch.add_argument('--endpoint')
    launch.add_argument('--resume', action='store_true')
    launch.add_argument('--prompt', help='Optional instruction for this turn; not a new production mode')
    args = parser.parse_args()
    if args.command == 'init':
        print(initialize(args.workspace, args.sdk_root, args.dependencies, args.input, args.skills,
                         previous=args.previous, asset_cache=args.asset_cache,
                         lane=args.lane, first_frame=args.first_frame,
                         interaction_page=args.interaction_page))
    else:
        raise SystemExit(run(args.workspace, args.endpoint, args.resume, args.prompt))


if __name__ == '__main__':
    main()
