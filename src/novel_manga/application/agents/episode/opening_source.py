"""Text-only opening-contract review before any paid image candidate exists."""
from __future__ import annotations

import copy
import hashlib
import json

from novel_manga.media.first_frames import interpret_review
from novel_manga.util import atomic_write_json, read_json

POLICY = 'first-frame-opening-source-v1'


def _key(clip_id, opening):
    payload = json.dumps({'policy': POLICY, 'clip_id': clip_id, 'opening': opening},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _path(ctx):
    return ctx.run_dir/'opening-source-checks.json'


def remember(ctx, prepared, reviews):
    saved = read_json(_path(ctx), {})
    if saved.get('policy') != POLICY:
        saved = {'policy': POLICY, 'reviews': {}}
    saved.setdefault('reviews', {})
    for row, contract, _ in prepared:
        cid = row['clip_id']
        review = reviews.get(cid)
        if review is None:
            continue
        opening = copy.deepcopy(contract['opening'])
        saved['reviews'][_key(cid, opening)] = {
            'clip_id': cid, 'opening': opening, 'review': interpret_review(review)}
    atomic_write_json(_path(ctx), saved)


def current_review(ctx, clip_id, opening):
    saved = read_json(_path(ctx), {})
    if saved.get('policy') != POLICY:
        return None
    record = saved.get('reviews', {}).get(_key(clip_id, opening))
    return interpret_review(record['review']) if record else None


def source_blockers(ctx, plan):
    from novel_manga.media.first_frames import current_contract, enabled, source_conflicts
    if not enabled(ctx):
        return {}
    saved = read_json(_path(ctx), {})
    if saved.get('policy') != POLICY or not saved.get('reviews'):
        return {}
    blocked = {}
    for clip in plan.get('clips', []):
        if clip.get('kind') != 'video':
            continue
        opening = current_contract(ctx, clip, [])['opening']
        conflicts = source_conflicts(current_review(ctx, clip['clip_id'], opening) or {})
        if conflicts:
            blocked[clip['clip_id']] = conflicts
    return blocked


def _validated_review(row):
    findings = row.get('findings')
    if not isinstance(findings, list):
        raise ValueError('invalid opening source finding: findings must be an array')
    for finding in findings:
        valid = (isinstance(finding, dict) and finding.get('origin') == 'source'
                 and finding.get('relation') in {'contradiction', 'underspecified', 'compatible'}
                 and all(isinstance(finding.get(key), str) and finding[key].strip()
                         for key in ('observed', 'expected', 'reason'))
                 and isinstance(finding.get('basis'), list) and len(finding['basis']) >= 2
                 and all(isinstance(path, str) and path.strip() for path in finding['basis']))
        if not valid:
            raise ValueError('invalid opening source finding: '+json.dumps(finding, ensure_ascii=False))
    return interpret_review({'findings': findings})


def review_batch(ctx, prepared):
    saved = read_json(_path(ctx), {})
    cached = saved.get('reviews', {}) if saved.get('policy') == POLICY else {}
    reviews = {}
    missing = []
    for row, contract, assets in prepared:
        cid = row['clip_id']
        opening = contract['opening']
        record = cached.get(_key(cid, opening))
        if record:
            reviews[cid] = interpret_review(record['review'])
        else:
            missing.append((row, contract, assets))
    if missing:
        from novel_manga.llm.client import ask_json, obj
        from novel_manga.review.endpoints import judge_settings
        payload = {'clips': [{'clip_id': row['clip_id'], 'opening': contract['opening']}
                             for row, contract, _ in missing]}
        prompt = (
            '只审每个镜头时间零点t=0的opening源合同内部是否自洽，不审图片，不评价剧情或审美。'
            'camera可能包含整镜运动；trigger之后的动作、actor_path箭头右侧、endpoint/终点和hold是后续过程，'
            '不得把这些后续状态当作开场冲突。只比较当前JSON实际存在的字段。'
            '只有两个或更多源要求无论如何都无法同时成立时，才返回relation=contradiction且origin=source；'
            '信息不足用underspecified，可以同时成立用compatible。'
            '每条finding给observed、expected、reason和basis；basis至少两个实际冲突字段路径，'
            'observed和expected引用对应原句，说明为何不能同时成立。不要根据常识补写缺失字段。'
            '逐clip返回reviews；无发现时findings=[]。\n'
            + json.dumps(payload, ensure_ascii=False))
        finding = obj({'observed': {'type': 'string'}, 'expected': {'type': 'string'},
                       'basis': {'type': 'array', 'minItems': 2, 'items': {'type': 'string'}},
                       'origin': {'type': 'string', 'enum': ['source']},
                       'relation': {'type': 'string', 'enum': ['contradiction', 'underspecified', 'compatible']},
                       'reason': {'type': 'string'}})
        answer = ask_json([{'type': 'text', 'text': prompt}],
                          obj({'reviews': {'type': 'array', 'items': obj({
                              'clip_id': {'type': 'string'}, 'findings': {'type': 'array', 'items': finding}})}}),
                          name='episode_opening_source_preflight', max_tokens=3000,
                          timeout=240, settings=judge_settings())
        expected = {row['clip_id'] for row, _, _ in missing}
        received = [row.get('clip_id') for row in answer.get('reviews', [])]
        if len(received) != len(set(received)) or set(received) != expected:
            raise ValueError('opening source review must return each requested clip exactly once')
        for row in answer['reviews']:
            reviews[row['clip_id']] = _validated_review(row)
        remember(ctx, missing, reviews)
    return reviews
