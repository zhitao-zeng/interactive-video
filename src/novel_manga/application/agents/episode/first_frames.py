"""Host-compiled fresh opening images; bounded candidates, existing supplier resumption."""
from __future__ import annotations

import copy
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from PIL import Image
import shutil

from novel_manga.util import atomic_write_json, read_json
from novel_manga.media.first_frames import (CompiledOpening, enabled, compile_opening, current_contract,
    matches_current_contract, source_opening, source_asset_state, source_assets_match, source_contracts_match,
    require_current, review_problems, source_conflicts, interpret_review, unchanged_opening_scale, REVIEW_POLICY,
    opening_state as compiled_opening_state, request_prompt as compiled_request_prompt)
from novel_manga.application.profiles import h3_stamp
from .contracts import validate_existing_request
from .opening_source import review_batch as review_opening_sources, current_review as current_opening_review
from .tool_inputs import input_file
from .arguments import service_interruption
from .input_contracts import opening_grant, operation_grant, source_patch_fields

MAX_CANDIDATES=3
FIRST_FRAME_REVIEW_MAX_TOKENS=8000


class FirstFrameReviewUncertain(ValueError):
    """Visual confirmation did not establish a rejection or a pass."""


def review_configuration(*,settings=None,max_tokens=None):
    """The actual review route and output controls, without credentials."""
    from novel_manga.review.endpoints import judge_settings
    from novel_manga.llm.policy import json_policy
    settings=judge_settings() if settings is None else settings
    controls=json_policy(settings.model,'review')
    maximum=FIRST_FRAME_REVIEW_MAX_TOKENS if max_tokens is None else max_tokens
    if settings.stream:
        maximum=max(maximum,settings.min_max_tokens)
        controls.setdefault('reasoning_effort',settings.reasoning)
    return {'policy':REVIEW_POLICY,'model':settings.model,'endpoints':list(settings.endpoints),
            'max_tokens':maximum,'reasoning':controls,'stream':settings.stream}


def ensure_current_h3(ctx,clip):
    cid=clip['clip_id']
    note=read_json(ctx.episode_dir/'review_feedback.json',{}).get(cid,'')
    expected=h3_stamp(clip,note)
    prompt=clip.get('prompt_h3')
    fresh=isinstance(prompt,str) and bool(prompt.strip()) and clip.get('prompt_h3_of')==expected
    if fresh:
        try:validate_existing_request(clip,prompt)
        except ValueError:fresh=False
    if fresh:return
    clip.pop('prompt_h3',None);clip.pop('prompt_h3_of',None)
    from novel_manga.application.rendering.h3 import convert
    from .arguments import CompileConflict
    source=read_json(ctx.episode_dir/'chapter_script.json',{}).get('shots') or clip.get('shots') or []
    source_shots=[copy.deepcopy(shot) for shot in source if shot.get('index') in clip.get('shot_indexes',[])]
    previous=(read_json(ctx.work_dir/'input/latest-first-frames.json',{}).get('clips') or {}).get(cid,{})
    proof=None
    if (previous.get('service_interrupted') and previous.get('source_conflicts')
            and previous.get('request_source_stamp')==expected):
        proof={key:copy.deepcopy(previous[key]) for key in ('source_conflicts','source_conflicts_confirmed',
            'shot_indexes','source_shots','source_review','compiler_revision_used','request_source_stamp') if key in previous}
    problems=[];errors=[];conflicts=[]
    if proof is None:
        convert(clip,tries=1,note=note,episode_dir=ctx.episode_dir,problems=problems,errors=errors,source_conflicts=conflicts)
        if conflicts:
            proof={'source_conflicts':copy.deepcopy(conflicts),'source_conflicts_confirmed':False,
                'shot_indexes':list(clip.get('shot_indexes') or []),'source_shots':source_shots,
                'request_source_stamp':expected,'compiler_revision_used':False}
    if proof is not None:
        conflicts=copy.deepcopy(proof['source_conflicts'])
        if 'source_review' not in proof:
            from novel_manga.application.preparation.audit import audit
            try:proof['source_review']=audit(ctx.episode_dir,concerns={cid:proof['source_conflicts']})
            except Exception as error:
                error.h3_source_claims=proof
                raise
        answer=proof['source_review']
        if answer.get('source_readable') is True and not answer.get('issues'):
            proof['compiler_revision_used']=True
            problems=[];errors=[];conflicts=[]
            convert(clip,tries=1,note=note,episode_dir=ctx.episode_dir,problems=problems,errors=errors,
                source_conflicts=conflicts,source_review={'conflicts':proof['source_conflicts'],'answer':answer})
        if conflicts:
            proof['revision_conflicts']=copy.deepcopy(conflicts)
            raise CompileConflict(f'{cid}: H3 compiler source claim requires resolution',proof)
    if errors:
        if proof is not None:errors[-1].h3_source_claims=proof
        raise errors[-1]
    try:
        if problems:raise ValueError(f'{cid}: H3 translation rejected: '+'; '.join(map(str,problems)))
        prompt=clip.get('prompt_h3')
        if not isinstance(prompt,str) or not prompt.strip():raise ValueError(f'{cid}: H3 translation produced no request')
        if clip.get('prompt_h3_of')!=h3_stamp(clip,note):
            raise ValueError(f'{cid}: H3 translation is stale for the accepted first frame')
        validate_existing_request(clip,prompt)
    except ValueError as error:
        if proof is not None:error.h3_source_claims=proof
        raise
    return proof


def check_redundant_h3(row,clip):
    from .requests import compose_request
    supplied=[]
    if 'prompt_h3' in row:supplied.append(row['prompt_h3'])
    if 'h3' in row:supplied.append(compose_request(clip,row['h3']))
    if any(value!=clip['prompt_h3'] for value in supplied):
        raise ValueError(f"{clip['clip_id']}: supplied H3 differs from the host-compiled request; "
            'omit h3/prompt_h3 and repair the confirmed source or request defect through the normal repair path')


def image_candidate_limit(ctx,cid):
    """Image-only authority; video, input and recheck grants stay independent."""
    grants=read_json(ctx.episode_dir/'repair_budget_grants.json',{})
    grant=grants.get(cid,{}).get('first_frame') or {}
    if not grant.get('authorization') or not grant.get('reason'):return MAX_CANDIDATES
    return max(MAX_CANDIDATES,int(grant.get('limit',MAX_CANDIDATES)))


def authored_row(ctx,clip,row,*,field_errors=None):
    return compile_opening(ctx,clip,row,field_errors=field_errors).row


def opening_state(row,contract=None):
    if isinstance(row,CompiledOpening):return compiled_opening_state(row.row,row.contract)
    return compiled_opening_state(row,contract)


def request_prompt(ctx,row,contract=None,*,references=()):
    if isinstance(row,CompiledOpening):return row.row['request_prompt']
    return compiled_request_prompt(ctx,row,contract,references=references)


def build(ctx,row,path):
    from novel_manga.config import Settings
    from novel_manga.providers.phanrouter import PhanRouterMediaProvider
    settings=Settings.from_env(provider='phanrouter',output_root=ctx.run_dir,admission_mode='preview')
    provider=PhanRouterMediaProvider(settings)
    compiled=row if isinstance(row,CompiledOpening) else None
    if compiled is not None:row=compiled.row
    refs=[ctx.novel_dir/r.path for r in compiled.references] if compiled is not None else [ctx.novel_dir/p for p in row['image_references']]
    from novel_manga.application.profiles import frame_spec, load_profile
    from novel_manga.style_packs import frame_style
    from novel_manga.media.first_frames import compose_opening_image_prompt
    ratio=frame_spec(load_profile(ctx.novel_dir))['video_ratio']
    prompt=row.get('request_prompt') or compose_opening_image_prompt(
        row['image_prompt'],frame_style(ctx.novel_dir),row['image_references'])
    provider.create_image(prompt,path,reference=refs[0],additional_references=tuple(refs[1:]),aspect_ratio=ratio)


def _review_context(ctx,row,contract=None):
    """The same scoped opening and ordered references for both visual calls."""
    from novel_manga.style_packs import opening_appearance
    compiled=row if isinstance(row,CompiledOpening) else None
    if compiled is not None:row,contract=compiled.row,compiled.contract
    review_contract=copy.deepcopy(contract)
    if 'style'in review_contract:review_contract['style']=opening_appearance(review_contract['style'])
    if 'image_references' in contract and list(contract['image_references'])!=row['image_references']:
        raise ValueError('image review must receive the exact current ordered reference images')
    if compiled is not None:
        supplied=[r.as_dict() for r in compiled.references]
    else:
        from novel_manga.media.reference_views import scope_of
        bindings={r['path']:dict(r) for r in contract.get('source_references',[]) if r.get('path')}
        for ref in contract.get('reference_views',[]):bindings[ref['path']]={**bindings.get(ref['path'],{}),**ref}
        supplied=[]
        for reference_path in row['image_references']:
            ref={**bindings.get(reference_path,{}),'path':reference_path}
            if scope:=scope_of(ctx.novel_dir,ref):ref['reference_scope']=scope
            supplied.append(ref)
    images=[{'image_number':index+2,**{k:ref[k] for k in ('path','name','role','reference_scope') if k in ref}}
            for index,ref in enumerate(supplied)]
    return row,review_contract,images


def _review_text_fields(value,path=''):
    """Return actual source text leaves; never treat reference images as text."""
    if isinstance(value,str):
        return {path:value} if value.strip() else {}
    if isinstance(value,dict):
        return {key:text for name,item in value.items()
                for key,text in _review_text_fields(item,f'{path}.{name}' if path else name).items()}
    if isinstance(value,list):
        return {key:text for i,item in enumerate(value)
                for key,text in _review_text_fields(item,f'{path}[{i}]').items()}
    return {}


def _review_evidence_value(payload,path):
    """Resolve a reported path against this exact review payload only."""
    if not isinstance(path,str) or not re.fullmatch(r'[^.\[\]]+(?:\[\d+\])?(?:\.[^.\[\]]+(?:\[\d+\])?)*',path):
        return None
    if path.startswith('author_text_fields.'):
        return payload['author_text_fields'].get(path.removeprefix('author_text_fields.'))
    if path in payload['author_text_fields']:
        return payload['author_text_fields'][path]
    if not (path.startswith(('opening.','character_facts[','prop_facts[','reference_images['))
            or path in {'image_prompt','visible_characters'}):
        return None
    def get(candidate):
        value=payload
        for part in re.findall(r'[^.\[\]]+|\[\d+\]',candidate):
            if part.startswith('['):
                index=int(part[1:-1])
                if not isinstance(value,list) or index>=len(value):return None
                value=value[index]
            else:
                if not isinstance(value,dict) or part not in value:return None
                value=value[part]
        return value
    value=get(path)
    return value if value is not None else get('opening.'+path)


def _source_opening_path(path):
    """Normalize only the existing review envelope, not story-field names."""
    path=path.removeprefix('author_text_fields.')
    while path.startswith('opening.'):
        path=path.removeprefix('opening.')
    return 'opening_frame.image_prompt' if path=='image_prompt' else path


_EXPLICIT_COLOUR=re.compile(
    r'(?i)(?<![a-z])(?:red|orange|yellow|green|blue|purple|violet|pink|black|white|'
    r'grey|gray|brown|beige|tan|khaki|olive|navy|teal|turquoise|ivory|cream|gold|golden|'
    r'silver|maroon|burgundy|crimson|scarlet|lavender|lilac|cyan|magenta|mustard)(?![a-z])'
    r'|(?:红|橙|黄|绿|青|蓝|紫|粉|黑|白|灰|棕|褐|米|卡其|驼|金|银|赭|靛|绛)'
    r'(?:色|外套|夹克|衬衫|上衣|裤|裙|鞋|袜|帽|灯|牌|伞|包|双肩|织带|布料)')


def _wardrobe_card_pointer(payload,path,ref):
    # A fact which merely names the selected visual card establishes which
    # reference to use; it does not independently assert the pictured style.
    match=re.fullmatch(r'character_facts\[(\d+)\]\.facts\.wardrobe\.value',path)
    if not match or ref.get('role')!='character' or ref.get('reference_scope')!='wardrobe_design':return False
    facts=(payload.get('opening') or {}).get('character_facts') or []
    index=int(match.group(1))
    value=_review_evidence_value(payload,path)
    return (index<len(facts) and bool(ref.get('name')) and facts[index].get('name')==ref.get('name')
            and isinstance(value,str) and bool(re.search(r'视觉卡|服装卡|参考卡|参考图|reference|wardrobe card',value,re.I)))


def _effective_finding(payload,finding,check):
    """Keep only evidence that can authorize a repair; preserve the raw answer elsewhere."""
    result=check['result'];category=check['evidence_class']
    detail={'id':check['id'],'model_result':result,'evidence_class':category,'result':result}
    if finding.get('origin')=='source' and category not in {'non_colour','mixed_or_unclear'}:
        detail.update(result='uncertain',reason='source conflict needs two current authored fields as evidence')
        return detail,None
    if category=='reference_colour_only':
        # Neither generated pictures nor the supplied cards currently carry a
        # supported, verified shared-light/color calibration record. The model's
        # own calibration assertion cannot create one.
        detail.update(result='uncertain',reason='reference-only colour has no verified shared calibration')
        return detail,None
    if category=='authored_explicit_colour':
        field=check['author_field_path'].removeprefix('author_text_fields.')
        quote=check['author_quote'];subject=check['author_subject']
        actual=payload['author_text_fields'].get(field)
        observed=finding.get('observed') or '';expected=finding.get('expected') or ''
        original_field=(finding.get('author_field_path') or '').removeprefix('author_text_fields.')
        if (not isinstance(actual,str) or not quote.strip() or quote not in actual
                or _source_opening_path(field)=='camera'
                or not _EXPLICIT_COLOUR.search(quote) or not subject.strip() or subject not in quote
                or subject not in observed or subject not in expected
                or _source_opening_path(original_field)!=_source_opening_path(field)
                or finding.get('author_subject')!=subject):
            detail.update(result='uncertain',reason='authored colour or its subject lacks matching current source evidence')
            return detail,None
        detail.update(author_field_path=field,author_quote=quote,author_subject=subject)
        if result!='confirmed':return detail,None
        selected=copy.deepcopy(finding)
        selected.update(expected=quote,basis=[field],reason=observed+'；'+quote)
        return detail,selected
    if result=='uncertain':return detail,None
    observation=check['non_colour_observation'];expected=check['non_colour_expected'];basis=check['non_colour_basis']
    values=[_review_evidence_value(payload,path) for path in basis]
    usable=(observation.strip() and expected.strip() and basis and all(value is not None for value in values))
    if finding.get('origin')=='source':
        usable=(usable and len(set(basis))>=2 and not any(path.startswith('reference_images') for path in basis)
                and isinstance(values[0],str) and isinstance(values[1],str)
                and {_source_opening_path(path) for path in basis}
                    =={_source_opening_path(path) for path in (finding.get('basis') or [])}
                and observation in values[0] and expected in values[1])
    if usable and finding.get('origin')=='image':
        reference_indexes={int(match.group(1)) for path in basis
                           if (match:=re.match(r'^reference_images\[(\d+)\](?:\.|$)',path))}
        text_basis=[path for path in basis if not path.startswith('reference_images[')]
        if len(reference_indexes)==1:
            index=next(iter(reference_indexes));refs=payload['reference_images']
            if (index<len(refs) and check['reference_use']=='wardrobe'
                    and refs[index].get('image_number')==check['reference_image_number']):
                text_basis=[path for path in text_basis if not _wardrobe_card_pointer(payload,path,refs[index])]
        if text_basis:
            field=check['author_field_path'].removeprefix('author_text_fields.')
            quote=check['author_quote'];subject=check['author_subject']
            actual=payload['author_text_fields'].get(field)
            usable=(isinstance(actual,str) and bool(quote.strip()) and quote==expected
                    and quote in actual
                    and (_source_opening_path(field) in {'camera','shot_scale','light'}
                         or bool(subject.strip()) and subject in quote and subject in observation)
                    and any(_source_opening_path(path)==_source_opening_path(field)
                                                for path in text_basis))
        if reference_indexes:
            refs=payload['reference_images'];number=check['reference_image_number'];use=check['reference_use']
            usable=(usable and len(reference_indexes)==1 and next(iter(reference_indexes))<len(refs))
            if usable:
                ref=refs[next(iter(reference_indexes))]
                role=ref.get('role');scope=ref.get('reference_scope')
                usable=(ref.get('image_number')==number and bool(ref.get('path')) and
                        ((use=='character_identity' and role=='character' and scope not in {'wardrobe_design','style_only_not_character_identity'}) or
                         (use=='wardrobe' and role=='character' and scope not in {'identity_portrait','style_only_not_character_identity'}) or
                         (use=='location' and role=='location') or
                         (use=='prop' and role in {'prop','character'}) or
                         (use=='style' and scope=='style_only_not_character_identity')))
        elif check['reference_image_number']!=0 or check['reference_use']!='none':
            usable=False
    if not usable:
        detail.update(result='uncertain',reason='non-colour observation or cited source evidence is missing or unverified')
        return detail,None
    if category=='mixed_or_unclear':
        detail['unresolved_colour']=True
        if result!='confirmed':
            detail.update(result='uncertain',reason='mixed evidence retains an unresolved colour claim')
            return detail,None
    if result!='confirmed':return detail,None
    selected=copy.deepcopy(finding)
    selected.update(observed=observation,expected=expected,
                    basis=list(finding['basis']) if finding.get('origin')=='source' else list(basis),
                    reason=observation+'；'+expected)
    return detail,selected


def _confirmation_payload(row,review_contract,images,raw):
    """The persisted source fields and raw review determine the effective result."""
    indexes=[i for i,finding in enumerate(raw.get('findings',[])) if finding.get('relation')=='contradiction']
    author_values={'opening':review_contract.get('opening',{}),
                   'character_facts':review_contract.get('character_facts',[]),
                   'prop_facts':review_contract.get('prop_facts',[]),'image_prompt':row['image_prompt']}
    author_fields=_review_text_fields(author_values)
    # Keep camera text for non-colour framing checks. It cannot certify a
    # current colour because a whole-shot camera may describe later events.
    # Fact-catalogue source paths are metadata, not the author's factual text.
    author_fields={key:value for key,value in author_fields.items()
                   if not key.endswith('.source')}
    return {'opening':review_contract,'visible_characters':row['visible_characters'],
        'image_prompt':row['image_prompt'],'reference_images':images,'author_text_fields':author_fields,
        'original_initial_review':raw,'contradiction_ids':indexes,
        'color_calibration':'no verified shared illumination or color calibration is supplied in this production record'}


def _review_from_confirmation(payload,raw,confirmation):
    """Recompute a review from untouched initial and confirmation responses."""
    from novel_manga.llm.client import ModelOutputMalformed
    findings=raw.get('findings',[]);indexes=payload['contradiction_ids']
    checks=confirmation.get('checks') if isinstance(confirmation,dict) else None
    if (not isinstance(checks,list) or len(checks)!=len(indexes)
            or any(not isinstance(c,dict) or type(c.get('id')) is not int
                   or c.get('result') not in {'confirmed','dismissed','uncertain'}
                   or c.get('evidence_class') not in {'reference_colour_only','authored_explicit_colour','non_colour','mixed_or_unclear'}
                   or c.get('calibration') not in {'verified_shared','unknown_or_unverified','not_applicable'}
                   or any(not isinstance(c.get(k),str) for k in ('author_field_path','author_quote','author_subject',
                          'non_colour_observation','non_colour_expected'))
                   or not isinstance(c.get('non_colour_basis'),list)
                   or any(not isinstance(p,str) for p in c.get('non_colour_basis',[]))
                   or type(c.get('reference_image_number')) is not int
                   or c.get('reference_use') not in {'none','character_identity','wardrobe','location','prop','style'}
                   or not isinstance(c.get('evidence'),str) or not c['evidence'].strip() for c in checks)
            or {c['id'] for c in checks}!=set(indexes)):
        raise ModelOutputMalformed('episode_first_frame_review_confirmation')
    effective=[];selected={}
    for item in checks:
        decision,finding=_effective_finding(payload,findings[item['id']],item)
        effective.append(decision)
        if finding is not None:selected[item['id']]=finding
    if any(c['result']=='uncertain' for c in effective) and not selected:
        failure=FirstFrameReviewUncertain('first-frame contradictions could not be confirmed or dismissed')
        failure.first_frame_confirmation_effective=copy.deepcopy(effective)
        raise failure
    confirmed={**copy.deepcopy(raw),'confirmation_effective':effective,
               'findings':[selected[i] if i in selected else finding for i,finding in enumerate(findings)
                           if i not in indexes or i in selected]}
    return interpret_review(confirmed)


def confirm_findings(ctx,path,row,contract,raw):
    """Independently check only reported contradictions against the same evidence."""
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    findings=raw.get('findings',[])
    indexes=[i for i,finding in enumerate(findings) if finding.get('relation')=='contradiction']
    if not indexes:return interpret_review(raw),None
    row,review_contract,images=_review_context(ctx,row,contract)
    payload=_confirmation_payload(row,review_contract,images,raw)
    prompt=('独立复核原首帧审核的每条contradiction。第一张图片是原首帧，其余图片严格按reference_images次序为原素材。'
        '给每个contradiction_ids原编号返回一次：confirmed表示实际可见事实与本镜零点明确要求确实相斥；'
        'dismissed表示有证据能同时成立；uncertain表示证据不足。只依据实际像素、本镜合同和素材，'
        '不要把原审观察直接当成事实，也不要把后续动作终点当作本镜开场的隐含禁止。'
        '同时为每条按其实际判断依据选择一类：reference_colour_only表示该条仅比较参考照片和首帧的色调或深浅，'
        '且作者本镜源字段没有对该对象明确写出该颜色；authored_explicit_colour表示作者本镜实际源字段'
        '明确写了该对象颜色，须提供author_field_path、包含颜色的原文逐字author_quote及对应对象author_subject；'
        '不得将源中不存在的颜色拼接到引文，最终要求以该引文为准；'
        'non_colour表示判断本身是物件存在/数量、版型款式、材质、人物、构图或时间等非纯色事实；'
        'mixed_or_unclear表示一条同时依赖颜色与其他事实，或无法可靠分类。'
        '源字段只是“按参考卡”不能算作者逐字写了该颜色；物件名带颜色也不能把物件缺失改成纯色差。'
        '来源颜色字段必须来自author_text_fields，且author_subject必须在原文引句和原疑点中逐字出现；'
        '仅authored_explicit_colour的字段和对象必须与初审author_field_path/author_subject绑定一致，不能在确认时换对象。'
        'author_quote只取该对象自身颜色的短句；不能用包含其他对象颜色的整段来证明该对象的颜色，不清楚则uncertain。'
        '不能用图片图号、模型自行描述、场景无关色彩或推断代替。'
        '无已提供的可核实共同光照、白平衡或颜色校准记录时，calibration填unknown_or_unverified；'
        '不要以看起来类似作为已校准证据。颜色不确定不能当通过；'
        '对已有归属及处置状态的具名剧情道具，若像素中明确多出同一人物随身的同类实例，且源未交代多件，'
        '不可仅因为衣装卡也画有该物件就凭空假定另有一件；源明确多件按源判断。'
        '仅外观相似但身份归属不明、被遮挡或画外的物件不应强认新增；'
        '每条non_colour或mixed_or_unclear应给仅涉及非颜色事实的non_colour_observation、non_colour_expected及non_colour_basis。'
        '非颜色属性可引用该问题实际相关的字段；景别等属性值本身可能不含对象称呼，不强求补写不存在的名词。'
        'image发现若依据作者文本，author_field_path必须是所引用的author_text_fields字段，'
        'author_quote和non_colour_expected必须是该字段同一段逐字原文，不得改写或脑补；'
        'author_subject是该原文与non_colour_observation均逐字包含的实际判断对象；'
        '若依据参考图，只引用一张reference_images数组中的实际图片，reference_image_number填写其image_number，'
        'reference_use按判断用途填写character_identity、wardrobe、location、prop或style；'
        '没有参考图时填写reference_image_number=0和reference_use=none。'
        'style_only_not_character_identity风格图和wardrobe_design服装图不可用于人物身份，'
        'identity_portrait人物头像不能代替服装参考；图片中事实只能由实际图片判断。'
        'basis使用本次合同真实字段路径或reference_images数组路径；source表示两个作者字段本身冲突，'
        '应标non_colour并保留原source归属，其两条basis必须是源字段，'
        'observation和expected须分别逐字摘自这两字段；无法提供时用uncertain。'
        '缺失关键物件及明显版型或材质问题仍应依据实际画面判断。给出逐条证据，不预设任何条目的结论。\n'
        +json.dumps(payload,ensure_ascii=False))
    schema=obj({'checks':{'type':'array','minItems':len(indexes),'maxItems':len(indexes),
        'items':obj({'id':{'type':'integer','enum':indexes},
                     'result':{'type':'string','enum':['confirmed','dismissed','uncertain']},
                     'evidence_class':{'type':'string','enum':['reference_colour_only','authored_explicit_colour','non_colour','mixed_or_unclear']},
                     'calibration':{'type':'string','enum':['verified_shared','unknown_or_unverified','not_applicable']},
                     'author_field_path':{'type':'string'},'author_quote':{'type':'string'},'author_subject':{'type':'string'},
                     'non_colour_observation':{'type':'string'},'non_colour_expected':{'type':'string'},
                     'non_colour_basis':{'type':'array','items':{'type':'string'}},
                     'reference_image_number':{'type':'integer'},
                     'reference_use':{'type':'string','enum':['none','character_identity','wardrobe','location','prop','style']},
                     'evidence':{'type':'string'}})}})
    confirmation=None
    try:
        confirmation=ask_json([{'type':'text','text':prompt},image_part(path,1280),
            *(image_part(ctx.novel_dir/p,1024) for p in row['image_references'])],
            schema,name='episode_first_frame_review_confirmation',max_tokens=FIRST_FRAME_REVIEW_MAX_TOKENS,
            timeout=240,settings=judge_settings())
        review=_review_from_confirmation(payload,raw,confirmation)
    except Exception as error:
        # A failed confirmation is not an image rejection; retain the initial
        # review along with the existing incomplete-review receipt.
        error.first_frame_initial_review=copy.deepcopy(raw)
        if confirmation is not None:error.first_frame_confirmation_raw=copy.deepcopy(confirmation)
        raise
    return review,confirmation


def check(ctx,path,row,contract=None):
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    original_row=row
    row,review_contract,images=_review_context(ctx,row,contract)
    instructions=('检查新生成的单一镜头首帧与原始素材及本镜开场状态。第一张是首帧，后面是原素材卡。'
        '本次只审时间零点t=0：以下JSON的opening.opening是源镜头，start_state及opening_frame描述开场。'
        'camera可能是一整镜的运动说明；只将其初始机位、景别、视线与明确持续的空间约束用于首帧，保留这些要求。'
        '其中trigger之后的动作、actor_path箭头右侧、endpoint/终点和hold描述后续过程或末态，不是首帧必须完成的动作。'
        '画面符合start_state时，不得仅因尚未完成camera中的后续动作判contradiction；'
        '若开场文字自身明确互相矛盾，应引用实际冲突原句说明源输入问题，不猜选末态来要求重画。'
        'reference_images逐项标明本次实际附件图号；identity_portrait只约束脸和头发，'
        'wardrobe_design只约束可见服装、体态和配饰，不用衣装卡中的脸覆盖身份肖像。'
        'style-reference和style_only_not_character_identity只约束色光、材质与渲染，'
        '不复制人物身份、衣装、剧情、文字、布局或POV。'
        '衣装卡的姿势、持物和背包佩戴状态仍以本镜opening当前状态为准。'
        '逐张描述实际可见内容后判断；报告明确可见的身份/服饰/空间或开场机位、持物、可见人物矛盾。'
        '若opening.style给出所选画风，核对其明确的媒介、材质与色光规则；'
        '原素材卡的拍摄媒介不覆盖所选画风。没有画风约定时不增加审美要求，轻微色差不自动失败。'
        '人物卡仅约束脸和可见稳定设计，不把卡片姿势/背景强加首帧。首帧必须是单一可拍摄画面，不能有拼图分格、字幕。'
        'character_facts给出当前人物稳定外观/服饰及来源字段，补充肖像卡未展示的部位；当前装备状态以opening的start_state、device_state、props、wears为准，'
        '不把稳定signature_prop当作本镜必须出现的持物，也不把后续动作或末态强加开场。'
        'prop_facts给出本镜相关道具已登记的身份、归属和设计及其来源，不新增当前入画、持有或佩戴要求；owner不等于本镜正在佩戴。'
        '对本镜具名道具，先在observations说明首帧中实际可见的实例及各自位置、持有人，再与当前状态核对；'
        '只描述首帧像素，参考卡里的物件不能算作首帧实例。不能只观察符合要求的一件而忽略同画面中额外可见的同款物件；'
        '道具未展示或身份看不清时照实说明，不要求它出镜，也不猜其位置。'
        '同一道具已按开场状态落入水中、放在台面或移交他人时，不能因衣装卡或character_facts.wardrobe仍包含该物件，'
        '就凭空假定另有第二件同款道具来消除画面中的持物或空间矛盾。源材料明确有多件时按实际多件处理；'
        '仅外观相似而身份未明确的物件不能强行认作同一个，证据不足用underspecified。'
        '但本镜已有身份归属及处置状态的具名剧情道具，不得凭空增加同一人物随身的额外同类实例；'
        '若画面明确多出这样的实例而源未交代多件，应报告image contradiction，不能仅以无法比对同款细节归为underspecified后放行。'
        '这不禁止未限定的普通背景物件，也不把画外、遮挡或身份确实无法归属的物件判成新增。'
        'POV不要求主角露脸；不要把后续动作或新人物提前放进开场。未展示的道具/身体部分不等于缺失。'
        '即使visible_characters为空，入画的腿、鞋、袖口等可见部位仍须与对应人物事实比较；不得要求画外部位出现。'
        '每条findings返回实际可见事实observed、对照要求expected及basis（本次contract字段路径或原素材图号），给出reason和relation。'
        '每条同时给author_field_path和author_subject：若判断依据是作者明确写出的对象属性，'
        '绑定实际源文本字段路径及原文中该对象的准确称呼；不能拿同字段其他对象的属性替代。'
        '只依赖参考图片、未指定文本属性或无法绑定时两者填空。后续确认沿用此对象和字段。'
        '同时返回origin：只有源字段彼此矛盾、即任何图片都无法同时满足时才标source；'
        'source的basis须引用至少两个实际冲突的源字段路径，observed和expected分别引用其原句，说明为何不能同时成立。'
        '源要求一致而实际图片不符合要求时标image；不要把图片画错标成source，也不要把源内部冲突转成重画要求。'
        'basis只能引用本次JSON中实际存在的字段路径；camera字符串中的endpoint、actor_path等标签不是独立JSON字段。'
        '引用camera时，expected须标出作为开场要求的原句，不能把后续终点改称开场终点。'
        'contradiction表示实际可见属性与明确当前依据不能同时成立；未展示、看不清或依据未指定是underspecified；可同时成立是compatible。'
        '只返回observations及findings，不另给自由通过结论或问题清单；程序只从明确contradiction生成通过结论和问题。无冲突可返回空findings。'
        '不替首帧脑补正确事实，不把审美偏好当成失败。\n')
    instructions+=json.dumps({'opening':review_contract,'visible_characters':row['visible_characters'],
        'image_prompt':row['image_prompt'],'reference_images':images},ensure_ascii=False)
    raw=ask_json([{'type':'text','text':instructions},image_part(path,1280),
        *(image_part(ctx.novel_dir/p,1024) for p in row['image_references'])],
        obj({'observations':{'type':'array','items':{'type':'string'}},
             'findings':{'type':'array','items':obj({
                 'observed':{'type':'string'},'expected':{'type':'string'},
                 'basis':{'type':'array','minItems':1,'items':{'type':'string'}},
                 'author_field_path':{'type':'string'},'author_subject':{'type':'string'},
                 'origin':{'type':'string','enum':['source','image']},
                 'relation':{'type':'string','enum':['contradiction','underspecified','compatible']},
                 'reason':{'type':'string'}})}}),
        name='episode_first_frame',max_tokens=FIRST_FRAME_REVIEW_MAX_TOKENS,timeout=240,settings=judge_settings())
    review,confirmation=confirm_findings(ctx,path,original_row,contract,raw)
    return {**review,'raw':raw,**({'confirmation':confirmation} if confirmation is not None else {})}


def review_input(row,contract,assets,references=(),candidate_path=None):
    result={'policy':REVIEW_POLICY,'source_contract':contract,'source_assets':assets,
            'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters']}
    if references:result['references']=[r.as_dict() for r in references]
    if candidate_path is not None:
        import hashlib
        result['candidate_sha256']=hashlib.sha256(Path(candidate_path).read_bytes()).hexdigest()
    return result


def review_input_matches(saved,current):
    expected=copy.deepcopy(current)
    saved_assets=saved.get('source_assets',[])
    if 'references' not in saved:expected.pop('references',None)
    if not any('hash' in asset for asset in saved_assets):
        for asset in expected.get('source_assets',[]):asset.pop('hash',None)
    actual_assets=expected.pop('source_assets',[])
    remaining={k:v for k,v in saved.items() if k!='source_assets'}
    saved_contract=remaining.pop('source_contract',None)
    expected_contract=expected.pop('source_contract',None)
    return (remaining==expected and source_contracts_match(saved_contract,expected_contract)
            and source_assets_match(saved_assets,actual_assets))


def review_candidate(ctx,path,row,contract=None,assets=None,checker=None):
    """Cache this one review by its actual evidence, retaining every raw response."""
    compiled=row if isinstance(row,CompiledOpening) else None
    if compiled is not None:
        if checker is None:checker=contract
        row,contract,assets=compiled.row,compiled.contract,compiled.assets
    candidate=path.parent
    inputs=review_input(row,contract,assets,compiled.references if compiled is not None else (),path)
    records=sorted((candidate/'reviews').glob('review_*.json'))
    for record in records:
        saved=read_json(record,{})
        if review_input_matches(saved.get('input',{}),inputs):return interpret_review(saved['review']),False,record
    from novel_manga.llm.client import ModelOutputTruncated
    configuration=review_configuration()
    try:
        answer=checker(ctx,path,compiled) if compiled is not None and checker is check else checker(ctx,path,row,contract)
    except Exception as error:
        first=getattr(error,'first_frame_initial_review',None)
        if first is None and not (isinstance(error,ModelOutputTruncated) and error.name=='episode_first_frame'):
            raise
        from novel_manga.service_errors import service_failure
        service=service_failure(error)
        failure={'stage':'review_incomplete','candidate':candidate.name,
                 'image':str(path.relative_to(ctx.novel_dir)),
                 'error':type(error).__name__+': '+str(error),
                 'review_configuration':configuration,**service}
        receipt={'input':inputs,**failure}
        if first is not None:receipt['initial_review_raw']=first
        if hasattr(error,'first_frame_confirmation_raw'):
            receipt['confirmation_raw']=error.first_frame_confirmation_raw
        if hasattr(error,'first_frame_confirmation_effective'):
            receipt['confirmation_effective']=error.first_frame_confirmation_effective
        incomplete_path=candidate/'review-incomplete.json'
        previous=read_json(incomplete_path,{})
        if previous:
            receipt['history']=[*(previous.get('history') or []),
                                {key:value for key,value in previous.items() if key!='history'}]
        atomic_write_json(incomplete_path,receipt)
        error.review_incomplete=failure
        raise
    raw=answer.get('raw',answer)
    confirmation=answer.get('confirmation')
    final={k:v for k,v in answer.items() if k not in {'raw','confirmation'}} if confirmation is not None else raw
    review=interpret_review(final)
    record=candidate/'reviews'/f'review_{len(records)+1:03d}.json'
    saved={'input':inputs,'raw':raw,'review':review}
    if confirmation is not None:saved['confirmation']=confirmation
    atomic_write_json(record,saved)
    if not (candidate/'review.json').exists():atomic_write_json(candidate/'review.json',raw)
    return review,True,record


def current_review(candidate,row,contract=None,assets=None):
    """Return the latest saved review for this candidate and these exact review inputs."""
    compiled=row if isinstance(row,CompiledOpening) else None
    if compiled is not None:row,contract,assets=compiled.row,compiled.contract,compiled.assets
    inputs=review_input(row,contract,assets,compiled.references if compiled is not None else (),candidate/'opening.png')
    records=sorted((candidate/'reviews').glob('review_*.json'))
    for record in reversed(records):
        saved=read_json(record,{})
        if review_input_matches(saved.get('input',{}),inputs):
            return interpret_review(saved.get('review') or saved.get('raw') or {})
    return None


def incomplete_review(ctx,candidate,compiled):
    """Project an unfinished review of retained pixels; never grant a content repair."""
    path=candidate/'opening.png'
    current=review_configuration()
    result={'candidate':candidate.name,'image':str(path.relative_to(ctx.novel_dir)),
            'review_configuration':current,'retryable':True}
    saved=read_json(candidate/'review-incomplete.json',{})
    inputs=review_input(compiled.row,compiled.contract,compiled.assets,compiled.references,path)
    # Policy belongs to review configuration. Failed reviews still identify the
    # same source/pixels after an instruction-policy change permits rechecking.
    evidence={**inputs,'policy':saved.get('input',{}).get('policy')}
    if saved and review_input_matches(saved.get('input',{}),evidence):
        result.update(error=saved['error'],failed_configuration=saved['review_configuration'],
                      evidence=str((candidate/'review-incomplete.json').relative_to(ctx.novel_dir)))
        if saved.get('service_interrupted'):
            result.update(service_interrupted=saved['service_interrupted'],retryable=saved.get('retryable') is True)
        elif saved.get('initial_review_raw') is not None and not saved['error'].startswith(
                ('ModelOutputTruncated:', 'FirstFrameReviewUncertain:')):
            result['retryable']=False
    else:
        # This stopped run predates typed truncation receipts. Recognize only
        # its exact recorded failure and unchanged generation evidence.
        latest=read_json(ctx.work_dir/'input/latest-first-frames.json',{})
        failure=(latest.get('clips') or {}).get(compiled.row['clip_id'],{})
        error='ValueError: episode_first_frame: JSON truncated at 1800 output tokens'
        generated=read_json(candidate/'input.json',{})
        original=review_input(generated,generated.get('source_contract'),generated.get('source_assets'),
                              compiled.references,path) if generated else {}
        if failure.get('error')==error and review_input_matches(original,inputs):
            configuration=review_configuration(max_tokens=1800)
            run=read_json(ctx.run_dir/'agent-run.json',{})
            route=(run.get('effective_route') or run.get('agent_route') or {}).get('roles') or {}
            if review:=route.get('review'):
                from novel_manga.llm.config import JsonEndpoint
                transport=review.get('transport') or {}
                settings=JsonEndpoint(review['model'],
                    tuple(p.strip().rstrip('/') for p in review['base_url'].split(',')),
                    stream=transport.get('QWEN38_LOCAL_STREAM','0')=='1',
                    reasoning=transport.get('QWEN38_LOCAL_REASONING','low'),
                    min_max_tokens=int(transport.get('QWEN38_LOCAL_MIN_MAX_TOKENS','0')))
                configuration=review_configuration(settings=settings,max_tokens=1800)
            result.update(error=error,failed_configuration=configuration,
                          evidence='input/latest-first-frames.json')
        elif (failure.get('error') and failure.get('retryable') is False
                and review_input_matches(original,inputs)):
            # Existing pixels alone do not turn a different terminal tool error
            # into permission to repeat it as a model-output recovery.
            result.update(error=failure['error'],retryable=False,
                          evidence='input/latest-first-frames.json')
    if 'failed_configuration' in result and 'service_interrupted' not in result and result['retryable'] is not False:
        result['retryable']=result['failed_configuration']!=current
    return result


def grant_dict(operation,clip,evidence):
    return operation_grant(operation,clip,evidence).as_dict()


def reference_subject_origin(ctx,clip,paths=()):
    from novel_manga.media.reference_views import canonical_paths,reference_selection
    opening=source_opening(ctx,clip) or {}
    return reference_selection(ctx,clip,subjects=opening.get('reference_subjects') or [],
        paths=canonical_paths(ctx,paths))['origin']


def reference_subject_grant(ctx,clip):
    opening=source_opening(ctx,clip) or {}
    finding={'origin':'source','relation':'contradiction',
        'observed':str(opening.get('reference_subjects') or []),
        'expected':'exact clip-registered subject names only',
        'basis':['opening.opening_frame.reference_subjects'],
        'reason':'the authored reference name is not registered for this clip'}
    return opening_grant(clip,[finding],{
        'opening':{'opening_frame':opening},'candidate':'reference-preflight'}).as_dict()


def reference_failure(ctx,clip,compiled=None,errors=None,*,include_missing=True,paths=()):
    if compiled is None:
        errors={}
        compiled=compile_opening(ctx,clip,{'clip_id':clip['clip_id'],'extra_references':[]},
            field_errors=errors,require_assets=False)
    errors=errors if errors is not None else {}
    field='opening_frame.reference_subjects'
    if field in errors:
        origin=reference_subject_origin(ctx,clip,paths)
        result={'field':field,'error':errors[field],'origin':origin}
        if origin=='source':result['grant']=reference_subject_grant(ctx,clip)
        return result
    for field,error in errors.items():
        if error.startswith('reference registration:'):
            return {'field':field,'error':error,'origin':'registration'}
    missing=[asset['path'] for asset in compiled.assets if asset.get('missing')]
    if include_missing and missing:
        roles={ref.path:ref.role for ref in compiled.references}
        field='image_references' if any(roles.get(path)!='style-reference' for path in missing) else 'style.examples'
        return {'field':field,'origin':'registration',
            'error':'reference registration: registered reference files are missing: '+str(missing)+
                    '; restore the registered files, not the authored story'}
    return None


def current_frame_grant(ctx,clip):
    projected=status(ctx,{'clips':[clip]}) or {}
    row=(projected.get('clips') or {}).get(clip['clip_id'],{})
    latest=read_json(ctx.work_dir/'input/latest-first-frames.json',{})
    service_row=(latest.get('clips') or {}).get(clip['clip_id'],{})
    service_grant=service_row.get('repair_grant') or {}
    interrupted = service_row.get('service_interrupted') or latest.get('service_interrupted')
    retryable = service_row.get('retryable', latest.get('retryable'))
    if (row.get('stage')!='review_incomplete' and interrupted and retryable is True
            and service_grant.get('operation')=='resume'):
        return service_grant
    if row.get('current') is True and row.get('repair_origin')!='source':return None
    if row.get('repair_grant'):return row['repair_grant']
    incomplete=row.get('review_incomplete') or {}
    if (row.get('stage')=='review_incomplete' and incomplete.get('service_interrupted')
            and incomplete.get('retryable') is True):
        return grant_dict('reuse_candidate',clip,{'origin':'review_incomplete',
                                                  'candidate':incomplete['candidate']})
    return None


def validate_frame_operation(row,grant):
    operation='retake_candidate' if 'retake_candidate' in row else 'reuse_candidate' if 'reuse_candidate' in row else 'resume'
    patches=source_patch_fields(row)
    if patches and operation!='resume' or patches and grant and grant['operation']=='resume':
        raise ValueError(f'{operation} cannot carry source patch fields: {sorted(patches)}')
    if grant is None or grant['operation']=='input':return
    allowed=grant['operation'];origin=(grant.get('evidence') or {}).get('origin') or 'current evidence'
    if operation=='retake_candidate' and allowed!=operation:
        raise ValueError(f'origin={origin} allows only {allowed}; retake_candidate is not authorized')
    if operation=='reuse_candidate' and allowed!=operation:
        raise ValueError(f'origin={origin} allows only {allowed}; reuse_candidate is not authorized')


def frame_selector_errors(row,candidates):
    """Check selector syntax and retained files, without reserving a candidate."""
    reusable=[path.name for path in candidates
              if (path/'input.json').is_file() and (path/'opening.png').is_file()]
    rejected=[path.name for path in candidates if path.name in reusable and (path/'rejected.json').is_file()]
    choices={'reuse_candidate':reusable,'retake_candidate':rejected}
    errors={}
    if 'retake_candidate' in row and 'reuse_candidate' in row:
        message='retake_candidate and reuse_candidate are mutually exclusive'
        return {name:message for name in ('retake_candidate','reuse_candidate')},choices
    if 'retake_candidate' in row:
        value=row['retake_candidate']
        if (not isinstance(value,dict) or set(value)!={'candidate','reason'}
                or not isinstance(value.get('candidate'),str)
                or not isinstance(value.get('reason'),str) or not value['reason'].strip()):
            errors['retake_candidate']='retake_candidate requires candidate and a nonempty reason'
        elif value['candidate'] not in rejected:
            errors['retake_candidate']=(f"retake_candidate.candidate={value['candidate']!r} must name this clip's "
                f'rejected candidate with an image; retained candidates: {rejected!r}')
    elif 'reuse_candidate' in row:
        value=row['reuse_candidate']
        if not isinstance(value,str) or value not in reusable:
            errors['reuse_candidate']=(f'reuse_candidate={value!r} must name this clip\'s existing generated '
                f'candidate; retained candidates: {reusable!r}')
    return errors,choices


def failure_result(compiled,error,candidate=None,shot_indexes=()):
    from novel_manga.service_errors import service_failure
    cid=compiled.row['clip_id']
    result={'ok':False,'error':type(error).__name__+': '+str(error),**service_failure(error)}
    if compilation:=getattr(error,'first_frame_h3',None):
        result.update(stage='h3_compile',**compilation)
    from .arguments import CompileConflict
    if isinstance(error,CompileConflict) and error.details.get('source_conflicts'):
        result.update(stage='h3_source_review',**copy.deepcopy(error.details),required_action='source_check')
    if proof:=getattr(error,'h3_source_claims',None):result.update(**copy.deepcopy(proof))
    if incomplete:=getattr(error,'review_incomplete',None):
        result.update(stage='review_incomplete',candidate=candidate or incomplete['candidate'],
                      review_incomplete=incomplete)
    if service_interruption(error):
        result['repair_grant']=grant_dict('resume',{'clip_id':cid,'shot_indexes':shot_indexes},
            {'origin':'service','candidate':candidate,'fingerprint_basis':compiled.row})
    return result


def _source_issue_message(findings):
    if findings and all(f.get('kind')=='continuity_clarification' and f.get('continuity_verified') is True
                        for f in findings):
        return '需要作者澄清当前开场状态'
    return 'opening source fields conflict'


def _set_source_grant(row,clip,findings,evidence):
    grant=opening_grant(clip,findings,evidence)
    row.pop('repair_grant',None)
    if grant is not None:row['repair_grant']=grant.as_dict()
    else:
        row.update(source_repair_unavailable=True,retryable=False,
                   required_action='No supported source field can repair these cited facts; retain evidence and stop.')


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
                else:
                    # A later successful review supersedes only the in-memory
                    # projection of an earlier rejection. Keep both receipts.
                    incomplete=latest/'review-incomplete.json'
                    if not incomplete.is_file() or incomplete.stat().st_mtime_ns < record.stat().st_mtime_ns:
                        rejected={}
            if rejected:
                row=rows[clip['clip_id']]
                row['last_rejection']={'candidate':latest.name,'error':rejected.get('error'),
                    'problems':review_problems(rejected.get('review')or{}),'evidence':rejected.get('evidence')}
                if conflicts:=source_conflicts(rejected.get('review')or{}):
                    row['last_rejection']['source_conflicts']=conflicts
                elif rejected.get('repair_grant') or any(f.get('origin')=='image' and f.get('relation')=='contradiction'
                        for f in (rejected.get('review') or {}).get('findings',[])):
                    row['repair_origin']='image'
                    row['repair_grant']=rejected.get('repair_grant') or grant_dict('retake_candidate',clip,
                        {'origin':'image','candidate':latest.name,'review':rejected['review']})
                if rejected.get('repair_grant') and not conflicts:
                    row['repair_grant']=rejected['repair_grant']
                if issue is not None:
                    row['next_action']=(
                        'Inspect the rejected image and review evidence. Correct the relevant source opening_frame through input repair '
                        'or correct the registered reference selection, then call first_frames. '
                        'After restoring missing original reference evidence, reuse_candidate can review an existing image against it without another generation. '
                        'If the source inputs are already correct and a visible pixel defect remains, request one bounded retake with '
                        "retake_candidate:{candidate:'candidate_NN',reason:'specific visible pixel defect'}; this uses one remaining image candidate.")
            saved_input=read_json(latest/'input.json',{})
            original_paths=saved_input.get('image_references') or []
            try:saved_compiled=compile_opening(ctx,clip,{'clip_id':clip['clip_id'],
                'extra_references':saved_input.get('extra_references') or []},require_assets=False)
            except (OSError,ValueError):saved_compiled=None
            restored=[] if saved_compiled is None else [r.path for r in saved_compiled.references
                      if r.reference_scope=='wardrobe_design' and r.path not in original_paths]
            last_input=read_json(reviews[-1],{}).get('input',{}) if reviews else {}
            reviewed_paths=(last_input.get('source_contract') or {}).get('image_references',[])
            if issue is not None and reviews and last_input.get('policy')!=REVIEW_POLICY:
                rows[clip['clip_id']]['review_policy_changed']={'previous':last_input.get('policy'),'current':REVIEW_POLICY}
                rows[clip['clip_id']]['repair_grant']=grant_dict('reuse_candidate',clip,
                    {'origin':'review_policy','candidate':latest.name,'previous':last_input.get('policy'),'current':REVIEW_POLICY})
                rows[clip['clip_id']]['next_action']=(
                    f"Opening review instructions changed. Use reuse_candidate:{latest.name!r} "
                    'without retake_candidate to review this retained image before any new generation. '
                    'Start with a single-row frames request and inspect its result before re-reviewing other images. '
                    'Keep source text, original requests and image counts. A new review can still reject a real visible defect.')
            if restored and not set(restored)<=set(reviewed_paths):
                rows[clip['clip_id']]['restored_original_reference_views']=restored
                rows[clip['clip_id']]['repair_grant']=grant_dict('reuse_candidate',clip,
                    {'origin':'reference_restoration','candidate':latest.name,'references':restored})
                rows[clip['clip_id']]['next_action']=(
                    f"Original supplied wardrobe evidence is now available. Use reuse_candidate:{latest.name!r} "
                    'without retake_candidate to review the retained image with these references first. '
                    'The host binds the required views; original requests and image counts are retained. This grants no new generation.')
            if issue is not None and reviews and last_input.get('policy')==REVIEW_POLICY and source_conflicts(review):
                if matches_current_contract(ctx,clip,last_input.get('source_contract') or {}):
                    rows[clip['clip_id']]['repair_origin']='source'
                    _set_source_grant(rows[clip['clip_id']],clip,source_conflicts(review),
                        {'opening':(last_input.get('source_contract') or {}).get('opening'),
                         'candidate':latest.name,'review_record':str(record.relative_to(ctx.novel_dir))})
                    rows[clip['clip_id']]['next_action']=(
                        'The reviewed source fields conflict with each other. Use normal input repair (check then apply), not retake. '
                        'Resolve the cited source requirements through the production Agent, then reuse_candidate to recheck the retained image. '
                        'No new image can resolve contradictory source requirements.')
                else:
                    rows[clip['clip_id']]['repair_grant']=grant_dict('reuse_candidate',clip,
                        {'origin':'source_changed','candidate':latest.name,'previous_contract':last_input.get('source_contract')})
                    rows[clip['clip_id']]['next_action']=(
                        f"The source changed since this rejection. Use reuse_candidate:{latest.name!r} to review the retained image "
                        'against the adopted source before another input revision or image generation.')
            row=rows[clip['clip_id']]
            accepted_candidate=(clip.get('first_frame') or {}).get('candidate')
            previous_contract=last_input.get('source_contract')
            if (issue is not None and reviews and accepted_candidate==latest.name
                    and (latest/'opening.png').is_file() and previous_contract
                    and review.get('consistent') is True and not review_problems(review)
                    and not row.get('repair_grant')
                    and not matches_current_contract(ctx,clip,previous_contract)):
                row['repair_grant']=grant_dict('reuse_candidate',clip,
                    {'origin':'source_changed','candidate':latest.name,'previous_contract':previous_contract})
                row['next_action']=(f"The accepted source changed. Use reuse_candidate:{latest.name!r} "
                                    'to review the retained image against the current source before generating again.')
            if (issue is not None and saved_compiled is not None and (latest/'opening.png').is_file()
                    and current_review(latest,saved_compiled) is None):
                row=rows[clip['clip_id']]
                row.update(stage='review_incomplete',review_incomplete=incomplete_review(ctx,latest,saved_compiled),
                           issue=f"{clip['clip_id']}: first-frame review incomplete; reuse {latest.name} without a new image")
                if (row.get('repair_grant') or {}).get('operation')!='reuse_candidate':
                    row.pop('repair_grant',None)
                    row['next_action']=f"Use reuse_candidate:{latest.name!r} to complete review of the retained image. " \
                                       'An incomplete review grants no source repair or image retake.'
                row.pop('repair_origin',None)
        failure=reference_failure(ctx,clip)
        if failure:
            row=rows[clip['clip_id']]
            row.update(invalid_fields=[failure['field']],argument_errors=[failure['error']],
                       repair_origin=failure['origin'])
            row['last_rejection']={'candidate':'reference-preflight','error':failure['error'],
                'problems':[failure['error']],
                'evidence':'input/chapter_script.json' if failure['origin']=='source' else 'input/catalog.json'}
            if failure.get('grant'):row['repair_grant']=failure['grant']
            else:row.pop('repair_grant',None)
            row['next_action']=(
                'Source field opening_frame.reference_subjects is invalid. Use the granted complete opening_frame '
                'input decision; call check then apply before first_frames. Do not change other story fields.'
                if failure['origin']=='source' else
                'Restore the registered clip/reference binding, file and frozen hash before first_frames; '
                'this registration failure does not authorize story edits or an input grant.')
        opening=current_contract(ctx,clip,[])['opening']
        preflight=current_opening_review(ctx,clip['clip_id'],opening)
        if (conflicts:=source_conflicts(preflight or {})) and not (failure and failure['origin']=='registration'):
            rows[clip['clip_id']]['repair_origin']='source'
            rows[clip['clip_id']]['last_rejection']={
                'candidate':'source-preflight','error':_source_issue_message(conflicts),
                'problems':review_problems(preflight),'source_conflicts':conflicts,
                'evidence':f"input/first_frames/{clip['clip_id']}/source-preflight.json"}
            _set_source_grant(rows[clip['clip_id']],clip,conflicts,
                {'opening':opening,'candidate':'source-preflight'})
            rows[clip['clip_id']]['next_action']=(
                '需要作者澄清当前开场状态。使用正常 input repair（check 后 apply），然后重新调用 first_frames；无需预设前镜状态延续。'
                if _source_issue_message(conflicts)!='opening source fields conflict' else
                'The opening source fields conflict before image generation. Use normal input repair (check then apply), not retake. '
                'Correct every cited source field through the production Agent, then call first_frames again. '
                'No new image can resolve contradictory source requirements.')
    return {'mode':'first-frame','extra_reference_policy':'needed','clips':rows,
            'command':'python3 /work/tools/episode.py first_frames output/first-frames.json',
            'schema':{'render_ready':False,'frames':[{'clip_id':'current clip id',
                'extra_references':['optional original clip image paths only for uncovered new content'],
                'reuse_candidate':'optional existing candidate_NN in this clip; reuse its saved image with the complete current review, no new image',
                'retake_candidate':{'candidate':'candidate_NN rejected candidate with saved opening.png','reason':'specific visible pixel defect; consumes one remaining image candidate'}}]}}


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
        def argument_failure(message,field='frames'):
            self.partial_outcome={'argument_errors':{'request':[message]},'invalid_fields':{'request':[field]},
                'image_candidates_allocated':0,'validated_clips':[],
                'validation_scope':'request_arguments_only; no images generated or accepted'}
            raise ValueError(message)
        try:draft=input_file(ctx,payload,'output/first-frames.json')
        except json.JSONDecodeError as error:
            argument_failure(f'Invalid first_frames JSON at line {error.lineno}, column {error.colno}: {error.msg}','file')
        except ValueError as error:argument_failure(str(error),'file')
        if isinstance(draft,dict) and draft.get('render_ready') is not None and type(draft['render_ready']) is not bool:
            argument_failure('render_ready must be a boolean','render_ready')
        rows=draft.get('frames') if isinstance(draft,dict) else None
        if not isinstance(rows,list) or not rows:argument_failure('frames requires a nonempty array')
        plan_path=ctx.episode_dir/'clip_plan.json';plan=read_json(plan_path)
        clips={c['clip_id']:c for c in plan['clips']}
        selected=set()
        for index,row in enumerate(rows):
            field=f'frames[{index}].clip_id'
            if not isinstance(row,dict) or not isinstance(row.get('clip_id'),str):
                argument_failure(f'{field} requires a current clip ID',field)
            cid=row['clip_id']
            if cid not in clips:argument_failure(f'{field}={cid!r} is unknown; current clips: {sorted(clips)}',field)
            if cid in selected:argument_failure(f'{field}={cid!r} is repeated; select each clip once',field)
            selected.add(cid)
        catalog=read_json(ctx.run_dir/'catalog.json',[])
        if isinstance(catalog,dict):catalog=catalog.get('assets',[])
        registered={r['path'] for r in catalog if r.get('role') in {'character','location','prop'} and r.get('path')}
        prepared=[];seen=set();argument_errors={};invalid_fields={};grants={};failures={};source_repairs=False
        for row in rows:
            if not isinstance(row,dict) or row.get('clip_id') not in clips or row['clip_id'] in seen:
                raise ValueError('frames must select distinct current clips')
            cid=row['clip_id'];seen.add(cid);clip=clips[cid]
            grants[cid]=current_frame_grant(ctx,clip)
            candidate_root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid
            selector_errors,choices=frame_selector_errors(row,sorted(candidate_root.glob('candidate_*')))
            try:
                validate_frame_operation(row,grants[cid])
            except ValueError as error:
                field=('retake_candidate' if 'retake_candidate' in row else
                       'reuse_candidate' if 'reuse_candidate' in row else 'source_patch')
                selector_errors.setdefault(field,str(error))
            if selector_errors:
                limits=(status(ctx,{'clips':[clip]}) or {}).get('clips',{}).get(cid,{})
                if 'retake_candidate' in row and limits.get('remaining_image_candidates')==0:
                    parent=((grants[cid] or {}).get('evidence') or {}).get('candidate','')
                    decision=read_json(candidate_root/parent/'retake-decision.json',{})
                    replacement=decision.get('replacement_candidate')
                    if not replacement or not (candidate_root/replacement/'input.json').is_file():
                        self.partial_outcome={'clips':{cid:{'ok':False,'error':'first-frame image candidate budget used',
                            'retryable':False,**{key:limits[key] for key in ('image_candidate_limit','remaining_image_candidates') if key in limits}}},
                            'image_candidates_allocated':0}
                        raise ValueError(f'{cid}: first-frame image candidate budget used')
                argument_errors[cid]=list(selector_errors.values());invalid_fields[cid]=sorted(selector_errors)
                failures[cid]={'ok':False,'error':'; '.join(argument_errors[cid]),'invalid_fields':invalid_fields[cid],
                    'selector_candidates':choices,
                    **{key:limits[key] for key in ('image_candidates','image_candidate_limit',
                                                  'remaining_image_candidates') if key in limits}}
                if grants[cid]:failures[cid]['repair_grant']=copy.deepcopy(grants[cid])
                continue
            errors={};invalid_types=set();supplied_paths=row.get('image_references') or []
            compiled=compile_opening(ctx,clip,row,field_errors=errors,require_assets=False)
            row=compiled.row
            failure=reference_failure(ctx,clip,compiled,errors,include_missing=False,paths=supplied_paths)
            if failure:errors.setdefault(failure['field'],failure['error'])
            registered.update(r.path for r in compiled.references)
            if not isinstance(row.get('image_prompt'),str) or not row['image_prompt'].strip():
                errors.setdefault('image_prompt','image_prompt requires text');invalid_types.add('image_prompt')
            for field in ('image_references','extra_references','visible_characters'):
                if not isinstance(row.get(field),list) or any(not isinstance(p,str) for p in row[field]) or len(set(row[field]))!=len(row[field]):
                    errors.setdefault(field,f'{field} requires distinct strings');invalid_types.add(field)
            original={r['path'] for r in clip.get('references',[]) if r.get('role') in {'character','location','prop'}}
            if ('image_references' not in invalid_types and 'opening_frame.reference_subjects' not in errors
                    and (not row['image_references'] or not set(row['image_references'])<=registered)):
                errors['image_references']=f'image_references must use registered source cards; current clip paths: {sorted(original & registered)}'
            if 'extra_references' not in invalid_types and not set(row['extra_references'])<=original:
                errors['extra_references']=f'extra_references must select current source image paths: {sorted(original)}'
            if errors:
                argument_errors[cid]=list(errors.values());invalid_fields[cid]=sorted(errors)
                failed={'ok':False,'error':'; '.join(argument_errors[cid]),'invalid_fields':invalid_fields[cid]}
                if failure:
                    failed['repair_origin']=failure['origin']
                    if failure.get('grant'):
                        failed['repair_grant']=failure['grant'];source_repairs=True
                failures[cid]=failed
                continue
            prepared.append(compiled)
        if argument_errors:
            self.partial_outcome={'argument_errors':argument_errors,'validation_targets':sorted(seen),
                                  'invalid_fields':invalid_fields,'clips':failures,'image_candidates_allocated':0,
                                  'validated_clips':[row['clip_id'] for row,_,_ in prepared],
                                  'validation_scope':'request_arguments_only; no images generated or accepted'}
            if source_repairs:self.partial_outcome['required_action']='apply'
            raise ValueError('Correct these first_frames fields before image generation: '+json.dumps(argument_errors,ensure_ascii=False))
        source_reviews=review_opening_sources(ctx,prepared)
        conflicts={cid:source_conflicts(review) for cid,review in source_reviews.items() if source_conflicts(review)}
        if conflicts:
            rejected={}
            for cid,findings in conflicts.items():
                evidence=ctx.work_dir/'input/first_frames'/cid/'source-preflight.json'
                atomic_write_json(evidence,source_reviews[cid])
                rejected[cid]={'ok':False,'error':_source_issue_message(findings),
                    'repair_origin':'source','problems':review_problems(source_reviews[cid]),
                    'source_conflicts':findings,'evidence':str(evidence.relative_to(ctx.work_dir))}
            outcome={'ok':False,'error':'opening_source_conflict','source_input_conflicts':conflicts,'clips':rejected}
            self.partial_outcome=outcome
            outcome['first_frames']=status(ctx,plan)
            atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
            return outcome
        compiled_with_assets=[];registration_errors={};registration_fields={};registration_rows={}
        for compiled in prepared:
            cid=compiled.row['clip_id'];clip=clips[cid];errors={}
            current=compile_opening(ctx,clip,compiled.row,field_errors=errors,require_assets=True)
            failure=reference_failure(ctx,clip,current,errors)
            if failure:errors.setdefault(failure['field'],failure['error'])
            if errors:
                registration_errors[cid]=list(errors.values());registration_fields[cid]=sorted(errors)
                registration_rows[cid]={'ok':False,'error':'; '.join(errors.values()),
                    'invalid_fields':registration_fields[cid],'repair_origin':'registration'}
            else:compiled_with_assets.append(current)
        if registration_errors:
            self.partial_outcome={'argument_errors':registration_errors,'invalid_fields':registration_fields,
                'clips':registration_rows,'image_candidates_allocated':0,'validation_targets':sorted(seen),
                'validated_clips':[row['clip_id'] for row,_,_ in compiled_with_assets],
                'validation_scope':'request_arguments_only; no images generated or accepted'}
            raise ValueError('Correct registered reference assets before image generation: '
                +json.dumps(registration_errors,ensure_ascii=False))
        prepared=compiled_with_assets
        results={};changed=[];reviewed_candidates=[]
        self.partial_outcome={'changed':changed,'reviewed_candidates':reviewed_candidates,'clips':results}
        def reserve(compiled):
            row,contract,assets=compiled
            cid=row['clip_id'];clip=clips[cid];grant=grants[cid]
            root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid;root.mkdir(parents=True,exist_ok=True)
            # A video-prompt edit does not redraw the same opening image.
            identity={k:row[k] for k in ('image_prompt','request_prompt','image_references','visible_characters')}
            identity.update(source_contract=contract,source_assets=assets,
                            references=[r.as_dict() for r in compiled.references])
            def generation_input(value):
                value=copy.deepcopy(value)
                value.get('source_contract',{}).pop('character_facts',None)
                value.get('source_contract',{}).get('opening',{}).pop('storyboard',None)
                if value.get('request_prompt'):
                    value['request_prompt']=value['request_prompt'].split('\n\nConfirmed defects to correct in this retake:',1)[0]
                return value
            def same_image_inputs(left,right):
                left=copy.deepcopy(left);right=copy.deepcopy(right)
                left_assets=left.pop('source_assets',[]);right_assets=right.pop('source_assets',[])
                left_contract=left.pop('source_contract',None);right_contract=right.pop('source_contract',None)
                return (left==right and source_contracts_match(left_contract,right_contract)
                        and source_assets_match(left_assets,right_assets))
            dirs=sorted(root.glob('candidate_*'))
            selector_errors,_=frame_selector_errors(row,dirs)
            if selector_errors:raise ValueError(f'{cid}: '+ '; '.join(selector_errors.values()))
            reuse=row.get('reuse_candidate')
            retake=row.get('retake_candidate')
            if retake is not None:
                parent=next((d for d in dirs if d.name==retake['candidate']),None)
                reviewed=current_review(parent,compiled)
                if reviewed is None:
                    raise ValueError(f'{cid}: current source/reference evidence differs from the saved review; '
                        f'use reuse_candidate:{parent.name!r} without retake_candidate to review the existing image first, with no new image generation')
                if reviewed.get('consistent') is True and not review_problems(reviewed):
                    raise ValueError(f'{cid}: retake_candidate must name a candidate rejected by image review')
                if source_conflicts(reviewed):
                    raise ValueError(f'{cid}: source input fields conflict; use normal input repair (check then apply), '
                        'then reuse_candidate to review the retained image. A retake cannot repair the source input.')
                guidance='\n\nConfirmed defects to correct in this retake: '+retake['reason'].strip()
                if guidance not in row['request_prompt']:
                    row['request_prompt']+=guidance
                    identity['request_prompt']=row['request_prompt']
                decision_path=parent/'retake-decision.json'
                decision=read_json(decision_path,{})
                if decision:
                    candidate=next((d for d in dirs if d.name==decision.get('replacement_candidate')),None)
                    if candidate is None:
                        raise ValueError(f'{cid}: saved retake replacement candidate is missing')
                    if not same_image_inputs(read_json(candidate/'input.json',{}),identity):
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
                saved=read_json(candidate/'input.json')
                row['request_prompt']=saved.get('request_prompt') or saved.get('image_prompt') or row['request_prompt']
            else:
                # Resume the newest matching retake chain before considering its rejected parent.
                candidate=None
                for parent in reversed(dirs):
                    decision=read_json(parent/'retake-decision.json',{})
                    replacement=next((d for d in dirs if d.name==decision.get('replacement_candidate')),None)
                    saved=read_json(replacement/'input.json',{}) if replacement else {}
                    if replacement and same_image_inputs(generation_input(saved),generation_input(identity)):
                        candidate=replacement
                        row['request_prompt']=saved.get('request_prompt') or row['request_prompt']
                        break
            if candidate is None:
                # Stable facts are review evidence, not a new paid image request.
                # Match the immutable generation input; keep its original style/prompt.
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
                    if 'references' not in saved:expected.pop('references',None)
                    if not any('hash' in asset for asset in saved.get('source_assets',[])):
                        for asset in expected['source_assets']:asset.pop('hash',None)
                    left=generation_input(saved);right=generation_input(expected)
                    if not same_image_inputs(left,right):continue
                    candidate=directory
                    if legacy:
                        contract={k:v for k,v in contract.items() if k!='style'}
                        row['request_prompt']=row['image_prompt']
                    break
            if candidate is None:
                source_review = (grant and grant['operation']=='reuse_candidate'
                                 and (grant.get('evidence') or {}).get('origin')=='source_changed')
                if grant and grant['operation'] in {'retake_candidate','reuse_candidate','resume'} and not source_review:
                    raise ValueError(f"{cid}: {grant['operation']} requires the original candidate; changed inputs cannot allocate a new candidate")
                if len(dirs)>=image_candidate_limit(ctx,cid):
                    results[cid]={'ok':False,'error':'first-frame image candidate budget used'};return None
                candidate=root/f'candidate_{len(dirs)+1:02d}';candidate.mkdir()
                atomic_write_json(candidate/'input.json',identity)
            return CompiledOpening(row,contract,assets,compiled.draft_frame,compiled.references),candidate

        def produce(compiled,candidate):
            row,contract,assets=compiled
            path=candidate/'opening.png'
            if not (candidate/'image-request.json').is_file():
                from novel_manga.application.profiles import frame_spec, load_profile
                atomic_write_json(candidate/'image-request.json',{
                    'authored_prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
                    'image_references':list(row['image_references']),
                    'extra_references':list(row['extra_references']),
                    'visible_characters':list(row['visible_characters']),
                    'references':[r.as_dict() for r in compiled.references],
                    'ratio':frame_spec(load_profile(ctx.novel_dir))['video_ratio'],
                    'opening_state':opening_state(compiled)})
            if not path.is_file():self.builder(ctx,compiled if self.builder is build else row,path) # existing supplier receipt resumes this exact immutable output
            with Image.open(path) as image:image.load()
            return review_candidate(ctx,path,compiled,self.checker)

        def adopt(task,review,reviewed,record):
            compiled,candidate=task
            row,contract,assets=compiled
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
                    _set_source_grant(rejected,clip,source_conflicts(review),
                        {'opening':contract.get('opening'),'candidate':candidate.name,
                         'review_record':str(record.relative_to(ctx.novel_dir))})
                    rejected['next_action']='Use normal input repair (check then apply), not retake; then reuse_candidate to review the retained image.'
                elif any(f.get('origin')=='image' and f.get('relation')=='contradiction'
                         for f in review.get('findings',[])):
                    rejected['repair_grant']=grant_dict('retake_candidate',clip,
                        {'origin':'image','candidate':candidate.name,'review':review,
                         'review_record':str(record.relative_to(ctx.novel_dir))})
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
                'references':[r.as_dict() for r in compiled.references],
                'prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
                'image_sha256':read_json(record,{}).get('input',{}).get('candidate_sha256'),
                'review':review,'review_record':str(record.relative_to(ctx.novel_dir)),
                'evidence':str(export.relative_to(ctx.work_dir)),
                'candidate':candidate.name,'supplier_receipt':str(path.with_suffix(path.suffix+'.task.json').relative_to(ctx.novel_dir))}
            newclip=copy.deepcopy(clip);newclip['first_frame']=frame
            try:
                compilation_review=ensure_current_h3(ctx,newclip)
            except Exception as error:
                error.first_frame_h3={'candidate':candidate.name,'image':frame['path'],
                    'review_record':frame['review_record'],'image_review_passed':True}
                raise
            check_redundant_h3(row,newclip)
            newclip.pop('prompt_h3_skip',None)
            if newclip!=clip:
                clip.clear();clip.update(newclip);changed.append(cid);atomic_write_json(plan_path,plan)
            atomic_write_json(candidate/'accepted.json',frame)
            results[cid]={'ok':True,'first_frame':frame}
            if compilation_review is not None:results[cid]['h3_source_review']=compilation_review
            if self.on_accepted:self.on_accepted(cid)

        # Only this caller allocates candidates, adopts plans and invokes callbacks.
        # Keep a rolling window so a failed service does not allocate or start the
        # rest of the batch. Already started work still settles and is adopted.
        from .runtime import paused
        pending={};next_row=0;first_error=None;dispatch_paused=False
        with ThreadPoolExecutor(max_workers=min(workers,len(prepared))) as pool:
            while True:
                while (first_error is None and not dispatch_paused and next_row<len(prepared) and len(pending)<workers
                       and not any(future.done() for future in pending)):
                    if paused():
                        dispatch_paused=True
                        break
                    compiled=prepared[next_row];next_row+=1
                    try:
                        task=reserve(compiled)
                        if task is not None:pending[pool.submit(produce,*task)]=task
                    except Exception as error:
                        results[compiled.row['clip_id']]=failure_result(compiled,error,
                            shot_indexes=clips[compiled.row['clip_id']].get('shot_indexes',[]))
                        first_error=error
                if not pending:break
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    task=pending.pop(future)
                    try:adopt(task,*future.result())
                    except Exception as error:
                        results[task[0].row['clip_id']]=failure_result(task[0],error,candidate=task[1].name,
                            shot_indexes=clips[task[0].row['clip_id']].get('shot_indexes',[]))
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
