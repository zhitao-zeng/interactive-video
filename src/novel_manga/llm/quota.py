"""Confirmed provider quota exhaustion shared by known credential groups."""
from __future__ import annotations
import json
import os
import time
import fcntl
import hashlib
from pathlib import Path
from .catalog import identify, quota_group, resource, catalogue


def directory():
    return Path(os.environ.get('NOVEL_SHARED_QUOTA_DIR') or
                '/mnt/disk1/zengzhitao/tmp/resource-quota')


def _registered_key(var):
    expected = os.environ.get(var or '')
    if not expected and var:
        path = Path(os.environ.get('NOVEL_PROJECT_ROOT') or Path(__file__).resolve().parents[3])/'.env'
        if path.is_file():
            for line in path.read_text().splitlines():
                if line.startswith(var+'='):
                    expected = line.split('=',1)[1].strip().strip('\"\'')
                    break
    return expected


def _group(name, row):
    members = catalogue()['quota_groups'].get(row.get('quota_group'), {}).get('key_vars', [])
    values = {var: _registered_key(var) for var in members}
    return quota_group(name, values)


def for_model(model, base_url, key=None):
    name, row = identify(model, base_url)
    return _group(name, row)


def marker(group, key=None):
    if key:
        # A private token namespace replaces a provider identity lookup on
        # every admission. It changes which stop applies across project roots
        # and key rotations. Neither the key nor this identifier is logged.
        family = 'phanrouter' if group.startswith('router-') else group.split('--',1)[0]
        return directory()/family/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    return directory()/f'{group}.json'


def check(group, key=None):
    if not group:
        return
    path = marker(group, key)
    if not path.is_file():
        return
    data = json.loads(path.read_text())
    if data.get('exhausted') and (not data.get('resets_at_epoch') or data['resets_at_epoch'] > time.time()):
        raise RuntimeError(f'resource quota exhausted: {group}; refresh account evidence before reuse')


def remember(group, status, body, *, key=None):
    if not group or status not in {402, 403, 429}:
        return False
    error = body.get('error', body) if isinstance(body, dict) else {}
    code = error.get('code') if isinstance(error, dict) else None
    message = error.get('message', '') if isinstance(error, dict) else ''
    if code not in {'quota_not_enough', 'insufficient_quota', 'insufficient_credits'}:
        if message != 'Both daily quota pools are exhausted.':
            return False
        code = 'insufficient_credits'
    if code == 'insufficient_credits' and error.get('available_points') not in (None, 0):
        return False
    # The gateway already owns daily reset. Do not pin its temporary 402 forever.
    if group == 'phanthy-account' and not error.get('resets_at_epoch'):
        return False
    path = marker(group, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.is_file() else {}
        history = previous.get('history', [])
        if previous:
            history = [*history, {k: v for k, v in previous.items() if k != 'history'}]
        data = {'group': group, 'exhausted': True, 'code': code, 'status': status,
                'observed_at_epoch': time.time(), 'history': history}
        if error.get('resets_at_epoch'):
            data['resets_at_epoch'] = error['resets_at_epoch']
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
        temp.replace(path)
    return True


def for_resource(name, key=None):
    row = resource(name)
    return _group(name, row)


def note_sdk_failure(group, text, *, key=None):
    if 'API Error:' not in text:
        return False
    if 'quota_not_enough' in text:
        return remember(group, 403, {'error': {'code':'quota_not_enough'}}, key=key)
    if 'Both daily quota pools are exhausted.' in text:
        return remember(group, 402, {'error': {'code':'insufficient_credits'}}, key=key)
    return False
