"""Review existing takes and record one shared episode execution result; never generate."""
import copy
import fcntl
import json
from pathlib import Path

from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.review.episode import review_episode
from novel_manga.application.review.store import current_takes
from novel_manga.review.acceptance import evaluate_acceptance
from novel_manga.util import atomic_write_json, read_json


def media_matches(plan, media, *, notes=None):
    """Whether the existing report can enter repair without first rendering the current plan."""
    return bool(media.get('clips')
                and media.get('clip_plan_fingerprint') == plan_fingerprint(plan)
                and (not media.get('prompt_h3_fingerprint')
                     or media['prompt_h3_fingerprint'] == h3_prompt_fingerprint(plan))
                and (notes is None or (media.get('review_feedback') or {}) == notes))


def review_saved(directory: Path, *, fresh=None, video_name='clip.mp4'):
    if video_name == 'clip.mp4':
        refresh_speech_evidence(directory)
    review = review_episode(directory, video_name=video_name, fresh=fresh)
    if video_name == 'clip.mp4':
        plan = read_json(directory / 'clip_plan.json', {})
        history.observe(directory, review, current_takes(directory, plan, review))
    return review


def refresh_speech_evidence(directory):
    """A policy refresh uses existing audio evidence and cannot submit video."""
    from novel_manga.media import speech
    directory = Path(directory)
    plan = read_json(directory/'clip_plan.json', {})
    media = read_json(directory/'thin_media_report.json', {})
    if not media_matches(plan, media, notes=read_json(directory/'review_feedback.json', {})):
        return False
    clips = {c['clip_id']: c for c in plan.get('clips', [])}
    bible = read_json(directory.parent/'story_bible.json', {})
    terms = [c['name'] for c in bible.get('characters') or [] if c.get('name')]
    aliases = read_json(directory.parent/'asr_aliases.json', {})
    changed = False
    for row in media.get('clips') or []:
        selected = row.get('selected') or {}
        clip = clips.get(row.get('clip_id'))
        if (not clip or not selected.get('video') or 'reference' not in selected
                or selected.get('speech_evaluation_policy') == speech.EVALUATION_POLICY):
            continue
        row['selected'] = speech.refresh(clip.get('spoken_text', ''), selected, terms, aliases,
                                        vocalizations=speech.planned_vocalizations(clip))
        if media.get('assembly')and any(c.get('subtitle')=='rejected_unplanned_speech'for c in selected.get('chunks',[])):
            from novel_manga.media.subtitles import align_chunks
            aligned=align_chunks([clip.get('spoken_text','')],row['selected'].get('chunks',[]))
            if any(c.get('subtitle')=='rejected_unplanned_speech'and text for c,text,score,pieces in aligned):
                media['assembly']['needs_subtitle_refresh']=True
        changed = True
    if changed:
        selected = {r['clip_id']: r.get('selected') or {} for r in media['clips']}
        media['gate_failed_clips'] = [cid for cid in media.get('gate_failed_clips') or []
                                      if selected.get(cid, {}).get('passed') is not True]
        atomic_write_json(directory/'thin_media_report.json', media)
    return changed


def validate_scoped_agent_review(directory, cid, artifact, *, work_dir, author_session):
    """Validate a session-owned review receipt for exactly one host-authorized current clip."""
    directory, work_dir = Path(directory), Path(work_dir)
    if not isinstance(artifact, dict) or artifact.get('schema') != 'scoped-agent-review-v1':
        raise ValueError('review_file must be a scoped-agent-review-v1 object')
    if artifact.get('clip_id') != cid:
        raise ValueError('review_file clip_id must match the keep target')
    if not author_session or artifact.get('author_session') != author_session:
        raise ValueError('review_file author_session must match the submitting episode Agent session')
    from novel_manga.application.review import confirmed
    criteria = confirmed.clip_review_criteria(directory, cid)
    if not criteria or artifact.get('review_criteria') != criteria:
        raise ValueError('review_file must carry the exact current host criteria for this clip')

    from novel_manga.application.review.store import current_takes
    plan = read_json(directory / 'clip_plan.json', {})
    previous = read_json(directory / 'episode_review.json', {})
    clip = next((row for row in plan.get('clips', []) if row.get('clip_id') == cid and row.get('kind') == 'video'), None)
    current = current_takes(directory, plan, previous).get(cid)
    if clip is None or current is None:
        raise ValueError(f'{cid}: scoped review requires a current selected video')
    if artifact.get('video') != current['video'] or artifact.get('take') != current['take']:
        raise ValueError(f'{cid}: review_file video/take does not match the current selected take')

    old = (previous.get('clips') or {}).get(cid) or {}
    if old.get('technical'):
        raise ValueError(f'{cid}: a current technical failure cannot be cleared by a picture review')
    media = read_json(directory / 'thin_media_report.json', {})
    selected = next((row.get('selected') or {} for row in media.get('clips', []) if row.get('clip_id') == cid), {})
    if selected.get('passed') is not True or cid in set(media.get('gate_failed_clips') or []):
        raise ValueError(f'{cid}: existing technical/media gate is not passed')
    raw_video = Path(selected.get('video') or '')
    if not raw_video.is_absolute():
        from novel_manga.application.review.store import ROOT
        raw_video = ROOT / raw_video
    task_id = read_json(raw_video.with_suffix(raw_video.suffix + '.task.json'), {}).get('task_id')
    if not task_id or artifact.get('task_id') != task_id:
        raise ValueError(f'{cid}: review_file task_id does not match the selected video generation task')

    expected_ids = list(clip.get('segment_ids') or [])
    from novel_manga.application.review.evidence import segment_texts
    texts = segment_texts(directory)
    expected_source = [{'segment_id': str(sid), 'text': str(texts.get(str(sid)) or '')} for sid in expected_ids]
    if artifact.get('source_evidence') != expected_source:
        raise ValueError(f'{cid}: review_file source_evidence does not match the current source segments')
    if artifact.get('source_segment_ids') != expected_ids:
        raise ValueError(f'{cid}: review_file source_segment_ids do not match the current clip')

    if artifact.get('assessment') not in {'pass', 'minor', 'fail'}:
        raise ValueError('review_file assessment must be pass, minor, or fail')
    if not isinstance(artifact.get('judgment'), str) or not artifact['judgment'].strip():
        raise ValueError('review_file judgment is required')
    if (artifact.get('repair_instruction') is not None
            and (not isinstance(artifact['repair_instruction'], str) or not artifact['repair_instruction'].strip())):
        raise ValueError('repair_instruction must be a non-empty string when supplied')
    observations = artifact.get('observations')
    if not isinstance(observations, list) or not observations:
        raise ValueError('review_file observations are required')
    has_video_frame = False
    for item in observations:
        if (not isinstance(item, dict) or item.get('kind') not in {'video_frame', 'reference'}
                or not isinstance(item.get('observation'), str) or not item['observation'].strip()):
            raise ValueError('each observation needs kind and a concrete observation')
        evidence_path = Path(item.get('file') or '')
        if evidence_path.is_absolute() or not evidence_path.parts:
            raise ValueError('observation files must be workspace-relative paths')
        evidence_path = work_dir / evidence_path
        if not evidence_path.resolve().is_relative_to(work_dir.resolve()) or not evidence_path.is_file():
            raise ValueError('observation file must exist inside the current episode work directory')
        if item.get('kind') == 'video_frame':
            has_time = (isinstance(item.get('times'), str) and bool(item['times'].strip())
                        or isinstance(item.get('time_seconds'), (int, float)))
            if not has_time:
                raise ValueError('video_frame observations require a time range or time_seconds')
            has_video_frame = True
    if not has_video_frame:
        raise ValueError('review_file needs at least one timed current video frame observation')

    required_findings = []
    for value in (old.get('identity_issue'), old.get('story_issue'), old.get('defect_issue')):
        if isinstance(value, str) and value.strip() and value not in required_findings:
            required_findings.append(value)
    presentation = old.get('presentation_check') or {}
    if (presentation.get('layout') in {'split', 'collage'}
            or presentation.get('body_scale') == 'obvious_error'
            or presentation.get('space_motion') == 'obvious_error'):
        value = presentation.get('evidence')
        if isinstance(value, str) and value.strip() and value not in required_findings:
            required_findings.append(value)
    precise = old.get('verify') or {}
    if precise.get('verdict') == 'obvious':
        for value in (precise.get('evidence'), precise.get('instruction')):
            if isinstance(value, str) and value.strip() and value not in required_findings:
                required_findings.append(value)
    claim = old.get('confirmed') or {}
    confirmed_check = claim.get('check') or {}
    check_evidence = (confirmed_check.get('evidence')
                      if confirmed_check.get('result') in {'unresolved', 'uncertain'} else None)
    for value in (claim.get('issue'), claim.get('instruction'), check_evidence):
        if isinstance(value, str) and value.strip() and value not in required_findings:
            required_findings.append(value)
    reviewed = artifact.get('reviewed_findings')
    if not isinstance(reviewed, list):
        raise ValueError('review_file reviewed_findings is required')
    actual = {}
    for item in reviewed:
        if (not isinstance(item, dict) or not isinstance(item.get('finding'), str)
                or item.get('disposition') not in {'resolved', 'not_demonstrated', 'upheld'}):
            raise ValueError('reviewed_findings need a finding and disposition')
        if item['finding'] in actual:
            raise ValueError('reviewed_findings must not duplicate a finding')
        actual[item['finding']] = item['disposition']
    if set(actual) != set(required_findings):
        raise ValueError('reviewed_findings must address each current automated blocker exactly once')
    if artifact['assessment'] in {'pass', 'minor'} and 'upheld' in actual.values():
        raise ValueError('pass/minor assessment cannot retain an upheld current blocker')

    independent = artifact.get('independent_review_file')
    if independent is not None:
        independent_path = Path(independent)
        if independent_path.is_absolute():
            raise ValueError('independent_review_file must be workspace-relative')
        independent_path = work_dir / independent_path
        if (not independent_path.resolve().is_relative_to(work_dir.resolve())
                or not independent_path.is_file()):
            raise ValueError('independent_review_file must exist inside the current episode work directory')
        independent_data = read_json(independent_path, None)
        if not isinstance(independent_data, dict):
            raise ValueError('independent_review_file must contain a JSON object')
        artifact = {**artifact, 'independent_review': independent_data}
    return artifact, current, old


def _publish_scoped_agent_review(directory, cid, artifact, current, previous, mode):
    """Publish a validated explicit Agent adjudication through the existing confirm evidence lane."""
    from novel_manga.episodes import chapter_of
    from novel_manga.application.review import confirmed
    from novel_manga.application.review.store import current_takes
    from novel_manga.review import reconciliation
    from novel_manga.review.contracts import POLICY

    old = (previous.get('clips') or {}).get(cid) or {}
    assessment = artifact['assessment']
    criteria = artifact['review_criteria']
    check = {'result': 'unresolved' if assessment == 'fail' else 'resolved',
             'video': current['video'], 'take': current['take'],
             'review_criteria': criteria, 'source': 'scoped Agent review_file',
             'evidence': artifact['judgment'], 'observations': artifact['observations']}
    old_claim = copy.deepcopy(old.get('confirmed'))
    if assessment == 'fail':
        claim = {'video': current['video'], 'take': current['take'],
                 'issue': artifact['judgment'],
                 'instruction': artifact.get('repair_instruction') or artifact['judgment'],
                 'source': 'scoped Agent review_file', 'check': check}
    else:
        claim = old_claim
        if claim:
            claim['check'] = check
    visual_verdict = {'pass': 'fine', 'minor': 'subtle', 'fail': 'obvious'}[assessment]
    row = {**current, 'severity': assessment, 'tier': 'must_fix' if assessment == 'fail' else 'optional',
           'review_criteria': criteria, 'verify': {'verdict': visual_verdict, 'evidence': artifact['judgment']},
           'story_ok': assessment != 'fail', 'story_kind': 'none' if assessment != 'fail' else 'other',
           'visual_defects': assessment == 'fail', 'defect_issue': artifact['judgment'] if assessment == 'fail' else '',
           'feedback': artifact['judgment'] if assessment == 'fail' else '',
           'scoped_agent_review': artifact,
           'contested_evidence': {'prior_automated_review': old,
                                  'reviewed_findings': artifact['reviewed_findings']}}
    from novel_manga.application.profiles import speech_gate_result
    from novel_manga.media.speech_repair import correction
    media = read_json(directory / 'thin_media_report.json', {})
    selected = next((entry.get('selected') or {} for entry in media.get('clips', [])
                     if entry.get('clip_id') == cid), {})
    row['speech_gate'] = speech_gate_result(directory.parent, selected, directory)
    if row['speech_gate'].get('passed') is not True:
        row.update(severity='fail', tier='must_fix', feedback=(
            correction(next(c for c in read_json(directory / 'clip_plan.json', {}).get('clips', [])
                            if c.get('clip_id') == cid), selected, include_missing=True)
            or '当前语音门未通过'))
    if claim:
        row['confirmed'] = claim
    record = {'ep': chapter_of(directory.name), 'clip': cid, 'mode': 'confirm', 'policy': POLICY,
              'review_mode': mode, **current, 'verdict': visual_verdict,
              'evidence': artifact['judgment'], 'instruction': row['feedback'],
              'review_criteria': criteria, 'task_id': artifact['task_id'],
              'author_session': artifact['author_session'], 'source_evidence': artifact['source_evidence'],
              'picture_review': row}
    if artifact.get('independent_review_file'):
        record['independent_review_file'] = artifact['independent_review_file']
        record['independent_review'] = artifact['independent_review']

    plan = read_json(directory / 'clip_plan.json', {})
    takes = current_takes(directory, plan, previous)
    if takes.get(cid) != current:
        raise ValueError(f'{cid}: selected take changed while publishing scoped review')
    state = directory.parent / 'repair_manager'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'verified.jsonl').open('a', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()
    key = reconciliation.evidence_key(record['ep'], cid, current['video'], current['take'])
    result = reconciliation.reconcile_review(directory, plan, previous, takes, {key: record}, {})
    enforcement_previous = copy.deepcopy(previous)
    if claim:
        enforcement_previous.setdefault('clips', {}).setdefault(cid, {})['confirmed'] = claim
    elif old_claim:
        enforcement_previous.setdefault('clips', {}).setdefault(cid, {}).pop('confirmed', None)
    result = confirmed.enforce(directory, result, enforcement_previous,
                              verify=False, review_criteria={cid: criteria})
    return result


def recheck_current_clip(directory: Path, cid: str, *, review_artifact=None, work_dir=None,
                         author_session=None) -> dict:
    """Review a keep proposal against unchanged inputs; publish only its current-take verdict."""
    from novel_manga.application.review import cast_video, confirmed, judges
    from novel_manga.application.profiles import speech_gate_result
    from novel_manga.media.speech_repair import correction, merge_note
    from novel_manga.models.bible import StoryBible
    from novel_manga.review.policy import compose_feedback, flag_line
    from novel_manga.application.review.evidence import review_mode
    from novel_manga.review.contracts import POLICY, current_review

    directory = Path(directory)
    refresh_speech_evidence(directory)
    plan = read_json(directory / 'clip_plan.json', {})
    script = read_json(directory / 'chapter_script.json', {})
    notes = read_json(directory / 'review_feedback.json', {})
    previous = read_json(directory / 'episode_review.json', {})
    criteria = confirmed.clip_review_criteria(directory, cid)
    mode = 'verify' if review_mode(directory/'work/keep_review'/cid) == 'verify' else 'classic'
    clip = next((c for c in plan.get('clips', []) if c.get('clip_id') == cid and c.get('kind') == 'video'), None)
    current = current_takes(directory, plan, previous).get(cid)
    if clip is None or current is None:
        raise ValueError(f'{cid}: keep recheck requires a current clip and selected video')
    media = read_json(directory / 'thin_media_report.json', {})
    selected = next((r.get('selected') or {} for r in media.get('clips', []) if r.get('clip_id') == cid), {})
    bible = StoryBible.model_validate(read_json(directory.parent / 'story_bible.json', {}))
    times = read_json(directory.parent / 'visual_grammar.json', {}).get('location_time', {})
    checked = copy.deepcopy(clip)
    checked['_review_video_model'] = cast_video.recorded_model(Path(selected.get('video') or current['video']))
    scoped_artifact = None
    try:
        if review_artifact is not None:
            if work_dir is None:
                raise ValueError('review_file requires its episode work directory')
            scoped_artifact, validated_current, _ = validate_scoped_agent_review(
                directory, cid, review_artifact, work_dir=work_dir, author_session=author_session)
            if validated_current != current:
                raise ValueError('review_file take changed during validation')
        else:
            options = {'review_criteria': criteria} if criteria else {}
            verdict = judges.judge_clip(checked, Path(current['video']), bible, times,
                                       selected.get('hypothesis', ''), directory / 'work/keep_review' / cid,
                                       source_script=copy.deepcopy(script), **options)
            if verdict.get('severity') not in {'pass', 'minor', 'fail'} or verdict.get('error'):
                raise ValueError('shared picture review incomplete: ' + str(verdict.get('error') or verdict.get('severity')))
            scoped = confirmed.enforce(directory, {'clips': {cid: {**verdict, **current}}, 'feedback': {}},
                                       previous, verify=True, review_criteria={cid: criteria})
    except Exception as error:
        raise ValueError(f'{cid}: keep recheck failed: {error}') from error
    if (read_json(directory / 'clip_plan.json', {}) != plan
            or read_json(directory / 'chapter_script.json', {}) != script
            or read_json(directory / 'review_feedback.json', {}) != notes):
        raise ValueError(f'{cid}: episode inputs changed during keep recheck')
    if current_takes(directory, plan, read_json(directory / 'episode_review.json', {})).get(cid) != current:
        raise ValueError(f'{cid}: selected take changed during keep recheck')

    if scoped_artifact is not None:
        review = _publish_scoped_agent_review(directory, cid, scoped_artifact, current, previous, mode)
        scoped = {'clips': {cid: review['clips'][cid]}, 'feedback': review.get('feedback', {})}
    else:
        review = None

    row = scoped['clips'][cid]
    if criteria:
        row['review_criteria'] = criteria
    row.update(policy=POLICY, review_mode=mode)
    speech = speech_gate_result(directory.parent, selected, directory)
    row['speech_gate'] = speech
    if speech.get('passed') is not True:
        row.update(severity='fail', tier='must_fix', feedback=merge_note(row.get('feedback', ''),
                   correction(clip, selected, include_missing=True) or '当前语音门未通过'))
    accepted = row.get('severity') in {'pass', 'minor'} and speech.get('passed') is True
    row['tier'] = 'optional' if accepted else 'must_fix'
    row['source_segment_ids'] = list(clip.get('segment_ids') or [])
    if review is None:
        review = copy.deepcopy(previous)
        if not current_review(previous, mode):
            review.pop('review_mode', None)  # one rechecked clip cannot certify the other clips' mode
        review.setdefault('clips', {})[cid] = row
    review.setdefault('clips', {})[cid] = row
    review.setdefault('feedback', {}).pop(cid, None)
    review['flags'] = [flag for flag in review.get('flags', []) if not str(flag).startswith(f'{cid}:')]
    if not accepted:
        note = compose_feedback(row, clip) or previous.get('feedback', {}).get(cid) or '当前片段复查未通过'
        review['feedback'][cid] = note
        review['flags'].append(flag_line(cid, row, 'must_fix'))
    atomic_write_json(directory / 'episode_review.json', review)
    history.observe(directory, review, {cid: current})
    return {'changed': [], 'accepted': [cid] if accepted else [], 'skip_render': True, 'review': review}


def save_result(directory, media, review, *, plan=None, rounds=(), corrections=(), blocked=None,
                write_media=True):
    """The same technical/content acceptance for a single run and a batch review."""
    plan = plan if plan is not None else read_json(directory / 'clip_plan.json', {})
    from novel_manga.application.review import final_cut
    final=media.get('final_cut_review') or read_json(directory/'final_cut_review.json',None)
    if final is not None:media['final_cut_review']=final
    final_current=final_cut.current(directory,media,final) if final is not None else False
    if final_current:
        review=final_cut.apply_feedback(directory,review,final)
        atomic_write_json(directory/'episode_review.json',review)
    planned = {c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video'}
    # Keep the established handling of historical unstamped media. A known stale
    # report, however, cannot certify a newly compiled plan by merely re-reading it.
    stale = ((media.get('clip_plan_fingerprint') is not None
              and media['clip_plan_fingerprint'] != plan_fingerprint(plan))
             or (bool(media.get('prompt_h3_fingerprint'))
                 and media['prompt_h3_fingerprint'] != h3_prompt_fingerprint(plan))
             or (bool(media) and (media.get('review_feedback') or {})
                 != read_json(directory / 'review_feedback.json', {})))
    acceptance = evaluate_acceptance(planned, media, review, stale=stale)
    result = acceptance.quality_report(
        rounds=rounds, corrections=corrections, blocked=blocked,
        generated_counts=managed.generated_counts(history.load(directory)),
    )
    native=final_cut.apply_feedback(directory,review,None)
    result['native_content_passed']=evaluate_acceptance(planned,media,native,stale=stale).content_passed
    if final is not None:
        final_passed=final_current and final.get('complete') is True and final.get('passed') is True and not final.get('issues') and not final.get('errors')
        result.update(final_cut_passed=bool(final_passed),
                      final_cut_complete=final_current and final.get('complete') is True,
                      final_cut_errors=final.get('errors',[]))
        if not final_passed:
            result.update(passed=False,content_passed=False,
                          reason='final movie review failed or is incomplete/stale')
    media['quality_review'] = result
    if write_media:
        atomic_write_json(directory / 'thin_media_report.json', media)
    atomic_write_json(directory / 'episode_execution.json', result)
    return media


def review_only(directory: Path, *, fresh=None, video_name='clip.mp4'):
    """Audit material already on disk; no renderer, preparation, trial or retake is started."""
    if video_name == 'clip.mp4':
        history.adopt_reviewed_run(directory)
    review = review_saved(directory, fresh=fresh, video_name=video_name)
    if video_name == 'clip.mp4':
        path = directory / 'thin_media_report.json'
        media = read_json(path, {})
        previous = media.get('quality_review') or {}
        save_result(directory, media, review, rounds=previous.get('repair_rounds', []),
                    corrections=previous.get('automatic_corrections', []),
                    blocked=previous.get('blocked', {}), write_media=path.is_file())
    return review
