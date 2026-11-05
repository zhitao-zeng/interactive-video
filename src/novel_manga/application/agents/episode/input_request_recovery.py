"""Correct a derived English request without revising its accepted source."""
from __future__ import annotations

import copy
from novel_manga.util import atomic_write_json, read_json


def edit(ctx, row, *, check_only=False):
    from .arguments import first_pass_scope
    from .contracts import validate_patch
    from .input_records import latest_input_records
    from .input_repair import repair
    allowed = {'clip_id', 'action', 'reason', 'prompt_h3', 'input_clip_id'}
    if set(row)-allowed or row.get('action') != 'edit' or not row.get('reason'):
        raise ValueError('same-source correction requires one English edit and input_clip_id')
    latest = latest_input_records(ctx.episode_dir).get(row.get('input_clip_id'))
    if latest is None:
        raise ValueError('no saved input candidate for this request')
    receipt, saved = latest
    record = copy.deepcopy(saved)
    decision = record.get('decision') or {}
    cid = row['clip_id']
    if (record.get('status') != 'incomplete' or record.get('published')
            or (record.get('source') or {}).get('consistent') is not True
            or not str(record.get('step') or '').startswith(('translate:', 'check:'))
            or cid not in (decision.get('clip_ids') or [decision.get('clip_id')])):
        raise ValueError('request correction requires an unfinished source-approved compilation')
    if any(decision.get(key) for key in ('characters', 'phases', 'locations')):
        raise ValueError('continue asset changes through their existing input operation')
    first_pass_scope(ctx, decision.get('clip_ids') or [decision['clip_id']])
    cache_path = receipt.with_suffix('')/'requests.json'
    translations = read_json(cache_path, {})
    old = translations.get(cid) or {}
    if not old.get('prompt_h3') or not old.get('prompt_h3_of'):
        raise ValueError('no compiled same-source request is available to correct')
    clip = next(c for c in read_json(ctx.episode_dir/'clip_plan.json')['clips'] if c['clip_id'] == cid)
    prompt = str(row.get('prompt_h3') or '').strip()
    validate_patch({**clip, **old}, prompt)
    if prompt == old['prompt_h3'].strip():
        raise ValueError('same-source request edit made no change')
    if check_only:
        return {'valid': True, 'clip_id': cid, 'input_clip_id': decision['clip_id']}
    # Keep the old English and error as evidence. Neither source, revision,
    # continuation, generation count nor time allowance is renewed here.
    number = len(record.get('request_edits', [])) + 1
    backup = cache_path.with_name(f'requests.before-edit-{number:03d}.json')
    atomic_write_json(backup, translations)
    record.setdefault('request_edits', []).append({'clip_id': cid, 'reason': row['reason'],
        'previous_error': record.get('error'), 'previous_step': record.get('step'),
        'previous_requests': backup.name})
    translations[cid] = {**old, 'prompt_h3': prompt}
    atomic_write_json(cache_path, translations)
    atomic_write_json(receipt, record)
    outcome = repair(ctx, decision, request_receipt=(receipt, record))
    return {'ok': not outcome.get('blocked'), **outcome, 'same_source_request_edit': cid}
