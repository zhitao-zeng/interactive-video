"""Probe the PhanRouter chat gateway body limit without generating anything.

An unknown model name keeps the request away from real inference: under the nginx limit the
gateway answers with a normal API error (model not found / invalid request), over it nginx
answers 413. Bodies are plain text so the probe is cheap to reason about.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ENDPOINT = 'https://cloud.phanthy.com/phanrouter/v1/chat/completions'
MODEL = '__gateway_body_limit_probe__'


def key():
    env = dict(os.environ)
    if env.get('PHANROUTER_IMAGE_API_KEY'):
        return env['PHANROUTER_IMAGE_API_KEY']
    for candidate in (Path('/mnt/disk1/zengzhitao/novel-manga-video/.env'),
                      Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.env')):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                if line.startswith('PHANROUTER_IMAGE_API_KEY='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    raise SystemExit('credential not found')


def send(size_bytes: int, client: httpx.Client, token: str):
    # Keep the JSON valid; the filler dominates the body size.
    body = json.dumps({'model': MODEL, 'max_tokens': 1, 'stream': False,
                       'messages': [{'role': 'user', 'content': 'x' * max(0, size_bytes - 200)}]})
    response = client.post(ENDPOINT, content=body.encode(),
                           headers={'Authorization': 'Bearer ' + token,
                                    'Content-Type': 'application/json'})
    return len(body.encode()), response.status_code, response.text[:160].replace('\n', ' ')


def main():
    token = key()
    client = httpx.Client(trust_env=False, timeout=180)  # direct route, like the agent runtimes
    sizes = [1, 2, 4, 8, 16, 32]
    accepted = 0
    rejected = None
    for mb in sizes:
        size = mb * 1024 * 1024
        try:
            sent, status, text = send(size, client, token)
        except Exception as error:
            print(f'{mb}MB transport error: {type(error).__name__}: {error}')
            break
        print(f'{mb}MB sent={sent} status={status} body={text}')
        if status == 413:
            rejected = size
            break
        if status in (401, 403):
            print('credential/authorization problem; stop')
            return 1
        accepted = size
    if rejected is None:
        print('no 413 up to 32MB; limit is above this probe')
        return 0
    low, high = accepted, rejected
    for _ in range(6):
        if high - low <= 256 * 1024:
            break
        mid = (low + high) // 2
        sent, status, text = send(mid, client, token)
        print(f'binary mid={mid/1048576:.2f}MB sent={sent} status={status}')
        if status == 413:
            high = mid
        else:
            low = mid
    print(json.dumps({'accepted_low_bytes': low, 'rejected_high_bytes': high,
                      'limit_between_mb': [round(low / 1048576, 2), round(high / 1048576, 2)]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
