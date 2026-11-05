"""Optional first-frame input: preserve source identities, expose actual video pictures."""
from __future__ import annotations

import copy
from pathlib import Path
from novel_manga.util import read_json

OPENING_FIELDS=('start_state','camera','light','in_frame','props','wears','location','scene_objects','opening_frame')


def enabled(ctx):
    profile=read_json(ctx.novel_dir/'profile.json',{})
    return profile.get('h3_input_mode')=='first-frame' or profile.get('sd_input_mode')=='first-frame'


def opening_contract(clip, image_references, *, shots=None):
    rows=shots if shots is not None else clip.get('shots',clip.get('first_frame_opening',[]))
    opening=rows[0] if rows else {}
    return {'opening':{k:copy.deepcopy(opening.get(k)) for k in OPENING_FIELDS if k!='opening_frame'or k in opening},
            'image_references':list(image_references),
            'source_references':[{k:r.get(k) for k in ('role','name','path','wearers')} for r in clip.get('references',[]) if r.get('role')!='voice']}


def current_contract(ctx, clip, image_references, *, include_style=False):
    directory=getattr(ctx,'episode_dir',None)
    script=read_json(Path(directory)/'chapter_script.json',{}) if directory else {}
    indexes=clip.get('shot_indexes',[])
    shots=[s for s in script.get('shots',[]) if s.get('index') in indexes]
    contract=opening_contract(clip,image_references,shots=shots or None)
    if include_style:
        from novel_manga.style_packs import frame_style
        contract['style']=frame_style(ctx.novel_dir)
    return contract


def source_asset_state(ctx, paths):
    return [{'path':p,'bytes':(ctx.novel_dir/p).stat().st_size,
             'modified_ns':(ctx.novel_dir/p).stat().st_mtime_ns} for p in paths]


def require_current(ctx, clip):
    if not enabled(ctx):return
    frame=clip.get('first_frame') or {}
    if not frame.get('path'):raise ValueError(f"{clip['clip_id']}: first frame missing; use first_frames")
    review=frame.get('review') or {}
    if review.get('consistent') is not True or review.get('problems'):
        raise ValueError(f"{clip['clip_id']}: first frame is not approved")
    contract=frame.get('source_contract',{})
    paths=contract.get('image_references',[])
    if current_contract(ctx,clip,paths,include_style='style' in contract)!=contract:
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
