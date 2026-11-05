"""EpisodeCompletion: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from novel_manga.util import atomic_write_json, read_json


from .tool_state import record_state
from novel_manga.application.production import episode_state


class EpisodeCompletion:
    def __init__(self, ctx, media, evidence):
        self.ctx = ctx
        self.media = media
        self.evidence = evidence

    def finish(self, payload):
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            raise ValueError('first pass hands completed video to the review queue; finish belongs to that queue')
        from novel_manga.application.repair import history
        from novel_manga.application.review.execution import save_result, media_matches
        plan = read_json(self.ctx.episode_dir / 'clip_plan.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        rounds = read_json(self.ctx.run_dir / 'tool-repair-decisions.json', [])
        from novel_manga.story.voice_delivery import episode_native_speech_errors
        if speech_errors := episode_native_speech_errors(self.ctx.episode_dir,report):
            return {'ok':False,'passed':False,'error':'native_speech_policy','required_action':'apply',
                    'clips':speech_errors,'message':'Correct task speech policy, per-line performance and actual native requests before finishing; retain existing takes and limits.'}
        if (self.ctx.run_dir/'interactive-delivery.json').is_file():
            selected={row['clip_id']:(row.get('selected')or{}).get('video') for row in report.get('clips',[])}
            missing=[clip['clip_id'] for clip in plan.get('clips',[]) if clip.get('kind')=='video'
                     and (not selected.get(clip['clip_id']) or not Path(selected[clip['clip_id']]).is_file())]
            if missing:
                from .readiness import first_frame_blocks
                frames=first_frame_blocks(self.ctx,plan)
                return {'ok':False,'passed':False,'error':'missing_video','clips':missing,
                    'required_action':'first_frames' if frames else 'render','first_frame_blocks':frames,
                    'message':'No complete movie exists yet. Resolve the current opening/video blockers using the original budgets before assembly or music mixing.'}
        unreviewed = self.evidence.unreviewed_clips(report, review)
        if unreviewed:
            return {'ok': False, 'error': 'missing_review', 'required_action': 'review',
                    'clips': unreviewed, 'message': 'Generated takes need independent review before finishing.'}
        from novel_manga.review.contracts import REVIEW_POLICY
        if review.get('clips') and not self.evidence.current_review(review):
            return {'ok': False, 'error': 'stale_review', 'required_action': 'review',
                    'message': 'Run review with the current policy and configured mode before finishing existing video.',
                    'review_policy': review.get('policy'), 'expected_review_policy': REVIEW_POLICY}
        batch_review = read_json(self.ctx.work_dir/'input/manifest.json', {}).get('production_mode') == 'batch_review'
        content_pending = bool(review.get('feedback') or report.get('failed_clips')
            or report.get('gate_failed_clips') or any(row.get('severity') in {'fail','review_error'}
                for row in (review.get('clips') or {}).values()))
        assembly_pending = ((report.get('status') == 'clips_ready' or (report.get('assembly')or{}).get('needs_subtitle_refresh'))
                            and not (batch_review and content_pending))
        if (assembly_pending or (report.get('assembly')or{}).get('final_video')) and not media_matches(
                plan, report, notes=read_json(self.ctx.episode_dir/'review_feedback.json', {})):
            return {'ok': False, 'error': 'stale_media', 'required_action': 'render',
                    'message': 'Current requests need render before existing takes can be assembled or delivered.'}
        if assembly_pending:
            report = self.media.assemble(report)
        report = self.media.refresh_technical_policy(report, plan)
        from novel_manga.application.music_selection import episode_music_errors
        if music_errors:=episode_music_errors(self.ctx.episode_dir,report):
            return {'ok':False,'passed':False,'error':'music_not_applied','required_action':'select_music',
                    'issues':music_errors,'message':'Apply the already selected music before finishing; retain existing takes.'}
        rounds = rounds[read_json(self.ctx.run_dir / 'prepared.json', {}).get('repair_round_start', 0):]
        blocked = {}
        for row in rounds:
            for cid in (row.get('changed', []) + row.get('accepted', [])
                        + row.get('input_repaired', []) + row.get('risk_accepted', [])):
                blocked.pop(cid, None)
            # Older batches required a proposal for every failed clip. An
            # unsubmitted operation is pending work, not independent defect evidence.
            blocked.update({cid:reason for cid,reason in (row.get('blocked')or{}).items()
                            if reason!='agent omitted a decision'})
        report = save_result(self.ctx.episode_dir, report, review, plan=plan, rounds=rounds,
            corrections=[cid for row in rounds for cid in row.get('changed', [])], blocked=blocked)
        delivery=read_json(self.ctx.run_dir/'interactive-delivery.json',{})
        if delivery and (report.get('assembly') or {}).get('final_video'):
            from novel_manga.interactive.delivery import reviewed_delivery
            from novel_manga.application.review.final_cut import apply_feedback
            final=reviewed_delivery(self.media.runner(),report,Path(delivery['destination']))
            report['final_cut_review']=final
            review=apply_feedback(self.ctx.episode_dir,review,final)
            atomic_write_json(self.ctx.episode_dir/'episode_review.json',review)
            atomic_write_json(self.ctx.episode_dir/'final_cut_review.json',final)
            report['final_audio_review_required'] = final.get('final_audio_review_required') is True
            if report['final_audio_review_required']:
                report['final_audio_review'] = final.get('audio_review') or {}
            if final.get('passed') is True:
                report.pop('final_audio_review_status', None)
            report=save_result(self.ctx.episode_dir,report,review,plan=plan,rounds=rounds,
                corrections=[cid for row in rounds for cid in row.get('changed',[])],blocked=blocked)
            if final.get('passed') is not True:
                evidence=self.evidence.review_evidence(review,report)
                actionable=any(history.remaining_takes(self.ctx.episode_dir,cid,preparation=True)>0
                               for cid in review.get('final_cut_feedback',{}))
                if actionable or final.get('complete') is not True:
                    return {'ok':False,'passed':False,'error':'final_movie_review_requires_repair',
                            'final_cut_review':final,'review_evidence':evidence,
                            'next':'Repair the current full-movie findings with the existing tools, then render and finish; retain all counts.'}
            if report.get('final_audio_review_required') and final.get('passed') is True:
                audio_review = report.get('final_audio_review') or {}
                final_movie = Path(final.get('movie') or '')
                audio_current = (audio_review.get('movie') == str(final_movie.resolve())
                    and audio_review.get('complete') is True and audio_review.get('passed') is True
                    and not audio_review.get('issues') and not audio_review.get('errors'))
                if not audio_current:
                    return {'ok': False, 'passed': False, 'error': 'final_audio_review_requires_repair',
                        'required_action': 'apply', 'movie': str(final_movie),
                        'final_audio_review': audio_review,
                        'next': 'Use the audio findings returned by final review with the existing repair tools; finish again after the current export passes audio review.'}
            elif report.get('final_audio_review_required'):
                report['final_audio_review_status'] = 'not_run_visual_gate'
        quality = report.get('quality_review') or {}
        episode_state.record(self.ctx.run_dir, 'acceptance_saved',
                             result='passed' if quality.get('passed') else 'needs_review',
                             passed=quality.get('passed') is True, remaining=quality.get('remaining', []))
        if (review.get('review_mode') == 'verify'
                and (report.get('quality_review') or {}).get('passed') is True
                and (report.get('assembly') or {}).get('pending_publish')):
            from novel_manga.application.repair.delivery import publish_if_ready, precise_review_blockers
            from novel_manga.application.review.store import current_takes
            from novel_manga.application.production.runs import episode_status
            takes = current_takes(self.ctx.episode_dir, plan, review)
            publication_inputs=None
            if delivery:
                from novel_manga.interactive.delivery import export_context
                publication_inputs=export_context(self.media.runner(),report)
            if not publish_if_ready(self.ctx.episode_dir, review, takes):
                expected = [c['clip_id'] for c in plan.get('clips', []) if c.get('kind') == 'video']
                unresolved = precise_review_blockers(review, takes, expected)
                return {'ok': False, 'passed': False, 'error': 'publication_gate',
                        'required_action': 'apply', 'clips': unresolved,
                        'publication_status': episode_status(self.ctx.episode_dir, True),
                        'message': 'The existing publication gate has not passed. Inspect current source/video evidence '
                                   'and use a bounded keep check or repair; a source exception alone does not clear '
                                   'an unresolved precise verdict.'}
            report = read_json(self.ctx.episode_dir / 'thin_media_report.json')
            if delivery:
                from novel_manga.interactive.delivery import record_native_publication
                record_native_publication(self.media.runner(),report,Path(delivery['destination']),publication_inputs)
            episode_state.record(self.ctx.run_dir, 'published', result='published',
                                 video=(report.get('assembly') or {}).get('final_video'))
        atomic_write_json(self.ctx.run_dir / 'result.json', report)
        final = self.ctx.run_dir / 'final-candidate'
        final.mkdir(exist_ok=True)
        movie = (report.get('assembly') or {}).get('final_video')
        copied_movie = None
        if movie and Path(movie).is_file():
            copied_movie = final / f'{self.ctx.episode_dir.name}-episode-agent.mp4'
            shutil.copy2(movie, copied_movie)
        for path in (self.ctx.run_dir / 'result.json', self.ctx.episode_dir / 'chapter_script.json',
                     self.ctx.episode_dir / 'clip_plan.json', self.ctx.episode_dir / 'episode_review.json',
                     self.ctx.episode_dir / 'episode_execution.json'):
            if path.is_file():
                shutil.copy2(path, final / path.name)
        for source in self.ctx.episode_dir.rglob('request.json'):
            target = final / 'requests' / source.relative_to(self.ctx.episode_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        voices = {path.parent.name: read_json(path, {}).get('generated_voice_takes', 0)
                  for path in (self.ctx.episode_dir / 'work/inner_voice').glob('*/voice.json')}
        quality = report.get('quality_review') or {}
        author_session = read_json(self.ctx.work_dir / 'episode-session.json', {})
        audio_required = report.get('final_audio_review_required') is True
        audio_review = report.get('final_audio_review') or {}
        reviewed_movie = Path((report.get('final_cut_review') or {}).get('movie') or '')
        final_visual = report.get('final_cut_review') or {}
        final_visual_passed = not final_visual or final_visual.get('passed') is True
        audio_passed = (not audio_required or
            (reviewed_movie.is_file() and audio_review.get('movie') == str(reviewed_movie.resolve())
             and audio_review.get('complete') is True and audio_review.get('passed') is True
             and not audio_review.get('issues') and not audio_review.get('errors')))
        manifest = {'method': ('one persistent sandbox agent owns authoring and host tool calls' if author_session
                              else 'existing requests executed by host with independent acceptance'),
            'passed': quality.get('passed') is True and final_visual_passed and audio_passed,
            'final_audio_review_required': audio_required,
            **({'final_audio_review_status': report['final_audio_review_status']}
               if report.get('final_audio_review_status') else {}),
            **({'final_audio_review': audio_review} if audio_required else {}),
            'quality_review': quality,
            'technical_passed': (report.get('assembly') or {}).get('thin_passed') is True,
            'actual_video_counts': history.generation_counts(self.ctx.episode_dir),
            'voice_recording_counts': voices, 'voice_recording_blocked': self.evidence.voice_recording_blocks(plan),
            'author_session': author_session,
            'movie': str(copied_movie) if copied_movie else None, 'frozen': read_json(self.ctx.run_dir / 'frozen.json')}
        atomic_write_json(final / 'manifest.json', manifest)
        atomic_write_json(self.ctx.work_dir / 'input/latest-finish.json', manifest)
        record_state(self.ctx, 'finished' if manifest['passed'] else 'finished_with_issues', movie=manifest['movie'])
        return {'ok': True, 'passed': manifest['passed'],
                'quality_review': {key: quality.get(key) for key in
                                  ('passed', 'missing_reviews', 'review_errors', 'remaining', 'reason')},
                'technical_passed': manifest['technical_passed'], 'movie': manifest['movie'],
                'artifact': 'input/latest-finish.json', 'manifest': str(final / 'manifest.json'),
                'generation_counts': manifest['actual_video_counts']}
