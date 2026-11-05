"""Agent selects a source-backed, speech-free room tone; host preserves native speech/video."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shutil

from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.repair.ambience import validate
from .tool_inputs import input_file

SOURCE_REVIEW_POLICY='episode-ambience-source-v2-scope-and-limits'

def candidates(ctx):
    rows=list(read_json(ctx.episode_dir/'repair_candidates.json',{}).get('audio')or[])
    interaction=read_json(ctx.episode_dir/'delivery_policy.json',{}).get('interaction_ambience')
    if interaction:rows.append(interaction)
    return {r['id']:r for r in rows if r.get('id')and r.get('path')}


def status(ctx):
    rows=candidates(ctx)
    return {'command':'python3 /work/tools/episode.py mix_audio output/audio-mix.json',
        'schema':{'candidate_id':'registered audio id','gain_db':'-36 to0; select from actual source/mix levels',
                  'source_quote':'verbatim episode evidence','reason':'why this environmental bed is appropriate'},
        'candidates':[{'id':r['id'],'source_path':r['path'],'review':r.get('review'),
                       'measurements':r.get('measurements'),'processing':r.get('processing')}for r in rows.values()],
        'policy':read_json(ctx.episode_dir/'delivery_policy.json',{}).get('episode_ambience'),
        'method':'additive environmental bed, native speech retained, video stream copied; no video generation or QC waiver'}


def source_check(ctx,entry,plan):
    from novel_manga.llm.client import ask_json,obj
    from novel_manga.review.endpoints import judge_settings
    source=(ctx.run_dir/'source.txt').read_text()
    script=read_json(ctx.episode_dir/'chapter_script.json',{})
    return ask_json([{'type':'text','text':
        '核对原文是否支持在现有原生对白/音效下面叠加此环境底声。只判断声音设计的来源与适用范围，'
        '你未听音频，不能声称听过或自然度合格。素材已有无语音证据；保留其合成/录制来源和未听审限制。'
        '环境底声不能改变对白、制造新事件或把无配乐要求换成音乐；候选必须适合本集连续空间。'
        '不要把静音门未过本身当成添加任意噪声的理由。\n'+json.dumps({'source':source,
        'shot_sound':[s.get('sfx')for s in script.get('shots',[])],
        'candidate':entry,'mix_plan':plan},ensure_ascii=False)+
        '\n请明确判断来源与声音设计范围是否一致。problems只列有具体原文依据、实际成立的来源矛盾，'
        '有矛盾则consistent=false。合成来源、未听审、自然度未知及有待成片验证的循环听感，'
        '写入limitations，不把未执行的听审假装为已发现的来源矛盾。'
        '来源符合则consistent=true、problems=[]；limitations始终保留，不代表听感已通过。'}],
        obj({'consistent':{'type':'boolean'},'problems':{'type':'array','items':{'type':'string'}},
             'limitations':{'type':'array','items':{'type':'string'}}}),
        name='episode_ambience_source',max_tokens=1200,timeout=180,settings=judge_settings())


class AudioMixOperations:
    def __init__(self,ctx,media,*,source_checker=None):
        self.ctx=ctx;self.media=media;self.source_checker=source_checker or source_check

    def prepare(self,payload):
        ctx=self.ctx;plan=input_file(ctx,payload,'output/audio-mix.json')
        entry=candidates(ctx).get(plan.get('candidate_id'))
        if entry is None:raise ValueError('select a registered source-reviewed audio candidate')
        report=read_json(ctx.episode_dir/'thin_media_report.json',{})
        movie=(report.get('assembly')or{}).get('final_video')
        if not movie or not Path(movie).is_file():
            raise ValueError('assemble the native episode before mixing an environmental bed')
        asset=validate(entry);gain=plan.get('gain_db')
        if type(gain)not in{int,float}or not math.isfinite(gain)or not -36<=gain<=0:
            raise ValueError('gain_db requires a finite number between -36 and0')
        source=(ctx.run_dir/'source.txt').read_text();quote=plan.get('source_quote')
        if not isinstance(quote,str)or not quote.strip()or quote not in source:
            raise ValueError('source_quote must quote the actual episode source verbatim')
        if not isinstance(plan.get('reason'),str)or not plan['reason'].strip():raise ValueError('reason requires text')
        path=ctx.run_dir/'audio-mix-candidates.json';records=read_json(path,[])
        identity={'plan':plan,'asset':entry}
        saved=next((r for r in records if r.get('inputs')==identity),None)
        if saved is None:
            if len(records)>=3:raise ValueError('audio mix source candidate budget used')
            saved={'inputs':copy.deepcopy(identity)};records.append(saved);atomic_write_json(path,records)
        review=saved.get('source_review')
        if review is None or saved.get('source_review_policy')!=SOURCE_REVIEW_POLICY:
            if review is not None:
                saved.setdefault('source_review_history',[]).append({'policy':saved.get('source_review_policy'),'review':review})
            review=self.source_checker(ctx,entry,plan)
            saved.update(source_review=review,source_review_policy=SOURCE_REVIEW_POLICY);atomic_write_json(path,records)
        if review.get('consistent')is not True or review.get('problems'):
            return {'ok':False,'error':'audio mix source rejected','source_review':review}
        folder=ctx.episode_dir/'work/ambience-assets';folder.mkdir(parents=True,exist_ok=True)
        target=folder/(entry['id']+asset.suffix)
        if target.resolve()!=asset.resolve():shutil.copy2(asset,target)
        policy=read_json(ctx.episode_dir/'delivery_policy.json',{})
        policy['episode_ambience']={'asset':{**copy.deepcopy(entry),'path':str(target.resolve()),
            'source_path':str(asset.resolve())},'gain_db':gain,'source_quote':quote,'reason':plan['reason'],'source_review':review}
        atomic_write_json(ctx.episode_dir/'delivery_policy.json',policy)
        from novel_manga.application.repair.ambience import apply_assembly
        report=apply_assembly(ctx.episode_dir,report,context=self.media.runner().context)
        from novel_manga.application.review.execution import save_result
        report=save_result(ctx.episode_dir,report,read_json(ctx.episode_dir/'episode_review.json',{}),
            plan=read_json(ctx.episode_dir/'clip_plan.json',{}))
        atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
        outcome={'ok':True,'assembly_changed':True,'video_generation':False,
            'technical_passed':(report.get('assembly')or{}).get('thin_passed'),
            'media_checks':(report.get('assembly')or{}).get('media_qc'),
            'source_review':review,'next':'Inspect actual mixed-media checks, then finish for full-movie review; keep native takes.'}
        atomic_write_json(ctx.work_dir/'input/latest-audio-mix.json',outcome)
        return outcome
