"""An accepted opening can start its first take while later images are being made."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from novel_manga.application.preparation.store import clip_video
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json


def take_inputs(clip,note):
    return {'plan':plan_fingerprint({'clips':[clip]}),
            'prompt_h3':None if clip.get('prompt_h3_skip') else clip.get('prompt_h3'),
            'note':note}


class FirstTakeStream:
    """One video/history/report writer; the image worker alone changes the plan."""

    def __init__(self,ctx,media):
        self.ctx=ctx;self.media=media;self.futures={};self.video_progress=False
        import os
        from novel_manga.config import Settings
        workers=int(os.environ.get('NOVEL_MEDIA_WORKERS',Settings.media_workers))
        workers=max(1,min(workers,4))
        self.pool=ThreadPoolExecutor(max_workers=workers)

    def __enter__(self):
        return self

    def __exit__(self,*error):
        # Already accepted work settles even when a later image operation fails.
        self.pool.shutdown(wait=True)

    def submit(self,cid):
        if cid not in self.futures:
            self.futures[cid]=self.pool.submit(self._render,cid)

    def _render(self,cid):
        directory=self.ctx.episode_dir
        runner=None;clip=None;video=clip_video(directory,cid);record=None;bound=False
        had_video=video is not None
        try:
            from novel_manga.application.rendering.reviewed import _load_current
            from novel_manga.media.first_frames import require_current
            from .contracts import validate_existing_request
            runner=self.media.runner()
            _load_current(runner)
            clip=next(c for c in runner.context.clip_plan['clips'] if c['clip_id']==cid)
            require_current(self.ctx,clip)
            validate_existing_request(clip,clip.get('prompt_h3',''))
            voices=self.media.evidence.voice_recording_blocks({'clips':[clip]})
            if voices:raise ValueError('voice recording blocked: '+str(voices))
            runner.context.options.first_pass_only=True
            runner.context.options.first_pass_clips={cid}
            runner.context.options.prescreen=False
            if video is not None:
                from novel_manga.media import cache,generation
                _,references,digests,_=generation.build_request(runner.context,clip,1)
                if not cache.request_matches(runner.context,clip,read_json(video.parent/'request.json',{}),
                                             references,digests,video=video):
                    raise ValueError('existing take differs from current inputs; keep it for Agent review, never automatically retake')
                bound=True
                history.complete_generation(directory,cid,video)
            else:
                runner.prepare_clips([clip])
                if blocked:=runner.context.state.blocked_clips.get(cid):
                    raise ValueError('first-take input blocked: '+str(blocked))
                history.adopt_reviewed_run(directory)
                history.begin_trial(directory,{cid},'episode_agent_stream_first_take',
                    after_plan=runner.context.clip_plan,after_notes=runner.context.feedback)
                record=history.load(directory);record['trials'][-1]['managed']=True
                history.save(directory,record)
                # Submit once; input edits and retakes return to the Agent.
                # Neither thread rewrites the plan during video submission.
                video=runner.generate_clip(clip,1)
                self.video_progress=True;bound=True
            analysis=runner.analyse_clip(clip,video)
            analysis={**analysis,'generated_this_run':bool(runner.context.state.clip(cid).generated)}
            row={'clip_id':cid,'attempts':[analysis],'selected':analysis}
        except Exception as error:
            existing=video or clip_video(directory,cid)
            selected={'video':str(existing),'passed':False} if existing else None
            self.video_progress |= not had_video and existing is not None
            prior=next((r for r in read_json(directory/'thin_media_report.json',{}).get('clips',[])
                        if r['clip_id']==cid),{})
            row={**prior,'clip_id':cid,'attempts':prior.get('attempts',[]),
                 'selected':prior.get('selected') or selected,
                 'error':type(error).__name__+': '+str(error)[:1000]}
            if not bound:
                row.pop('stream_inputs',None)
        if runner is not None and clip is not None and bound:
            row['stream_inputs']=take_inputs(clip,runner.context.feedback.get(cid,''))
            if record is not None:
                history.record_render(directory,{'clips':[row],
                    'clip_plan_fingerprint':plan_fingerprint(runner.context.clip_plan),
                    'review_feedback':runner.context.feedback})
        self._save(row)
        return {'ok':not row.get('error'),'selected':bool(row.get('selected')),
                'technical_passed':(row.get('selected') or {}).get('passed'),
                **({'error':row['error']} if row.get('error') else {})}

    def _save(self,row):
        """Preserve every sibling result; never certify an old take against new inputs."""
        directory=self.ctx.episode_dir
        path=directory/'thin_media_report.json'
        previous=read_json(path,{})
        plan=read_json(directory/'clip_plan.json')
        notes=read_json(directory/'review_feedback.json',{})
        by_id={r['clip_id']:r for r in previous.get('clips',[])}
        by_id[row['clip_id']]=row
        clips=[by_id[c['clip_id']] for c in plan['clips'] if c['clip_id'] in by_id]
        pending=[c['clip_id'] for c in plan['clips'] if c.get('kind')=='video'
                 and not (by_id.get(c['clip_id']) or {}).get('selected')]
        matches=not pending and all(
            by_id[c['clip_id']].get('stream_inputs')==take_inputs(c,notes.get(c['clip_id'],''))
            for c in plan['clips'] if c.get('kind')=='video')
        failed=[r['clip_id'] for r in clips if r.get('error') or not r.get('selected')]
        gates=[r['clip_id'] for r in clips if r.get('selected') and not r['selected'].get('passed')]
        report={**previous,'episode':directory.name,'policy':'thin-media-v22-coverage-gate',
            'clips':clips,'failed_clips':failed,'gate_failed_clips':gates,
            'pending_clips':pending,'review_feedback':notes,'assembly':None,
            'status':'clips_failed' if pending or failed else 'clips_ready',
            'clip_plan_fingerprint':plan_fingerprint(plan) if matches else 'streaming-first-takes',
            'prompt_h3_fingerprint':h3_prompt_fingerprint(plan) if matches else None}
        report.pop('quality_review',None);report.pop('final_cut_review',None)
        atomic_write_json(path,report)
        atomic_write_json(self.ctx.work_dir/'input/latest-render.json',report)

    def result(self):
        outcomes={}
        for cid,future in self.futures.items():
            try:outcomes[cid]=future.result()
            except Exception as error:
                outcomes[cid]={'ok':False,'error':type(error).__name__+': '+str(error)[:1000]}
        return {'video_stream':outcomes,'video_progress':self.video_progress,
                'generation_counts':history.generation_counts(self.ctx.episode_dir),
                'next':'Review existing takes; repair confirmed defects through the Agent. This stream never retakes existing video.'}
