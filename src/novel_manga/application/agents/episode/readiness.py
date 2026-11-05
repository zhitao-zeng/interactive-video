"""One legal next action for the existing episode driver.

The Agent remains the author of decisions; this module only reports which host
operation is actually ready from current artifacts.  It never generates,
starts a job, or treats a completed job's old unresolved list as pending work.
"""
from __future__ import annotations

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


def review_is_current(ctx, review):
    from novel_manga.application.review.evidence import review_mode
    from novel_manga.review.contracts import current_review
    if not review.get('clips') or not current_review(
            review, review_mode(ctx.episode_dir / 'work/review/current')):
        return False
    from .evidence import EpisodeEvidence
    media = read_json(ctx.episode_dir / 'thin_media_report.json', {})
    return not EpisodeEvidence.unreviewed_clips(media, review)


def automatic_action(ctx, status, *, first_pass_only=False):
    """Dispatch deterministic stages once; failed unchanged work returns to the Agent."""
    if status.get('adopted') is not True:
        return None
    job = status.get('job') or {}
    if job.get('status') in {'accepted', 'running'}:
        return None
    action = next_action(ctx, status)['action']
    allowed = {'render'} if first_pass_only else {'render', 'review', 'finish'}
    if action not in allowed:
        return None
    if job.get('action') == action and job.get('status') in {'completed','failed'}:
        return None
    return action


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
    from .input_records import service_blocker
    if blocker := service_blocker(status):
        return {'action': 'agent', 'reason': 'the shared service is unusable; stop this batch',
                'service_interrupted': blocker}
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
    from .opening_source import source_blockers
    opening_conflicts = source_blockers(ctx, plan)
    if opening_conflicts:
        from novel_manga.media.first_frames import review_problems
        frame_rows = (status.get('first_frames') or {}).get('clips') or {}
        source_conflicts = {}
        for cid, findings in opening_conflicts.items():
            saved = (frame_rows.get(cid) or {}).get('last_rejection') or {}
            source_conflicts[cid] = saved if saved.get('source_conflicts') == findings else {
                'candidate': 'source-preflight', 'problems': review_problems({'findings': findings}),
                'source_conflicts': findings,
                'evidence': f'input/first_frames/{cid}/source-preflight.json'}
        return {'action':'agent',
                'reason':'Current opening source fields conflict. Write source-supported action=input decisions in output/repair.json; '
                         'call check then apply before first_frames or render. Reusing or redrawing an image cannot resolve the source conflict.',
                'source_input_conflicts':source_conflicts,
                'blocked':{cid:row.get('problems') or ['source input conflict'] for cid,row in source_conflicts.items()}}
    first = first_frame_blocks(ctx, plan)
    if first:
        source_conflicts={cid:row.get('last_rejection') or {}
                          for cid,row in ((status.get('first_frames') or {}).get('clips') or {}).items()
                          if cid in first and row.get('repair_origin')=='source'}
        if source_conflicts:
            return {'action':'agent',
                    'reason':'Current opening source fields conflict. Write source-supported action=input decisions in output/repair.json; '
                             'call check then apply before first_frames. Reusing or redrawing an image cannot resolve the source conflict.',
                    'source_input_conflicts':source_conflicts,
                    'blocked':{cid:row.get('problems') or ['source input conflict'] for cid,row in source_conflicts.items()}}
        return {'action': 'first_frames',
                'reason': 'accepted input/opening change needs a fresh reviewed opening before video',
                'blocked': first}
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
    if (review.get('feedback') or media.get('failed_clips') or media.get('gate_failed_clips')
            or any(row.get('severity') in {'fail','review_error'} for row in (review.get('clips') or {}).values())):
        return {'action':'agent','reason':'current findings require an authored repair decision before acceptance'}
    from novel_manga.application.music_selection import episode_music_errors
    if errors := episode_music_errors(ctx.episode_dir, media):
        return {'action': 'select_music', 'reason': '; '.join(errors), 'blocked': errors}
    assembly = media.get('assembly') or {}
    if media.get('status') == 'clips_ready' or assembly.get('needs_subtitle_refresh'):
        return {'action': 'finish', 'reason': 'normal assembly and its QC run during finish'}
    if assembly.get('final_video'):
        if (media.get('quality_review') or {}).get('passed') is not True:
            return {'action': 'agent', 'reason': 'current review needs an authored repair decision'}
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
