"""Text-only opening-contract review before any paid image candidate exists."""
from __future__ import annotations

import copy
import hashlib
import json

from novel_manga.media.first_frames import interpret_review
from novel_manga.util import atomic_write_json, read_json

POLICY = 'first-frame-opening-source-v2-adjacent-context'
_UNSET=object()


def _source_facts(contract):
    return {key:copy.deepcopy(contract.get(key) or []) for key in ('character_facts','prop_facts')}


def _current_source_contract(ctx,clip,shots=None):
    from novel_manga.media.first_frames import current_contract
    current=_current_shot(_source_shots(ctx,shots),clip) if shots is not None else None
    return current_contract(ctx,clip,[],shots=[current] if current else None)


def _key(clip_id, opening, previous_shot_context=None, current_time_context=None, source_facts=None):
    payload = json.dumps({'policy': POLICY, 'clip_id': clip_id, 'opening': opening,
                          'previous_shot_context':previous_shot_context,
                          'current_time_context':current_time_context or {},
                          'source_facts':source_facts or _source_facts({})},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _path(ctx):
    return ctx.run_dir/'opening-source-checks.json'


def _source_shots(ctx,shots=None):
    if shots is not None:return list(shots)
    return read_json(ctx.episode_dir/'chapter_script.json',{}).get('shots') or []


def _current_shot(shots,clip):
    index=(clip.get('shot_indexes') or [None])[0]
    return next((shot for shot in shots if shot.get('index')==index),None)


def previous_shot_context(ctx,clip,*,shots=None):
    """Verbatim immediately preceding source, only when scene IDs match."""
    rows=_source_shots(ctx,shots);index=(clip.get('shot_indexes') or [None])[0]
    pos=next((i for i,shot in enumerate(rows) if shot.get('index')==index),None)
    if pos is None or pos==0:return None
    previous,current=rows[pos-1],rows[pos]
    if not current.get('scene_id') or previous.get('scene_id')!=current.get('scene_id'):
        return None
    return {'shot_index':previous.get('index'),'scene_id':previous.get('scene_id'),
            **{key:copy.deepcopy(previous[key]) for key in ('scene_time','start_state','event','end_state')
               if previous.get(key) not in (None,'',[],{})}}


def current_time_context(ctx,clip,*,shots=None):
    shot=_current_shot(_source_shots(ctx,shots),clip) or {}
    return {key:copy.deepcopy(shot[key]) for key in ('scene_time','scene_transition')
            if shot.get(key) not in (None,'',[],{})}


def _clips(ctx):
    return {clip['clip_id']:clip for clip in read_json(ctx.episode_dir/'clip_plan.json',{}).get('clips',[])}


def remember(ctx, prepared, reviews, *, shots=None):
    saved = read_json(_path(ctx), {})
    if saved.get('policy') != POLICY:
        # Earlier records remain evidence, but their old-policy keys cannot
        # certify this policy's extra adjacent context.
        saved = {**saved,'policy':POLICY}
    saved.setdefault('reviews', {})
    clips=_clips(ctx)
    for row, contract, _ in prepared:
        cid = row['clip_id']
        review = reviews.get(cid)
        if review is None:
            continue
        opening = copy.deepcopy(contract['opening'])
        clip=clips.get(cid,{})
        prior=previous_shot_context(ctx,clip,shots=shots)
        timing=current_time_context(ctx,clip,shots=shots)
        facts=_source_facts(contract)
        saved['reviews'][_key(cid,opening,prior,timing,facts)] = {
            'clip_id':cid,'opening':opening,'previous_shot_context':prior,
            'current_time_context':timing,'source_facts':facts,'review':interpret_review(review)}
    atomic_write_json(_path(ctx), saved)


def current_review(ctx,clip_id,opening,prior=_UNSET,timing=_UNSET,*,shots=None):
    saved = read_json(_path(ctx), {})
    if saved.get('policy') != POLICY:
        return None
    if prior is _UNSET or timing is _UNSET:
        clip=_clips(ctx).get(clip_id,{})
        if prior is _UNSET:prior=previous_shot_context(ctx,clip,shots=shots)
        if timing is _UNSET:timing=current_time_context(ctx,clip,shots=shots)
    clip=_clips(ctx).get(clip_id,{})
    facts=_source_facts(_current_source_contract(ctx,clip,shots=shots))
    record = saved.get('reviews', {}).get(_key(clip_id,opening,prior,timing,facts))
    return interpret_review(record['review']) if record else None


def pending_context(ctx,plan,*,shots=None):
    """Only previously checked current openings whose adjacent context changed.

    This is a read-only route fact, not a model call or an image invalidation.
    Old policy records and clips never checked under this policy are ignored.
    """
    from novel_manga.media.first_frames import enabled
    if not enabled(ctx):return {}
    saved=read_json(_path(ctx),{})
    if saved.get('policy')!=POLICY:return {}
    records=saved.get('reviews') or {};rows=_source_shots(ctx,shots);pending={}
    for clip in plan.get('clips',[]):
        if clip.get('kind')!='video':continue
        cid=clip['clip_id'];shot=_current_shot(rows,clip)
        if shot is None:continue
        contract=_current_source_contract(ctx,clip,shots=rows)
        opening=contract['opening'];facts=_source_facts(contract)
        matching=[record for record in records.values() if record.get('clip_id')==cid
                  and record.get('opening')==opening and 'previous_shot_context' in record
                  and 'source_facts' in record]
        if not matching:continue
        prior=previous_shot_context(ctx,clip,shots=rows)
        timing=current_time_context(ctx,clip,shots=rows)
        if _key(cid,opening,prior,timing,facts) not in records:
            pending[cid]={'opening':opening,'previous_shot_context':prior,'current_time_context':timing,
                          'source_facts':facts}
    return pending


def source_blockers(ctx, plan, *, shots=None):
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
        current=_current_shot(_source_shots(ctx,shots),clip) if shots is not None else None
        opening = current_contract(ctx,clip,[],shots=[current] if current else None)['opening']
        prior=previous_shot_context(ctx,clip,shots=shots)
        timing=current_time_context(ctx,clip,shots=shots)
        conflicts = source_conflicts(current_review(ctx,clip['clip_id'],opening,prior,timing) or {})
        if conflicts:
            blocked[clip['clip_id']] = conflicts
    return blocked


def _validated_review(row, *, opening=None, previous_shot_context=None):
    """Keep existing contradictions and ground only the new clarification kind."""
    findings = row.get('findings')
    if not isinstance(findings, list):
        raise ValueError('invalid opening source finding: findings must be an array')
    sanitized=[]
    for finding in findings:
        clarification=isinstance(finding,dict) and finding.get('kind')=='continuity_clarification'
        valid = (isinstance(finding, dict) and finding.get('origin') == 'source'
                 and finding.get('relation') in {'contradiction', 'underspecified', 'compatible'}
                 and all(isinstance(finding.get(key),str) for key in ('observed','expected','reason'))
                 and bool(finding['reason'].strip())
                 and (clarification or all(finding[key].strip() for key in ('observed','expected')))
                 and isinstance(finding.get('basis'), list) and len(finding['basis']) >= 2
                 and all(isinstance(path, str) and path.strip() for path in finding['basis']))
        if not valid:
            raise ValueError('invalid opening source finding: '+json.dumps(finding, ensure_ascii=False))
        item=copy.deepcopy(finding)
        if item.get('kind')=='continuity_clarification':
            if item.get('relation')!='underspecified' or not isinstance(previous_shot_context,dict):
                raise ValueError('continuity clarification requires an underspecified same-scene historical source')
            basis=item['basis']
            current_paths={'opening.start_state':(opening or {}).get('start_state'),
                           'opening.opening_frame.image_prompt':((opening or {}).get('opening_frame') or {}).get('image_prompt')}
            prior_paths={f'previous_shot_context.{key}':previous_shot_context.get(key)
                         for key in ('start_state','event','end_state')}
            if len(basis)!=2 or basis[0] not in current_paths or basis[1] not in prior_paths:
                raise ValueError('continuity clarification requires current-opening then immediately prior field paths')
            current,previous=current_paths[basis[0]],prior_paths[basis[1]]
            if (not isinstance(current,str) or not current.strip()
                    or not isinstance(previous,str) or not previous.strip()):
                raise ValueError('continuity clarification requires nonempty actual current and previous source fields')
            # The model only selects the fields.  Use their actual full source
            # values; never guess the paths or copy its generated quotation.
            item.update(observed=current,expected=previous,continuity_verified=True)
            item['reason']=('当前镜原文：'+item['observed']+'；前镜历史原文：'+item['expected']+
                            '。请作者判断当前相关状态，并按实际需要写清开场；不预设延续，不要求画外物件入镜。')
        elif item.get('kind') not in (None,'source_contradiction','other'):
            raise ValueError('invalid opening source finding kind')
        sanitized.append(item)
    return interpret_review({'findings':sanitized,'raw_preflight':copy.deepcopy(row)})


def review_batch(ctx, prepared, *, shots=None):
    saved = read_json(_path(ctx), {})
    cached = saved.get('reviews', {}) if saved.get('policy') == POLICY else {}
    reviews = {}
    missing = []
    clips=_clips(ctx)
    for row, contract, assets in prepared:
        cid = row['clip_id']
        opening = contract['opening']
        clip=clips.get(cid,{})
        prior=previous_shot_context(ctx,clip,shots=shots)
        timing=current_time_context(ctx,clip,shots=shots)
        record = cached.get(_key(cid,opening,prior,timing,_source_facts(contract)))
        if record:
            reviews[cid] = interpret_review(record['review'])
        else:
            missing.append((row, contract, assets))
    if missing:
        from novel_manga.llm.client import ask_json, obj
        from novel_manga.review.endpoints import judge_settings
        payload = {'clips': [{'clip_id':row['clip_id'],'opening':contract['opening'],
                              **_source_facts(contract),
                              'previous_shot_context':previous_shot_context(ctx,clips.get(row['clip_id'],{}),shots=shots),
                              'current_time_context':current_time_context(ctx,clips.get(row['clip_id'],{}),shots=shots)}
                             for row, contract, _ in missing]}
        prompt = (
            '只审每个镜头时间零点t=0的opening源合同与有据的连续性澄清，不审图片，不评价剧情或审美。'
            'character_facts和prop_facts是已投影到本镜的作者事实；opening中的reference_subjects是已输入的参考名称。'
            '不要假定全局设定或未传入图片的具体外观已输入，也不要仅因图片未传给本次文本审查就要求补写其外观。'
            'start_state/opening_frame/storyboard.frame=t=0，event/action_beats属于后续过程。'
            'camera可能包含整镜运动；trigger之后的动作、actor_path箭头右侧、endpoint/终点和hold是后续过程，'
            '不得把这些后续状态当作开场冲突。previous_shot_context是上一同场镜原文，仅作历史背景，'
            '不是本镜开场或入镜要求；当前明确状态、已写转移或跳切优先。'
            '不能因前镜未写转移就断言该状态必然延续，也不得强制画外物件入镜。'
            '本镜可见人物相关的先前明确穿戴或持物状态，若本镜开场仅笼统引用或未交代，'
            '且相关人物部位可能入画，请返回kind=continuity_clarification、relation=underspecified，'
            '仅请作者判断当前状态，不预设延续；已写清、明确转移、跳切或画外无关时不要报。'
            '只有两个或更多源要求无论如何都无法同时成立时，才返回relation=contradiction且origin=source；'
            '信息不足用underspecified，可以同时成立用compatible。'
            '澄清的basis必须依次给opening.start_state或opening.opening_frame.image_prompt，'
            '以及previous_shot_context.start_state/event/end_state中的一个真实字段；'
            '澄清的observed和expected留空，宿主按所选字段读取完整真实原文。'
            '其他源矛盾的basis至少两个实际字段路径，observed和expected仍引用对应原句。'
            '每条finding给kind、observed、expected、reason和basis，不要根据常识补写缺失字段。'
            '逐clip返回reviews；无发现时findings=[]。\n'
            + json.dumps(payload, ensure_ascii=False))
        finding = obj({'kind':{'type':'string','enum':['source_contradiction','continuity_clarification','other']},
                       'observed': {'type': 'string'}, 'expected': {'type': 'string'},
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
        openings={row['clip_id']:contract['opening'] for row,contract,_ in missing}
        for row in answer['reviews']:
            cid=row['clip_id'];prior=previous_shot_context(ctx,clips.get(cid,{}),shots=shots)
            reviews[cid] = _validated_review(row,opening=openings[cid],previous_shot_context=prior)
        remember(ctx,missing,reviews,shots=shots)
    return reviews
