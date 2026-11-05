"""Dispatch the existing independent reviewer and return evidence to original directors."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


class ReviewProtocolError(RuntimeError):
    pass


def cleanup_local_audio(workspace):
    """Reclaim abandoned local sensors only after their owning Agent has ended."""
    workspace = Path(workspace).resolve()
    stopped = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = [v.decode() for v in (proc / 'cmdline').read_bytes().split(b'\0') if v]
            if proc.stat().st_uid != os.getuid() or not any(Path(v).name == 'audio_worker.py' for v in args):
                continue
            job = Path(args[-1]).resolve()
            if not job.is_relative_to(workspace) or not job.is_file():
                continue
            rows = json.loads(job.read_text()).get('jobs', [])
            os.kill(int(proc.name), signal.SIGTERM)
            stopped.append({'pid': int(proc.name), 'job': str(job),
                            'completed': [r['output'] for r in rows if Path(r['output']).is_file()],
                            'unfinished': [r['output'] for r in rows if not Path(r['output']).is_file()]})
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    if stopped:
        path = workspace / 'local-audio-cleanup.json'
        previous = json.loads(path.read_text()) if path.is_file() else []
        previous.append({'at': time.time(), 'reason': 'owning Agent ended; requests and all completed outputs retained',
                         'workers': stopped})
        path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def pending_window_grant(workspace):
    path = Path(workspace) / 'window-grant.json'
    if not path.is_file():
        return None
    try:
        grant = json.loads(path.read_text())
        if not grant.get('consumed_at') and int(grant.get('seconds') or 0) > 0 and str(grant.get('authorized_by') or '').strip():
            return grant
    except (OSError, ValueError, TypeError):
        pass
    return None


def repair_budget(workspace):
    workspace = Path(workspace)
    if not (workspace / 'session-path.txt').is_file():
        return 'Original director session is missing; no replacement session will be created'
    try:
        budget = json.loads((workspace / 'run-budget.json').read_text())
    except (OSError, ValueError):
        return 'Original director budget is missing'
    if budget['provider_requests'] >= budget['request_limit']:
        return 'Original director request limit reached'
    if time.time() * 1000 >= budget['deadline_at'] and not pending_window_grant(workspace):
        return 'Original director wall deadline reached'
    return None


def authorize_repair_window(root, job):
    """Apply the human's batch allowance once, only after a real viewing-blocking rejection."""
    policy_path = Path(root) / 'repair-time-policy.json'
    if not policy_path.is_file() or job.get('independent_review') != 'rejected' or not job.get('movie'):
        return False
    if not Path(job['movie']).is_file() or repair_budget(job['workspace']) != 'Original director wall deadline reached':
        return False
    policy = json.loads(policy_path.read_text())
    if job['id'] not in policy.get('eligible_episode_ids', []) or not policy.get('authorized_by'):
        return False
    workspace = Path(job['workspace'])
    grant_path = workspace / 'window-grant.json'
    budget = json.loads((workspace / 'run-budget.json').read_text())
    if grant_path.exists() or budget.get('window_grants'):
        return False
    grant = {'seconds': int(policy['seconds']), 'authorized_by': policy['authorized_by'],
             'approved_at': policy.get('approved_at'), 'policy': str(policy_path),
             'reason': 'Independent viewing-blocking rejection; original director window expired',
             'review': job.get('review', {}).get('directory'),
             'scope': 'time only, once per episode; original session/request/take limits unchanged'}
    grant_path.write_text(json.dumps(grant, ensure_ascii=False, indent=2) + '\n')
    return True


def load_review(record):
    try:
        result = json.loads((Path(record['directory']) / 'output/review.json').read_text())
    except (OSError, ValueError) as error:
        raise ReviewProtocolError('Independent reviewer left no readable review.json') from error
    reported = Path(result.get('film', ''))
    target = Path(record['movie'])
    # Reports may name the film by its filename; the exact initialized digest still binds the bytes.
    named_target = reported.resolve() == target.resolve() or str(reported) == target.name
    if (not named_target
            or result.get('sha256') != record['sha256']
            or digest(record['movie']) != record['sha256']):
        raise ReviewProtocolError('Review targets a different movie version')
    if result.get('verdict') not in ['accepted', 'rejected', 'incomplete']:
        raise ReviewProtocolError('Unknown independent verdict')
    if not isinstance(result.get('findings'), list) or 'coverage' not in result or 'limits' not in result:
        raise ReviewProtocolError('Independent report is missing findings/coverage/limits')
    coverage = result['coverage']
    if not isinstance(coverage, dict) or not coverage or not any(coverage.values()):
        raise ReviewProtocolError('Independent coverage declarations are empty or malformed')
    if not isinstance(result['limits'], list):
        raise ReviewProtocolError('Independent limits must be an explicit list')
    for finding in result['findings']:
        if finding.get('severity') not in ['blocking', 'advisory'] or finding.get('owner') not in ['episode', 'tool', 'series']:
            raise ReviewProtocolError('Finding is missing severity or owner')
    blocking = any(f['severity'] == 'blocking' for f in result['findings'])
    if blocking != (result['verdict'] == 'rejected'):
        raise ReviewProtocolError('Independent verdict contradicts its blocking findings')
    return result


class Reviews:
    def __init__(self, controller):
        self.c = controller
        self.claimed = set()
        self.paused = False
        self.entry = self.c.root / 'reviewer-code-snapshot-v3/scripts/pi_video_agent.py'
        if not self.entry.is_file():
            self.entry = self.c.entry

    def seed(self):
        path = self.c.root / 'review-seeds.json'
        if not path.exists():
            return
        for record in json.loads(path.read_text()):
            job = next(j for j in self.c.all_jobs() if j['id'] == record['id'])
            if not job.get('review'):
                job['review'] = record
                job['independent_review'] = 'running'
        self.c.save()

    def recheck_protocol_reports(self):
        """Reconsume existing receipts after a gateway fix; never rerun the reviewer."""
        for job in self.c.all_jobs():
            if job.get('independent_review') != 'protocol_error' or not job.get('review'):
                continue
            try:
                self.settle(job, job['review'])
                job.pop('review_error', None)
            except Exception as error:
                self.paused = True
                self.c.state['review_queue'] = {'status':'blocked', 'episode':job['id'], 'reason':str(error)}
            self.c.save()

    def claim(self):
        with self.c.mutex:
            if self.paused:
                return None
            jobs = [j for j in self.c.all_jobs() if j.get('movie') and j['state'] == 'complete_movie_present'
                    and j['id'] not in self.claimed]
            existing = [j for j in jobs if j.get('independent_review') == 'running' and j.get('review')]
            pending = [j for j in jobs if j.get('independent_review') in [None, 'not_claimed', 'pending']]
            # Give an unexpired original director a chance to repair before its existing deadline.
            def priority(job):
                path = Path(job['workspace']) / 'run-budget.json'
                try:
                    deadline = json.loads(path.read_text())['deadline_at']
                except (OSError, ValueError, KeyError):
                    deadline = 0
                return (job.get('repair_status') != 'new_export_ready',
                        deadline <= time.time() * 1000,
                        deadline if deadline > time.time() * 1000 else 0, job['id'])
            choices = existing + sorted(pending, key=priority)
            if not choices:
                return None
            job = choices[0]
            self.claimed.add(job['id'])
            self.c.update(job, independent_review='running')
            return job, job.get('review') if job in existing else None

    def start(self, job):
        workspace = Path(job['workspace'])
        sequence = len(job.get('review_history', [])) + 1
        directory = self.c.root / 'independent-reviews' / job['id'] / f'review-{sequence:03d}'
        directory.parent.mkdir(parents=True, exist_ok=True)
        if directory.exists():
            raise ReviewProtocolError(f'Existing review workspace must be inspected, not reset: {directory}')
        timeline = Path(job['movie'] + '.timeline.json')
        baseline = workspace.parent / 'selected-package/episode/02-script.json'
        if not baseline.is_file():
            raise ReviewProtocolError(f'Original source package is missing: {baseline}')
        brief = (
            f'制作授权见 {workspace / "TASK.md"}，该文件只用于理解人类要求，不是导演自审。'
            '只依据成片、原始素材和自己的证据判断；不要读取导演self-review、日志、会话或检查结果。'
            '允许制作授权内合理改编、大幅重组与自然口语，不以原文措辞不同或审美偏好阻塞。'
            '只拒绝影响观看的问题；轻微服饰/年龄/构图细节写建议。没有可靠时间表就用真实秒数，不猜镜头编号。'
            '没有配乐本身不阻塞，仍需检查实际对白和现场声。ASR/F0/轨长不能独自证明声线身份或内容通过。'
            '如实记录画面与声音证据覆盖范围；证据不足记incomplete。先落盘，剩余个位数请求停止取证。'
            '本轮2小时/96次请求，不追加额度、不生成/修改媒体、不改生产任务。')
        previous_review = job.get('review')
        if job.get('repair_status') == 'new_export_ready' and previous_review:
            brief += (
                f' 这是原导演修复后的新导出。旧独立判词在{Path(previous_review["directory"]) / "output/review.json"}，'
                f'旧版备份在{Path(previous_review["directory"]) / "producer-export-before-repair.mp4"}。'
                '重点核实原阻塞是否消除及相邻衔接，并快速检查全片有没有新增严重问题；'
                '新时间位置以实际新片为准，旧秒数只是线索。可复用未受影响的真实证据，写明复用范围；'
                '不因已经修过就默认通过，也不必重复整片所有技术取证。')
        command = [str(self.c.python), str(self.entry), 'init', str(directory),
                   '--sdk-root', str(self.c.sdk_root),
                   '--dependencies', str(self.c.dependencies),
                   '--role', 'reviewer', '--review-film', job['movie'],
                   '--review-baseline', str(baseline), '--review-brief', brief,
                   '--input', str(baseline.parent.parent), '--wall-seconds', '7200',
                   '--max-model-requests', '96']
        if timeline.is_file():
            command += ['--review-timeline', str(timeline)]
        initialized = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if initialized.returncode:
            raise ReviewProtocolError('Reviewer init failed: ' + (initialized.stdout + initialized.stderr)[-1200:])
        audio = json.loads((workspace / 'audio-runtime.json').read_text())
        audio['device'] = 'cpu'
        (directory / 'audio-runtime.json').write_text(json.dumps(audio, ensure_ascii=False, indent=2) + '\n')
        config_path = directory / 'local.json'
        config = json.loads(config_path.read_text())
        config['audio_service_url'] = os.getenv('AUDIO_SERVICE_URL', '')
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        match = re.search(r'SHA256：`([0-9a-f]{64})`', (directory / 'TASK.md').read_text())
        if not match:
            raise ReviewProtocolError('Initialized reviewer did not bind its export')
        record = {'directory': str(directory), 'movie': job['movie'], 'sha256': match.group(1),
                  'status': 'running', 'sequence': sequence}
        previous = job.get('review')
        if previous and previous.get('status') == 'rejected' and previous['sha256'] == record['sha256']:
            self.c.update(job, independent_review='rejected', repair_status='unchanged_export',
                          repair_error='Original director returned the same rejected movie; no repeated paid review')
            return None
        with (directory / 'reviewer.log').open('a') as log:
            process = subprocess.Popen([str(self.c.python), str(self.entry), 'run', str(directory),
                                        '--endpoint', 'official-ds41'], cwd=directory,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        record['pid'] = process.pid
        self.c.update(job, review=record)
        print('review started', job['id'], process.pid, flush=True)
        return record, process

    def settle(self, job, record):
        result = load_review(record)
        record['status'] = result['verdict']
        record['blocking_count'] = sum(f['severity'] == 'blocking' for f in result['findings'])
        with self.c.mutex:
            job.setdefault('review_history', []).append(dict(record))
            job['review'] = record
            job['independent_review'] = result['verdict']
            if result['verdict'] == 'rejected':
                feedback = Path(record['directory']) / 'repair-feedback.md'
                feedback.write_text(
                    '# 独立审片反馈：交回原导演\n\n'
                    f'判词与实际证据：{Path(record["directory"]) / "output/review.json"}\n'
                    f'审看的版本：{record["movie"]}\nSHA256：{record["sha256"]}\n\n'
                    '只修 severity=blocking 的观看问题；advisory 不要求重拍。你继续负责如何修，不由宿主改稿。'
                    '使用原会话、原额度、原镜头次数与回执；不改预算、不自行续期、不可换名规避次数。'
                    '已有受理任务沿原task_id查询。保留旧导出和全部素材，输出新文件名，不覆盖已审MP4。'
                    '修好后更新正常delivery与自审；无法在原额度内完成就如实说明，不伪造通过。\n')
                job['repair_feedback'] = str(feedback)
                authorize_repair_window(self.c.root, job)
                reason = repair_budget(job['workspace'])
                if reason:
                    job.update(repair_status='blocked_original_limits', repair_error=reason)
                else:
                    job.update(state='repair_queued', repair_status='queued')
            self.c.save()
            self.c.condition.notify_all()
        print('review finished', job['id'], result['verdict'], flush=True)

    def worker(self):
        while True:
            with self.c.condition:
                selected = self.claim()
                if selected is None:
                    if not self.c.has_work():
                        return
                    self.c.condition.wait(5)
                    continue
            job, existing = selected
            record = existing
            try:
                if existing:
                    self.c.wait_existing(existing)
                else:
                    launched = self.start(job)
                    if launched is None:
                        continue
                    record, process = launched
                    process.wait()
                cleanup_local_audio(record['directory'])
                self.settle(job, record)
            except Exception as error:
                with self.c.condition:
                    self.paused = True
                    self.c.update(job, independent_review='protocol_error', review_error=str(error))
                    self.c.state['review_queue'] = {'status': 'blocked', 'reason': str(error), 'episode': job['id']}
                    self.c.save()
                    self.c.condition.notify_all()
                print('review queue paused', job['id'], str(error), flush=True)
            finally:
                with self.c.condition:
                    self.claimed.discard(job['id'])
                    self.c.condition.notify_all()
