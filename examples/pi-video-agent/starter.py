#!/usr/bin/env python3
"""Copy a modifiable starting kit, or launch the workspace's own Pi code."""
import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


VIEWPOINTS = {
    'first-person': '全程第一视角：当前任务全程使用同一主角眼位；手、袖口和局部自身身体可从画面边缘入镜，'
                    '其他角色可以露脸。不能拍成自拍或外部机位看主角；可切镜，不要求一镜到底。',
    'mixed': '混合视角：按戏混用主观与客观视角，不规定比例；在主观镜头中明确谁在看。',
    'auto': '导演自定：由导演按戏选择，不额外限定视角。',
}

LANES = ('sd2.0-fast', 'sd2.0-mini', 'MiniMax-H3', 'FastH3-Omni')
FIRST_FRAME_POLICIES = ('none', 'selective', 'required')
INTERACTION_PAGE_POLICIES = ('required', 'none')
H3_MODES = ('local', 'cloud')


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
    candidates = [Path(sdk_root) / 'configs/pipeline.json']
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        found = find(data.get('resources') or {})
        if found:
            return found
    return ''


def _atomic_write_json(path, data):
    """Write JSON atomically so concurrent readers never see a partial file."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


@contextlib.contextmanager
def _batch_lock(manifest):
    """Exclusive advisory lock for batches sharing the same manifest directory."""
    lock_path = manifest.with_name('batch-state.lock')
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise ValueError(f'Another batch is already running in {manifest.parent}') from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _expose_previous(workspace, previous):
    """Copy a same-episode earlier attempt so the new Agent cannot edit the original."""
    previous = Path(previous).resolve()
    if not previous.is_dir():
        raise ValueError(f'previous attempt is not a directory: {previous}')
    target = workspace / 'input' / 'previous'
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    shutil.copytree(previous, target)
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
        '复用不等于免检；当前为工作区私有副本，修改副本不得影响原目录。',
    ]
    (workspace / 'input' / 'PREVIOUS.md').write_text('\n'.join(lines) + '\n')
    return len(media)


def initialize(workspace, sdk_root, dependencies, inputs=None, skills=None, previous=None, asset_cache=None,
               lane=None, first_frame='selective', interaction_page='required', h3_mode='local',
               wall_seconds=14400, max_model_requests=256, viewpoint='auto', fasth3_endpoint=None):
    workspace, sdk_root = Path(workspace).resolve(), Path(sdk_root).resolve()
    viewpoint_instruction = VIEWPOINTS[viewpoint]
    sys.path.insert(0, str(sdk_root / 'src'))
    from novel_manga.providers.fast_h3 import normalize_endpoint
    if fasth3_endpoint is not None:
        fasth3_endpoint = normalize_endpoint(fasth3_endpoint)
    if lane == 'FastH3-Omni' and not (fasth3_endpoint or '').strip():
        raise ValueError('FastH3-Omni requires an explicit --fasth3-endpoint; no machine default is allowed')
    if lane == 'FastH3-Omni' and first_frame == 'required':
        raise ValueError('first_frame_policy=required is incompatible with FastH3-Omni; only selective or none is allowed')
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
    from novel_manga.util import load_dotenv
    load_dotenv(sdk_root / '.env')
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
    asr_python=os.getenv('PI_ASR_PYTHON','/mnt/disk1/zengzhitao/asr_models_eval/.venv/bin/python')
    asr_model=os.getenv('PI_ASR_MODEL_DIR','/mnt/disk1/zengzhitao/asr_models_eval/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17')
    config = {'sdk_root': str(sdk_root), 'python': sys.executable,
              'dependencies': str(Path(dependencies).resolve()),
              'asr_python': asr_python, 'asr_model_dir': asr_model,
              'context_window': 1048576, 'max_output_tokens': 128000,
              'wall_seconds': wall_seconds, 'max_model_requests': max_model_requests,
              'audio_service_url': os.environ.get('AUDIO_SERVICE_URL', ''),
              'novel_asset_publish_dir': _asset_publish_dir(sdk_root),
              'media_lane': lane or '',
              'fasth3_endpoint': (fasth3_endpoint or '').rstrip('/'),
              'first_frame_policy': first_frame,
              'interaction_page': interaction_page,
              'h3_mode': h3_mode, 'viewpoint': viewpoint,
              'max_request_bytes': 10485760, 'keep_recent_images': 4}
    (workspace / 'local.json').write_text(json.dumps(config, indent=2) + '\n')
    (workspace / 'PRODUCTION.md').write_text(
        '# 生产选项（启动时由用户选择）\n\n'
        f'- 视频路线：{lane or "未锁定（agent 可自行选择）"}\n'
        f'- 首帧策略：{first_frame}\n'
        f'- 片尾选项页：{interaction_page}\n'
        f'- H3 模式：{h3_mode}\n\n'
        '路线、首帧、片尾选项页和 H3 模式是用户选项，不由 agent 自行切换；'
        '`tools/media.py` 会拒绝与路线/首帧策略不符的请求。\n'
        '- `none`：不允许首帧；`selective`：由 agent 决定哪些镜头需要首帧；`required`：每个视频请求都必须带首帧。\n'
        '- 路线 `sd2.0-fast`、`sd2.0-mini` 只接受 `action=video`；路线 `MiniMax-H3` 只接受 `action=h3`；'
        '路线 `FastH3-Omni` 只接受 `action=fasth3`。\n'
        '- `FastH3-Omni` 只使用普通 reference，不支持显式 first_frame；required 与其不兼容，selective 不等于 keyframe 已验证。\n'
        '- `sd2.0-fast` 是 720P；`sd2.0-mini` 是约 480P（样片 864×496，竖屏约 496×864）。\n'
        '- `interaction_page=required`：成片最后必须用 `tools/interaction.py` 追加一个正好 3 秒的 A/B/C 选项页，'
        '并保持到最后一帧；`none`：不做独立选项页。\n'
        '- `h3_mode=local`：H3 走本地池；`h3_mode=cloud`：H3 走 PhanRouter 云端（768×1344/24fps，共享图片 key 配额）。\n'
        f'\n## 视角选择：{viewpoint}\n\n{viewpoint_instruction}\n\n'
        '视角是交给创作 Agent 的拍法要求，遵循人类 TASK 和后续明确要求；启动选项不覆盖它们。\n'
    )
    capabilities={'python':sys.executable,'sdk_root':str(sdk_root),
        'image_model':'gpt-image-2','video_models':['sd2.0-fast','sd2.0-mini','MiniMax-H3'],
        'h3_endpoint':'pool:'+str(sdk_root/'configs/h3_pool.json'),
        'asset_publish_dir':config['novel_asset_publish_dir'],
        'credentials_configured':{name:bool(os.getenv(name))
            for name in ['PHANROUTER_IMAGE_API_KEY','PHANTHY_API_KEY']},
        'asr':{'python':asr_python,'model_dir':asr_model,
               'available':Path(asr_python).is_file()and Path(asr_model,'model.int8.onnx').is_file()},
        'audio_runtime':'audio-runtime.json','audio_service_config':'local.json.audio_service_url',
        'agent_budget':{'wall_seconds':wall_seconds,'provider_requests':max_model_requests,'max_output_tokens':128000}}
    if fasth3_endpoint:
        capabilities['fasth3'] = {'model': 'FastH3-Omni', 'endpoint': config['fasth3_endpoint'],
                                  'restart_clears_jobs_and_content': True}
    (workspace/'CAPABILITIES.json').write_text(json.dumps(capabilities,ensure_ascii=False,indent=2)+'\n')
    shutil.copy2(Path(__file__), workspace / 'starter.py')
    (workspace / 'owner').write_text('Pi editable starter / user-owned experiment; no generation launched by init.\n')
    return workspace


def run(workspace, endpoint=None, resume=False, prompt=None):
    workspace = Path(workspace).resolve()
    config = json.loads((workspace / 'local.json').read_text())
    task_text = (workspace / 'TASK.md').read_text(encoding='utf-8').strip()
    if not task_text or task_text.startswith('# 待填写的制作任务') or '尚未授权付费生成' in task_text:
        raise ValueError('Fill the workspace TASK.md with the authorized task before run.')
    for name in ('wall_seconds', 'max_model_requests'):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise ValueError(f'local.json {name} must be a positive integer frozen at init')
    root = Path(config['sdk_root'])
    sys.path.insert(0, str(root / 'src'))
    from novel_manga.util import load_dotenv
    load_dotenv(root / '.env')
    env = dict(os.environ, PI_RUN_DIR=str(workspace), PI_PROJECT_ROOT=str(root))
    # The provider publishes reference copies under NOVEL_ASSET_PUBLISH_DIR, which must be the
    # directory the public base actually serves; keep it with the run so a resume cannot drift.
    if config.get('novel_asset_publish_dir'):
        env['NOVEL_ASSET_PUBLISH_DIR'] = config['novel_asset_publish_dir']
    previous_budget = workspace / 'run-budget.json'
    deadline = json.loads(previous_budget.read_text())['deadline_at'] / 1000 if previous_budget.exists() else time.time() + config.get('wall_seconds',14400)
    env['PI_DEADLINE_EPOCH'] = str(deadline)
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


def _positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer, got {value!r}')
    return value


def run_batch(manifest):
    """Validate the whole manifest under a directory lock, init sequentially, then run in parallel.

    The manifest is fully validated before any `initialize`/`run` call and before the shared
    directory lock is released. `workers` defaults to the episode count and must be a positive
    integer. Workspace paths must not overlap (siblings are allowed). Resume must be a real
    boolean. All init options are checked without side effects.

    Each episode keeps its own workspace, session and budgets. A failure in one episode does not
    block or cancel the others. The return value records process completion only; `ok` does not
    mean content was accepted.

    After each episode finishes (including init failures), its workspace, returncode and
    completion timestamp are written atomically to `batch-state.json` next to the manifest.
    State-write failures are treated as batch infrastructure errors and are not converted into
    a fake accepted/complete result.

    The coordinator reviews, recovers or uploads each episode separately; the batch stage does
    not schedule content or fake an accepted status.
    """
    manifest = Path(manifest).resolve()
    data = json.loads(manifest.read_text(encoding='utf-8'))
    episodes = data.get('episodes')
    if not isinstance(episodes, list) or not episodes:
        raise ValueError('batch manifest needs a nonempty episodes list')
    workers = data.get('workers', len(episodes))
    if type(workers) is not int or workers <= 0:
        raise ValueError('workers must be a positive integer')

    # Make the SDK source importable for FastH3 endpoint normalization during pre-check.
    # If the manifest omits a top-level sdk_root, we defer the import until an episode
    # supplies one; episodes without any sdk_root cannot select FastH3-Omni.
    import_sdk_root = data.get('sdk_root')
    if not import_sdk_root:
        for item in episodes:
            if item.get('sdk_root'):
                import_sdk_root = item['sdk_root']
                break
    normalize_endpoint = None
    if import_sdk_root:
        sys.path.insert(0, str(Path(import_sdk_root) / 'src'))
        from novel_manga.providers.fast_h3 import normalize_endpoint as _normalize_endpoint
        normalize_endpoint = _normalize_endpoint

    # Validate the whole manifest before any init, run or state file creation.
    seen = []
    validated = []
    for idx, item in enumerate(episodes):
        if not isinstance(item, dict):
            raise ValueError(f'episode {idx} must be an object')

        raw_resume = item.get('resume')
        if raw_resume is not None and not isinstance(raw_resume, bool):
            raise ValueError(f'episode {idx} resume must be a boolean')
        resume = bool(raw_resume)

        ws_path = item.get('workspace')
        if not ws_path:
            raise ValueError(f'episode {idx} missing workspace')
        workspace = Path(ws_path).resolve()
        for seen_ws in seen:
            if workspace == seen_ws or workspace in seen_ws.parents or seen_ws in workspace.parents:
                raise ValueError(f'episode workspaces overlap: {workspace} and {seen_ws}')
        seen.append(workspace)

        if workspace.exists():
            if not resume:
                raise ValueError(f'Existing episode workspace requires resume=true: {workspace}')
        else:
            if resume:
                raise ValueError(f'Cannot resume a missing episode workspace: {workspace}')

        ep_sdk_root = item.get('sdk_root') or data.get('sdk_root')
        ep_dependencies = item.get('dependencies') or data.get('dependencies')
        if not resume:
            if not ep_sdk_root:
                raise ValueError(f'episode {idx} missing sdk_root')
            if not ep_dependencies:
                raise ValueError(f'episode {idx} missing dependencies')

        lane = item.get('lane')
        if lane is not None and lane not in LANES:
            raise ValueError(f'episode {idx} invalid lane {lane}')

        first_frame = item.get('first_frame', 'selective')
        if first_frame not in FIRST_FRAME_POLICIES:
            raise ValueError(f'episode {idx} invalid first_frame {first_frame}')

        interaction_page = item.get('interaction_page', 'required')
        if interaction_page not in INTERACTION_PAGE_POLICIES:
            raise ValueError(f'episode {idx} invalid interaction_page {interaction_page}')

        h3_mode = item.get('h3_mode', 'local')
        if h3_mode not in H3_MODES:
            raise ValueError(f'episode {idx} invalid h3_mode {h3_mode}')

        viewpoint = item.get('viewpoint', data.get('viewpoint', 'auto'))
        if viewpoint not in VIEWPOINTS:
            raise ValueError(f'episode {idx} invalid viewpoint {viewpoint}')

        wall_seconds = item.get('wall_seconds', data.get('wall_seconds', 14400))
        _positive_int(wall_seconds, f'episode {idx} wall_seconds')

        max_model_requests = item.get('max_model_requests', data.get('max_model_requests', 256))
        _positive_int(max_model_requests, f'episode {idx} max_model_requests')

        fasth3_endpoint = item.get('fasth3_endpoint', data.get('fasth3_endpoint'))
        if lane == 'FastH3-Omni' or fasth3_endpoint is not None:
            if not fasth3_endpoint:
                raise ValueError(f'episode {idx} FastH3-Omni requires an explicit fasth3_endpoint')
            if normalize_endpoint is None:
                raise ValueError(f'episode {idx} sdk_root required to validate fasth3_endpoint')
            fasth3_endpoint = normalize_endpoint(fasth3_endpoint)
        if lane == 'FastH3-Omni' and first_frame == 'required':
            raise ValueError('first_frame_policy=required is incompatible with FastH3-Omni')

        init_kwargs = {
            'sdk_root': ep_sdk_root,
            'dependencies': ep_dependencies,
            'inputs': item.get('input'),
            'skills': item.get('skills'),
            'previous': item.get('previous'),
            'asset_cache': item.get('asset_cache'),
            'lane': lane,
            'first_frame': first_frame,
            'interaction_page': interaction_page,
            'h3_mode': h3_mode,
            'wall_seconds': wall_seconds,
            'max_model_requests': max_model_requests,
            'viewpoint': viewpoint,
            'fasth3_endpoint': fasth3_endpoint,
        }
        validated.append({'item': item, 'workspace': workspace, 'resume': resume,
                          'index': idx, 'init_kwargs': init_kwargs})

    with _batch_lock(manifest):
        state_path = manifest.with_name('batch-state.json')
        state = {
            'manifest': str(manifest),
            'workers': workers,
            'episodes': [
                {'workspace': str(v['workspace']), 'returncode': None, 'completed_at': None}
                for v in validated
            ],
        }
        _atomic_write_json(state_path, state)

        rows = [None] * len(episodes)
        state_lock = threading.Lock()

        def mark_completed(index, returncode, error=None):
            completed_at = datetime.now(timezone.utc).isoformat()
            row = rows[index] or {'workspace': str(validated[index]['workspace'])}
            row['returncode'] = returncode
            row['completed_at'] = completed_at
            if error is not None:
                row['error'] = error
            rows[index] = row
            state['episodes'][index] = {
                'workspace': str(validated[index]['workspace']),
                'returncode': returncode,
                'completed_at': completed_at,
            }
            if error is not None:
                state['episodes'][index]['error'] = error
            with state_lock:
                _atomic_write_json(state_path, state)

        # Initialize sequentially; each workspace is created in manifest order.
        for v in validated:
            idx, workspace, resume = v['index'], v['workspace'], v['resume']
            row = {'workspace': str(workspace)}
            if not resume:
                try:
                    initialize(workspace, **v['init_kwargs'])
                except Exception as error:
                    error_text = f'{type(error).__name__}: {error}'
                    mark_completed(idx, 2, error_text)
                    continue
            rows[idx] = row

        # Run the remaining episodes in parallel, keeping each session independent.
        def run_one(v):
            idx, item, workspace, resume = v['index'], v['item'], v['workspace'], v['resume']
            row = rows[idx]
            try:
                rc = run(workspace, endpoint=item.get('endpoint'),
                         resume=resume, prompt=item.get('prompt'))
            except Exception as error:
                rc = 2
                row['error'] = f'{type(error).__name__}: {error}'
            mark_completed(idx, rc, row.get('error'))
            return row

        pending = [v for v in validated
                   if rows[v['index']] is not None and 'returncode' not in rows[v['index']]]
        if pending:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(run_one, pending))

        result_rows = []
        for idx in range(len(episodes)):
            row = rows[idx] or {'workspace': str(validated[idx]['workspace'])}
            if 'returncode' not in row:
                row['returncode'] = 2
                row['error'] = 'episode did not run'
            result_rows.append(row)
        return {'episodes': result_rows, 'ok': all(row['returncode'] == 0 for row in result_rows), 'workers': workers}


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
                      help='Same-episode earlier attempt; privately copied under input/previous with a media index')
    init.add_argument('--asset-cache', type=Path,
                      help='Series-specific recurring assets to seed into input/ when absent; no default. '
                           'For the zhiyao series pass series-assets/zhiyao')
    init.add_argument('--lane', choices=LANES,
                      help='Pinned video lane from the start; media.py rejects the other lane')
    init.add_argument('--fasth3-endpoint', help='Explicit UniServe endpoint for FastH3-Omni; no machine default')
    init.add_argument('--first-frame', choices=FIRST_FRAME_POLICIES, default='selective',
                      help='First-frame policy: none, agent-selective, or required for every video request')
    init.add_argument('--interaction-page', choices=INTERACTION_PAGE_POLICIES, default='required',
                      help='Append the fixed 3-second A/B/C page at the end')
    init.add_argument('--h3-mode', choices=H3_MODES, default='local',
                      help='H3 route: local pool or PhanRouter cloud (cloud is 768p/24fps)')
    init.add_argument('--wall-seconds', type=int, default=14400)
    init.add_argument('--max-model-requests', type=int, default=256)
    init.add_argument('--viewpoint', choices=tuple(VIEWPOINTS), default='auto',
                      help='Camera viewpoint: first-person throughout, mixed by scene, or director-selected (default)')
    launch = sub.add_parser('run')
    launch.add_argument('workspace', type=Path)
    launch.add_argument('--endpoint')
    launch.add_argument('--resume', action='store_true')
    launch.add_argument('--prompt', help='Optional instruction for this turn; not a new production mode')
    batch = sub.add_parser('batch')
    batch.add_argument('manifest', type=Path,
                       help='Independent episode init/run settings; init sequential, run parallel up to manifest workers')
    args = parser.parse_args()
    if args.command == 'init':
        print(initialize(args.workspace, args.sdk_root, args.dependencies, args.input, args.skills,
                         previous=args.previous, asset_cache=args.asset_cache,
                         lane=args.lane, first_frame=args.first_frame,
                         interaction_page=args.interaction_page, h3_mode=args.h3_mode,
                         wall_seconds=args.wall_seconds, max_model_requests=args.max_model_requests,
                         viewpoint=args.viewpoint, fasth3_endpoint=args.fasth3_endpoint))
    elif args.command == 'run':
        raise SystemExit(run(args.workspace, args.endpoint, args.resume, args.prompt))
    else:
        result = run_batch(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result['ok'] else 2)


if __name__ == '__main__':
    main()
