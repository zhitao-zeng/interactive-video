"""Agent-owned music selection using real candidates and existing assembly/review."""
import copy
import math
from pathlib import Path

from novel_manga.application import music_selection
from novel_manga.util import atomic_write_json,read_json,run,media_duration
from .tool_inputs import input_file

SOURCE_REVIEW_POLICY = 'episode-music-source-v3-query-intent'

def status(ctx):
    try:music_selection.configuration(getattr(ctx,'project_root',Path()));configured=True
    except ValueError:configured=False
    latest=read_json(ctx.work_dir/'input/latest-music-apply.json',{})
    authored=read_json(ctx.episode_dir/'chapter_script.json',{}).get('music_plan')
    return {'configured':configured,
        'adoption_action':'apply_timeline' if (authored or {}).get('cues') else 'apply',
        'last_rejection':latest if latest.get('ok') is False else None,
        'command':'python3 /work/tools/episode.py select_music output/music-selection.json',
        'search':{'action':'search','query':'source-backed mood/scene/avoid requirements; local candidates only','limit':3,
            'track_ids':'optional list of original IDs for exact candidate lookup, not an adoption'},
        'apply':{'action':'apply','search_id':'search-001','candidate_id':'returned candidate id',
            'source_quote':'verbatim source','reason':'why music fits this scene',
            'review':{'status':'accepted','reason':'Agent assessment, not rank','audio_evidence':'provided evidence file',
                'limitations':['actual evidence/listening limits']},'start_seconds':0,'seconds':'selected source interval length','gain_db':-24},
        'decline':{'action':'decline','search_id':'search-001','candidate_id':'returned candidate id',
            'review':{'status':'uncertain or rejected','reason':'actual mismatch or missing evidence'}},
        'apply_timeline':{'action':'apply_timeline','source_quote':'verbatim source','reason':'story music plan',
            'cues':[{'id':'cue1','search_id':'search-001','candidate_id':'BGM-009',
                'review':'same accepted review/evidence object as apply','source_quote':'verbatim cue source','reason':'cue intent',
                'source_start_seconds':0,'source_seconds':30,'gain_db':-24,'loop':False,
                'start':{'shot':1,'edge':'start','offset_seconds':0},'end':{'shot':3,'edge':'end','offset_seconds':0},
                'fade_in_seconds':1,'fade_out_seconds':1}],
            'silences':[{'start':{'shot':2,'edge':'start'},'end':{'shot':2,'edge':'end'},'source_quote':'verbatim listening beat','reason':'leave original sound clear'}]},
        'authored_plan':authored,
        'policy':read_json(ctx.episode_dir/'delivery_policy.json',{}).get('episode_music'),
        'rules':'Search is not adoption. Uncertain/rejected candidates cannot be applied. Keep source no-music decisions, native dialogue and current take budgets. Finish still reviews the actual exported movie.'}


def source_check(ctx,candidate,plan):
    from novel_manga.llm.client import ask_json,obj
    from novel_manga.review.endpoints import judge_settings
    import json
    source=music_selection.source_inputs(ctx.episode_dir,ctx.run_dir/'source.txt')
    return ask_json([{'type':'text','text':'核对配乐采用是否符合批准原文、任务及声音设计。明确无配乐的范围必须保留；'
        '仅按已选曲目及Agent提供的音频证据核对，不冒称你听过音乐。曲库排名不是符合剧情的证据。'
        'authored_music_plan提供叙事配乐意图，按其cues与silences核对进入、退出和留白范围，保留精确anchors。'
        'cues[].query是检索与音色意向，不自动构成逐项配器硬约束。先从批准原文、任务及明确成片声音要求识别硬约束；'
        '制作Agent可凭提供的实际音频证据，在采用理由中说明实际音色如何承担相同叙事作用。'
        '未证实的乐器不得宣称匹配；替代音色是否适合具体情节仍需判断，不能仅因query是意向就自动通过。'
        '必须区分原生生成与后期成片：native_bgm=None，或audio_plan同时写music_delivery=postmix与bgm=None，'
        '表示H3不生成音乐、由后期加配乐，不是禁止成片配乐。现场声清单、安静近讲或未逐镜列出音乐，'
        '本身不构成后期禁配乐；明确要求最终成片仅现场声或无配乐的范围仍不能覆盖。'
        '本工具由制作Agent明确采用曲目；候选此前unreviewed、试配待评价、user_adopted为空，'
        '不代表还需用户逐首确认，也不推翻本次有音频证据与理由的Agent采用。不得凭此要求额外用户批准。'
        '不足的听觉覆盖写入limitations；真实源约束冲突写problems并consistent=false。\n'+json.dumps({
            'source':source,
            'candidate':candidate,'plan':plan},ensure_ascii=False)}],
        obj({'consistent':{'type':'boolean'},'problems':{'type':'array','items':{'type':'string'}},
            'limitations':{'type':'array','items':{'type':'string'}}}),
        name='episode_music_source',max_tokens=1600,timeout=180,settings=judge_settings())


class MusicOperations:
    def __init__(self,ctx,media,*,source_checker=None):
        self.ctx=ctx;self.media=media;self.source_checker=source_checker or source_check

    def prepare(self,payload):
        ctx=self.ctx;plan=input_file(ctx,payload,'output/music-selection.json')
        if plan.get('action')=='apply_timeline':return self.timeline(plan)
        authored=read_json(ctx.episode_dir/'chapter_script.json',{}).get('music_plan') or {}
        if plan.get('action')=='apply' and authored.get('cues'):
            raise ValueError('This authored story music needs action=apply_timeline with its cue IDs and anchors: '
                +str([row.get('id') for row in authored['cues']])
                +'. Source silences are inherited automatically; a single whole-film apply cannot represent this plan.')
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
        if plan.get('action')not in {'apply','decline'}:raise ValueError('music action must be search, apply, apply_timeline or decline')
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
        candidate,start,seconds,gain=self.selection(plan,candidate)
        asset=Path(candidate['path'])
        inputs={'candidate':candidate,'plan':plan,'source':music_selection.source_inputs(ctx.episode_dir,ctx.run_dir/'source.txt'),
                'source_review_policy':SOURCE_REVIEW_POLICY}
        decisions=read_json(ctx.run_dir/'music-decisions.json',[])
        saved=next((r for r in decisions if r['inputs']==inputs),None)
        if saved is None:
            checked=self.source_checker(ctx,candidate,plan)
            saved={'inputs':inputs,'source_review':checked};decisions.append(saved)
            atomic_write_json(ctx.run_dir/'music-decisions.json',decisions)
        checked=saved['source_review']
        if checked.get('consistent')is not True or checked.get('problems'):
            outcome={'ok':False,'error':'music source rejected','source_review':checked,
                'next':'Read the specific source_review problems and revise the candidate selection; do not resubmit the same rejected selection.'}
            atomic_write_json(ctx.work_dir/'input/latest-music-apply.json',outcome)
            return outcome
        output=ctx.episode_dir/'work/music-assets'/f'selection-{decisions.index(saved)+1:03d}.wav'
        output.parent.mkdir(parents=True,exist_ok=True)
        if not output.is_file():
            run(['ffmpeg','-nostdin','-y','-v','error','-ss',str(start),'-i',str(asset),'-t',str(seconds),
                 '-ar','48000','-ac','2','-c:a','pcm_s16le',str(output)])
        candidate.update(path=str(output),source_path=str(asset),review=copy.deepcopy(review),selected_seconds=media_duration(output))
        return self.publish({'asset':candidate,'gain_db':gain,'source_quote':plan['source_quote'],
            'reason':plan['reason'],'source_interval':{'start':start,'seconds':seconds},'source_review':checked,
            'source_approval':{'file':str(ctx.run_dir/'music-decisions.json'),'record':decisions.index(saved)}})

    def selection(self,plan,candidate=None):
        ctx=self.ctx;sid=plan.get('search_id','')
        if not isinstance(sid,str)or Path(sid).name!=sid:raise ValueError('select a current search_id')
        if candidate is None:
            result=read_json(ctx.run_dir/'music-searches'/sid/'result.json',{})
            candidate=next((copy.deepcopy(r)for r in result.get('candidates',[])if r['id']==plan.get('candidate_id')),None)
        if candidate is None:raise ValueError('select a registered music candidate')
        review=plan.get('review')or{}
        if not isinstance(review,dict)or review.get('status')!='accepted'or not str(review.get('reason')or'').strip():
            raise ValueError('music requires an explicit accepted Agent review')
        expected=ctx.work_dir/'input/music'/sid/(candidate['id']+'.json')
        if review.get('audio_evidence')!=str(expected.relative_to(ctx.work_dir))or not expected.is_file():raise ValueError('music review must cite provided audio evidence')
        self.source_reason(plan)
        start=plan.get('start_seconds');seconds=plan.get('seconds');gain=plan.get('gain_db')
        if any(type(v)not in {int,float}or not math.isfinite(v)for v in(start,seconds,gain)):raise ValueError('music range/gain must be finite')
        if start<0 or seconds<=0 or start+seconds>media_duration(Path(candidate['path']))+.02:raise ValueError('selected music range exceeds actual audio')
        if not -36<=gain<=0:raise ValueError('music gain_db must be from-36 to0')
        if read_json(ctx.novel_dir/'profile.json',{}).get('bgm_path'):raise ValueError('Resolve existing bgm_path before selecting another music bed')
        return candidate,start,seconds,gain

    def source_reason(self,row):
        source=(self.ctx.run_dir/'source.txt').read_text();quote=row.get('source_quote')
        if not isinstance(quote,str)or not quote.strip()or quote not in source:raise ValueError('source_quote must quote the current source verbatim')
        if not str(row.get('reason')or'').strip():raise ValueError('music adoption requires reason')

    def timeline(self,plan):
        from novel_manga.application.music_timeline import planned_timeline,resolve
        ctx=self.ctx;plan=copy.deepcopy(plan)
        self.source_reason(plan);cues=plan.get('cues');silences=plan.get('silences',[])
        if not isinstance(cues,list)or not cues:raise ValueError('music cues must be nonempty')
        if not isinstance(silences,list):raise ValueError('music silences must be a list')
        for row in silences:
            if not isinstance(row,dict):raise ValueError('music silence requires an object')
            self.source_reason(row)
        # The renderer always retains source-authored gaps. Give the source
        # reviewer that same effective plan, rather than a partial edit object.
        effective=list(silences)
        authored=(read_json(ctx.episode_dir/'chapter_script.json',{}).get('music_plan') or {}).get('silences',[])
        for row in authored:
            if row not in effective:effective.append(copy.deepcopy(row))
        plan['silences']=silences=effective
        prepared=[]
        for row in cues:
            if not isinstance(row,dict)or not isinstance(row.get('id'),str)or not row['id'].strip():raise ValueError('music cue requires a distinct text id')
            selection={**row,'start_seconds':row.get('source_start_seconds'),'seconds':row.get('source_seconds')}
            candidate,start,seconds,gain=self.selection(selection)
            prepared.append({**copy.deepcopy(row),'asset':{**candidate,'review':row['review'],'selected_seconds':seconds}})
        provisional={**plan,'cues':prepared}
        resolve(ctx.episode_dir,provisional,planned_timeline(ctx.episode_dir)) # reject malformed anchors before an LLM call
        inputs={'plan':plan,'candidates':[r['asset']for r in prepared],'source':music_selection.source_inputs(ctx.episode_dir,ctx.run_dir/'source.txt'),
                'source_review_policy':SOURCE_REVIEW_POLICY}
        path=ctx.run_dir/'music-decisions.json';records=read_json(path,[])
        saved=next((r for r in records if r['inputs']==inputs),None)
        if saved is None:
            saved={'inputs':inputs,'source_review':self.source_checker(ctx,[r['asset']for r in prepared],plan)}
            records.append(saved);atomic_write_json(path,records)
        checked=saved['source_review']
        if checked.get('consistent')is not True or checked.get('problems'):
            outcome={'ok':False,'error':'music source rejected','source_review':checked,
                'next':'Read the specific source_review problems and revise the candidate selection; do not resubmit the same rejected selection.'}
            atomic_write_json(ctx.work_dir/'input/latest-music-apply.json',outcome)
            return outcome
        folder=ctx.episode_dir/'work/music-assets'/f'timeline-selection-{records.index(saved)+1:03d}';folder.mkdir(parents=True,exist_ok=True)
        for i,row in enumerate(prepared):
            asset=row['asset'];original=Path(asset['path']);output=folder/f'cue-{i+1:03d}.wav'
            if not output.is_file():
                run(['ffmpeg','-nostdin','-y','-v','error','-ss',str(row['source_start_seconds']),'-i',str(original),
                    '-t',str(row['source_seconds']),'-ar','48000','-ac','2','-c:a','pcm_s16le',str(output)])
            asset.update(path=str(output),source_path=str(original),selected_seconds=media_duration(output),
                source_interval={'start':row['source_start_seconds'],'seconds':row['source_seconds']})
        return self.publish({'cues':prepared,'silences':copy.deepcopy(silences),'source_review':checked,
            'source_quote':plan['source_quote'],'reason':plan['reason'],
            'source_approval':{'file':str(path),'record':records.index(saved)}})

    def publish(self,music):
        ctx=self.ctx;policy=read_json(ctx.episode_dir/'delivery_policy.json',{})
        policy['episode_music']=music
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
            'video_generation':False,'source_review':music['source_review'],'music':music,
            'next':'Assemble if needed, inspect current media checks, then finish for actual delivered-movie review; listening limits remain.'}
        atomic_write_json(ctx.work_dir/'input/latest-music-apply.json',outcome)
        # New usage is separate from the supplied workbook and original trial ledger.
        ledger=ctx.project_root/'.runtime/music-selection/usages.json';usages=read_json(ledger,[])
        entry={'episode_dir':str(ctx.episode_dir),'music':music,'stage':'mixed_candidate'if before else'agent_selected','user_adopted':None}
        if not any(r.get('episode_dir')==entry['episode_dir']and r.get('music')==music for r in usages):
            usages.append(entry);atomic_write_json(ledger,usages)
        return outcome
