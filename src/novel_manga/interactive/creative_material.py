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
    if manifest.get('mode')=='adaptation':
        # Adaptations bind a supplied screenplay and take design facts from the
        # approved series material; only the reduced script contract applies here.
        return adaptation_issues(episode, manifest)
    if manifest.get('mode')!='original':
        return ['Automatic creative production currently requires original mode; use the adaptation handoff']
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


def _normalize_original_script(script):
    for shot in script['shots']:
        i=shot['index'];shot.update(segment_id=f'S{i:03d}',segment_ids=[f'S{i:03d}'],
            shot_id=f'S{i:03d}',origin_index=i,source_kind='original_agent',source_quote=shot['event'])
        shot['visual_prompt']=copy.deepcopy(shot['start_state'])
        shot['motion_prompt']=copy.deepcopy(shot['event'])
    return script


def normalized_episode(episode, manifest):
    normalized=copy.deepcopy(episode)
    script=normalized.get('chapter_script')
    if not isinstance(script,dict) or not isinstance(script.get('shots'),list):
        return normalized
    if manifest.get('mode')=='adaptation':
        scenes={s.get('scene_id'):s for s in (normalized.get('spatial_plan') or {}).get('scenes',[])}
        normalized['chapter_script']=_normalize_adaptation_script(script,scenes)
    else:
        normalized['chapter_script']=_normalize_original_script(script)
    return normalized


def material(episode, manifest, *, style=None):
    episode=normalized_episode(episode,manifest)
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
    interaction=copy.deepcopy(episode['interaction'])
    interaction.update(seconds=3,series_title=title)
    source=source_document(episode,manifest)
    return {'profile':profile,'bible':bible.model_dump(mode='json'),'script':script,'voice_specs':{},
        'groups':[{'source_segment':f'G{i:03d}','shot_indexes':g['shot_indexes']}
                  for i,g in enumerate(episode['clips'],1)],
        'interaction':interaction,'overlays':[],'source_seconds':sum(s['duration_seconds']for s in script['shots']),
        'delivery_policy':{'extra_visual_overlays':False}},source


def _style_snapshot_matches(style_source,destination):
    import hashlib
    from novel_manga.style_packs import normalized_style
    expected,attachments=normalized_style(style_source)
    if read_json(destination/'style.json',{})!=expected:return False
    root=destination/'style-examples'
    actual={path.relative_to(destination).as_posix():path
            for path in root.rglob('*') if path.is_file()}
    if set(actual)!=set(attachments):return False
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    return all(digest(actual[relative])==digest(source) for relative,source in attachments.items())


def write_material(episode, manifest, destination, *, style_source=None):
    destination=Path(destination)
    value,source=material(episode,manifest,style=read_json(Path(style_source)) if style_source else None)
    target=destination/'material.json'
    if target.exists():
        if read_json(target)!=value or (destination/'source.md').read_text()!=source:
            raise ValueError('Keep existing material; a changed authored draft needs a new edition')
        if style_source and not _style_snapshot_matches(style_source,destination):
            raise ValueError('Keep the existing frozen style snapshot; changed style or attachments require a new edition')
        return destination
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'owner').write_text('Original creative Agent handoff / deterministic binding, no host-authored story\n')
    atomic_write_json(target,value);(destination/'source.md').write_text(source)
    atomic_write_json(destination/'layout.json',episode['spatial_plan'])
    if style_source:
        from novel_manga.style_packs import export_style
        export_style(style_source,destination)
    return destination


def adaptation_designs(episode, bible):
    """Keep supplied series identities; bind the author's new episode designs."""
    additions={'characters':[],'props':[]};issues=[]
    design=episode.get('production_design') or {}
    known={row['name'] for row in bible.get('characters',[])}
    for kind,model in [('characters',Character),('props',Prop)]:
        existing={row['name'] for row in bible.get(kind,[])}
        for row in design.get(kind,[]):
            if row.get('name') in existing:continue
            try:item=model.model_validate(row)
            except (ValidationError,TypeError) as error:
                issues.append(f'production_design.{kind}: {error}');continue
            if not item.appearance.strip():
                issues.append(f'production_design.{kind}: {item.name} needs its authored appearance');continue
            additions[kind].append(item.model_dump(mode='json'));existing.add(item.name)
        if kind=='characters':known.update(existing)
    for shot in (episode.get('chapter_script') or {}).get('shots',[]):
        names=set()
        for key in ('characters','in_frame'):
            if isinstance(shot.get(key),list):names.update(shot[key])
        names.update(t.get('speaker_name') or t.get('speaker') for t in shot.get('turns',[]) if t.get('text'))
        unknown=names-known-{None,''}
        if unknown:issues.append(f'shot {shot["index"]}: actors {sorted(unknown)} have no supplied series identity; provide their source-supported production_design.characters designs before handoff')
    return additions,issues


def _adaptation_locations(episode, existing):
    """The exact location list materialized from supplied names and authored scenes."""
    locations=list(existing)
    known={loc.split('：',1)[0] for loc in locations}
    location_designs={row['name']:row.get('description','') for row in
        (episode.get('production_design') or {}).get('locations',[]) if isinstance(row,dict) and row.get('name')}
    shots_by_scene={}
    for shot in (episode.get('chapter_script') or {}).get('shots',[]):
        sid=shot.get('scene_id')
        if sid and shot.get('location'):shots_by_scene.setdefault(sid,shot['location'])
    for scene in (episode.get('spatial_plan') or {}).get('scenes',[]):
        name=scene.get('location') or shots_by_scene.get(scene.get('scene_id'),'')
        if name and name not in known:
            anchors=scene.get('anchors') or []
            desc='；'.join(x if isinstance(x,str) else json.dumps(x,ensure_ascii=False)
                          for x in (anchors if isinstance(anchors,list) else [anchors])) or name
            if authored:=location_designs.get(name):desc=authored+'；固定空间锚点：'+desc
            locations.append(f'{name}：{desc}')
            known.add(name)
    return locations


def adaptation_issues(episode, manifest, *, bible=None):
    """Adaptation drafts bind a supplied screenplay; design facts come from the
    approved series material, so only the script contract is checked here."""
    shots=(episode.get('chapter_script') or {}).get('shots',[])
    if not shots:
        return ['adaptation draft needs chapter_script.shots']
    scenes={s.get('scene_id'):s for s in (episode.get('spatial_plan') or {}).get('scenes',[])}
    issues=[]
    for s in shots:
        if not s.get('duration_seconds'):
            issues.append(f"shot {s.get('index')}: needs duration_seconds")
        if s.get('scene_id') and s['scene_id'] not in scenes:
            issues.append(f"shot {s['index']}: spatial_plan has no scene {s['scene_id']}")
    indexes=[s['index'] for s in shots]
    if indexes!=list(range(1,len(shots)+1)):
        issues.append('adaptation shots must be indexed 1..N in order')
    covered=[i for g in episode.get('clips',[]) for i in g['shot_indexes']]
    if sorted(covered)!=indexes:
        issues.append('adaptation clips must cover every shot exactly once')
    series=(manifest.get('task') or {}).get('series_material')
    if bible is None and series:
        from novel_manga.application.configuration import project_root
        path=Path(series);path=path if path.is_absolute() else project_root()/path
        bible=read_json(path/'material.json',{}).get('bible')
    if bible:issues.extend(adaptation_designs(episode,bible)[1])
    registered={loc.split('：',1)[0] for loc in _adaptation_locations(episode,(bible or {}).get('locations',[]))}
    for shot in shots:
        scene=scenes.get(shot.get('scene_id')) or {}
        location=scene.get('location') or shot.get('location')
        if location not in registered:
            issues.append(f"shot {shot['index']} (scene {shot.get('scene_id')!r}): location {location!r} is not registered for material handoff. "
                f"Registered location names: {sorted(registered)!r}. "
                'The author must declare the source-supported place in the matching spatial_plan.scenes.location '
                '(split/rebind the scene if its shots use different places), keep production_design.locations consistent, '
                'or correct the shot reference to an existing name only when the source supports it. Preserve other approved fields.')
    return issues


def _normalize_adaptation_script(script, scenes):
    for i,s in enumerate(script['shots'],1):
        sid=f'S{i:02d}'
        standard=scenes.get(s.get('scene_id'),{}).get('location')
        if standard and s.get('location')!=standard:
            s['location']=standard
        for key in ('sfx','purpose','audio_plan'):
            if isinstance(s.get(key),list):
                s[key]='；'.join(map(str,s[key])) or '无'
        s.update(segment_id=f'seg_{i}',segment_ids=[f'seg_{i}'],shot_id=sid,label=sid,
                 origin_index=i,source_kind='adapted-agent',authored_duration_seconds=s['duration_seconds'])
        s.setdefault('source_quote',s.get('event') or s.get('start_state') or '')
        s['visual_prompt']=copy.deepcopy(s.get('start_state') or '')
        s['motion_prompt']=copy.deepcopy(s.get('event') or '')
    return script


def write_adaptation_material(episode, manifest, destination, *, series_material,
                              package_script, style_source=None):
    """Merge a validated adaptation candidate into an approved series material.

    The series material supplies bible/voice/reference facts; the candidate
    supplies the bound script, groups, interaction and any new scenes."""
    episode=normalized_episode(episode,manifest)
    destination=Path(destination);series_material=Path(series_material)
    base=read_json(series_material/'material.json')
    if issues:=adaptation_issues(episode,manifest,bible=base['bible']):
        raise ValueError('; '.join(issues))
    bible=copy.deepcopy(base['bible'])
    additions,errors=adaptation_designs(episode,bible)
    if errors:raise ValueError('; '.join(errors))
    for kind,rows in additions.items():bible.setdefault(kind,[]).extend(rows)
    bible['locations']=_adaptation_locations(episode,bible['locations'])
    script=copy.deepcopy(episode['chapter_script'])
    shots={s['index']:s for s in script['shots']}
    groups=[]
    for number,clip in enumerate(episode.get('clips',[]),1):
        first=shots[clip['shot_indexes'][0]]
        speakers={t.get('speaker') or t.get('speaker_name') for s in
                  (shots[i] for i in clip['shot_indexes']) for t in s.get('turns',[])}
        groups.append({'source_segment':f'S{number:02d}','shot_indexes':clip['shot_indexes'],
            'request_seconds':clip.get('request_seconds') or sum(shots[i]['duration_seconds'] for i in clip['shot_indexes']),
            'location':first['location'],'speaker':sorted(speakers)[0] if speakers else first.get('characters',[''])[0]})
    profile=copy.deepcopy(base['profile'])
    profile.update(manifest.get('profile') or {})
    profile.setdefault('h3_speech_policy','no_reference_performance_first')
    # Lane mode (h3_input_mode) is chosen by the caller, not the material: baking
    # first-frame in here made SD editions refuse to start (2026-10-23).
    interaction=copy.deepcopy(episode['interaction']);interaction.setdefault('seconds',3)
    segments=[{'segment_id':f'seg_{i}','text':'\n'.join(filter(None,[s.get('start_state'),s.get('event'),
        *[f"{t.get('speaker') or t.get('speaker_name','')}：{t['text']}" for t in s.get('turns',[])],s.get('end_state')]))}
        for i,s in enumerate(script['shots'],1)]
    value={'profile':profile,'bible':bible,'groups':groups,'segments':segments,'overlays':[],
        'interaction':interaction,'voice_specs':copy.deepcopy(base.get('voice_specs') or {}),
        'script':script,'reference_images':copy.deepcopy(base.get('reference_images') or []),
        'source_archive':manifest.get('source_archive'),'source_seconds':round(sum(s['duration_seconds'] for s in script['shots']),2),
        'source_seconds_kind':'authored_budget','target_seconds':round(sum(s['duration_seconds'] for s in script['shots'])+interaction['seconds'],2),
        'style_snapshot':'style.json','asset_status':copy.deepcopy(base.get('asset_status') or {})}
    target=destination/'material.json'
    if target.exists():
        if read_json(target)!=value:
            raise ValueError('Keep existing material; a changed adaptation draft needs a new edition')
        if (destination/'source.md').read_bytes()!=Path(package_script).read_bytes():
            raise ValueError('Keep existing source; a changed package screenplay needs a new edition')
        if style_source and not _style_snapshot_matches(style_source,destination):
            raise ValueError('Keep the existing frozen style snapshot; changed style or attachments require a new edition')
        return destination
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'owner').write_text('Adaptation creative Agent handoff / series-material merge, deterministic binding\n')
    atomic_write_json(target,value)
    import shutil
    shutil.copy2(package_script,destination/'source.md')
    atomic_write_json(destination/'layout.json',episode.get('spatial_plan') or {})
    if style_source:
        from novel_manga.style_packs import export_style
        export_style(style_source,destination)
    return destination
