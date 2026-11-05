"""Agent-owned music selection using real candidates and existing assembly/review."""
import copy
import math
from pathlib import Path

from novel_manga.application import music_selection
from novel_manga.util import atomic_write_json,read_json,run,media_duration
from .tool_inputs import input_file


def status(ctx):
    try:music_selection.configuration(getattr(ctx,'project_root',Path()));configured=True
    except ValueError:configured=False
    return {'configured':configured,'command':'python3 /work/tools/episode.py select_music output/music-selection.json',
        'search':{'action':'search','query':'source-backed mood/scene/avoid requirements; local candidates only','limit':3},
        'apply':{'action':'apply','search_id':'search-001','candidate_id':'returned candidate id',
            'source_quote':'verbatim source','reason':'why music fits this scene',
            'review':{'status':'accepted','reason':'Agent assessment, not rank','audio_evidence':'provided evidence file',
                'limitations':['actual evidence/listening limits']},'start_seconds':0,'seconds':'selected source interval length','gain_db':-24},
        'decline':{'action':'decline','search_id':'search-001','candidate_id':'returned candidate id',
            'review':{'status':'uncertain or rejected','reason':'actual mismatch or missing evidence'}},
        'policy':read_json(ctx.episode_dir/'delivery_policy.json',{}).get('episode_music'),
        'rules':'Search is not adoption. Uncertain/rejected candidates cannot be applied. Keep source no-music decisions, native dialogue and current take budgets. Finish still reviews the actual exported movie.'}


def source_check(ctx,candidate,plan):
    from novel_manga.llm.client import ask_json,obj
    from novel_manga.review.endpoints import judge_settings
    import json
    source=(ctx.run_dir/'source.txt').read_text()
    script=read_json(ctx.episode_dir/'chapter_script.json',{})
    return ask_json([{'type':'text','text':'核对配乐采用是否符合批准原文、任务及声音设计。明确无配乐的范围必须保留；'
        '仅按已选曲目及Agent提供的音频证据核对，不冒称你听过音乐。曲库排名不是符合剧情的证据。'
        '不足的听觉覆盖写入limitations；真实源约束冲突写problems并consistent=false。\n'+json.dumps({
            'source':source,'shot_audio':[{k:s.get(k)for k in ('index','purpose','event','sfx','audio_plan')}for s in script.get('shots',[])],
            'candidate':candidate,'plan':plan},ensure_ascii=False)}],
        obj({'consistent':{'type':'boolean'},'problems':{'type':'array','items':{'type':'string'}},
            'limitations':{'type':'array','items':{'type':'string'}}}),
        name='episode_music_source',max_tokens=1600,timeout=180,settings=judge_settings())


class MusicOperations:
    def __init__(self,ctx,media,*,source_checker=None):
        self.ctx=ctx;self.media=media;self.source_checker=source_checker or source_check

    def prepare(self,payload):
        ctx=self.ctx;plan=input_file(ctx,payload,'output/music-selection.json')
        folder=ctx.run_dir/'music-searches'
        if plan.get('action')=='search':
            result=music_selection.search(ctx.project_root,plan,folder)
            # Only explicitly supplied evidence is exposed; no reviewer is said to have heard the file.
            for candidate in result['candidates']:
                report=candidate['audio_evidence'].get('report')
                if report and Path(report).is_file():
                    target=ctx.work_dir/'input/music'/result['search_id']/(candidate['id']+'.json')
                    atomic_write_json(target,read_json(Path(report)))
                    candidate['audio_evidence']['report']=str(target.relative_to(ctx.work_dir))
            atomic_write_json(ctx.work_dir/'input/latest-music-search.json',result)
            return result
        if plan.get('action')not in {'apply','decline'}:raise ValueError('music action must be search, apply or decline')
        # Use host-retained candidates, never a caller-provided path or rank.
        sid=plan.get('search_id','')
        if not isinstance(sid,str)or Path(sid).name!=sid:raise ValueError('select a current music search_id')
        result=read_json(folder/sid/'result.json',{})
        candidate=next((r for r in result.get('candidates',[])if r['id']==plan.get('candidate_id')),None)
        if candidate is None:raise ValueError('select a registered candidate from this search')
        candidate=copy.deepcopy(candidate)
        review=plan.get('review')or{}
        if plan['action']=='decline':
            if review.get('status')not in {'uncertain','rejected'}or not str(review.get('reason')or'').strip():
                raise ValueError('decline requires an uncertain/rejected assessment and reason')
            path=ctx.run_dir/'music-assessments.json';rows=read_json(path,[])
            row={'search_id':sid,'candidate_id':candidate['id'],'review':review}
            added=not any(r['search_id']==sid and r['candidate_id']==candidate['id']and
                r['review']['status']==review['status']for r in rows)
            if added:rows.append(row);atomic_write_json(path,rows)
            outcome={'ok':True,'music_declined':True,'music_adopted':False,'assessment_added':added,'assessment':row}
            atomic_write_json(ctx.work_dir/'input/latest-music-apply.json',outcome);return outcome
        if review.get('status')!='accepted'or not str(review.get('reason')or'').strip():
            raise ValueError('music requires an explicit accepted Agent review; uncertain/rejected is not adoption')
        expected=ctx.work_dir/'input/music'/sid/(candidate['id']+'.json')
        if review.get('audio_evidence')!=str(expected.relative_to(ctx.work_dir))or not expected.is_file():
            raise ValueError('music review must cite its provided audio evidence')
        source=(ctx.run_dir/'source.txt').read_text()
        if not isinstance(plan.get('source_quote'),str)or not plan['source_quote'].strip()or plan['source_quote']not in source:
            raise ValueError('source_quote must quote the current source verbatim')
        if not str(plan.get('reason')or'').strip():raise ValueError('music adoption requires reason')
        start=plan.get('start_seconds');seconds=plan.get('seconds');gain=plan.get('gain_db')
        if any(type(v)not in {int,float}or not math.isfinite(v)for v in (start,seconds,gain)):
            raise ValueError('music range and gain require finite numbers')
        asset=Path(candidate['path']);duration=media_duration(asset)
        if start<0 or seconds<=0 or start+seconds>duration+.02:raise ValueError('selected music range exceeds actual audio')
        if not -36<=gain<=0:raise ValueError('music gain_db must be from-36 to0')
        if read_json(ctx.novel_dir/'profile.json',{}).get('bgm_path'):
            raise ValueError('Existing bgm_path must be resolved before adding selected music; do not stack two music beds')
        inputs={'candidate':candidate,'plan':plan}
        decisions=read_json(ctx.run_dir/'music-decisions.json',[])
        saved=next((r for r in decisions if r['inputs']==inputs),None)
        if saved is None:
            checked=self.source_checker(ctx,candidate,plan)
            saved={'inputs':inputs,'source_review':checked};decisions.append(saved)
            atomic_write_json(ctx.run_dir/'music-decisions.json',decisions)
        checked=saved['source_review']
        if checked.get('consistent')is not True or checked.get('problems'):
            return {'ok':False,'error':'music source rejected','source_review':checked}
        output=ctx.episode_dir/'work/music-assets'/f'selection-{decisions.index(saved)+1:03d}.wav'
        output.parent.mkdir(parents=True,exist_ok=True)
        if not output.is_file():
            run(['ffmpeg','-nostdin','-y','-v','error','-ss',str(start),'-i',str(asset),'-t',str(seconds),
                 '-ar','48000','-ac','2','-c:a','pcm_s16le',str(output)])
        candidate.update(path=str(output),source_path=str(asset),review=copy.deepcopy(review))
        policy=read_json(ctx.episode_dir/'delivery_policy.json',{})
        policy['episode_music']={'asset':candidate,'gain_db':gain,'source_quote':plan['source_quote'],
            'reason':plan['reason'],'source_interval':{'start':start,'seconds':seconds},'source_review':checked}
        atomic_write_json(ctx.episode_dir/'delivery_policy.json',policy)
        report=read_json(ctx.episode_dir/'thin_media_report.json',{})
        before=(report.get('assembly')or{}).get('final_video')
        if before:
            from novel_manga.application.repair.ambience import apply_assembly
            from novel_manga.application.review.execution import save_result
            report=apply_assembly(ctx.episode_dir,report,context=self.media.runner().context)
            report=save_result(ctx.episode_dir,report,read_json(ctx.episode_dir/'episode_review.json',{}),
                plan=read_json(ctx.episode_dir/'clip_plan.json',{}))
            atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
        outcome={'ok':True,'music_adopted':True,'assembly_changed':bool(before and before!=(report.get('assembly')or{}).get('final_video')),
            'video_generation':False,'source_review':checked,'music':policy['episode_music'],
            'next':'Assemble if needed, inspect current media checks, then finish for actual delivered-movie review; listening limits remain.'}
        atomic_write_json(ctx.work_dir/'input/latest-music-apply.json',outcome)
        return outcome
