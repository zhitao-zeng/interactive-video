"""Agent-authored fresh opening images; bounded candidates, existing supplier resumption."""
from __future__ import annotations

import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from PIL import Image
import shutil

from novel_manga.util import atomic_write_json, read_json
from novel_manga.media.first_frames import enabled, current_contract, source_asset_state, require_current, review_problems, interpret_review, unchanged_opening_scale, REVIEW_POLICY
from novel_manga.application.profiles import h3_stamp
from .contracts import validate_existing_request
from .tool_inputs import input_file

MAX_CANDIDATES=3


def image_candidate_limit(ctx,cid):
    """Image-only authority; video, input and recheck grants stay independent."""
    grants=read_json(ctx.episode_dir/'repair_budget_grants.json',{})
    grant=grants.get(cid,{}).get('first_frame') or {}
    if not grant.get('authorization') or not grant.get('reason'):return MAX_CANDIDATES
    return max(MAX_CANDIDATES,int(grant.get('limit',MAX_CANDIDATES)))


def source_opening(ctx,clip):
    directory=getattr(ctx,'episode_dir',None)
    script=read_json(Path(directory)/'chapter_script.json',{})if directory else{}
    index=(clip.get('shot_indexes')or[None])[0]
    shot=next((s for s in script.get('shots',[])if s.get('index')==index),None)
    if shot is None:shot=(clip.get('first_frame_opening')or clip.get('shots')or[{}])[0]
    return copy.deepcopy(shot.get('opening_frame'))


def authored_row(ctx,clip,row):
    """The creator owns the opening prompt; production binds cards and executes it."""
    row=copy.deepcopy(row);opening=source_opening(ctx,clip)
    if opening is None:return row # retained historical input has no authored prompt
    from novel_manga.story.fields import opening_frame_error
    if issue:=opening_frame_error(opening):raise ValueError(issue)
    for field in ('image_prompt','visible_characters'):
        if field in row and row[field]!=opening[field]:
            raise ValueError(
                'Edit source opening_frame through input repair before changing its production prompt or visible people. '
                f"For {clip['clip_id']}, write output/repair.json with decisions:[{{action:'input',clip_id:'{clip['clip_id']}',"
                "reason:'specific observed defect',source_quote:'verbatim source for this clip',"
                f"shots:[{{index:{clip['shot_indexes'][0]},opening_frame:<complete corrected opening_frame object>}}]}}]. "
                'Call check then apply. After the source repair is adopted, omit image_prompt and visible_characters '
                'from the first_frames row to inherit that source. Reference-selection-only corrections may change '
                'image_references directly; inspect opening_frame.reference_subjects when choosing cards.')
        row[field]=copy.deepcopy(opening[field])
    return row


def build(ctx,row,path):
    from novel_manga.config import Settings
    from novel_manga.providers.phanrouter import PhanRouterMediaProvider
    settings=Settings.from_env(provider='phanrouter',output_root=ctx.run_dir,admission_mode='preview')
    provider=PhanRouterMediaProvider(settings)
    refs=[ctx.novel_dir/p for p in row['image_references']]
    from novel_manga.application.profiles import frame_spec
    from novel_manga.style_packs import frame_style, first_frame_prompt
    ratio=frame_spec(read_json(ctx.novel_dir/'profile.json',{}))['video_ratio']
    prompt=row.get('request_prompt') or first_frame_prompt(row['image_prompt'],frame_style(ctx.novel_dir))
    provider.create_image(prompt,path,reference=refs[0],additional_references=tuple(refs[1:]),aspect_ratio=ratio)


def check(ctx,path,row,contract):
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    instructions=('检查新生成的单一镜头首帧与原始素材及本镜开场状态。第一张是首帧，后面是原素材卡。'
        '逐张描述实际可见内容后判断；报告明确可见的身份/服饰/空间或开场机位、持物、可见人物矛盾。'
        '若opening.style给出所选画风，核对其明确的媒介、材质、色光与构图规则；'
        '原素材卡的拍摄媒介不覆盖所选画风。没有画风约定时不增加审美要求，轻微色差不自动失败。'
        '人物卡仅约束脸和可见稳定设计，不把卡片姿势/背景强加首帧。首帧必须是单一可拍摄画面，不能有拼图分格、字幕。'
        'character_facts给出当前人物稳定外观/服饰及来源字段，补充肖像卡未展示的部位；当前装备状态以opening的start_state、device_state、props、wears为准，'
        '不把稳定signature_prop当作本镜必须出现的持物，也不把后续动作或末态强加开场。'
        'POV不要求主角露脸；不要把后续动作或新人物提前放进开场。未展示的道具/身体部分不等于缺失。'
        '即使visible_characters为空，入画的腿、鞋、袖口等可见部位仍须与对应人物事实比较；不得要求画外部位出现。'
        '每条findings返回实际可见事实observed、对照要求expected及basis（本次contract字段路径或原素材图号），给出reason和relation。'
        'contradiction表示实际可见属性与明确当前依据不能同时成立；未展示、看不清或依据未指定是underspecified；可同时成立是compatible。'
        '只返回observations及findings，不另给自由通过结论或问题清单；程序只从明确contradiction生成通过结论和问题。无冲突可返回空findings。'
        '不替首帧脑补正确事实，不把审美偏好当成失败。\n'+json.dumps({'opening':contract,'visible_characters':row['visible_characters'],'image_prompt':row['image_prompt']},ensure_ascii=False))
    raw=ask_json([{'type':'text','text':instructions},image_part(path,1280),
        *(image_part(ctx.novel_dir/p,1024) for p in row['image_references'])],
        obj({'observations':{'type':'array','items':{'type':'string'}},
             'findings':{'type':'array','items':obj({
                 'observed':{'type':'string'},'expected':{'type':'string'},
                 'basis':{'type':'array','minItems':1,'items':{'type':'string'}},
                 'relation':{'type':'string','enum':['contradiction','underspecified','compatible']},
                 'reason':{'type':'string'}})}}),
        name='episode_first_frame',max_tokens=1800,timeout=240,settings=judge_settings())
    return {**interpret_review(raw),'raw':raw}


def review_candidate(ctx,path,row,contract,assets,checker):
    """Cache this one review by its actual evidence, retaining every raw response."""
    candidate=path.parent
    review_input={'policy':REVIEW_POLICY,'source_contract':contract,'source_assets':assets,
                  'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters']}
    records=sorted((candidate/'reviews').glob('review_*.json'))
    for record in records:
        saved=read_json(record,{})
        if saved.get('input')==review_input:return interpret_review(saved['review']),False,record
    answer=checker(ctx,path,row,contract)
    raw=answer.get('raw',answer)
    review=interpret_review(raw)
    record=candidate/'reviews'/f'review_{len(records)+1:03d}.json'
    atomic_write_json(record,{'input':review_input,'raw':raw,'review':review})
    if not (candidate/'review.json').exists():atomic_write_json(candidate/'review.json',raw)
    return review,True,record


def status(ctx,plan):
    if not enabled(ctx):return None
    rows={}
    root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name
    for clip in plan.get('clips',[]):
        if clip.get('kind')!='video':continue
        try:require_current(ctx,clip);issue=None
        except (ValueError,OSError) as error:issue=str(error)
        candidates=sorted((root/clip['clip_id']).glob('candidate_*/input.json'))
        limit=image_candidate_limit(ctx,clip['clip_id'])
        rows[clip['clip_id']]={'current':issue is None,'issue':issue,'image_candidates':len(candidates),
                              'authored_opening_frame':source_opening(ctx,clip),
                              'image_candidate_limit':limit,'remaining_image_candidates':max(0,limit-len(candidates)),
                              'first_frame':{k:v for k,v in (clip.get('first_frame') or {}).items() if k in {'path','extra_references','visible_characters','evidence','candidate'}}}
        if candidates:
            latest=candidates[-1].parent;rejected=read_json(latest/'rejected.json',{})
            reviews=sorted((latest/'reviews').glob('review_*.json'))
            if reviews:
                record=reviews[-1];review=interpret_review(read_json(record,{})['review'])
                if review.get('consistent')is not True or review_problems(review):
                    export=ctx.work_dir/'input/first_frames'/clip['clip_id']/latest.name
                    if read_json(export/'review.json',{})!=review:export=export/'reviews'/record.stem
                    rejected={'error':'first-frame image review rejected','review':review,
                              'evidence':str(export.relative_to(ctx.work_dir))}
            if rejected:
                row=rows[clip['clip_id']]
                row['last_rejection']={'candidate':latest.name,'error':rejected.get('error'),
                    'problems':review_problems(rejected.get('review')or{}),'evidence':rejected.get('evidence')}
                if issue is not None:
                    row['next_action']=(
                        'Inspect the rejected image and review evidence. Correct the relevant source opening_frame through input repair '
                        'or correct the registered reference selection, then call first_frames. '
                        'If those inputs are already corrected, submit the changed row. '
                        'An unchanged image request returns this cached rejection and cannot generate another candidate.')
    return {'mode':'first-frame','extra_reference_policy':'needed','clips':rows,
            'command':'python3 /work/tools/episode.py first_frames output/first-frames.json',
            'schema':{'render_ready':False,'frames':[{'clip_id':'current clip id','image_prompt':'single opening image prompt',
                'image_references':['registered source image paths, ordered'],
                'visible_characters':['people actually visible at t=0; POV can be empty'],
                'reuse_candidate':'optional existing candidate_NN in this clip; reuse its saved image with the complete current review, no new image',
                'extra_references':['original clip image paths only for uncovered new content'],
                'h3':{'shots':['English shot body with source <DIALOGUE_n> slots'],'soundscape':'English environment sound'},
                'prompt_h3':'optional complete six-section alternative; actual Picture numbering; preserve Subject/Audio ids and dialogue',
                'source_quote':'verbatim current source evidence'}]}}


class FirstFrameOperations:
    def __init__(self,ctx,*,builder=None,checker=None,on_accepted=None):
        self.ctx=ctx;self.builder=builder or build;self.checker=checker or check
        self.on_accepted=on_accepted

    def prepare(self,payload):
        ctx=self.ctx
        self.partial_outcome={}
        if not enabled(ctx):raise ValueError('first_frames requires the optional first-frame mode')
        # Read the existing image/media setting without requiring provider credentials
        # for a cache-only operation or an injected builder.
        from novel_manga.config import Settings
        workers=int(os.environ.get('NOVEL_MEDIA_WORKERS',Settings.media_workers))
        if not 1<=workers<=8:raise ValueError('NOVEL_MEDIA_WORKERS must be between 1 and 8')
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
            row=authored_row(ctx,clips[row['clip_id']],row)
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
            contract=current_contract(ctx,clip,row['image_references'],include_style=True)
            from novel_manga.style_packs import first_frame_prompt
            row['request_prompt']=first_frame_prompt(row['image_prompt'],contract['style'])
            draft_frame={'path':'pending.png','extra_references':row['extra_references'],'visible_characters':row['visible_characters']}
            from .requests import compose_request
            if not isinstance(row.get('prompt_h3'),str) or not row['prompt_h3'].strip():
                row['prompt_h3']=compose_request({**clip,'first_frame':draft_frame},row.get('h3'))
            validate_existing_request({**clip,'first_frame':draft_frame},row['prompt_h3'])
            prepared.append((row,contract,source_asset_state(ctx,row['image_references'])))
        results={};changed=[];reviewed_candidates=[]
        self.partial_outcome={'changed':changed,'reviewed_candidates':reviewed_candidates,'clips':results}
        def reserve(row,contract,assets):
            cid=row['clip_id'];clip=clips[cid]
            root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid;root.mkdir(parents=True,exist_ok=True)
            # A video-prompt edit does not redraw the same opening image.
            identity={k:row[k] for k in ('image_prompt','request_prompt','image_references','visible_characters')}
            identity.update(source_contract=contract,source_assets=assets)
            dirs=sorted(root.glob('candidate_*'))
            reuse=row.get('reuse_candidate')
            if 'reuse_candidate'in row:
                candidate=next((d for d in dirs if d.name==reuse),None)
                if candidate is None or not (candidate/'input.json').is_file() or not (candidate/'opening.png').is_file():
                    raise ValueError(f'{cid}: reuse_candidate must name this clip\'s existing generated candidate')
                saved=read_json(candidate/'input.json')
                row['request_prompt']=saved.get('request_prompt') or saved.get('image_prompt') or row['request_prompt']
            else:
                candidate=next((d for d in dirs if read_json(d/'input.json',{})==identity),None)
            if candidate is None:
                # Stable facts are review evidence, not a new paid image request.
                # Match the immutable generation input; keep its original style/prompt.
                def generation_input(value):
                    value=copy.deepcopy(value)
                    value.get('source_contract',{}).pop('character_facts',None)
                    return value
                for directory in dirs:
                    saved=read_json(directory/'input.json',{})
                    expected=copy.deepcopy(identity)
                    saved_contract=saved.get('source_contract',{})
                    if 'shot_scale'not in saved_contract.get('opening',{}) and unchanged_opening_scale(clip,contract):
                        expected['source_contract']['opening'].pop('shot_scale',None)
                    if 'character_facts'not in saved_contract and 'device_state'not in saved_contract.get('opening',{}):
                        expected['source_contract']['opening'].pop('device_state',None)
                    legacy='request_prompt'not in saved
                    if legacy:
                        expected.pop('request_prompt',None)
                        expected['source_contract'].pop('style',None)
                    if generation_input(saved)!=generation_input(expected):continue
                    candidate=directory
                    if legacy:
                        contract={k:v for k,v in contract.items() if k!='style'}
                        row['request_prompt']=row['image_prompt']
                    break
            if candidate is None:
                if len(dirs)>=image_candidate_limit(ctx,cid):
                    results[cid]={'ok':False,'error':'first-frame image candidate budget used'};return None
                candidate=root/f'candidate_{len(dirs)+1:02d}';candidate.mkdir()
                atomic_write_json(candidate/'input.json',identity)
            return row,contract,assets,candidate

        def produce(row,contract,assets,candidate):
            path=candidate/'opening.png'
            if not path.is_file():self.builder(ctx,row,path) # existing supplier receipt resumes this exact immutable output
            with Image.open(path) as image:image.load()
            return review_candidate(ctx,path,row,contract,assets,self.checker)

        def adopt(task,review,reviewed,record):
            row,contract,assets,candidate=task
            cid=row['clip_id'];clip=clips[cid];path=candidate/'opening.png'
            if reviewed:reviewed_candidates.append(cid+'/'+candidate.name)
            export=ctx.work_dir/'input/first_frames'/cid/candidate.name
            if (export/'review.json').exists() and read_json(export/'review.json')!=review:
                export=export/'reviews'/record.stem
            export.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,export/path.name);atomic_write_json(export/'review.json',review)
            shutil.copy2(record,export/'review-record.json')
            if review.get('consistent') is not True or review_problems(review):
                rejected={'error':'first-frame image review rejected','review':review,
                          'problems':review_problems(review),'evidence':str(export.relative_to(ctx.work_dir))}
                if not (candidate/'rejected.json').exists():atomic_write_json(candidate/'rejected.json',rejected)
                # A newly rejected alternative does not revoke a different selection.
                # Rechecking the currently selected pixels does revoke their old pass.
                if (clip.get('first_frame')or{}).get('path')==str(path.relative_to(ctx.novel_dir)):
                    clip['first_frame'].update(review=review,review_record=str(record.relative_to(ctx.novel_dir)),
                                               evidence=rejected['evidence'])
                    atomic_write_json(plan_path,plan)
                results[cid]={'ok':False,**rejected};return
            frame={'path':str(path.relative_to(ctx.novel_dir)),'extra_references':row['extra_references'],
                'visible_characters':row['visible_characters'],'source_contract':contract,'source_assets':assets,
                'prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
                'review':review,'review_record':str(record.relative_to(ctx.novel_dir)),
                'evidence':str(export.relative_to(ctx.work_dir)),
                'candidate':candidate.name,'supplier_receipt':str(path.with_suffix(path.suffix+'.task.json').relative_to(ctx.novel_dir))}
            newclip=copy.deepcopy(clip);newclip['first_frame']=frame;newclip['prompt_h3']=row['prompt_h3'].strip()
            newclip.pop('prompt_h3_skip',None)
            newclip['prompt_h3_of']=h3_stamp(newclip,read_json(ctx.episode_dir/'review_feedback.json',{}).get(cid,''))
            if newclip!=clip:
                clip.clear();clip.update(newclip);changed.append(cid);atomic_write_json(plan_path,plan)
            atomic_write_json(candidate/'accepted.json',frame)
            results[cid]={'ok':True,'first_frame':frame}
            if self.on_accepted:self.on_accepted(cid)

        # Only this caller allocates candidates, adopts plans and invokes callbacks.
        # Keep a rolling window so a failed service does not allocate or start the
        # rest of the batch. Already started work still settles and is adopted.
        pending={};next_row=0;first_error=None
        with ThreadPoolExecutor(max_workers=min(workers,len(prepared))) as pool:
            while True:
                while (first_error is None and next_row<len(prepared) and len(pending)<workers
                       and not any(future.done() for future in pending)):
                    row,contract,assets=prepared[next_row];next_row+=1
                    try:
                        task=reserve(row,contract,assets)
                        if task is not None:pending[pool.submit(produce,*task)]=task
                    except Exception as error:
                        results[row['clip_id']]={'ok':False,'error':type(error).__name__+': '+str(error)}
                        first_error=error
                if not pending:break
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    task=pending.pop(future)
                    try:adopt(task,*future.result())
                    except Exception as error:
                        results[task[0]['clip_id']]={'ok':False,'error':type(error).__name__+': '+str(error)}
                        if first_error is None:first_error=error
        # Stable summaries do not delay ready callbacks.
        order={row['clip_id']:i for i,(row,_,_) in enumerate(prepared)}
        changed.sort(key=order.__getitem__)
        reviewed_candidates.sort(key=lambda name:order[name.split('/')[0]])
        ordered={cid:results[cid] for cid in order if cid in results}
        results.clear();results.update(ordered)
        atomic_write_json(ctx.work_dir/'input/clip_plan.json',plan)
        if first_error is not None:raise first_error
        outcome={'ok':all(r.get('ok') for r in results.values()),'changed':changed,'reviewed_candidates':reviewed_candidates,'clips':results,'first_frames':status(ctx,plan)}
        atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
        return outcome
