"""Resident Eureka audio service: one model per GPU, FIFO queue, path allowlist.

Start it with the Eureka Python environment, not the workspace Python:

  <audio-runtime.json python> tools/audio_service.py \
    --host 127.0.0.1 --port 18790 --allow-root <workspace> \
    --device cuda --cuda-visible-devices 5 \
    --audio-source-root /path/Eureka-Audio --audio-model-dir /path/eureka-adapter

Clients submit with a retained caller job id and poll the same job. A lost response
does not start another worker; rerun the same request to query/recover its job.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

QUEUE: queue.Queue = queue.Queue()
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
SERVICE = None


class AudioService:
    def __init__(self, args):
        self.args = args
        self.allow_root = Path(args.allow_root).resolve()
        self.device = args.device
        if self.device.startswith('cuda'):
            os.environ['CUDA_VISIBLE_DEVICES'] = args.cuda_visible_devices
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        sys.path.insert(0, args.audio_source_root)
        import torch
        import torchaudio
        import soundfile

        def load(path, *a, **k):
            data, sr = soundfile.read(path, dtype='float32', always_2d=True)
            return torch.from_numpy(data.T.copy()), sr

        torchaudio.load = load
        from eureka_infer.api import EurekaAudio
        self.model = EurekaAudio(args.audio_model_dir, device=self.device)
        threading.Thread(target=self.worker, daemon=True).start()

    def checked(self, value) -> Path:
        path = Path(value).resolve()
        if not path.is_relative_to(self.allow_root):
            raise ValueError(f'path outside allow-root: {value}')
        return path

    def submit(self, jobs, runtime, job_id=None) -> str:
        if not jobs:
            raise ValueError('empty job list')
        for row in jobs:
            self.checked(row['audio'])
            self.checked(row['output'])
        job = {'id': str(job_id or uuid.uuid4().hex), 'status': 'queued', 'jobs': jobs, 'runtime': runtime}
        with LOCK:
            existing = JOBS.get(job['id'])
            if existing:
                if existing['jobs'] != jobs or existing['runtime'] != runtime:
                    raise ValueError('Existing audio job id belongs to different inputs')
                return job['id']
            JOBS[job['id']] = job
        QUEUE.put(job)
        return job['id']

    def worker(self) -> None:
        while True:
            job = QUEUE.get()
            try:
                with LOCK:
                    job['status'] = 'running'
                observations = []
                for row in job['jobs']:
                    audio = self.checked(row['audio'])
                    output = self.checked(row['output'])
                    if output.is_file():
                        observations.append(json.loads(output.read_text()))
                        continue
                    started = time.monotonic()
                    response = self.model.generate([{'role': 'user', 'content': [
                        {'type': 'audio_url', 'audio_url': {'url': str(audio)}},
                        {'type': 'text', 'text': '只依据实际音频转录听清的中文，描述说话人数、音乐环境声、可懂度、爆音和异常断音。听不清就说明，不推测剧情。'}]}],
                        max_new_tokens=int(job['runtime'].get('max_new_tokens', 256)), do_sample=False)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps({'model': 'Eureka-Audio-Instruct', 'device': self.device,
                        'audio': str(audio), 'source_window': row.get('source_window'),
                        'inference_seconds': round(time.monotonic() - started, 3),
                        'response': response}, ensure_ascii=False, indent=2) + '\n')
                    observations.append(json.loads(output.read_text()))
                with LOCK:
                    job['status'] = 'done'
                    job['observations'] = observations
            except Exception as error:  # retained for the client; the queue keeps moving
                with LOCK:
                    job['status'] = 'error'
                    job['error'] = f'{type(error).__name__}: {error}'
            finally:
                QUEUE.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = 'EurekaAudioService/1.0'

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            self._json({'ok': True, 'device': SERVICE.device, 'queue_depth': QUEUE.qsize(),
                        'allow_root': str(SERVICE.allow_root)})
            return
        if self.path.startswith('/job/'):
            job_id = self.path.split('/', 2)[-1]
            with LOCK:
                job = JOBS.get(job_id)
                if job is None:
                    self._json({'ok': False, 'error': 'unknown job'}, 404)
                    return
                self._json({'ok': True, 'status': job['status'], 'observations': job.get('observations'),
                            'error': job.get('error')})
            return
        self._json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        if self.path != '/audio':
            self._json({'ok': False, 'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            request = json.loads(self.rfile.read(length) or b'{}')
            job_id = SERVICE.submit(request.get('jobs') or [], request.get('runtime') or {}, request.get('job_id'))
        except Exception as error:
            self._json({'ok': False, 'error': f'{type(error).__name__}: {error}'}, 400)
            return
        self._json({'ok': True, 'job_id': job_id})

    def log_message(self, fmt, *args):
        sys.stderr.write((fmt % args) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18790)
    parser.add_argument('--allow-root', type=Path, required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--cuda-visible-devices', default='0')
    parser.add_argument('--audio-source-root', required=True)
    parser.add_argument('--audio-model-dir', required=True)
    args = parser.parse_args()
    global SERVICE
    SERVICE = AudioService(args)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({'ready': True, 'device': args.device, 'allow_root': str(args.allow_root),
                      'port': server.server_address[1]}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
