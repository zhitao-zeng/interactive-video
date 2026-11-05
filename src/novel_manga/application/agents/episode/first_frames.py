"""Agent-authored fresh opening images; bounded candidates, existing supplier resumption."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from PIL import Image
import shutil

from novel_manga.util import atomic_write_json, read_json
from novel_manga.media.first_frames import enabled, current_contract, source_asset_state, require_current
from novel_manga.application.profiles import h3_stamp
from .contracts import validate_existing_request
from .tool_inputs import input_file

MAX_CANDIDATES=3


def build(ctx,row,path):
    from novel_manga.config import Settings
    from novel_manga.providers.phanrouter import PhanRouterMediaProvider
    settings=Settings.from_env(provider='phanrouter',output_root=ctx.run_dir,admission_mode='preview')
    provider=PhanRouterMediaProvider(settings)
    refs=[ctx.novel_dir/p for p in row['image_references']]
    from novel_manga.application.profiles import frame_spec
    ratio=frame_spec(read_json(ctx.novel_dir/'profile.json',{}))['video_ratio']
    provider.create_image(row['image_prompt'],path,reference=refs[0],additional_references=tuple(refs[1:]),aspect_ratio=ratio)


def check(ctx,path,row,contract):
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    instructions=('检查新生成的单一镜头首帧与原始素材及本镜开场状态。第一张是首帧，后面是原素材卡。'
        '逐张描述实际可见内容后判断；只报告明确可见的身份/服饰/空间或开场机位、持物、可见人物矛盾。'
        '人物卡仅约束脸和可见稳定设计，不把卡片姿势/背景强加首帧。首帧必须是单一可拍摄画面，不能有拼图分格、字幕。'
        'POV不要求主角露脸；不要把后续动作或新人物提前放进开场。未展示的道具/身体部分不等于缺失。'
        '不替首帧脑补正确事实，不把审美偏好当成失败。\n'+json.dumps({'opening':contract,'visible_characters':row['visible_characters'],'image_prompt':row['image_prompt']},ensure_ascii=False))
    return ask_json([{'type':'text','text':instructions},image_part(path,1280),
        *(image_part(ctx.novel_dir/p,1024) for p in row['image_references'])],
        obj({'consistent':{'type':'boolean'},'observations':{'type':'array','items':{'type':'string'}},
             'problems':{'type':'array','items':{'type':'string'}}}),
        name='episode_first_frame',max_tokens=1800,timeout=240,settings=judge_settings())


def status(ctx,plan):
    if not enabled(ctx):return None
    rows={}
    root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name
    for clip in plan.get('clips',[]):
        if clip.get('kind')!='video':continue
        try:require_current(ctx,clip);issue=None
        except (ValueError,OSError) as error:issue=str(error)
        candidates=list((root/clip['clip_id']).glob('candidate_*/input.json'))
        rows[clip['clip_id']]={'current':issue is None,'issue':issue,'image_candidates':len(candidates),
                              'remaining_image_candidates':max(0,MAX_CANDIDATES-len(candidates)),
                              'first_frame':{k:v for k,v in (clip.get('first_frame') or {}).items() if k in {'path','extra_references','visible_characters','evidence','candidate'}}}
    return {'mode':'first-frame','extra_reference_policy':'needed','clips':rows,
            'command':'python3 /work/tools/episode.py first_frames output/first-frames.json',
            'schema':{'frames':[{'clip_id':'current clip id','image_prompt':'single opening image prompt',
                'image_references':['registered source image paths, ordered'],
                'visible_characters':['people actually visible at t=0; POV can be empty'],
                'extra_references':['original clip image paths only for uncovered new content'],
                'h3':{'shots':['English shot body with source <DIALOGUE_n> slots'],'soundscape':'English environment sound'},
                'prompt_h3':'optional complete six-section alternative; actual Picture numbering; preserve Subject/Audio ids and dialogue',
                'source_quote':'verbatim current source evidence'}]}}


class FirstFrameOperations:
    def __init__(self,ctx,*,builder=None,checker=None):
        self.ctx=ctx;self.builder=builder or build;self.checker=checker or check

    def prepare(self,payload):
        ctx=self.ctx
        if not enabled(ctx):raise ValueError('first_frames requires the optional first-frame mode')
        draft=input_file(ctx,payload,'output/first-frames.json')
        rows=draft.get('frames')
        if not isinstance(rows,list) or not rows:raise ValueError('frames requires a nonempty array')
        plan_path=ctx.episode_dir/'clip_plan.json';plan=read_json(plan_path)
        clips={c['clip_id']:c for c in plan['clips']}
        catalog=read_json(ctx.run_dir/'catalog.json',[])
        if isinstance(catalog,dict):catalog=catalog.get('assets',[])
        registered={r['path'] for r in catalog if r.get('role') in {'character','location','prop'} and r.get('path')}
        segments={r['segment_id']:r['text'] for r in read_json(ctx.episode_dir/'segments.json',[])}
        prepared=[];seen=set()
        for row in rows:
            if not isinstance(row,dict) or row.get('clip_id') not in clips or row['clip_id'] in seen:
                raise ValueError('frames must select distinct current clips')
            row=copy.deepcopy(row)
            cid=row['clip_id'];seen.add(cid);clip=clips[cid]
            for field in ('image_prompt','source_quote'):
                if not isinstance(row.get(field),str) or not row[field].strip():raise ValueError(f'{cid}: {field} requires text')
            for field in ('image_references','extra_references','visible_characters'):
                if not isinstance(row.get(field),list) or any(not isinstance(p,str) for p in row[field]) or len(set(row[field]))!=len(row[field]):
                    raise ValueError(f'{cid}: {field} requires distinct strings')
            if not row['image_references'] or not set(row['image_references'])<=registered:
                raise ValueError(f'{cid}: image_references must use registered source cards, never old video/frames')
            original={r['path'] for r in clip.get('references',[]) if r.get('role') in {'character','location','prop'}}
            if not set(row['extra_references'])<=original:
                raise ValueError(f'{cid}: extra_references must select current source image paths')
            source='\n'.join(segments.get(s,'') for s in clip.get('segment_ids',[]))
            if row['source_quote'] not in source:raise ValueError(f'{cid}: source_quote must quote this clip source verbatim')
            contract=current_contract(ctx,clip,row['image_references'])
            draft_frame={'path':'pending.png','extra_references':row['extra_references'],'visible_characters':row['visible_characters']}
            from .requests import compose_request
            if not isinstance(row.get('prompt_h3'),str) or not row['prompt_h3'].strip():
                row['prompt_h3']=compose_request({**clip,'first_frame':draft_frame},row.get('h3'))
            validate_existing_request({**clip,'first_frame':draft_frame},row['prompt_h3'])
            prepared.append((row,contract,source_asset_state(ctx,row['image_references'])))
        results={};changed=[];reviewed_candidates=[]
        for row,contract,assets in prepared:
            cid=row['clip_id'];clip=clips[cid]
            root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid;root.mkdir(parents=True,exist_ok=True)
            # A video-prompt edit does not redraw the same opening image.
            identity={k:row[k] for k in ('image_prompt','image_references','visible_characters')}
            identity.update(source_contract=contract,source_assets=assets)
            dirs=sorted(root.glob('candidate_*'))
            candidate=next((d for d in dirs if read_json(d/'input.json',{})==identity),None)
            if candidate is None:
                if len(dirs)>=MAX_CANDIDATES:
                    results[cid]={'ok':False,'error':'first-frame image candidate budget used'};continue
                candidate=root/f'candidate_{len(dirs)+1:02d}';candidate.mkdir()
                atomic_write_json(candidate/'input.json',identity)
            path=candidate/'opening.png'
            rejected=read_json(candidate/'rejected.json',{})
            if rejected:results[cid]={'ok':False,**rejected};continue
            if not path.is_file():self.builder(ctx,row,path) # existing supplier receipt resumes this exact immutable output
            with Image.open(path) as image:image.load()
            review=read_json(candidate/'review.json',{})
            if not review:
                review=self.checker(ctx,path,row,contract);atomic_write_json(candidate/'review.json',review)
                reviewed_candidates.append(cid+'/'+candidate.name)
            export=ctx.work_dir/'input/first_frames'/cid/candidate.name;export.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,export/path.name);atomic_write_json(export/'review.json',review)
            if review.get('consistent') is not True or review.get('problems'):
                rejected={'error':'first-frame image review rejected','review':review,'evidence':str(export.relative_to(ctx.work_dir))}
                atomic_write_json(candidate/'rejected.json',rejected);results[cid]={'ok':False,**rejected};continue
            frame={'path':str(path.relative_to(ctx.novel_dir)),'extra_references':row['extra_references'],
                'visible_characters':row['visible_characters'],'source_contract':contract,'source_assets':assets,
                'prompt':row['image_prompt'],'review':review,'evidence':str(export.relative_to(ctx.work_dir)),
                'candidate':candidate.name,'supplier_receipt':str(path.with_suffix(path.suffix+'.task.json').relative_to(ctx.novel_dir))}
            newclip=copy.deepcopy(clip);newclip['first_frame']=frame;newclip['prompt_h3']=row['prompt_h3'].strip()
            newclip.pop('prompt_h3_skip',None)
            newclip['prompt_h3_of']=h3_stamp(newclip,read_json(ctx.episode_dir/'review_feedback.json',{}).get(cid,''))
            if newclip!=clip:
                clip.clear();clip.update(newclip);changed.append(cid);atomic_write_json(plan_path,plan)
            atomic_write_json(candidate/'accepted.json',frame)
            results[cid]={'ok':True,'first_frame':frame}
        atomic_write_json(ctx.work_dir/'input/clip_plan.json',plan)
        outcome={'ok':all(r.get('ok') for r in results.values()),'changed':changed,'reviewed_candidates':reviewed_candidates,'clips':results,'first_frames':status(ctx,plan)}
        atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
        return outcome
