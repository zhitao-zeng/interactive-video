"""Optional first-frame input: preserve source identities, expose actual video pictures."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from novel_manga.util import read_json

OPENING_FIELDS=('start_state','camera','light','in_frame','props','wears','location','scene_objects','opening_frame','device_state','shot_scale')
REVIEW_POLICY='first-frame-explicit-relations-v11-grounded-evidence'


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
        elif role=='style-reference':
            instruction=('style_only_not_character_identity: color, light, materials and rendering only; '
                         'do not copy character identity, wardrobe, story, text, layout or POV')
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


def opening_shot(clip, *, shots=None):
    rows=shots or clip.get('first_frame_opening') or clip.get('shots') or []
    return rows[0] if rows else {}


def opening_contract(clip, image_references, *, shots=None):
    opening=opening_shot(clip,shots=shots)
    compiled_opening={k:copy.deepcopy(opening.get(k)) for k in OPENING_FIELDS
                      if k not in {'opening_frame','device_state','shot_scale'}or k in opening}
    board=opening.get('storyboard') or {}
    if 'frame' in board:
        compiled_opening['storyboard']={'frame':copy.deepcopy(board['frame'])}
    return {'opening':compiled_opening,
            'image_references':list(image_references),
            'source_references':[{k:r.get(k) for k in ('role','name','path','wearers')} for r in clip.get('references',[]) if r.get('role')!='voice']}


@dataclass(frozen=True)
class CompiledOpening:
    row: dict
    contract: dict
    assets: list
    draft_frame: dict
    references: tuple=()

    def __iter__(self):
        yield self.row
        yield self.contract
        yield self.assets


def source_opening(ctx,clip):
    directory=getattr(ctx,'episode_dir',None)
    script=read_json(Path(directory)/'chapter_script.json',{}) if directory else {}
    index=(clip.get('shot_indexes')or[None])[0]
    shot=next((s for s in script.get('shots',[]) if s.get('index')==index),None)
    if shot is None:shot=opening_shot(clip)
    return copy.deepcopy(shot.get('opening_frame'))


def opening_state(row,contract):
    opening=contract.get('opening') or {}
    facts={}
    for key in ('start_state','camera','light','in_frame','props','wears',
                'location','scene_objects','device_state','shot_scale'):
        value=opening.get(key)
        if value not in (None,'',[],{}):facts[key]=copy.deepcopy(value)
    if opening.get('opening_frame'):
        frame=copy.deepcopy(opening['opening_frame'])
        frame.pop('image_prompt',None);frame.pop('visible_characters',None)
        if frame:facts['opening_frame']=frame
    if contract.get('character_facts'):facts['character_facts']=copy.deepcopy(contract['character_facts'])
    if contract.get('prop_facts'):facts['prop_facts']=copy.deepcopy(contract['prop_facts'])
    return facts


def request_prompt(ctx,row,contract,*,references=()):
    import json
    prompt=compose_opening_image_prompt(row['image_prompt'],contract.get('style') or {},
        row.get('image_references',contract.get('image_references',[])),references,
        opening=contract.get('opening'))
    facts=opening_state(row,contract)
    if not facts:return prompt
    if contract.get('prop_facts'):
        prompt+='\nRegistered prop facts identify objects and their design; ownership or inclusion on a reference card is not current wearing or permission to duplicate an object. Current opening state determines placement and possession; preserve explicitly authored multiple objects.'
    return (prompt+'\n\nApproved opening state (source contract, not new staging direction):\n'
            +json.dumps(facts,ensure_ascii=False,sort_keys=True))


def compile_opening(ctx,clip,row=None,*,lane='h3',field_errors=None,require_assets=True):
    from .reference_views import canonical_paths,compile_references,opening_references
    row=copy.deepcopy(row or {})
    row.pop('source_quote',None)
    row.setdefault('clip_id',clip['clip_id'])
    row.setdefault('extra_references',[])
    shot_index=(clip.get('shot_indexes') or [0])[0]
    def draft():
        return {'path':'pending.png','extra_references':row.get('extra_references',[]),
                'visible_characters':row.get('visible_characters',[])}
    def record(field,error):
        if field_errors is None:raise ValueError(f"{clip['clip_id']}: {error}")
        field_errors.setdefault(field,error)
    opening=source_opening(ctx,clip)
    if opening is not None:
        from novel_manga.story.fields import opening_frame_error
        if issue:=opening_frame_error(opening):
            if field_errors is None:raise ValueError(issue)
            field_errors['opening_frame']=issue
            return CompiledOpening(row,{},[],draft(),())
        for field in ('image_prompt','visible_characters'):
            if field in row and row[field]!=opening[field]:
                error=(
                    f'{field} differs from the approved source opening_frame. '
                    'Edit source opening_frame through input repair before changing its production prompt or visible people. '
                    f"For {clip['clip_id']}, write output/repair.json with decisions:[{{action:'input',clip_id:'{clip['clip_id']}',"
                    "reason:'specific observed defect',source_quote:'verbatim source for this clip',"
                    f"shots:[{{index:{shot_index},opening_frame:<complete corrected opening_frame object>}}]}}]. "
                    'Call check then apply. After the source repair is adopted, omit image_prompt and visible_characters '
                    'from the first_frames row to inherit that source. Reference selection follows '
                    'opening_frame.reference_subjects and host expansion; extra_references is the only supplement.')
                record(field,error)
            row[field]=copy.deepcopy(opening[field])
    invalid_references=any(field in row and (not isinstance(row[field],list)
        or any(not isinstance(path,str) for path in row[field])) for field in ('image_references','extra_references'))
    if invalid_references:return CompiledOpening(row,{},[],draft(),())
    from .reference_views import reference_selection
    originals=[r for r in clip.get('references',[]) if r.get('role') in {'character','location','prop'}]
    catalog=read_json(Path(ctx.run_dir)/'catalog.json',[]) if getattr(ctx,'run_dir',None) else []
    if isinstance(catalog,dict):catalog=catalog.get('assets',[])
    subjects=(opening or {}).get('reference_subjects')
    original_names={r.get('name') for r in originals}
    explicit='image_references' in row and not row.get('reuse_candidate')
    supplied=canonical_paths(ctx,row['image_references']) if explicit else []
    selection=reference_selection(ctx,clip,subjects=subjects or [],paths=supplied)
    unknown=selection['unbound_subjects']
    if unknown:
        if selection['origin']=='registration':
            error=('reference registration: resolve catalog-unbound subjects '
                   f'{selection["catalog_unbound_subjects"]} and bindings before author repair. '
                   f'Truly unknown names are deferred until binding is fixed: {selection["unknown_subjects"]}. '
                   'Do not issue an input grant or change the authored story for catalog registration.')
        else:
            error=(
                f'source field opening_frame.reference_subjects has unregistered subject names: {unknown}. '
                f'Use only exact clip-registered names {sorted(original_names)}; never infer identity from a basename or alias. '
                'Edit the complete source opening_frame through input repair before changing reference selection. '
                f"For {clip['clip_id']}, write output/repair.json with decisions:[{{action:'input',clip_id:'{clip['clip_id']}',"
                "reason:'specific observed defect',source_quote:'verbatim source for this clip',"
                f"shots:[{{index:{shot_index},opening_frame:<complete corrected opening_frame object>}}]}}]. "
                'Call check then apply, then omit image_references to inherit host expansion.')
        record('opening_frame.reference_subjects',error)
    paths=[r['path'] for r in originals if not subjects or r.get('name') in subjects]
    expanded=opening_references(ctx,clip,paths)
    compiled_paths=[r['path'] for r in expanded]
    known={r['path'] for r in [*originals,*catalog]
           if r.get('role') in {'character','location','prop'} and r.get('path')}
    known.update(r['path'] for r in expanded)
    if selection['catalog_unbound_paths']:
        error=('reference registration: image_references contains catalog-only paths not bound to this clip: '
               f'{selection["catalog_unbound_paths"]}. Restore clip binding; no author input grant is allowed.')
        record('image_references',error)
    elif set(supplied)-known:
        error=(f'image_references must use registered source cards; current clip paths: '
               f'{sorted({r["path"] for r in originals} & known)}')
        record('image_references',error)
    if explicit and supplied!=compiled_paths and not unknown:
        error=(
            'image_references differs from the host-compiled opening references. '
            'Edit source opening_frame.reference_subjects through input repair before changing reference selection. '
            f"For {clip['clip_id']}, write output/repair.json with decisions:[{{action:'input',clip_id:'{clip['clip_id']}',"
            "reason:'specific observed defect',source_quote:'verbatim source for this clip',"
            f"shots:[{{index:{shot_index},opening_frame:<complete corrected opening_frame object>}}]}}]. "
            'Call check then apply. After the source repair is adopted, omit image_references to inherit '
            'the host-compiled references; extra_references remains the only supplemental entry.')
        record('image_references',error)
    row['image_references']=compiled_paths
    try:
        references=compile_references(ctx.novel_dir,expanded,require_files=require_assets)
    except (OSError,ValueError) as error:
        if field_errors is None or not hasattr(error,'reference_path'):raise
        field='style.examples' if error.reference_role=='style-reference' else 'image_references'
        record(field,'reference registration: '+str(error)+
               '; restore the registered file/binding and frozen hash, not the authored story')
        return CompiledOpening(row,{},[],draft(),())
    row['image_references']=[r.path for r in references]
    if isinstance(row['extra_references'],list) and all(isinstance(p,str) for p in row['extra_references']):
        row['extra_references']=list(dict.fromkeys(canonical_paths(ctx,row['extra_references'])))
    contract=current_contract(ctx,clip,row['image_references'],include_style=True,references=expanded,
                              extra_references=row['extra_references'])
    if isinstance(row.get('image_prompt'),str) and row['image_prompt'].strip():
        row['request_prompt']=request_prompt(ctx,row,contract,references=references)
    assets=source_asset_state(ctx,row['image_references'],references=references,allow_missing=not require_assets)
    return CompiledOpening(row,contract,assets,draft(),references)


def unchanged_opening_scale(clip,contract):
    """Older reviews omitted scale; their saved source opening still records it."""
    opening=opening_shot(clip)
    return bool(opening) and opening.get('shot_scale')==contract.get('opening',{}).get('shot_scale')


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


def prop_facts(ctx,contract):
    """Project registered identities mentioned in the current opening, without placing them."""
    opening=contract['opening'];texts=[]
    def collect(value):
        if isinstance(value,str):texts.append(value)
        elif isinstance(value,dict):
            for item in value.values():collect(item)
        elif isinstance(value,(list,tuple)):
            for item in value:collect(item)
    for field in ('start_state','props','scene_objects','wears','device_state'):
        collect(opening.get(field))
    collect((opening.get('opening_frame') or {}).get('image_prompt'))
    collect((opening.get('storyboard') or {}).get('frame'))
    result=[]
    for index,prop in enumerate(read_json(ctx.novel_dir/'story_bible.json',{}).get('props',[])):
        forms=[prop.get('name'),*(prop.get('aliases') or [])]
        if not any(form and form in text for form in forms for text in texts):continue
        facts={field:{'value':copy.deepcopy(prop[field]),'source':f'story_bible.json:props[{index}].{field}'}
               for field in ('name','aliases','category','appearance','material','owner','quote') if prop.get(field)}
        if facts:result.append({'name':prop['name'],'facts':facts})
    return result


def current_contract(ctx, clip, image_references, *, include_style=False, shots=None, references=None,
                     extra_references=None):
    if shots is None:
        directory=getattr(ctx,'episode_dir',None)
        script=read_json(Path(directory)/'chapter_script.json',{}) if directory else {}
        indexes=clip.get('shot_indexes',[])
        shots=[s for s in script.get('shots',[]) if s.get('index') in indexes]or None
    if references is None:
        from .reference_views import opening_references
        references=opening_references(ctx,clip,image_references)
    refs=references
    image_references=[r['path'] for r in refs]
    contract=opening_contract(clip,image_references,shots=shots)
    if extra_references is None:
        extra_references=(clip.get('first_frame')or{}).get('extra_references',[])
    selected=set(image_references)|set(extra_references)
    contract['source_references']=[r for r in contract['source_references'] if r.get('path') in selected]
    scoped=[{k:r[k] for k in ('path','role','name','reference_scope') if k in r}
            for r in refs if r.get('reference_scope') in (
                'identity_portrait','wardrobe_design','style_only_not_character_identity')]
    if scoped:contract['reference_views']=scoped
    contract['character_facts']=character_facts(ctx,clip,contract)
    if props:=prop_facts(ctx,contract):contract['prop_facts']=props
    if include_style:
        from novel_manga.style_packs import frame_style
        contract['style']=frame_style(ctx.novel_dir)
    return contract


def source_contracts_match(saved,current):
    """Compare legacy unfiltered reference metadata only within the selected/extra scope."""
    if saved is None or current is None:return saved==current
    saved=copy.deepcopy(saved);current=copy.deepcopy(current)
    saved_references=saved.pop('source_references',None)
    current_references=current.pop('source_references',None)
    if saved!=current:return False
    if saved_references is None or current_references is None:
        return saved_references==current_references
    image_paths=set(current.get('image_references',[]))
    extras={r.get('path') for r in current_references if r.get('path') not in image_paths}
    def scoped(contract,references):
        selected=set(contract.get('image_references',[]))|extras
        return [r for r in references if r.get('path') in selected]
    return scoped(saved,saved_references)==scoped(current,current_references)


def matches_current_contract(ctx,clip,contract,*,shots=None):
    """Compare current source facts within the evidence scope this image recorded."""
    from novel_manga.style_packs import style_example_references
    def style_views(rows):
        return [{k:r.get(k) for k in ('path','role','name','reference_scope')}
                for r in rows if r.get('reference_scope')=='style_only_not_character_identity']
    if style_views(style_example_references(ctx.novel_dir))!=style_views(contract.get('reference_views') or []):
        return False
    paths=contract.get('image_references',[])
    opening=source_opening(ctx,clip) if shots is None else opening_shot(clip,shots=shots).get('opening_frame')
    if opening and 'reference_subjects' in opening:
        subjects=opening.get('reference_subjects') or []
        paths=[r['path'] for r in clip.get('references',[])
               if r.get('role') in {'character','location','prop'}
               and (not subjects or r.get('name') in subjects)]
    current=current_contract(ctx,clip,paths,include_style='style'in contract,shots=shots)
    if 'shot_scale'not in contract.get('opening',{}) and unchanged_opening_scale(clip,current):
        current['opening'].pop('shot_scale',None)
    if 'character_facts'not in contract:
        current.pop('character_facts',None)
        if 'device_state'not in contract.get('opening',{}):current['opening'].pop('device_state',None)
    return source_contracts_match(contract,current)


def _verified_continuity_clarification(finding):
    return (finding.get('origin') == 'source' and finding.get('relation') == 'underspecified'
            and finding.get('kind') == 'continuity_clarification'
            and finding.get('continuity_verified') is True)


def review_problems(review):
    """Structured relations are the decision; old unstructured reports keep their gate."""
    if 'findings'not in review:return list(review.get('problems')or [])
    problems=[]
    for finding in review.get('findings',[]):
        if finding.get('relation')=='contradiction' or _verified_continuity_clarification(finding):
            reason=finding.get('reason') or f"{finding.get('observed','')} / {finding.get('expected','')}"
            if reason not in problems:problems.append(reason)
    return problems


def source_conflicts(review):
    return [finding for finding in review.get('findings',[])
            if finding.get('origin')=='source' and (finding.get('relation')=='contradiction'
                or _verified_continuity_clarification(finding))]


def interpret_review(review):
    if 'findings'not in review:return copy.deepcopy(review)
    problems=review_problems(review)
    return {**copy.deepcopy(review),'consistent':not problems,'problems':problems}


def source_asset_state(ctx, paths, references=None, *, include_hash=False, allow_missing=False):
    import hashlib
    by_path={r.path:r for r in references} if references is not None else {}
    result=[]
    for path in paths:
        disk=ctx.novel_dir/path
        if allow_missing and not disk.is_file():
            result.append({'path':path,'missing':True,'hash':by_path[path].hash if path in by_path else ''})
            continue
        row={'path':path,'bytes':disk.stat().st_size,'modified_ns':disk.stat().st_mtime_ns}
        if include_hash or path in by_path:
            row['hash']=by_path[path].hash if path in by_path else hashlib.sha256(disk.read_bytes()).hexdigest()
        result.append(row)
    return result


def source_assets_match(saved,current):
    if saved is None or current is None or len(saved)!=len(current):return saved==current
    pairs=zip(saved,current)
    if all('hash' in before and 'hash' in after for before,after in pairs):
        return all(before.get('path')==after.get('path') and before['hash']==after['hash']
                   for before,after in zip(saved,current))
    return saved==current


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
    saved_assets=frame.get('source_assets')
    hashed=bool(saved_assets) and any('hash' in row for row in saved_assets)
    if saved_assets is not None and not source_assets_match(
            saved_assets,source_asset_state(ctx,paths,include_hash=hashed)):
        raise ValueError(f"{clip['clip_id']}: first frame stale after source asset changes; use first_frames")
    path=ctx.novel_dir/frame['path']
    if not path.is_file():raise ValueError(f"{clip['clip_id']}: first frame missing on disk")
    if frame.get('image_sha256'):
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frame['image_sha256']:
            raise ValueError(f"{clip['clip_id']}: first frame pixels are stale; review the replaced image")


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
