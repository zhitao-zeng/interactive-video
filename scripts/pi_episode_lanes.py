"""Task-owned episode queues around the normal Pi run entry; no creative stages."""
import argparse
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from pi_episode_review import Reviews, repair_budget, pending_window_grant, cleanup_local_audio, authorize_repair_window

CAPACITY = {'cloud-h3': 2, 'gpu52-fast': 3, 'gpu16-h3': 1}
PROJECT = Path('/mnt/disk1/zengzhitao/interactive-video')
MUSIC_NOTE = '''

## 本批能力补充（启动前冻结）
音乐能力以本工作区 CAPABILITIES.json 为准。已配置时用现有 music 工具检索、试听并自主选择曲段，
保留署名和实际混音证据；明确需要静默的段落由导演判断，不必全片铺满。
若工具明确返回 `SoulTuner runtime is not configured`，保存错误，沿用原生声音继续交付。
不要全盘搜索配乐工具、猜环境、安装软件或重启服务。音频设备与服务以本工作区配置为准；已备好服务就沿同job查询，不另起本地模型。
普通 bash 未写 timeout 时最多等待 900 秒；显式 timeout 也不会超过本集剩余墙钟。
长媒体操作继续使用合理的显式 timeout；原时间、请求数和任务回执不变。
'''


class RepairUnavailable(RuntimeError):
    pass


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def prepare_pending(workspace, runtime):
    """Install the selected fix only before an episode's first-ever run."""
    workspace, runtime = Path(workspace), Path(runtime)
    markers = ['session-path.txt', 'run-budget.json', 'agent-run.json']
    if any((workspace / name).exists() for name in markers) or list(workspace.glob('*.jsonl')):
        raise RuntimeError(f'Already started; refusing to replace tools or initialize again: {workspace}')
    task = (workspace / 'TASK.md').read_text()
    if not task.strip() or task.startswith('# 待填写的制作任务'):
        raise RuntimeError(f'Prepared episode is missing its authorized task: {workspace}')
    for name in ['runtime.mjs', 'bash_limits.mjs']:
        shutil.copy2(runtime / name, workspace / 'tools' / name)
    config_path = workspace / 'local.json'
    config = json.loads(config_path.read_text())
    config['bash_timeout_seconds'] = 900
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    for name in ['SYSTEM.md', 'CAPABILITIES.md']:
        target = workspace / name
        text = target.read_text()
        if '## 本批能力补充（启动前冻结）' not in text:
            target.write_text(text + MUSIC_NOTE)
    (workspace / 'execution-runtime.json').write_text(json.dumps({
        'installed_at': now(), 'selected_runtime': str(runtime),
        'files': ['tools/runtime.mjs', 'tools/bash_limits.mjs'],
        'scope': 'before first run only; original SDK, task and quotas preserved',
    }, ensure_ascii=False, indent=2) + '\n')


class Controller:
    def __init__(self, root):
        self.root = Path(root)
        self.state = json.loads((self.root / 'state.json').read_text())
        self.mutex = threading.RLock()
        self.condition = threading.Condition(self.mutex)
        self.claimed = set()
        self.paused = set()
        self.runtime = self.root / 'frozen-tools-v2'
        self.entry = self.root / 'frozen-main/scripts/pi_video_agent.py'
        self.grant_entry = self.root / 'reviewer-code-snapshot-v2/scripts/pi_video_agent.py'
        self.python = PROJECT / 'runtime/python/bin/python'
        self.dependencies = PROJECT / 'runtime/pi/node_modules'
        self.reviews = None

    def all_jobs(self):
        return self.state['jobs'] + self.state.get('previous_movies', [])

    def has_work(self):
        for job in self.all_jobs():
            if job['state'] in ['running', 'repairing']:
                return True
            if job['state'] in ['prepared', 'repair_queued', 'resume_queued'] and job['lane'] not in self.paused:
                return True
            if (self.reviews and not self.reviews.paused and job.get('movie')
                    and job['state'] == 'complete_movie_present'
                    and job.get('independent_review') in [None, 'not_claimed', 'pending', 'running']):
                return True
        return bool(self.reviews and self.reviews.claimed)

    def save(self):
        with self.mutex:
            temp = self.root / 'state.json.partial'
            temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + '\n')
            temp.replace(self.root / 'state.json')

    def update(self, job, **values):
        with self.mutex:
            job.update(values)
            self.save()
            self.condition.notify_all()

    def claim(self, lane):
        with self.mutex:
            for job in self.all_jobs():
                if job['lane'] == lane and job.get('repair_status') == 'blocked_original_limits':
                    authorize_repair_window(self.root, job)
                if (job['lane'] == lane and job['state'] == 'complete_movie_present'
                        and job.get('independent_review') == 'rejected'
                        and job.get('repair_status') == 'blocked_original_limits'
                        and pending_window_grant(job['workspace']) and not repair_budget(job['workspace'])):
                    self.update(job, state='repair_queued', repair_status='queued', repair_error=None)
                if job['lane'] == lane and job['state'] == 'repair_queued':
                    reason = repair_budget(job['workspace'])
                    if reason == 'Original director wall deadline reached':
                        authorize_repair_window(self.root, job)
                        reason = repair_budget(job['workspace'])
                    if reason:
                        self.update(job, state='complete_movie_present', repair_status='blocked_original_limits',
                                    repair_error=reason, independent_review='rejected')
            if lane in self.paused:
                return None
            # Existing runners keep one slot each; they never receive another run/resume.
            for phase in ['running', 'repairing', 'resume_queued', 'repair_queued', 'prepared']:
                for job in self.all_jobs():
                    if job['lane'] == lane and job['state'] == phase and job['id'] not in self.claimed:
                        self.claimed.add(job['id'])
                        return job, phase in ['running', 'repairing']
            return None

    def wait_existing(self, job):
        # The project's Python has no pidfd_open. Watch only this known runner, not system-wide jobs.
        stat = Path('/proc') / str(job['pid']) / 'stat'
        while True:
            try:
                process_state = stat.read_text().rpartition(') ')[2].split()[0]
            except FileNotFoundError:
                return
            if process_state == 'Z':
                return
            time.sleep(2)

    def dispatch(self, job):
        workspace = Path(job['workspace'])
        command = [str(self.python), str(self.entry), 'run', str(workspace),
                   '--endpoint', job.get('endpoint', 'official-ds41')]
        if job['state'] == 'resume_queued':
            reason = repair_budget(workspace)
            if reason:
                raise RepairUnavailable(reason)
            before = workspace / 'recoveries' / f'connection-{job["automatic_session_resumes"]:03d}'
            before.mkdir(parents=True, exist_ok=False)
            for name in ['run-budget.json', 'agent-run.json', 'session-path.txt']:
                shutil.copy2(workspace / name, before / name)
            command += ['--resume', '--prompt',
                        '上次模型连接中断。继续原任务：先核对已经受理的task_id、已有回片与工作记录，'
                        '不重复POST、不换会话、不重置时限/额度/计次，沿用已完成素材继续正常交付。']
            self.update(job, state='running', resumed_at=now(), active_operation='production')
        elif job['state'] == 'repair_queued':
            reason = repair_budget(workspace)
            if reason:
                raise RepairUnavailable(reason)
            review_dir = Path(job['review']['directory'])
            backup = review_dir / 'producer-export-before-repair.mp4'
            if not backup.exists():
                shutil.copy2(job['movie'], backup)
            before = review_dir / 'original-director-before-resume'
            before.mkdir(exist_ok=False)
            for name in ['run-budget.json', 'agent-run.json', 'session-path.txt']:
                if (workspace / name).exists():
                    shutil.copy2(workspace / name, before / name)
            job.setdefault('repair_history', []).append({
                'review': str(review_dir), 'reviewed_export_backup': str(backup),
                'original_state_backup': str(before), 'started_at': now()})
            grant = pending_window_grant(workspace)
            if grant:
                # The existing one-use grant launcher starts the added clock here, never in the queue.
                command[1] = str(self.grant_entry)
                for name in ['runtime.mjs', 'bash_limits.mjs']:
                    old = workspace / 'tools' / name
                    if old.exists():
                        shutil.copy2(old, before / name)
                    shutil.copy2(self.runtime / name, old)
                job['repair_history'][-1].update(
                    time_grant_file=str(workspace / 'window-grant.json'), runner_entry=str(self.grant_entry),
                    harness_update='bounded runtime applied while this original producer was stopped')
            command += ['--resume', '--prompt',
                        '按独立审片反馈继续你原来的制作任务：' + job['repair_feedback'] +
                        '。只处理影响观看的blocking，不因advisory重拍。沿用原会话、原额度、原take计数；'
                        '保留旧导出并使用新输出名，修好后正常交付并退出，供独立审片复核。']
            if grant:
                command[-1] += (f' 用户明确授权本次追加{int(grant["seconds"])}秒修复时间，启动器一次消费授权；'
                                '原请求上限与图片/视频take上限不变，不再自行续期，只修本次blocking。')
            self.update(job, state='repairing', repair_status='running', active_operation='repair')
        else:
            prepare_pending(workspace, self.runtime)
            self.update(job, state='running', started_at=now(), dispatch_generation=3,
                        active_operation='production')
        log_path = workspace.parent / 'production.log'
        with log_path.open('a') as log:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            self.update(job, pid=process.pid)
            print(now(), 'started', job['id'], job['lane'], process.pid, flush=True)
            return process.wait()

    def queue_connection_resume(self, job, record):
        if (record.get('error') != 'Connection error.' or job.get('automatic_session_resumes', 0) >= 1
                or repair_budget(job['workspace'])):
            return False
        self.update(job, state='resume_queued', automatic_session_resumes=1,
                    resume_reason=record['error'], producer_error=record['error'])
        return True

    def finish(self, job, returncode, adopted):
        workspace = Path(job['workspace'])
        movie = None
        delivery = workspace / 'output/delivery.json'
        if delivery.is_file():
            data = json.loads(delivery.read_text())
            candidate = data.get('movie') or data.get('final_video')
            if candidate and Path(candidate).is_file():
                movie = candidate
        run_path = workspace / 'agent-run.json'
        record = json.loads(run_path.read_text()) if run_path.is_file() else {}
        if not movie and self.queue_connection_resume(job, record):
            return
        ended = record.get('ended_at') and record.get('status') == 'session_ended'
        successful = bool(movie and not record.get('error') and
                          (ended if adopted else returncode == 0))
        with self.mutex:
            if job.get('active_operation') == 'repair' and not movie:
                self.update(job, state='complete_movie_present', independent_review='rejected',
                            repair_status='failed', repair_error=record.get('error') or 'No successful repaired export',
                            candidate_movie=movie)
                return
            self.update(job, state='complete_movie_present' if movie else 'needs_attention',
                        returncode=returncode, ended_at=now(), movie=movie,
                        completion_evidence='original_runner_record' if adopted else 'own_child_exit',
                        independent_review='pending' if movie else 'not_claimed',
                        producer_successful_exit=successful, producer_error=record.get('error'))
            if job.get('active_operation') == 'repair':
                job['repair_status'] = 'new_export_ready'
                self.save()
            if not movie:
                self.paused.add(job['lane'])
                self.state['lanes'][job['lane']]['status'] = 'blocked'
                self.state['lanes'][job['lane']]['reason'] = {
                    'episode': job['id'], 'error': record.get('error') or 'No successful movie delivery',
                }
                self.save()
        print(now(), 'finished', job['id'], job['state'], flush=True)

    def worker(self, lane):
        while True:
            with self.condition:
                if lane in self.paused:
                    return
                selected = self.claim(lane)
                if selected is None:
                    if not self.has_work():
                        return
                    self.condition.wait(5)
                    continue
            job, adopted = selected
            try:
                if adopted:
                    print(now(), 'keeping original runner', job['id'], job['pid'], flush=True)
                    self.wait_existing(job)
                    code = None  # It was not our child; do not invent an exit code.
                else:
                    code = self.dispatch(job)
                cleanup_local_audio(job['workspace'])
                self.finish(job, code, adopted)
            except RepairUnavailable as error:
                self.update(job, state='complete_movie_present' if job.get('movie') else 'needs_attention',
                            repair_status='blocked_original_limits', repair_error=str(error),
                            independent_review='rejected' if job.get('movie') else 'not_claimed')
            except Exception as error:
                self.update(job, state='needs_attention', dispatch_error=str(error), ended_at=now())
                with self.mutex:
                    self.paused.add(lane)
                    self.state['lanes'][lane]['status'] = 'blocked'
                    self.state['lanes'][lane]['reason'] = str(error)
                    self.save()
                print(now(), 'stopped lane', lane, str(error), flush=True)
                return
            finally:
                with self.condition:
                    self.claimed.discard(job['id'])
                    self.condition.notify_all()

    def run(self):
        leases = []
        folder = Path('/mnt/disk1/zengzhitao/tmp/inflight/h3pool-instances/172.28.4.52_30022')
        for slot in (0, 1):
            handle = (folder / f'slot_{slot:02d}.lock').open('a+')
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            leases.append(handle)
        previous = self.state['pid']
        self.state.update(pid=os.getpid(), status='running', dispatch_generation=3,
                          previous_controller_pid=previous, switched_at=now(), capacity=CAPACITY)
        self.state['lanes'] = {lane: {'status': 'running', 'capacity': count}
                               for lane, count in CAPACITY.items()}
        self.reviews = Reviews(self)
        self.reviews.seed()
        self.state['review_queue'] = {'status': 'running', 'capacity': 2}
        self.reviews.recheck_protocol_reports()
        for job in self.all_jobs():
            if job['state'] == 'needs_attention' and not job.get('movie'):
                path = Path(job['workspace']) / 'agent-run.json'
                if path.is_file():
                    self.queue_connection_resume(job, json.loads(path.read_text()))
        self.save()
        (self.root / 'controller.json').write_text(json.dumps({
            'pid': os.getpid(), 'previous_controller_pid': previous,
            'script': str(Path(__file__).resolve()), 'capacity': CAPACITY,
            'switched_at': now(), 'adopted_runners': [
                {'id': j['id'], 'pid': j['pid']} for j in self.state['jobs'] if j['state'] == 'running'],
        }, ensure_ascii=False, indent=2) + '\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=sum(CAPACITY.values()) + 2) as pool:
            futures = [pool.submit(self.worker, lane)
                       for lane, count in CAPACITY.items() for _ in range(count)]
            futures += [pool.submit(self.reviews.worker) for _ in range(2)]
            for future in futures:
                future.result()
        for lane in CAPACITY:
            if lane not in self.paused:
                self.state['lanes'][lane]['status'] = 'finished'
        self.state['status'] = ('finished' if all(j['state'] == 'complete_movie_present'
                                                and j.get('independent_review') == 'accepted'
                                                for j in self.all_jobs()) else 'needs_attention')
        self.state['ended_at'] = now()
        self.save()
        for lease in leases:
            lease.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root', type=Path)
    args = parser.parse_args()
    # This is the same lock as the original dispatcher. No new worker starts before ownership moves.
    with (args.run_root / 'controller.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        Controller(args.run_root).run()
