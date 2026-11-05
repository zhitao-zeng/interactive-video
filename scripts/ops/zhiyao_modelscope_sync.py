"""Sync complete 知遥 deliveries to the authorized ModelScope dataset."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / '.runtime/zhiyao-modelscope-20261023'
PROGRESS = Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/zcode-supervision-20261023/.runtime/zcode-supervision-20261023/progress.json')
PRODUCTION = Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022')
REPO = 'Flame4pd/zhiyao-episodes'
URL = f'https://modelscope.cn/datasets/{REPO}'


def now():
    return datetime.now().astimezone().isoformat()


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def source(path):
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def probe(movie):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type,codec_name,width,height', '-of', 'json', str(movie)],
        check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if not any(s['codec_type'] == 'video' for s in data['streams']):
        raise ValueError('Delivery lacks video')
    if not any(s['codec_type'] == 'audio' for s in data['streams']):
        raise ValueError('Delivery lacks audio')
    return {'seconds': float(data['format']['duration']), 'streams': data['streams']}


def collect():
    progress = read(PROGRESS)
    rows, media = [], []
    for episode in progress['episodes']:
        code, work, title = episode['id'].split('_', 2)
        order = int(work[1:])
        row = {'order': order, 'episode': code, 'title': title, 'status': '准备中'}
        # Read the current receipt, rather than trusting a cached path in progress.
        current_name = episode['active_edition'].removeprefix('zhiyao-')
        current_receipt = PRODUCTION / 'delivery/zhiyao' / current_name / 'delivery.json'
        receipts = [current_receipt, *[Path(d['receipt']) for d in episode.get('deliveries', [])]]
        for receipt_path in dict.fromkeys(receipts):
            receipt = read(receipt_path, {})
            raw = receipt.get('final_video')
            if not raw:
                continue
            movie = Path(raw)
            if not movie.is_file() or not movie.stat().st_size:
                continue
            if not movie.is_relative_to(PRODUCTION / 'delivery/zhiyao'):
                raise ValueError('Only complete delivery exports may be uploaded')
            cover = movie.parent / 'cover.jpeg'
            if not cover.is_file():
                raise ValueError(f'Matching cover missing for {code}')
            info = probe(movie)
            if abs(info['seconds'] - float(receipt['duration'])) > 0.2:
                raise ValueError(f'Delivery duration mismatch for {code}')
            stem = f'{work}_{code}_{title}'
            public_movie = f'episodes/{stem}.mp4'
            public_cover = f'covers/{stem}.jpeg'
            row.update(status='已上传', seconds=round(info['seconds'], 3),
                       bytes=movie.stat().st_size, file=public_movie, cover=public_cover)
            selected = {'id': episode['id'], 'order': order, 'receipt': str(receipt_path),
                        'movie': source(movie), 'cover': source(cover),
                        'file': public_movie, 'cover_file': public_cover, 'probe': info}
            # Publish only attribution fields; the original credits file contains private paths.
            credits = []
            for cue in (receipt.get('music_selection') or {}).get('cues', []):
                asset = cue.get('asset') or {}
                credit = {key: asset[key] for key in
                          ['title', 'artist', 'license_id', 'license_url', 'source_url'] if asset.get(key)}
                if credit and credit not in credits:
                    credits.append(credit)
            if credits:
                row['music_credits'] = f'credits/{stem}.json'
                selected['credits_file'] = row['music_credits']
                selected['credits'] = {'tracks': credits, 'edits': '节选、调节音量、淡入淡出；部分片段循环。'}
            media.append(selected)
            break
        rows.append(row)
    rows.sort(key=lambda row: row['order'])
    media.sort(key=lambda row: row['order'])
    if [r['order'] for r in rows] != list(range(1, 21)):
        raise ValueError('Expected exactly W01 through W20')
    return {'observed_at': now(), 'repository': REPO, 'episodes': rows, 'media': media}


def stage_file(original, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.samefile(original):
        return
    target.unlink(missing_ok=True)
    target.hardlink_to(original)


def public_index(manifest):
    return {'title': '知遥', 'total_episodes': 20,
            'uploaded_episodes': len(manifest['media']), 'episodes': manifest['episodes']}


def metadata(manifest, stage):
    index = public_index(manifest)
    write(stage / 'episodes.json', index)
    lines = ['---', 'license: other', 'language:', '- zh', '---', '', '# 知遥 · 20集', '',
             f"已收录 {index['uploaded_episodes']} 集，按制作序排列。其余剧集准备好后继续补充。", '',
             '| 制作序 | 集号 | 剧名 | 时长 | 视频 | 封面 |', '|---|---|---|---|---|---|']
    for row in manifest['episodes']:
        duration = f"{int(row['seconds']) // 60}:{int(row['seconds']) % 60:02d}" if row.get('seconds') else '—'
        video = f"[播放 / 下载]({quote(row['file'], safe='/')})" if row.get('file') else '准备中'
        cover = f"[封面]({quote(row['cover'], safe='/')})" if row.get('cover') else '—'
        lines.append(f"| W{row['order']:02d} | {row['episode']} | {row['title']} | {duration} | {video} | {cover} |")
    if any(row.get('music_credits') for row in manifest['episodes']):
        lines += ['', '配乐署名见对应的 `credits/` 文件。']
    (stage / 'README.md').write_text('\n'.join(lines) + '\n')


def inventory(api):
    return {f.path: int(f.size) for f in api.list_repo_files(REPO, 'dataset')}


def sync(api):
    manifest = collect()
    write(RUN / 'manifest.json', manifest)
    status = {'at': now(), 'repository': REPO, 'url': URL, 'eligible_episodes': len(manifest['media']),
              'target_episodes': 20, 'status': 'uploading'}
    write(RUN / 'status.json', status)
    if not api.repo_exists(REPO, 'dataset'):
        api.create_repo(REPO, 'dataset', visibility='public', license='other',
                        chinese_name='知遥 · 20集', description='《知遥》20集剧集视频、封面与分集索引。')
    remote = inventory(api)
    stage = RUN / 'stage'
    saved = read(RUN / 'uploaded.json', {})
    verified = dict(saved)
    for row in manifest['media']:
        expected = {row['file']: row['movie']['bytes'], row['cover_file']: row['cover']['bytes']}
        same = saved.get(row['id'], {}).get('movie') == row['movie'] and saved.get(row['id'], {}).get('cover') == row['cover']
        if same and all(remote.get(path) == size for path, size in expected.items()):
            continue
        stage_file(Path(row['movie']['path']), stage / row['file'])
        stage_file(Path(row['cover']['path']), stage / row['cover_file'])
        allow = list(expected)
        if row.get('credits_file'):
            write(stage / row['credits_file'], row['credits'])
            allow.append(row['credits_file'])
        api.upload_folder(REPO, 'dataset', stage, allow_patterns=allow,
                          commit_message=f"收录 W{row['order']:02d} · {row['id'].split('_', 2)[2]}",
                          max_workers=3, disable_tqdm=True, sync_remote_repo=False)
        remote = inventory(api)
        if not all(remote.get(path) == (stage / path).stat().st_size for path in allow):
            raise ValueError(f"Remote media incomplete for {row['id']}")
        for key in ['movie', 'cover']:
            if source(Path(row[key]['path'])) != row[key]:
                raise ValueError('Delivery changed during upload; resync the current version')
        verified[row['id']] = row
        write(RUN / 'uploaded.json', verified)
        status.update(at=now(), verified_episodes=len(verified), last_uploaded=row['id'])
        write(RUN / 'status.json', status)
        print(f"VERIFIED {len(verified)}/20 {row['id']}", flush=True)
    metadata(manifest, stage)
    expected_index = public_index(manifest)
    last_index = read(RUN / 'verified-index.json')
    if last_index != expected_index or 'README.md' not in remote:
        api.upload_folder(REPO, 'dataset', stage, allow_patterns=['README.md', 'episodes.json'],
                          commit_message=f"更新20集索引，已收录{len(manifest['media'])}集",
                          max_workers=2, disable_tqdm=True, sync_remote_repo=False)
    remote = inventory(api)
    downloaded = Path(api.download_file(REPO, 'dataset', 'episodes.json',
                      local_dir=RUN / 'remote-index', force=True))
    if read(downloaded) != expected_index:
        raise ValueError('Remote episode index differs')
    paths = [row[key] for row in manifest['media'] for key in ['file', 'cover_file']]
    if not all(remote.get(path) == (stage / path).stat().st_size for path in paths):
        raise ValueError('Remote inventory has missing media')
    write(RUN / 'verified-index.json', expected_index)
    write(RUN / 'remote-files.json', remote)
    status.update(at=now(), status='complete' if len(manifest['media']) == 20 else 'watching',
                  verified_episodes=len(manifest['media']), pending_episodes=20 - len(manifest['media']),
                  verified_media_files={path: remote[path] for path in paths})
    write(RUN / 'status.json', status)
    print(f"SYNCED {len(manifest['media'])}/20 {URL}", flush=True)
    return len(manifest['media'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=120)
    args = parser.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    if args.collect_only:
        manifest = collect()
        write(RUN / 'manifest.json', manifest)
        metadata(manifest, RUN / 'stage')
        print(json.dumps({'episodes': len(manifest['episodes']), 'complete': len(manifest['media']),
                          'bytes': sum(r['movie']['bytes'] for r in manifest['media'])}))
        return
    # Dataset transfers use a direct command-scoped route, preserving host service settings.
    for key in list(os.environ):
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            del os.environ[key]
    from modelscope_hub import HubApi
    logging.disable(logging.CRITICAL)
    api = HubApi()
    if api.whoami().username != 'Flame4pd':
        raise ValueError('Unexpected ModelScope account')
    with (RUN / 'sync.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write(RUN / 'worker.json', {'pid': os.getpid(), 'started_at': now(), 'watch': args.watch})
        while True:
            try:
                count = sync(api)
            except Exception as error:
                error_type = type(error).__name__
                detail = str(error) if isinstance(error, ValueError) and str(error).startswith((
                    'Remote episode index differs', 'Remote inventory has missing media',
                    'Remote media incomplete', 'Delivery changed during upload')) else error_type
                write(RUN / 'error.json', {'at': now(), 'type': error_type, 'detail': detail})
                state = read(RUN / 'status.json', {})
                state.update(at=now(), status='retry_wait', error_type=error_type)
                write(RUN / 'status.json', state)
                print(f'SYNC ERROR {type(error).__name__}', flush=True)
                if not args.watch or error_type in {'AuthenticationError', 'AuthorizationError'}:
                    raise SystemExit(1)
                time.sleep(args.interval)
                continue
            if not args.watch or count == 20:
                break
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
