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
    if media.get('final_audio_review_required'):
        audio = media.get('final_audio_review') or read_json(ctx.episode_dir/'final_audio_review.json', {})
        movie = (media.get('final_cut_review') or {}).get('movie')
        if (audio.get('movie') != movie or audio.get('complete') is not True
                or audio.get('passed') is not True or audio.get('issues') or audio.get('errors')):
            return {'action': 'agent', 'reason': 'the current delivered movie has unresolved or incomplete full-audio review',
                    'audio_review': audio}
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
    first = first_frame_blocks(ctx, plan)
    if first:
        return {'action': 'first_frames',
                'reason': 'accepted input/opening change needs a fresh reviewed opening before video',
                'blocked': first}
    from novel_manga.application.review.execution import media_matches
    notes = read_json(ctx.episode_dir / 'review_feedback.json', {})
    videos = [c for c in plan.get('clips', []) if c.get('kind') == 'video']
    if videos and not media_started(ctx.episode_dir):
        return {'action': 'render', 'reason': 'opening and request inputs are current; no video has started'}
    if media and not media_matches(plan, media, notes=notes):
        return {'action': 'render', 'reason': 'current requests are newer than the selected takes'}
    if review.get('clips') and not review_is_current(ctx, review):
        return {'action': 'review', 'reason': 'review evidence is from an older policy or take'}
    if media.get('clips') and not review.get('clips'):
        return {'action': 'review', 'reason': 'selected takes have no current independent review'}
    from novel_manga.application.music_selection import episode_music_errors
    if errors := episode_music_errors(ctx.episode_dir, media):
        return {'action': 'select_music', 'reason': '; '.join(errors), 'blocked': errors}
    assembly = media.get('assembly') or {}
    if media.get('status') == 'clips_ready' or assembly.get('needs_subtitle_refresh'):
        return {'action': 'finish', 'reason': 'normal assembly and its QC run during finish'}
    if assembly.get('final_video'):
        if (media.get('quality_review') or {}).get('passed') is not True:
            return {'action': 'agent', 'reason': 'current review needs an authored repair decision'}
        return {'action': 'finish', 'reason': 'current assembly and review are ready for acceptance'}
    if manifest.get('agent_phase') == 'input_repair':
        return {'action': 'agent', 'reason': 'source/input repair decision is pending'}
    if not media:
        return {'action': 'render', 'reason': 'no render evidence for the adopted plan'}
    return {'action': 'agent', 'reason': 'inspect current evidence and take the next authored decision'}
