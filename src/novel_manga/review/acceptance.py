"""Content and technical outcomes, independent of files, models and scheduling."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from novel_manga.review.verdicts import invalid_verdict_ids
from novel_manga.util import read_json


@dataclass(frozen=True)
class EpisodeAcceptance:
    content_passed: bool
    technical_passed: bool
    missing_reviews: list[str]
    review_errors: list[str]
    remaining: list[str]
    stale: bool

    @property
    def passed(self) -> bool:
        return self.content_passed and self.technical_passed

    def quality_report(self, *, rounds=(), corrections=(), blocked=None, generated_counts=None):
        result = {'passed':self.passed, 'content_passed':self.content_passed,
                  'repair_rounds':list(rounds), 'automatic_corrections':sorted(set(corrections)),
                  'blocked':blocked or {}, 'missing_reviews':self.missing_reviews,
                  'review_errors':self.review_errors, 'remaining':self.remaining,
                  'generated_counts':generated_counts}
        if self.stale:
            result['reason']='media predates the current plan, H3 request or corrections'
        return result


def evaluate_acceptance(planned, media, review, *, stale=False) -> EpisodeAcceptance:
    rows=review.get('clips') or {}
    missing=sorted(set(planned)-rows.keys())
    errors=invalid_verdict_ids(rows)
    content=bool(planned) and not media.get('failed_clips') and not media.get('gate_failed_clips') \
        and not review.get('feedback') and not missing and not errors and not stale
    technical=bool((media.get('assembly') or {}).get('thin_passed'))
    return EpisodeAcceptance(bool(content),technical,missing,errors,
                             sorted(review.get('feedback') or {}),bool(stale))


def acceptance_status(episode_dir, run_dir=None):
    import hashlib
    from novel_manga.application.production.runs import episode_status
    from novel_manga.application.review.final_cut import blocking_issues, current
    episode_dir = Path(episode_dir)
    run_dir = Path(run_dir) if run_dir else None
    media = read_json(episode_dir/'thin_media_report.json',{})
    quality = media.get('quality_review') or {}
    visual = media.get('final_cut_review') or read_json(episode_dir/'final_cut_review.json',{})
    audio = media.get('final_audio_review') or read_json(episode_dir/'final_audio_review.json',{})
    technical = episode_status(episode_dir,True)
    movie = str(visual.get('movie') or '')
    visual_ok = (visual.get('complete') is True and visual.get('passed') is True and bool(movie)
                 and not blocking_issues(visual.get('issues') or []) and not visual.get('errors'))
    audio_ok = (audio.get('movie') == movie and audio.get('complete') is True
                and audio.get('passed') is True and not audio.get('issues') and not audio.get('errors'))
    formal_ok = True
    if run_dir:
        manifest = read_json(run_dir/'final-candidate/manifest.json',{})
        formal = read_json(run_dir/'final-candidate/result.json',{})
        formal_visual = formal.get('final_cut_review') or {}
        formal_audio = formal.get('final_audio_review') or manifest.get('final_audio_review') or {}
        formal_ok = bool(manifest and manifest.get('passed') is True
                         and formal_visual.get('movie') == movie
                         and formal_audio.get('movie') == movie)
        visual_ok = visual_ok and current(episode_dir, media, visual)
    else:
        manifest = {}
        visual_ok = visual_ok and movie == str((media.get('assembly') or {}).get('final_video') or '')
    delivery = read_json(run_dir/'interactive-delivery.json',{}) if run_dir else {}
    if delivery:
        destination = Path(delivery.get('destination') or '')
        receipt = read_json(destination/'delivery.json',{})
        frozen = receipt.get('accepted_export') or {}
        cover = destination/'cover.jpeg'
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ''
        formal_ok = bool(formal_ok and receipt.get('acceptance_protocol') == 'current-export-v1'
                         and receipt.get('status') == 'accepted'
                         and receipt.get('final_video') == movie
                         and frozen.get('movie') == movie
                         and frozen.get('visual_context') == visual.get('context')
                         and frozen.get('audio_movie') == audio.get('movie')
                         and frozen.get('video_sha256') == digest(Path(movie))
                         and frozen.get('cover_sha256') == digest(cover))
    passed = (technical == 'done' and quality.get('passed') is True and visual_ok and audio_ok and formal_ok)
    if passed:
        status = 'accepted'
    elif technical in {'no_plan','pending','plan_blocked','stale'}:
        status = technical
    else:
        status = 'candidate'
    return {'status':status,'passed':passed,'technical_status':technical,
            'content_passed':quality.get('content_passed') is True,
            'final_visual':visual,'final_audio':audio,
            'formal_receipt':manifest}
