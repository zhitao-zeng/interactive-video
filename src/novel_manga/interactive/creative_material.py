"""Bind an original author's screenplay/design to the existing material interface."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from pydantic import ValidationError
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.util import atomic_write_json, read_json


def source_document(episode, manifest):
    """Original authored facts, excluding self-citing source_quote and music metadata."""
    lines=[episode.get('title','原创短剧'),manifest['task']['brief'],episode.get('logline','')]
    for shot in (episode.get('chapter_script') or {}).get('shots',[]):
        lines.append(f"镜头{shot['index']}")
        for key in ('scene_id','location','purpose','start_state','event','end_state','camera','light','sfx'):
            value=shot.get(key)
            if value:lines.append(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
        for turn in shot.get('turns',[]):
            lines.extend([turn['speaker_name'],turn['text'],turn.get('emotion','')])
    lines.append(json.dumps(episode.get('spatial_plan',{}),ensure_ascii=False,indent=2))
    lines.append(json.dumps(episode.get('production_design',{}),ensure_ascii=False,indent=2))
    return '\n\n'.join(lines)+'\n'


def design_issues(episode, manifest):
    if manifest.get('mode')!='original':
        return ['Automatic creative production currently requires original mode; preserve adaptation source handoff']
    if manifest['task'].get('world_card_seconds'):
        return ['Automatic original handoff does not yet support world cards; use approved material intake']
    from novel_manga.story.voice_delivery import native_speech_selected
    if not native_speech_selected(manifest.get('profile',{})):
        return ['Original automatic handoff requires no_reference_performance_first; locked voices need supplied voice assets']
    design=episode.get('production_design')
    if not isinstance(design,dict):
        return ['production_design must provide characters, locations, props, palette and continuity_rules']
    issues=[];characters=[];props=[]
    for name,model,items in [('characters',Character,characters),('props',Prop,props)]:
        rows=design.get(name,[])
        if not isinstance(rows,list):
            issues.append(f'production_design.{name} must be a list');continue
        for row in rows:
            try:items.append(model.model_validate(row))
            except ValidationError as error:issues.append(f'production_design.{name}: {error}')
        names=[x.name for x in items]
        if len(set(names))!=len(names):issues.append(f'production_design.{name} names must be unique')
        for item in items:
            if not item.appearance.strip():issues.append(f'{item.name}: appearance needs a concrete physical design')
            if isinstance(item,Character) and not item.wardrobe.strip():
                issues.append(f'{item.name}: wardrobe needs a concrete design')
    locations=design.get('locations',[])
    if not isinstance(locations,list) or any(not isinstance(x,dict) or any(
            not isinstance(x.get(k),str) or not x[k].strip() for k in ('name','description')) for x in locations):
        issues.append('production_design.locations needs name and concrete description');locations=[]
    if len({x['name'] for x in locations})!=len(locations):issues.append('production location names must be unique')
    if not design.get('palette'):issues.append('production_design.palette needs the selected art direction')
    if not isinstance(design.get('continuity_rules'),list):issues.append('production_design.continuity_rules must be a list')
    actors={c.name for c in characters};places={x['name'] for x in locations};objects={p.name for p in props}
    shots=(episode.get('chapter_script') or {}).get('shots',[])
    for s in shots:
        if not s.get('shot_scale'):issues.append(f"shot {s['index']}: provide shot_scale matching the authored camera")
        needed=set(s.get('characters',[]))|set(s.get('in_frame',[]))|{t['speaker_name'] for t in s.get('turns',[])}
        if needed-actors:issues.append(f"shot {s['index']}: missing character design {sorted(needed-actors)}")
        if s.get('location')not in places:issues.append(f"shot {s['index']}: missing location design {s.get('location')}")
        if set(s.get('props',[]))-objects:issues.append(f"shot {s['index']}: missing prop design {sorted(set(s['props'])-objects)}")
    locations_by_shot={s['index']:s['location'] for s in shots}
    for group in episode.get('clips',[]):
        if len({locations_by_shot.get(i) for i in group['shot_indexes']})>1:
            issues.append('A generation group must stay in one location')
    source=source_document(episode,manifest)
    music=(episode.get('chapter_script') or {}).get('music_plan') or {}
    for row in [*music.get('cues',[]),*music.get('silences',[])]:
        if not row.get('source_quote') or row['source_quote']not in source:
            issues.append('music source_quote must quote an authored event, dialogue or sound verbatim')
    return issues


def material(episode, manifest, *, style=None):
    if issues:=design_issues(episode,manifest):raise ValueError('; '.join(issues))
    from novel_manga.application.profiles import load_style
    profile={'frame':'9:16','tier':'quality','genre':'generic','resolution':'480p',
        'expressions':False,'two_views':'off','episode_shape':'fixed','speech_gate':'enforce',
        'props':True,'camera_policy':'authored',**manifest['profile']}
    style=load_style(profile) if style is None else style
    design=episode['production_design'];title=episode.get('title','原创短剧')
    bible=StoryBible(novel_title=title,genre=manifest['project'],visual_style=style['visual_style'],
        palette=design['palette'],characters=design['characters'],props=design.get('props',[]),
        locations=[x['name']+'：'+x['description'] for x in design['locations']],
        continuity_rules=design['continuity_rules'],style_fingerprint='original-'+profile.get('style','3d'))
    script=copy.deepcopy(episode['chapter_script'])
    script.update(authored_family=manifest['project'],video_title=title,source_title=title,
                  episode_index=1,profile=profile)
    for shot in script['shots']:
        i=shot['index'];shot.update(segment_id=f'S{i:03d}',segment_ids=[f'S{i:03d}'],
            shot_id=f'S{i:03d}',origin_index=i,source_kind='original_agent',source_quote=shot['event'])
        shot.setdefault('visual_prompt',shot['start_state'])
        shot.setdefault('motion_prompt',shot['event'])
    interaction=copy.deepcopy(episode['interaction'])
    interaction.update(seconds=3,series_title=title)
    source=source_document(episode,manifest)
    return {'profile':profile,'bible':bible.model_dump(mode='json'),'script':script,'voice_specs':{},
        'groups':[{'source_segment':f'G{i:03d}','shot_indexes':g['shot_indexes']}
                  for i,g in enumerate(episode['clips'],1)],
        'interaction':interaction,'overlays':[],'source_seconds':sum(s['duration_seconds']for s in script['shots']),
        'delivery_policy':{'extra_visual_overlays':False}},source


def write_material(episode, manifest, destination, *, style_source=None):
    destination=Path(destination)
    value,source=material(episode,manifest,style=read_json(Path(style_source)) if style_source else None)
    target=destination/'material.json'
    if target.exists():
        if read_json(target)!=value or (destination/'source.md').read_text()!=source:
            raise ValueError('Keep existing material; a changed authored draft needs a new edition')
        return destination
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'owner').write_text('Original creative Agent handoff / deterministic binding, no host-authored story\n')
    atomic_write_json(target,value);(destination/'source.md').write_text(source)
    atomic_write_json(destination/'layout.json',episode['spatial_plan'])
    if style_source:
        import shutil
        shutil.copy2(style_source,destination/'style.json')
    return destination
