"""Optional first-frame input: preserve source identities, expose actual video pictures."""
from __future__ import annotations

import copy
from pathlib import Path
from novel_manga.util import read_json

OPENING_FIELDS=('start_state','camera','light','in_frame','props','wears','location','scene_objects','opening_frame','device_state','shot_scale')
REVIEW_POLICY='first-frame-explicit-relations-v3-appearance-scope'


def compose_opening_image_prompt(authored,style,image_references,references=(),*,opening=None):
    """Bind the actual uploaded images without summarizing the author's facts."""
    from novel_manga.style_packs import first_frame_prompt
    by_path={str(ref['path']):ref for ref in references if ref.get('path')}
    lines=[]
    for index,path in enumerate(image_references,1):
        ref=by_path.get(str(path),{});role=ref.get('role','reference')
        name=ref.get('name')or Path(path).stem
        if role=='character':
            scope=('face and hair only'if ref.get('reference_scope') in ('identity_portrait','identity-baseline')
                   else 'clothing and body proportions only; face and hair come from the identity portrait'
                   if ref.get('reference_scope') in ('wardrobe_design','wardrobe-visual-card') else 'character identity and design')
            instruction=f'{scope}; use the authored clothing and current equipment state, not the card pose or background'
        elif role=='location':
            instruction='space, architecture and materials; current time, camera and prop state follow the authored opening'
        elif role=='prop':
            instruction='object design; current owner, placement and state follow the authored opening'
        else:instruction='apply only the role described in the authored opening'
        lines.append(f'Image {index}: {role} {name} — {instruction}.')
    body=authored
    if (opening or{}).get('light'):
        body+='\n\nCurrent opening light (source):\n'+opening['light']
    body=first_frame_prompt(body,style)
    if not lines:return body
    return ('Input reference images (uploaded in exactly this order):\n'+'\n'.join(lines)
            +'\nReference cards do not add subjects or panels to the authored opening.'
            +'\n\nSingle static opening frame:\n'+body)


def enabled(ctx):
    novel_dir=getattr(ctx,'novel_dir',None)
    if novel_dir is None:
        return False
    profile=read_json(novel_dir/'profile.json',{})
    return profile.get('h3_input_mode')=='first-frame' or profile.get('sd_input_mode')=='first-frame'


def opening_contract(clip, image_references, *, shots=None):
    rows=shots if shots is not None else clip.get('shots',clip.get('first_frame_opening',[]))
    opening=rows[0] if rows else {}
    return {'opening':{k:copy.deepcopy(opening.get(k)) for k in OPENING_FIELDS if k not in {'opening_frame','device_state','shot_scale'}or k in opening},
            'image_references':list(image_references),
            'source_references':[{k:r.get(k) for k in ('role','name','path','wearers')} for r in clip.get('references',[]) if r.get('role')!='voice']}


def unchanged_opening_scale(clip,contract):
    """Older reviews omitted scale; their saved source opening still records it."""
    rows=clip.get('first_frame_opening')or clip.get('shots')or []
    return bool(rows) and rows[0].get('shot_scale')==contract.get('opening',{}).get('shot_scale')


def character_facts(ctx,clip,contract):
    """Selected identities, including a POV actor whose limbs may enter the frame."""
    from novel_manga.application.identity.phases import LOOK_FIELDS, chapter_of, load_phases, phase_for
    refs=list(clip.get('references',[]))
    run_dir=getattr(ctx,'run_dir',None)
    missing=set(contract['image_references'])-{r.get('path') for r in refs}
    catalog=read_json(Path(run_dir)/'catalog.json',[]) if run_dir and missing else []
    if isinstance(catalog,dict):catalog=catalog.get('assets',[])
    refs.extend(r for r in catalog if r.get('path') in contract['image_references'])
    names={r.get('name') for r in refs if r.get('role')=='character'}
    opening=contract['opening']
    names.update(opening.get('in_frame') or [])
    names.update((opening.get('opening_frame')or{}).get('reference_subjects',[]))
    aliases=read_json(ctx.novel_dir/'bible_aliases.json',{})
    names={aliases.get(name,name) for name in names}
    directory=getattr(ctx,'source_episode_dir',None) or getattr(ctx,'episode_dir',None)
    chapter=chapter_of(directory) if directory else None
    phases=load_phases(ctx.novel_dir)
    result=[]
    for i,character in enumerate(read_json(ctx.novel_dir/'story_bible.json',{}).get('characters',[])):
        name=character.get('name')
        if name not in names:continue
        phase=phase_for(phases,name,chapter)
        facts={}
        for field in (*LOOK_FIELDS,'face_anchors'):
            source=f'story_bible.json:characters[{i}].{field}'
            value=character.get(field)
            override='base_costume' if field=='wardrobe' and phase and 'base_costume'in phase and 'wardrobe'not in phase else field
            if phase and override in phase:
                value=phase[override]
                index=phases[name].index(phase)
                source=f'series_assets/phases.json:characters.{name}[{index}].{override}'
            if value:facts[field]={'value':copy.deepcopy(value),'source':source}
        if facts:result.append({'name':name,'facts':facts})
    return result


def current_contract(ctx, clip, image_references, *, include_style=False, shots=None):
    if shots is None:
        directory=getattr(ctx,'episode_dir',None)
        script=read_json(Path(directory)/'chapter_script.json',{}) if directory else {}
        indexes=clip.get('shot_indexes',[])
        shots=[s for s in script.get('shots',[]) if s.get('index') in indexes]or None
    from .reference_views import opening_references
    refs=opening_references(ctx,clip,image_references)
    image_references=[r['path'] for r in refs]
    contract=opening_contract(clip,image_references,shots=shots)
    scoped=[{k:r[k] for k in ('path','role','name','reference_scope') if k in r}
            for r in refs if r.get('reference_scope') in ('identity_portrait','wardrobe_design')]
    if scoped:contract['reference_views']=scoped
    contract['character_facts']=character_facts(ctx,clip,contract)
    if include_style:
        from novel_manga.style_packs import frame_style
        contract['style']=frame_style(ctx.novel_dir)
    return contract


def matches_current_contract(ctx,clip,contract,*,shots=None):
    """Compare current source facts within the evidence scope this image recorded."""
    current=current_contract(ctx,clip,contract.get('image_references',[]),
                             include_style='style'in contract,shots=shots)
    if 'shot_scale'not in contract.get('opening',{}) and unchanged_opening_scale(clip,current):
        current['opening'].pop('shot_scale',None)
    if 'character_facts'not in contract:
        current.pop('character_facts',None)
        if 'device_state'not in contract.get('opening',{}):current['opening'].pop('device_state',None)
    return current==contract


def review_problems(review):
    """Structured relations are the decision; old unstructured reports keep their gate."""
    if 'findings'not in review:return list(review.get('problems')or [])
    problems=[]
    for finding in review.get('findings',[]):
        if finding.get('relation')=='contradiction':
            reason=finding.get('reason') or f"{finding.get('observed','')} / {finding.get('expected','')}"
            if reason not in problems:problems.append(reason)
    return problems


def interpret_review(review):
    if 'findings'not in review:return copy.deepcopy(review)
    problems=review_problems(review)
    return {**copy.deepcopy(review),'consistent':not problems,'problems':problems}


def source_asset_state(ctx, paths):
    return [{'path':p,'bytes':(ctx.novel_dir/p).stat().st_size,
             'modified_ns':(ctx.novel_dir/p).stat().st_mtime_ns} for p in paths]


def require_current(ctx, clip):
    if not enabled(ctx):return
    frame=clip.get('first_frame') or {}
    if not frame.get('path'):raise ValueError(f"{clip['clip_id']}: first frame missing; use first_frames")
    review=interpret_review(frame.get('review') or {})
    if review.get('consistent') is not True or review_problems(review):
        raise ValueError(f"{clip['clip_id']}: first frame is not approved")
    contract=frame.get('source_contract',{})
    paths=contract.get('image_references',[])
    if not matches_current_contract(ctx,clip,contract):
        raise ValueError(f"{clip['clip_id']}: first frame stale after opening/source changes; use first_frames")
    if frame.get('source_assets') is not None and source_asset_state(ctx,paths)!=frame['source_assets']:
        raise ValueError(f"{clip['clip_id']}: first frame stale after source asset changes; use first_frames")
    if not (ctx.novel_dir/frame['path']).is_file():raise ValueError(f"{clip['clip_id']}: first frame missing on disk")


def video_metadata(clip):
    frame=clip.get('first_frame')
    if not frame:return [r for r in clip.get('references',[]) if r.get('role')!='voice']
    refs={r['path']:r for r in clip.get('references',[]) if r.get('role')!='voice'}
    return [{'path':frame['path'],'role':'first_frame','name':'Complete opening frame',
             'reference_scope':'exact_opening_frame','visible_characters':frame.get('visible_characters',[])},
            *(refs[path] for path in frame.get('extra_references',[]))]


def picture_map(clip):
    frame=clip.get('first_frame')
    originals=[r for r in clip.get('references',[]) if r.get('role')!='voice']
    if not frame:return {i:i for i in range(1,len(originals)+1)}
    extras={path:i for i,path in enumerate(frame.get('extra_references',[]),2)}
    return {i:extras.get(r['path'],1) for i,r in enumerate(originals,1)}


def retain_opening(before,after):
    # Carry the accepted image, never certify it for a changed opening. require_current
    # compares it against the newly published script before another video submission.
    for key in ('first_frame','first_frame_opening'):
        if key in before:after[key]=copy.deepcopy(before[key])
