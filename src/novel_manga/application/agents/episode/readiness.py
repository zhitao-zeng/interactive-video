"""One legal next action and deterministic request for the episode driver.

The host owns normal stage routing from current artifacts; an Agent is needed
only when the route requires an authored decision. This module never generates,
starts a job, or treats a completed job's old unresolved list as pending work.
"""
from __future__ import annotations

import hashlib
import json

from novel_manga.util import read_json
from novel_manga.application.production.artifacts import media_started


def first_frame_blocks(ctx, plan):
    """Current video clips whose approved opening frame is missing or stale."""
    from novel_manga.media.first_frames import enabled, require_current
    if not enabled(ctx):
        return {}
    blocks = {}
    for clip in plan.get('clips', []):
        if clip.get('kind') != 'video':
            continue
        if not clip.get('first_frame'):
            # Render cannot invent an approved opening; first_frames owns it.
            blocks[clip['clip_id']] = 'first frame missing; use first_frames'
            continue
        try:
            require_current(ctx, clip)
        except (ValueError, OSError) as error:
            blocks[clip['clip_id']] = str(error)
    return blocks


def _opening_author_reason(rows):
    findings=[finding for row in rows.values() for finding in row.get('source_conflicts',[])]
    if findings and all(row.get('source_conflicts') for row in rows.values()) and all(f.get('kind')=='continuity_clarification'
                        and f.get('relation')=='underspecified'
                        and f.get('continuity_verified') is True for f in findings):
        return ('The current opening needs author clarification of the cited continuity context. '
                'Write source-supported action=input decisions in output/repair.json; '
                'call check then apply before first_frames or render.')
    return ('Current opening source fields conflict. Write source-supported action=input decisions in output/repair.json; '
            'call check then apply before first_frames or render. Reusing or redrawing an image cannot resolve the source conflict.')


def review_is_current(ctx, review):
    from novel_manga.application.review.evidence import review_mode
    from novel_manga.review.contracts import current_review
    if not review.get('clips') or not current_review(
            review, review_mode(ctx.episode_dir / 'work/review/current')):
        return False
    from .evidence import EpisodeEvidence
    media = read_json(ctx.episode_dir / 'thin_media_report.json', {})
    return not EpisodeEvidence.unreviewed_clips(media, review)


def _episode_json(ctx, name):
    directory = getattr(ctx, 'episode_dir', None)
    return read_json(directory / name, {}) if directory is not None else {}


def _request_version(plan, media):
    from novel_manga.application.profiles import h3_prompt_fingerprint, plan_fingerprint
    return {'current_plan': plan_fingerprint(plan), 'current_h3': h3_prompt_fingerprint(plan),
            'selected_plan': media.get('clip_plan_fingerprint'),
            'selected_h3': media.get('prompt_h3_fingerprint')}


def _source_plan(plan):
    fields = ('clip_id', 'kind', 'shot_indexes', 'segment_ids', 'shots')
    return [{key: clip[key] for key in fields if key in clip}
            for clip in plan.get('clips', []) if clip.get('kind') == 'video']


def _take_state(ctx, plan, review):
    if getattr(ctx, 'episode_dir', None) is None:
        return {}
    from novel_manga.application.review.store import current_takes
    return current_takes(ctx.episode_dir, plan, review)


def _review_policy(ctx):
    from novel_manga.review.contracts import REVIEW_POLICY
    mode = None
    if getattr(ctx, 'episode_dir', None) is not None:
        from novel_manga.application.review.evidence import review_mode
        mode = review_mode(ctx.episode_dir / 'work/review/current')
    return {'policy': REVIEW_POLICY, 'mode': mode}


def _review_state(review):
    fields = ('video', 'take', 'severity', 'complete', 'passed', 'confirmed', 'resolved')
    clips = {}
    for cid, row in (review.get('clips') or {}).items():
        current = {key: row[key] for key in fields if key in row}
        check = row.get('check') or {}
        if check:
            current['check'] = {key: check[key] for key in ('result', 'policy', 'review_mode') if key in check}
        clips[cid] = current
    return {'policy': review.get('policy'), 'review_mode': review.get('review_mode'),
            'feedback': review.get('feedback') or {},
            'final_cut_feedback': review.get('final_cut_feedback') or {}, 'clips': clips}


def _video_identity(video):
    if not video:
        return None
    from pathlib import Path
    from novel_manga.review.storage import take_identity
    path = Path(video)
    return {'video': str(path), 'take': take_identity(path)}


def _final_cut_state(ctx, media, visual, assembly):
    from novel_manga.application.review import final_cut, independent
    directory = getattr(ctx, 'episode_dir', None)
    if directory is None:
        return {'policy': final_cut.POLICY, 'context': None}
    movie = visual.get('movie') or assembly.get('final_video')
    current_context = final_cut.context(directory, media, movie) if movie else None
    independent_required = independent.required(directory)
    saved_independent = _episode_json(ctx, 'independent_review.json') if independent_required else {}
    embedded_independent = (visual.get('independent_review') or {}) if independent_required else {}
    verdict_fields = ('movie', 'policy', 'complete', 'passed', 'issues', 'errors', 'context')
    return {
        'policy': final_cut.POLICY,
        'saved_policy': visual.get('policy'),
        'context': current_context,
        'saved_context': visual.get('context'),
        'independent': {
            'required': independent_required,
            'policy': independent.POLICY,
            'saved': {key: saved_independent[key] for key in verdict_fields if key in saved_independent},
            'embedded': {key: embedded_independent[key] for key in verdict_fields if key in embedded_independent},
        },
    }


from novel_manga.application.review.final_audio import freshness as _audio_freshness


def _acceptance_state(ctx, media):
    def fields(row, names):
        return {key: row[key] for key in names if key in row}

    assembly = media.get('assembly') or {}
    visual = media.get('final_cut_review') or {}
    audio = media.get('final_audio_review') or _episode_json(ctx, 'final_audio_review.json')
    audio_required = media.get('final_audio_review_required') is True
    movie = visual.get('movie') or assembly.get('final_video')
    return {
        'quality_review': fields(media.get('quality_review') or {},
            ('passed', 'content_passed', 'remaining', 'missing_reviews', 'review_errors')),
        'assembly': fields(assembly,
            ('final_video', 'needs_subtitle_refresh', 'pending_publish', 'thin_passed')),
        'final_video_identity': _video_identity(assembly.get('final_video')),
        'final_cut_review': fields(visual,
            ('movie', 'complete', 'passed', 'issues', 'errors', 'final_audio_review_required')),
        'final_cut': _final_cut_state(ctx, media, visual, assembly),
        'reviewed_movie_identity': _video_identity(visual.get('movie')),
        'final_audio_review_required': audio_required,
        'final_audio_review_status': media.get('final_audio_review_status'),
        'final_audio_review': fields(audio, ('movie', 'complete', 'passed', 'issues', 'errors')),
        'final_audio_freshness': _audio_freshness(ctx, audio, movie) if audio_required or audio else None,
    }


def _remaining_targets(ctx, status, action, plan, media, review, takes):
    from .evidence import EpisodeEvidence
    unreviewed = status.get('unreviewed_clips')
    if unreviewed is None:
        unreviewed = EpisodeEvidence.unreviewed_clips(media, review)
    if action == 'render':
        return {'without_selected_take': [clip['clip_id'] for clip in plan.get('clips', [])
                    if clip.get('kind') == 'video' and clip['clip_id'] not in takes]}
    if action == 'review':
        return {'unreviewed_clips': sorted(unreviewed)}
    return {'unreviewed_clips': sorted(unreviewed),
            'publication_blockers': status.get('publication_blockers') or []}


def _automatic_fingerprint(ctx, status, request, plan):
    action = request['action']
    inputs = {'input_repairs': status.get('input_repairs') or {}}
    if action == 'first_frames':
        targets = {row['clip_id'] for row in request['document']['frames']}
        inputs['clips'] = [clip for clip in plan.get('clips', [])
                           if clip.get('clip_id') in targets]
        frame_rows = (status.get('first_frames') or {}).get('clips') or {}
        fields = ('invalid_fields','argument_errors','repair_origin','repair_grant')
        inputs['preflight'] = {cid:{key:frame_rows[cid][key] for key in fields if key in frame_rows[cid]}
                               for cid in sorted(targets) if cid in frame_rows}
        inputs['references'] = {}
        if getattr(ctx, 'novel_dir', None) is not None:
            from novel_manga.media.first_frames import compile_opening
            for clip in inputs['clips']:
                cid = clip['clip_id']
                errors = {}
                try:
                    compiled = compile_opening(ctx, clip,
                        {'clip_id':cid, 'extra_references':[]},
                        field_errors=errors, require_assets=False)
                except (OSError, ValueError) as error:
                    inputs['references'][cid] = {'error':f'{type(error).__name__}: {error}'}
                else:
                    inputs['references'][cid] = {
                        'invalid_fields':sorted(errors), 'argument_errors':errors,
                        'image_references':compiled.row.get('image_references'),
                        'assets':compiled.assets,
                        'references':[reference.as_dict() for reference in compiled.references],
                    }
    else:
        media = _episode_json(ctx, 'thin_media_report.json')
        review = _episode_json(ctx, 'episode_review.json')
        takes = _take_state(ctx, plan, review)
        inputs.update(request_version=_request_version(plan, media), takes=takes,
                      targets=_remaining_targets(ctx, status, action, plan, media, review, takes))
        if action in {'review', 'finish'}:
            inputs.update(review_policy=_review_policy(ctx), source_plan=_source_plan(plan))
        if action == 'finish':
            inputs.update(review=_review_state(review), acceptance=_acceptance_state(ctx, media))
    normalized = {'route': request['route'], 'payload': request['payload'],
                  'document': request.get('document'), 'inputs': inputs}
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def automatic_request(ctx, status, *, first_pass_only=False):
    """Build one deterministic host request from the latest legal route."""
    if status.get('adopted') is not True:
        return None
    job = status.get('job') or {}
    if job.get('status') in {'accepted', 'running'}:
        return None
    route = next_action(ctx, status)
    if route.get('retryable') is False:
        return None
    action = route['action']
    allowed = {'first_frames', 'render'} if first_pass_only else {
        'first_frames', 'render', 'review', 'finish'}
    if action not in allowed:
        return None
    if action in {'review', 'finish'} and route.get('retryable') is False:
        return None
    if route.get('error') in {'source_repair_unavailable','compiler_source_disagreement'}:return None
    same_terminal = job.get('action') == action and job.get('status') in {'completed', 'failed'}
    previous_fingerprint = None
    if same_terminal:
        if (job.get('result') or {}).get('service_interrupted'):
            return None
        run_dir = getattr(ctx, 'run_dir', None)
        if run_dir is not None:
            saved = read_json(run_dir / 'agent-run.json', {})
            previous_fingerprint = (saved.get('next_action') or {}).get('automatic_fingerprint')
    request = {'action': action, 'route': route, 'payload': {}}
    plan = _episode_json(ctx, 'clip_plan.json')
    if action == 'first_frames':
        blocked = route.get('blocked') or {}
        if isinstance(blocked, dict):
            targets = set(blocked)
        elif isinstance(blocked, (list, tuple, set)):
            targets = set(blocked)
        else:
            return None
        rows = (status.get('first_frames') or {}).get('clips') or {}
        frames=[];review_recovery=False
        source_rechecks=set(route.get('source_preflight_rechecks') or {})
        for clip in plan.get('clips', []):
            cid=clip['clip_id']
            if clip.get('kind')!='video' or cid not in targets:continue
            frame_status=rows.get(cid) or {}
            incomplete=frame_status.get('review_incomplete') or {}
            if incomplete.get('retryable') is False:continue
            if frame_status.get('invalid_fields') or frame_status.get('repair_origin')=='registration':continue
            grant=frame_status.get('repair_grant') or {}
            if grant and grant.get('operation') not in {'reuse_candidate','resume'}:continue
            frame={'clip_id':cid,'extra_references':[]}
            if cid in source_rechecks:
                # The approved pixels are retained. The normal first_frames
                # entry first rechecks source, then reuses its current review.
                current=(rows.get(cid) or {}).get('first_frame') or clip.get('first_frame') or {}
                if not current.get('candidate'):return None
                frame['reuse_candidate']=current['candidate']
            elif incomplete:
                frame['reuse_candidate']=incomplete['candidate']
                review_recovery=True
            elif grant.get('operation')=='reuse_candidate':
                candidate=(grant.get('evidence') or {}).get('candidate')
                if not candidate:continue
                frame['reuse_candidate']=candidate
                review_recovery=True
            frames.append(frame)
        if not frames:
            return None
        request.update(payload={'file': 'output/first-frames.json'},
                       document={'render_ready': False, 'frames': frames})
        if same_terminal:
            if any(row['clip_id'] not in source_rechecks
                   and (rows.get(row['clip_id']) or {}).get('repair_origin') == 'image'
                   and (rows.get(row['clip_id']) or {}).get('last_rejection')
                   for row in frames):
                return None
    outcome = job.get('result') or {}
    ready_publication = (action == 'finish' and outcome.get('error') == 'publication_gate'
                         and outcome.get('clips') == [])
    if same_terminal and not previous_fingerprint and not (action=='first_frames' and (review_recovery or source_rechecks)) and not route.get('reconcile_request_rejections') and not ready_publication:
        return None
    fingerprint = _automatic_fingerprint(ctx, status, request, plan)
    if same_terminal and fingerprint == previous_fingerprint:
        return None
    return {**request, 'fingerprint': fingerprint}


def automatic_action(ctx, status, *, first_pass_only=False):
    """Compatibility view of the action selected by automatic_request()."""
    request = automatic_request(ctx, status, first_pass_only=first_pass_only)
    return request['action'] if request is not None else None


def _cached_request_rejections(ctx, plan, media):
    from novel_manga.application.preparation.request_check import cached_current_rejection
    clips={clip['clip_id']:clip for clip in plan.get('clips',[])}
    confirmed={}
    for row in media.get('clips') or []:
        cid=row.get('clip_id')
        if (cid not in clips or not row.get('error') or row.get('attempts') or row.get('selected')
                or row.get('service_interrupted') or row.get('required_action')=='reconcile_submission'):
            continue
        root=ctx.episode_dir/'work/clips'/cid
        if any(root.glob('attempt_*/clip.mp4.task.json')) or any(root.glob('attempt_*/clip.mp4')):
            continue
        answer=cached_current_rejection(ctx.episode_dir,clips[cid])
        if answer and (media.get('blocked_clips') or {}).get(cid)==['request: '+p for p in answer['problems']]:
            confirmed[cid]=answer
    return confirmed


def _persistent_service_failures(status, media, review):
    from novel_manga.service_errors import is_terminal_failure, service_failure, service_failure_from_saved
    failures = []

    def add(row, action, clip_id=None):
        if not is_terminal_failure(row) and not row.get('service_interrupted'):
            return
        current = dict(row)
        current['action'] = action
        if clip_id is not None:
            current['clip_id'] = clip_id
            if row.get('service_interrupted'):
                current['service_interrupted'] = {**row['service_interrupted'],
                    'clip_id': row['service_interrupted'].get('clip_id', clip_id)}
        failures.append(current)

    job = status.get('job') or {}
    add(job.get('result') or {}, job.get('action') or 'render')
    for row in media.get('clips') or []:
        add(row, 'render', row.get('clip_id'))
    for cid, row in (review.get('clips') or {}).items():
        add(row, 'review', cid)
    for cid, row in (status.get('input_repairs') or {}).items():
        if not row.get('service_interruption') and row.get('error_kind') != 'service_interrupted':
            continue
        saved = row.get('service_interruption')
        if saved:
            failure = service_failure_from_saved(saved, required_action=row.get('required_action'))
        else:
            text = str(row.get('error') or '')
            error = TimeoutError(text) if 'timeout' in text.lower() else RuntimeError(text)
            failure = service_failure(error)
        current = {**row, **failure,
            'service_interrupted': saved or failure.get('service_interrupted')
                or {'http_status': None, 'detail': str(row.get('error') or '')[:500]}}
        add(current, 'apply', cid)
    return failures


def _persistent_service_route(failures):
    from novel_manga.service_errors import service_vote
    first = next((row for row in failures if row.get('service_interrupted')), failures[0])
    route = {'action': first['action'],
             'reason': 'persistent service failures are not authored content findings',
             'service_failure_clips': sorted({row.get('clip_id') or (row.get('service_interrupted') or {}).get('clip_id')
                 for row in failures if row.get('clip_id') or (row.get('service_interrupted') or {}).get('clip_id')}),
             **service_vote(failures)}
    if not any(row.get('service_interrupted') for row in failures):
        route.update(error='media_execution_failed',
                     reason='current generation execution failed without a content finding; retain receipts and stop')
    if first.get('required_action') and 'required_action' not in route:
        route['required_action'] = first['required_action']
    elif first['action'] == 'apply' and 'required_action' not in route:
        route['required_action'] = 'resume_input'
    return route


def _final_service_route(ctx, media):
    assembly = media.get('assembly') or {}
    visual = media.get('final_cut_review') or {}
    audio = media.get('final_audio_review') or _episode_json(ctx, 'final_audio_review.json')
    visual_failed = visual.get('service_interrupted') and visual.get('movie')
    audio_failed = audio.get('service_interrupted') and audio.get('movie') == visual.get('movie')
    if not visual_failed and not audio_failed:
        return None
    state = _final_cut_state(ctx, media, visual, assembly)
    if (state.get('saved_policy') != state.get('policy')
            or state.get('saved_context') != state.get('context')):
        return None
    failures = []
    if visual_failed:
        failures.append({**visual, 'action':'finish'})
    if audio_failed:
        failures.append({**audio, 'action':'finish'})
    return _persistent_service_route(failures)


def next_action(ctx, status=None):
    """The precise legal next host action and its evidence.

    ``action`` is one of the existing tool names, ``wait`` for a running job,
    or ``agent`` when an authored decision is the missing work.  A completed
    job's unresolved-clips list is never a reason to wait.
    """
    status = status or {}
    plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    media = read_json(ctx.episode_dir / 'thin_media_report.json', {})
    review = read_json(ctx.episode_dir / 'episode_review.json', {})
    manifest = read_json(ctx.work_dir / 'input/manifest.json', {})
    if not plan.get('clips'):
        return {'action': 'adopt' if (ctx.work_dir / 'output/episode.json').is_file() else 'agent',
                'reason': 'no adopted episode plan is current'}
    job = status.get('job') or {}
    if job.get('status') in {'accepted', 'running'}:
        return {'action': 'wait', 'reason': f"{job.get('action')} {job.get('id')} is still running"}
    failures = _persistent_service_failures(status, media, review)
    if any(row.get('service_interrupted') for row in failures):
        return _persistent_service_route(failures)
    # A compiler's source claim is evidence for content checking, not a
    # confirmed pixel defect or an execution failure. Ignore it after the
    # relevant author source changes; ordinary compilation then checks anew.
    latest_frames=read_json(ctx.work_dir/'input/latest-first-frames.json',{})
    current_source=_episode_json(ctx,'chapter_script.json').get('shots') or []
    source_claims={};frame_failures={}
    for result in (latest_frames,job.get('result') or {}):
        if isinstance(result.get('clips'),dict):frame_failures.update(result['clips'])
    for cid,row in frame_failures.items():
        if not isinstance(row,dict):continue
        if row.get('stage')!='h3_source_review' or not row.get('source_conflicts'):continue
        current=[shot for shot in current_source if shot.get('index') in row.get('shot_indexes',[])]
        if current==row.get('source_shots'):source_claims[cid]=row
    if source_claims:
        disagreements={cid:row for cid,row in source_claims.items() if row.get('compiler_revision_used') is True
                       and (row.get('source_review') or {}).get('source_readable') is True
                       and not row['source_review'].get('issues')}
        if disagreements:
            return {'action':'first_frames','error':'compiler_source_disagreement','retryable':False,
                    'reason':'Independent source review did not confirm the compiler claim, but its single evidenced revision still conflicts; retain the image and stop.',
                    'blocked':disagreements}
        return {'action':'agent','reason':'H3 source claims were independently reviewed. Check the actual source_review issues through normal source-backed check/apply; do not assume every compiler claim is true, retake an approved image, or retry unchanged translation.',
                'request_input_conflicts':source_claims,'source_conflicts_confirmed':False,
                'blocked':{cid:row['source_conflicts'] for cid,row in source_claims.items()}}
    generation_failures = [row for row in failures if row['action'] == 'render'
                           and row.get('retryable') is False
                           and (row.get('error') or row.get('retake_error'))]
    request_rejections=_cached_request_rejections(ctx,plan,media)
    if generation_failures:
        # Old workers recorded a grounded input rejection as a terminal ValueError.
        # Re-run only this cached preflight through the normal renderer, so the
        # normal result replaces that old type before any authored input repair.
        covered=lambda row: (row.get('clip_id') in request_rejections or
            (isinstance(row.get('clips'),dict) and bool(row['clips']) and set(row['clips'])<=request_rejections.keys()))
        missing={clip['clip_id'] for clip in plan.get('clips',[]) if clip.get('kind')=='video'
                 and not any(row.get('clip_id')==clip['clip_id'] and (row.get('selected') or {}).get('video')
                             for row in media.get('clips') or [])}
        if (request_rejections and missing and missing<=request_rejections.keys()
                and all(covered(row) for row in generation_failures)):
            return {'action':'render','reason':'Reconcile confirmed cached request rejections through the normal renderer; no new submission is eligible',
                    'reconcile_request_rejections':sorted(request_rejections)}
        return _persistent_service_route(generation_failures)
    if request_rejections:
        return {'action':'agent','reason':'Current request check found a content conflict. Use source-supported input/edit repair: check then apply before render.',
                'request_input_conflicts':request_rejections,
                'blocked':{cid:answer['problems'] for cid,answer in request_rejections.items()}}
    preparation = read_json(ctx.work_dir/'input/latest-preparation.json', {})
    if preparation.get('error') == 'local_preparation_required' and media.get('blocked_clips'):
        from .local_preparation import SOURCE_AUDIT_POLICY
        cached = read_json(ctx.run_dir/'local-source-check.json', {})
        if cached.get('policy') != SOURCE_AUDIT_POLICY:
            return {'action': 'first_frames',
                    'reason': 'Source audit policy or original evidence changed; revalidate before authored repair',
                    'blocked': media['blocked_clips']}
        return {'action': 'agent',
                'reason': 'Current local source/input validation is unresolved. Verify the original evidence; use recheck_input '
                          'for a false finding, otherwise write supported input or edit decisions in output/repair.json. '
                          'Call check then apply before first_frames or render.',
                'blocked': media['blocked_clips'],
                **({'source_audit': preparation['source_audit']} if preparation.get('source_audit') else {})}
    from .opening_source import source_blockers, pending_context
    opening_conflicts = source_blockers(ctx, plan)
    if opening_conflicts:
        from novel_manga.media.first_frames import review_problems
        frame_rows = (status.get('first_frames') or {}).get('clips') or {}
        unsupported=[cid for cid in opening_conflicts if (frame_rows.get(cid) or {}).get('source_repair_unavailable')]
        if unsupported:
            return {'action':'first_frames','error':'source_repair_unavailable','retryable':False,
                    'reason':'Current source evidence has no supported field repair; retain it and stop.',
                    'blocked':{cid:opening_conflicts[cid] for cid in unsupported}}
        source_conflicts = {}
        for cid, findings in opening_conflicts.items():
            saved = (frame_rows.get(cid) or {}).get('last_rejection') or {}
            source_conflicts[cid] = saved if saved.get('source_conflicts') == findings else {
                'candidate': 'source-preflight', 'problems': review_problems({'findings': findings}),
                'source_conflicts': findings,
                'evidence': f'input/first_frames/{cid}/source-preflight.json'}
        return {'action':'agent',
                'reason':_opening_author_reason(source_conflicts),
                'source_input_conflicts':source_conflicts,
                'blocked':{cid:row.get('problems') or ['source input conflict'] for cid,row in source_conflicts.items()}}
    pending = pending_context(ctx, plan)
    frame_rows = (status.get('first_frames') or {}).get('clips') or {}
    rechecks = {}
    for clip in plan.get('clips', []):
        cid=clip.get('clip_id')
        if cid not in pending or not clip.get('first_frame'):continue
        row=frame_rows.get(cid) or {}
        if 'current' in row:
            current=row['current'] is True
        else:
            from novel_manga.media.first_frames import require_current
            try:require_current(ctx,clip);current=True
            except (ValueError,OSError):current=False
        if current:rechecks[cid]=pending[cid]
    if rechecks:
        return {'action':'first_frames',
                'reason':'A previously checked adjacent source changed; recheck its opening and reuse the approved image.',
                'blocked':{cid:'adjacent source preflight is stale' for cid in rechecks},
                'source_preflight_rechecks':rechecks}
    first = first_frame_blocks(ctx, plan)
    if first:
        frame_rows = (status.get('first_frames') or {}).get('clips') or {}
        source_conflicts={cid:row.get('last_rejection') or {}
                          for cid,row in frame_rows.items()
                          if cid in first and row.get('repair_origin')=='source'}
        if source_conflicts:
            return {'action':'agent',
                    'reason':_opening_author_reason(source_conflicts),
                    'source_input_conflicts':source_conflicts,
                    'blocked':{cid:row.get('problems') or ['source input conflict'] for cid,row in source_conflicts.items()}}
        route = {'action': 'first_frames',
                 'reason': 'accepted input/opening change needs a fresh reviewed opening before video',
                 'blocked': first}
        current_rows = {cid:frame_rows.get(cid) or {} for cid in first}
        incomplete={cid:row['review_incomplete'] for cid,row in current_rows.items() if row.get('review_incomplete')}
        if incomplete:
            route['review_incomplete']=incomplete
            route['reason']='Retained opening images need completed review; reuse their candidates without a new image'
            for cid in incomplete:first[cid]=current_rows[cid]['issue']
        for key in ('invalid_fields','argument_errors','repair_origin','repair_grant'):
            values = {cid:row[key] for cid,row in current_rows.items() if key in row}
            if values:
                route[key] = values
        registration_rows = {cid:row for cid,row in current_rows.items()
                             if row.get('repair_origin') or row.get('invalid_fields')
                             or row.get('argument_errors')}
        if (registration_rows.keys() == first.keys() and registration_rows
                and all(row.get('repair_origin') == 'registration' and not row.get('repair_grant')
                        for row in registration_rows.values())):
            route.update(error='first_frame_registration', retryable=False,
                         reason='Restore registered reference bindings, files and frozen hashes; no authored repair is authorized')
        return route
    from novel_manga.application.review.execution import media_matches
    notes = read_json(ctx.episode_dir / 'review_feedback.json', {})
    videos = [c for c in plan.get('clips', []) if c.get('kind') == 'video']
    if videos and (not media_started(ctx.episode_dir) or not media):
        return {'action': 'render', 'reason': 'opening and request inputs are current; no video has started'}
    if media and not media_matches(plan, media, notes=notes):
        return {'action': 'render', 'reason': 'current requests are newer than the selected takes'}
    if review.get('clips') and not review_is_current(ctx, review):
        return {'action': 'review', 'reason': 'review evidence is from an older policy or take'}
    if media.get('clips') and not review.get('clips'):
        return {'action': 'review', 'reason': 'selected takes have no current independent review'}
    review_errors = {cid: row for cid, row in (review.get('clips') or {}).items()
                     if row.get('severity') == 'review_error'}
    if review_errors:
        from novel_manga.service_errors import is_terminal_failure, service_failure, service_failure_from_saved, service_vote
        votes = []
        job_result = job.get('result') or {}
        if is_terminal_failure(job_result):
            votes.append(job_result)
        for cid, row in review_errors.items():
            outage = row.get('service_interrupted')
            if outage:
                failure = service_failure_from_saved(outage, required_action=row.get('required_action'))
            elif 'retryable' in row:
                failure = {'retryable':row['retryable']}
            else:
                failure = service_failure(RuntimeError(str(row.get('error') or 'review incomplete')))
            if 'retryable' in row:
                failure['retryable'] = row['retryable']
            votes.append({**row, **failure})
        route = {'action': 'review', 'reason': 'current review is incomplete; no content rejection was established',
                 'blocked': sorted(review_errors), **service_vote(votes)}
        required = next((row.get('required_action') for row in votes if row.get('required_action')), None)
        if required and 'required_action' not in route:
            route['required_action'] = required
        return route
    if (review.get('feedback') or media.get('failed_clips') or media.get('gate_failed_clips')
            or any(row.get('severity') == 'fail' for row in (review.get('clips') or {}).values())):
        return {'action':'agent','reason':'current findings require an authored repair decision before acceptance'}
    from novel_manga.application.music_selection import episode_music_errors
    if errors := episode_music_errors(ctx.episode_dir, media):
        return {'action': 'agent', 'required_action': 'select_music',
                'reason': '; '.join(errors), 'blocked': errors}
    assembly = media.get('assembly') or {}
    if media.get('status') == 'clips_ready' or assembly.get('needs_subtitle_refresh'):
        return {'action': 'finish', 'reason': 'normal assembly and its QC run during finish'}
    if assembly.get('final_video'):
        if (media.get('quality_review') or {}).get('passed') is not True:
            return {'action': 'agent', 'reason': 'current review needs an authored repair decision'}
        service_route = _final_service_route(ctx, media)
        if service_route is not None:
            return service_route
        final_visual = media.get('final_cut_review') or {}
        if (media.get('final_audio_review_required')
                and final_visual.get('complete') is True and final_visual.get('passed') is True
                and media.get('final_audio_review_status') != 'not_run_visual_gate'):
            audio = media.get('final_audio_review') or read_json(
                ctx.episode_dir/'final_audio_review.json', {})
            movie = final_visual.get('movie')
            if (audio.get('movie') != movie or audio.get('complete') is not True
                    or audio.get('passed') is not True or audio.get('issues') or audio.get('errors')):
                return {'action': 'agent', 'reason': 'the current delivered movie has unresolved or incomplete full-audio review',
                        'audio_review': audio}
        return {'action': 'finish', 'reason': 'current assembly and review are ready for acceptance'}
    if manifest.get('agent_phase') == 'input_repair':
        return {'action': 'agent', 'reason': 'source/input repair decision is pending'}
    if not media:
        return {'action': 'render', 'reason': 'no render evidence for the adopted plan'}
    return {'action': 'agent', 'reason': 'inspect current evidence and take the next authored decision'}
