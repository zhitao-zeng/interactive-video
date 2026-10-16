"""Confirmed picture failures stay open until their replacement is checked against the finding."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from novel_manga.util import read_json
from novel_manga.llm.client import ask_json, image_part
from novel_manga.review.endpoints import judge_settings
from novel_manga.application.review.evidence import clip_frames

SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['observations', 'checks', 'premise_valid', 'premise_evidence'], 'properties': {
              'premise_valid': {'type': ['boolean', 'null']},
              'premise_evidence': {'type': 'string'},
              'observations': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
              'checks': {'type': 'array', 'minItems': 1, 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['criterion', 'scope', 'met', 'evidence'],
                  'properties': {'criterion': {'type': 'string'}, 'scope': {'type': 'string', 'enum': ['visual', 'audio', 'source']}, 'met': {'type': ['boolean', 'null']},
                                 'evidence': {'type': 'string'}}}}}}


def finding_result(answer):
    if answer.get('premise_valid') is False:
        return {**answer, 'result': 'uncertain', 'checked_scope': 'visual',
                'error_kind': 'review_premise_conflict',
                'evidence': answer.get('premise_evidence', ''),
                'next': 'Reconcile actual source and reference assets before retaking; the old claim is not evidence.'}
    checks = [c for c in answer.get('checks') or [] if c.get('scope', 'visual') == 'visual']
    result = ('unresolved' if any(c.get('met') is False for c in checks) else
              'resolved' if checks and all(c.get('met') is True for c in checks) else 'uncertain')
    return {**answer, 'result': result, 'checked_scope': 'visual',
            'evidence': '；'.join(c.get('evidence', '') for c in checks if c.get('met') is not True)
                        or '；'.join(c.get('evidence', '') for c in checks)}


def resolved_on_take(claim, video, take):
    check = claim.get('check') or {}
    return check.get('video') == str(video) and check.get('take') == take and check.get('result') == 'resolved'


def clip_review_criteria(directory, cid):
    """Return host-authored criteria for one clip; absent files preserve legacy review behavior."""
    path = Path(directory) / 'review_criteria.json'
    if not path.is_file():
        return []
    data = read_json(path, {})
    clips = data.get('clips', {}) if isinstance(data, dict) else {}
    if not isinstance(clips, dict):
        raise ValueError('review_criteria.json must contain a clips object')
    criteria = clips.get(cid, [])
    if (not isinstance(criteria, list) or any(not isinstance(item, str) or not item.strip() for item in criteria)):
        raise ValueError(f'review criteria for {cid} must be a list of non-empty strings')
    return [item.strip() for item in criteria]


def apply_claim(verdict, claim, video, take):
    verdict = copy.deepcopy(verdict)
    base=verdict.get('confirmed_base')
    if not verdict.get('confirmed'):
        base={'video':str(video),'take':take,'verdict':copy.deepcopy(verdict)}
    if base:
        verdict['confirmed_base']=base
    check = claim.get('check') or {}
    resolved = resolved_on_take(claim, video, take)
    verdict['confirmed'] = claim
    if resolved and base and base.get('video')==str(video) and base.get('take')==take:
        # Restore the current take's broad verdict; only our own failure overlay
        # is removed. Other picture/speech failures remain in that base verdict.
        verdict={**copy.deepcopy(base['verdict']),'confirmed':claim,'confirmed_base':base}
    if check.get('error_kind') == 'review_premise_conflict':
        verdict['defect_kind'] = 'review_premise_conflict'
        verdict['required_action'] = 'source_or_asset_recheck'
    if not resolved:
        verdict.update(severity='fail', tier='must_fix', scripted=False,
                       defect_issue=claim['issue'], feedback=claim['instruction'])
        # This is confirmed evidence, including defects the broad verifier does not measure.
        verdict['verify'] = {**(verdict.get('verify') or {}), 'verdict': 'obvious',
                             'evidence': claim['issue'], 'instruction': claim['instruction']}
    return verdict



def judge_finding(clip, video, claim, work, novel_dir=None, review_criteria=()):
    """Inspect current ordered frames for the concrete acceptance condition; never auto-clear on a broad pass."""
    frames = clip_frames(Path(video), Path(work), 6)
    from .evidence import segment_texts
    episode = next((parent for parent in Path(work).parents if (parent/'segments.json').is_file()), None)
    segments = segment_texts(episode) if episode else {}
    passage = '\n'.join(segments.get(str(s), '') for s in clip.get('segment_ids', []))
    stages=[s for s in read_json(episode/'chapter_script.json',{}).get('shots',[])
            if s.get('index') in clip.get('shot_indexes',[])] if episode else []
    parts = []; seen = set()
    for ref in clip.get('references', []):
        if ref.get('role') not in {'character','prop','location'} or ref.get('name') in seen:
            continue
        path = Path(ref['path'])
        if not path.is_absolute():
            if novel_dir is None:
                raise ValueError('relative character reference needs its novel directory')
            path = Path(novel_dir) / path
        seen.add(ref['name'])
        label='角色身份参考' if ref.get('role')=='character' else '道具/场景设计参考'
        parts.extend([{'type':'text', 'text': label+'（不是视频帧，不计入画面人数）：'+ref['name']+
                      ('，这是该角色闭合头盔时的样子，仍是角色本人，不是无人空甲。' if ref.get('view') == 'closed' else '')},
                      image_part(path, 640)])
    for i, frame in enumerate(frames,1):
        parts.extend([{'type':'text','text':f'当前视频第{i}帧'},image_part(frame,1280)])
    parts.append({'type': 'text', 'text': (
        '前面的角色参考图只用于辨认身份与服装；只检查随后标明的6张当前视频帧。先按参考资料确认谁是谁，逐帧列出实际观察，'
        '再把下面的正向验收目标拆成checks。每条check引用目标中的要求写criterion，'
        '本次只把与原确认画面缺陷直接相关的修复条件作为visual验收；目标中附带的原剧情/未改动作保留提醒'
        '作为source记录，不扩大成新的画面重拍条件。合理侧脸、背影或未采到单个手势，不代表本次明确的画面缺陷仍存在。'
        '根据图片填写met：符合为true，不符合为false，看不清为null；evidence必须对应当前帧事实。'
        '读字类要求只依据当前视频的实体牌面或纸张像素，先逐字记录实际能读到的字，不能辨认的部分明确记为无法辨认。'
        '参考图、原文、目标及对白中的正确文字不能替代当前画面的文字，也不能用它们补字或把近形错字纠正后再声称字形正确。'
        '能猜到规则大意不等于醒目文字无错字或伪字；正常字体、字大小和书写风格差异本身不构成失败。'
        'scope标明visual（画面）、audio（声音）或source（原文）。本调用只验画面，听不到台词或无法核对原文不是画面失败，分别标audio/source和null；只在visual项中判断画面是否满足要求。'
        '不另写总判定，也不猜测旧版本出了什么错。不要添加目标没有要求的条件。'
        '角色可以只露背影、半身或戴闭合头盔；看不到脸并不表示独立空甲或额外角色。'
        '肩背属于同一身体时不多计；同一个身体在不同帧转身、改变姿势也不多计。'
        '只有同一帧里在不同位置同时出现两个独立身体/空甲，才是数量增加。'
        '动作按先后帧的起点和终点核对；目标只要求踏上台阶时，不额外要求整个人走进车厢。'
        '旧评审指控不是事实，目标中的性别、外貌、身份和在场前提也需核对。'
        '先独立读取实际角色卡、当前帧和下方原文，premise_valid表示目标前提是否成立；'
        '前提与原文或参考图冲突时填false并写具体premise_evidence，缺少证据填null；'
        '这类冲突需先修输入/资产，不能靠重拍正确画面来迎合错误指控。' +
        ('\n本次由宿主提供的逐镜验收标准（仅这些明确容差覆盖较严格的旧判词；不覆盖原文明确事实，也不豁免身份、座位或道具空间关系的实际错误）：\n- ' + '\n- '.join(review_criteria) if review_criteria else '') +
        '\n对应原文：' + (passage or '本次未提供，不能推断原文事实。') +
        '\n当前采用分镜的可见状态、动作与穿戴（依据，非新的重拍条件）：' +
        json.dumps([{k:s[k] for k in ('index','motion_prompt','visual_prompt','wears','props','in_frame','scene_time') if k in s} for s in stages],ensure_ascii=False) +
        '\n本次原确认缺陷（用于确定验收范围，不当作新视频事实）：' + claim.get('issue','') +
        '\n待核对的验收目标：' + claim['instruction'] + '\n只输出JSON。')})
    answer = ask_json(parts, SCHEMA, name='confirmed_picture_check', max_tokens=1800,
                      settings=judge_settings())
    return finding_result(answer)


def enforce(directory, report, previous, *, verify=False, review_criteria=None):
    """Read-only unless the caller publishes the returned report. Resolved checks are bound to a take."""
    result = copy.deepcopy(report)
    plan = {c['clip_id']: c for c in read_json(directory / 'clip_plan.json', {}).get('clips', [])}
    for cid, old in (previous.get('clips') or {}).items():
        claim = copy.deepcopy(old.get('confirmed'))
        row = result.get('clips', {}).get(cid)
        if not claim or not row or not row.get('video') or not row.get('take'):
            continue
        # A take may carry positive review evidence without ever having had a
        # failure. It has no repair instruction and must not become a finding.
        if not claim.get('issue') and not claim.get('instruction'):
            continue
        video, take = row['video'], row['take']; check = claim.get('check') or {}
        criteria = (review_criteria or {}).get(cid)
        if criteria is None:
            criteria = clip_review_criteria(directory, cid)
        criteria = list(criteria)
        criteria_changed = (bool(criteria) and check.get('review_criteria') != criteria
                            or not criteria and bool(check.get('review_criteria'))
                            and check.get('result') == 'resolved')
        if claim.get('video') == video and claim.get('take') == take and not criteria_changed:
            if (criteria and check.get('video') == video and check.get('take') == take
                    and check.get('review_criteria') == criteria):
                checked = True
            else:
                # Confirmed on this very take: not re-judged on it, and no check written on it clears it.  A model
                # reading the same frames cleared two human-confirmed findings (ch12-1 clip_19/20, 2026-09-26).
                claim.pop('check', None)
                checked = True
        else:
            checked = (check.get('video') == video and check.get('take') == take
                       and not criteria_changed)
        if criteria_changed:
            claim.pop('check', None)
        if verify and not checked:
            try:
                if criteria:
                    answer = judge_finding(plan[cid], video, claim,
                                           directory / 'work/confirmed_review' / cid, directory.parent,
                                           review_criteria=criteria)
                else:
                    answer = judge_finding(plan[cid], video, claim,
                                           directory / 'work/confirmed_review' / cid, directory.parent)
                claim['check'] = {**answer, 'video': video, 'take': take,
                                  **({'review_criteria': criteria} if criteria else {})}
            except Exception as error:
                claim['check'] = {'result': 'uncertain', 'video': video, 'take': take,
                                  'evidence': f'检查未完成：{type(error).__name__}',
                                  **({'review_criteria': criteria} if criteria else {})}
        row = apply_claim(row, claim, video, take)
        result['clips'][cid] = row
        if row.get('severity') == 'fail' and not resolved_on_take(claim, video, take):
            from novel_manga.media.speech_repair import merge_note
            feedback = result.setdefault('feedback', {})
            feedback[cid] = merge_note(feedback.get(cid, ''), claim['instruction'])
        elif resolved_on_take(claim, video, take) and result.get('feedback', {}).get(cid) == claim['instruction']:
            result['feedback'].pop(cid)
            if row.get('severity') == 'fail' and row.get('tier') == 'must_fix':
                from novel_manga.review.policy import compose_feedback
                if instruction := compose_feedback(row, plan.get(cid)):
                    result['feedback'][cid] = instruction
    return result
