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
from novel_manga.media.first_frames import enabled, current_contract, matches_current_contract, source_asset_state, require_current, review_problems, source_conflicts, interpret_review, unchanged_opening_scale, REVIEW_POLICY
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


def authored_row(ctx,clip,row,*,field_errors=None):
    """The creator owns the opening prompt; production binds cards and executes it."""
    row=copy.deepcopy(row);opening=source_opening(ctx,clip)
    row.setdefault('extra_references',[])
    if isinstance(row['extra_references'],list) and all(isinstance(p,str)for p in row['extra_references']):
        from novel_manga.media.reference_views import canonical_paths
        row['extra_references']=canonical_paths(ctx,row['extra_references'])
    if isinstance(row.get('image_references'),list) and all(isinstance(p,str)for p in row['image_references']):
        from novel_manga.media.reference_views import opening_references
        row['image_references']=[ref['path'] for ref in opening_references(ctx,clip,row['image_references'])]
    if opening is None:return row # retained historical input has no authored prompt
    from novel_manga.story.fields import opening_frame_error
    if issue:=opening_frame_error(opening):
        if field_errors is None:raise ValueError(issue)
        field_errors['opening_frame']=issue
        return row
    for field in ('image_prompt','visible_characters'):
        if field in row and row[field]!=opening[field]:
            error=(
                f'{field} differs from the approved source opening_frame. '
                'Edit source opening_frame through input repair before changing its production prompt or visible people. '
                f"For {clip['clip_id']}, write output/repair.json with decisions:[{{action:'input',clip_id:'{clip['clip_id']}',"
                "reason:'specific observed defect',source_quote:'verbatim source for this clip',"
                f"shots:[{{index:{clip['shot_indexes'][0]},opening_frame:<complete corrected opening_frame object>}}]}}]. "
                'Call check then apply. After the source repair is adopted, omit image_prompt and visible_characters '
                'from the first_frames row to inherit that source. Reference-selection-only corrections may change '
                'image_references directly; inspect opening_frame.reference_subjects when choosing cards.')
            if field_errors is None:raise ValueError(error)
            field_errors[field]=error
        # The approved value permits other independent argument checks. Any
        # recorded conflict still rejects the entire batch before generation.
        row[field]=copy.deepcopy(opening[field])
    return row


def opening_state(row, contract):
    """The same approved opening-state facts the review is judged against.

    The image supplier receives only what the source opening contract says.
    It is not given extra staging instructions (hands, framing exclusions) that
    the review does not require; a pole/mount staying out of frame is the one
    approved exclusion and lives in the authored prompt itself.
    """
    opening = contract.get('opening') or {}
    facts = {}
    for key in ('start_state', 'camera', 'light', 'in_frame', 'props', 'wears',
                'location', 'scene_objects', 'device_state', 'shot_scale'):
        value = opening.get(key)
        if value not in (None, '', [], {}):
            facts[key] = copy.deepcopy(value)
    if opening.get('opening_frame'):
        frame = copy.deepcopy(opening['opening_frame'])
        frame.pop('image_prompt', None)
        frame.pop('visible_characters', None)
        if frame:
            facts['opening_frame'] = frame
    if contract.get('character_facts'):
        facts['character_facts'] = copy.deepcopy(contract['character_facts'])
    return facts


def request_prompt(ctx, row, contract, *, references=()):
    """Compile the actual image request from the reviewed opening contract."""
    from novel_manga.media.first_frames import compose_opening_image_prompt
    prompt = compose_opening_image_prompt(
        row['image_prompt'], contract.get('style') or {},
        row.get('image_references', contract.get('image_references', [])),
        references, opening=contract.get('opening'))
    facts = opening_state(row, contract)
    if not facts:
        return prompt
    return (prompt + '\n\nApproved opening state (source contract, not new staging direction):\n'
            + json.dumps(facts, ensure_ascii=False, sort_keys=True))


def build(ctx,row,path):
    from novel_manga.config import Settings
    from novel_manga.providers.phanrouter import PhanRouterMediaProvider
    settings=Settings.from_env(provider='phanrouter',output_root=ctx.run_dir,admission_mode='preview')
    provider=PhanRouterMediaProvider(settings)
    refs=[ctx.novel_dir/p for p in row['image_references']]
    from novel_manga.application.profiles import frame_spec, load_profile
    from novel_manga.style_packs import frame_style
    from novel_manga.media.first_frames import compose_opening_image_prompt
    ratio=frame_spec(load_profile(ctx.novel_dir))['video_ratio']
    prompt=row.get('request_prompt') or compose_opening_image_prompt(
        row['image_prompt'],frame_style(ctx.novel_dir),row['image_references'])
    provider.create_image(prompt,path,reference=refs[0],additional_references=tuple(refs[1:]),aspect_ratio=ratio)


def check(ctx,path,row,contract):
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    from novel_manga.style_packs import opening_appearance
    review_contract=copy.deepcopy(contract)
    if 'style'in review_contract:review_contract['style']=opening_appearance(review_contract['style'])
    instructions=('检查新生成的单一镜头首帧与原始素材及本镜开场状态。第一张是首帧，后面是原素材卡。'
        '本次只审时间零点t=0：以下JSON的opening.opening是源镜头，start_state及opening_frame描述开场。'
        'camera可能是一整镜的运动说明；只将其初始机位、景别、视线与明确持续的空间约束用于首帧，保留这些要求。'
        '其中trigger之后的动作、actor_path箭头右侧、endpoint/终点和hold描述后续过程或末态，不是首帧必须完成的动作。'
        '画面符合start_state时，不得仅因尚未完成camera中的后续动作判contradiction；'
        '若开场文字自身明确互相矛盾，应引用实际冲突原句说明源输入问题，不猜选末态来要求重画。'
        'reference_images逐项标明本次实际附件图号；identity_portrait只约束脸和头发，'
        'wardrobe_design只约束可见服装、体态和配饰，不用衣装卡中的脸覆盖身份肖像。'
        '衣装卡的姿势、持物和背包佩戴状态仍以本镜opening当前状态为准。'
        '逐张描述实际可见内容后判断；报告明确可见的身份/服饰/空间或开场机位、持物、可见人物矛盾。'
        '若opening.style给出所选画风，核对其明确的媒介、材质、色光与构图规则；'
        '原素材卡的拍摄媒介不覆盖所选画风。没有画风约定时不增加审美要求，轻微色差不自动失败。'
        '人物卡仅约束脸和可见稳定设计，不把卡片姿势/背景强加首帧。首帧必须是单一可拍摄画面，不能有拼图分格、字幕。'
        'character_facts给出当前人物稳定外观/服饰及来源字段，补充肖像卡未展示的部位；当前装备状态以opening的start_state、device_state、props、wears为准，'
        '不把稳定signature_prop当作本镜必须出现的持物，也不把后续动作或末态强加开场。'
        'POV不要求主角露脸；不要把后续动作或新人物提前放进开场。未展示的道具/身体部分不等于缺失。'
        '即使visible_characters为空，入画的腿、鞋、袖口等可见部位仍须与对应人物事实比较；不得要求画外部位出现。'
        '每条findings返回实际可见事实observed、对照要求expected及basis（本次contract字段路径或原素材图号），给出reason和relation。'
        '同时返回origin：只有源字段彼此矛盾、即任何图片都无法同时满足时才标source；'
        'source的basis须引用至少两个实际冲突的源字段路径，observed和expected分别引用其原句，说明为何不能同时成立。'
        '源要求一致而实际图片不符合要求时标image；不要把图片画错标成source，也不要把源内部冲突转成重画要求。'
        'basis只能引用本次JSON中实际存在的字段路径；camera字符串中的endpoint、actor_path等标签不是独立JSON字段。'
        '引用camera时，expected须标出作为开场要求的原句，不能把后续终点改称开场终点。'
        'contradiction表示实际可见属性与明确当前依据不能同时成立；未展示、看不清或依据未指定是underspecified；可同时成立是compatible。'
        '只返回observations及findings，不另给自由通过结论或问题清单；程序只从明确contradiction生成通过结论和问题。无冲突可返回空findings。'
        '不替首帧脑补正确事实，不把审美偏好当成失败。\n')
    if 'image_references' in contract and list(contract['image_references'])!=row['image_references']:
        raise ValueError('image review must receive the exact current ordered reference images')
    from novel_manga.media.reference_views import scope_of
    bindings={r['path']:dict(r) for r in contract.get('source_references',[]) if r.get('path')}
    for ref in contract.get('reference_views',[]):bindings[ref['path']]={**bindings.get(ref['path'],{}),**ref}
    supplied=[]
    for reference_path in row['image_references']:
        ref={**bindings.get(reference_path,{}),'path':reference_path}
        if scope:=scope_of(ctx.novel_dir,ref):ref['reference_scope']=scope
        supplied.append(ref)
    instructions+=json.dumps({'opening':review_contract,'visible_characters':row['visible_characters'],
        'image_prompt':row['image_prompt'],'reference_images':[
            {'image_number':index+2,**{k:ref[k] for k in ('path','name','role','reference_scope') if k in ref}}
            for index,ref in enumerate(supplied)]},ensure_ascii=False)
    raw=ask_json([{'type':'text','text':instructions},image_part(path,1280),
        *(image_part(ctx.novel_dir/p,1024) for p in row['image_references'])],
        obj({'observations':{'type':'array','items':{'type':'string'}},
             'findings':{'type':'array','items':obj({
                 'observed':{'type':'string'},'expected':{'type':'string'},
                 'basis':{'type':'array','minItems':1,'items':{'type':'string'}},
                 'origin':{'type':'string','enum':['source','image']},
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


def current_review(candidate,row,contract,assets):
    """Return the latest saved review for this candidate and these exact review inputs."""
    review_input={'policy':REVIEW_POLICY,'source_contract':contract,'source_assets':assets,
                  'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters']}
    records=sorted((candidate/'reviews').glob('review_*.json'))
    for record in reversed(records):
        saved=read_json(record,{})
        if saved.get('input')==review_input:
            return interpret_review(saved.get('review') or saved.get('raw') or {})
    return None


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
                if conflicts:=source_conflicts(rejected.get('review')or{}):
                    row['last_rejection']['source_conflicts']=conflicts
                if issue is not None:
                    row['next_action']=(
                        'Inspect the rejected image and review evidence. Correct the relevant source opening_frame through input repair '
                        'or correct the registered reference selection, then call first_frames. '
                        'After restoring missing original reference evidence, reuse_candidate can review an existing image against it without another generation. '
                        'If the source inputs are already correct and a visible pixel defect remains, request one bounded retake with '
                        "retake_candidate:{candidate:'candidate_NN',reason:'specific visible pixel defect'}; this uses one remaining image candidate.")
            from novel_manga.media.reference_views import opening_references
            saved_input=read_json(latest/'input.json',{})
            original_paths=saved_input.get('image_references') or []
            restored=[r['path'] for r in opening_references(ctx,clip,original_paths)
                      if r.get('reference_scope')=='wardrobe_design' and r['path'] not in original_paths]
            last_input=read_json(reviews[-1],{}).get('input',{}) if reviews else {}
            reviewed_paths=(last_input.get('source_contract') or {}).get('image_references',[])
            if issue is not None and reviews and last_input.get('policy')!=REVIEW_POLICY:
                rows[clip['clip_id']]['review_policy_changed']={'previous':last_input.get('policy'),'current':REVIEW_POLICY}
                rows[clip['clip_id']]['next_action']=(
                    f"Opening review instructions changed. Use reuse_candidate:{latest.name!r} "
                    'without retake_candidate to review this retained image before any new generation. '
                    'Start with a single-row frames request and inspect its result before re-reviewing other images. '
                    'Keep source text, original requests and image counts. A new review can still reject a real visible defect.')
            if restored and not set(restored)<=set(reviewed_paths):
                rows[clip['clip_id']]['restored_original_reference_views']=restored
                rows[clip['clip_id']]['next_action']=(
                    f"Original supplied wardrobe evidence is now available. Use reuse_candidate:{latest.name!r} "
                    'without retake_candidate to review the retained image with these references first. '
                    'The host binds the required views; original requests and image counts are retained. This grants no new generation.')
            if issue is not None and reviews and last_input.get('policy')==REVIEW_POLICY and source_conflicts(review):
                if matches_current_contract(ctx,clip,last_input.get('source_contract') or {}):
                    rows[clip['clip_id']]['repair_origin']='source'
                    rows[clip['clip_id']]['next_action']=(
                        'The reviewed source fields conflict with each other. Use normal input repair (check then apply), not retake. '
                        'Resolve the cited source requirements through the production Agent, then reuse_candidate to recheck the retained image. '
                        'No new image can resolve contradictory source requirements.')
                else:
                    rows[clip['clip_id']]['next_action']=(
                        f"The source changed since this rejection. Use reuse_candidate:{latest.name!r} to review the retained image "
                        'against the adopted source before another input revision or image generation.')
    return {'mode':'first-frame','extra_reference_policy':'needed','clips':rows,
            'command':'python3 /work/tools/episode.py first_frames output/first-frames.json',
            'schema':{'render_ready':False,'frames':[{'clip_id':'current clip id','image_prompt':'single opening image prompt',
                'image_references':['registered source image paths, ordered'],
                'visible_characters':['people actually visible at t=0; POV can be empty'],
                'reuse_candidate':'optional existing candidate_NN in this clip; reuse its saved image with the complete current review, no new image',
                'retake_candidate':{'candidate':'candidate_NN rejected candidate with saved opening.png','reason':'specific visible pixel defect; consumes one remaining image candidate'},
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
        prepared=[];seen=set();argument_errors={};invalid_fields={}
        for row in rows:
            if not isinstance(row,dict) or row.get('clip_id') not in clips or row['clip_id'] in seen:
                raise ValueError('frames must select distinct current clips')
            cid=row['clip_id'];seen.add(cid);clip=clips[cid]
            errors={};invalid_types=set()
            row=authored_row(ctx,clip,row,field_errors=errors)
            for field in ('image_prompt','source_quote'):
                if not isinstance(row.get(field),str) or not row[field].strip():
                    errors.setdefault(field,f'{field} requires text');invalid_types.add(field)
            for field in ('image_references','extra_references','visible_characters'):
                if not isinstance(row.get(field),list) or any(not isinstance(p,str) for p in row[field]) or len(set(row[field]))!=len(row[field]):
                    errors.setdefault(field,f'{field} requires distinct strings');invalid_types.add(field)
            original={r['path'] for r in clip.get('references',[]) if r.get('role') in {'character','location','prop'}}
            if 'image_references' not in invalid_types and (not row['image_references'] or not set(row['image_references'])<=registered):
                errors['image_references']=f'image_references must use registered source cards; current clip paths: {sorted(original & registered)}'
            extras_valid='extra_references' not in invalid_types and set(row['extra_references'])<=original
            if 'extra_references' not in invalid_types and not extras_valid:
                errors['extra_references']=f'extra_references must select current source image paths: {sorted(original)}'
            source='\n'.join(segments.get(s,'') for s in clip.get('segment_ids',[]))
            if 'source_quote' not in invalid_types and row['source_quote'] not in source:
                errors['source_quote']=('source_quote must quote this clip source verbatim, not a summary. '
                    f'Read segments.json for segment_ids={clip.get("segment_ids",[])}. '
                    f'Current scoped source text: {source}')
            from .requests import compose_request
            if extras_valid and 'visible_characters' not in invalid_types:
                draft_frame={'path':'pending.png','extra_references':row['extra_references'],'visible_characters':row['visible_characters']}
                request_field='prompt_h3' if isinstance(row.get('prompt_h3'),str) and row['prompt_h3'].strip() else 'h3'
                try:
                    if request_field=='h3':
                        row['prompt_h3']=compose_request({**clip,'first_frame':draft_frame},row.get('h3'))
                    validate_existing_request({**clip,'first_frame':draft_frame},row['prompt_h3'])
                except ValueError as error:errors[request_field]=str(error)
            if errors:
                argument_errors[cid]=list(errors.values());invalid_fields[cid]=sorted(errors);continue
            contract=current_contract(ctx,clip,row['image_references'],include_style=True)
            bindings={r['path']:r for r in catalog if r.get('path')}
            bindings.update({r['path']:{**bindings.get(r['path'],{}),**r} for r in clip.get('references',[]) if r.get('path')})
            from novel_manga.media.reference_views import opening_references
            bindings.update({r['path']:r for r in opening_references(ctx,clip,row['image_references'])})
            row['request_prompt']=request_prompt(ctx,row,contract,references=bindings.values())
            prepared.append((row,contract,source_asset_state(ctx,row['image_references'])))
        if argument_errors:
            self.partial_outcome={'argument_errors':argument_errors,'validation_targets':sorted(seen),
                                  'invalid_fields':invalid_fields,
                                  'validated_clips':[row['clip_id'] for row,_,_ in prepared],
                                  'validation_scope':'request_arguments_only; no images generated or accepted'}
            raise ValueError('Correct these first_frames fields before image generation: '+json.dumps(argument_errors,ensure_ascii=False))
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
            retake=row.get('retake_candidate')
            if retake is not None and 'reuse_candidate' in row:
                raise ValueError(f'{cid}: retake_candidate and reuse_candidate are mutually exclusive')
            if retake is not None:
                if (not isinstance(retake,dict) or set(retake)!={'candidate','reason'}
                        or not isinstance(retake.get('candidate'),str)
                        or not isinstance(retake.get('reason'),str) or not retake['reason'].strip()):
                    raise ValueError(f'{cid}: retake_candidate requires candidate and a nonempty reason')
                parent=next((d for d in dirs if d.name==retake['candidate']),None)
                if (parent is None or not (parent/'input.json').is_file() or not (parent/'opening.png').is_file()
                        or not (parent/'rejected.json').is_file()):
                    raise ValueError(f'{cid}: retake_candidate must name this clip\'s rejected candidate with an image')
                reviewed=current_review(parent,row,contract,assets)
                if reviewed is None:
                    raise ValueError(f'{cid}: current source/reference evidence differs from the saved review; '
                        f'use reuse_candidate:{parent.name!r} without retake_candidate to review the existing image first, with no new image generation')
                if reviewed.get('consistent') is True and not review_problems(reviewed):
                    raise ValueError(f'{cid}: retake_candidate must name a candidate rejected by image review')
                if source_conflicts(reviewed):
                    raise ValueError(f'{cid}: source input fields conflict; use normal input repair (check then apply), '
                        'then reuse_candidate to review the retained image. A retake cannot repair the source input.')
                decision_path=parent/'retake-decision.json'
                decision=read_json(decision_path,{})
                if decision:
                    candidate=next((d for d in dirs if d.name==decision.get('replacement_candidate')),None)
                    if candidate is None:
                        raise ValueError(f'{cid}: saved retake replacement candidate is missing')
                    if read_json(candidate/'input.json',{})!=identity:
                        raise ValueError(f'{cid}: saved retake replacement belongs to different image inputs')
                else:
                    if len(dirs)>=image_candidate_limit(ctx,cid):
                        results[cid]={'ok':False,'error':'first-frame image candidate budget used'};return None
                    candidate=root/f'candidate_{len(dirs)+1:02d}'
                    candidate.mkdir()
                    atomic_write_json(candidate/'input.json',identity)
                    atomic_write_json(decision_path,{'replacement_candidate':candidate.name,'reason':retake['reason'],
                        'input_relationship':{'parent_candidate':parent.name,'replacement_input':identity}})
            elif 'reuse_candidate'in row:
                candidate=next((d for d in dirs if d.name==reuse),None)
                if candidate is None or not (candidate/'input.json').is_file() or not (candidate/'opening.png').is_file():
                    raise ValueError(f'{cid}: reuse_candidate must name this clip\'s existing generated candidate')
                saved=read_json(candidate/'input.json')
                row['request_prompt']=saved.get('request_prompt') or saved.get('image_prompt') or row['request_prompt']
            else:
                # Resume the newest matching retake chain before considering its rejected parent.
                candidate=None
                for parent in dirs:
                    decision=read_json(parent/'retake-decision.json',{})
                    replacement=next((d for d in dirs if d.name==decision.get('replacement_candidate')),None)
                    if replacement and read_json(replacement/'input.json',{})==identity:
                        candidate=replacement
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
            if not (candidate/'image-request.json').is_file():
                from novel_manga.application.profiles import frame_spec, load_profile
                atomic_write_json(candidate/'image-request.json',{
                    'authored_prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
                    'image_references':list(row['image_references']),
                    'extra_references':list(row['extra_references']),
                    'visible_characters':list(row['visible_characters']),
                    'ratio':frame_spec(load_profile(ctx.novel_dir))['video_ratio'],
                    'opening_state':opening_state(row,contract)})
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
                          'problems':review_problems(review),'evidence':str(export.relative_to(ctx.work_dir)),
                          'repair_origin':'source' if source_conflicts(review) else 'image'}
                if rejected['repair_origin']=='source':
                    rejected['next_action']='Use normal input repair (check then apply), not retake; then reuse_candidate to review the retained image.'
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
        from .lifecycle import paused
        pending={};next_row=0;first_error=None;dispatch_paused=False
        with ThreadPoolExecutor(max_workers=min(workers,len(prepared))) as pool:
            while True:
                while (first_error is None and not dispatch_paused and next_row<len(prepared) and len(pending)<workers
                       and not any(future.done() for future in pending)):
                    if paused():
                        dispatch_paused=True
                        break
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
        if dispatch_paused:
            self.partial_outcome.update(paused=True,pending_clips=[row['clip_id'] for row,_,_ in prepared[next_row:]])
        atomic_write_json(ctx.work_dir/'input/clip_plan.json',plan)
        if first_error is not None:raise first_error
        outcome={'ok':not dispatch_paused and len(results)==len(prepared) and all(r.get('ok') for r in results.values()),
                 **self.partial_outcome,'first_frames':status(ctx,plan)}
        atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
        return outcome
