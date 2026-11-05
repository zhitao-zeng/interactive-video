"""Validate and format a creative candidate without admitting media production."""
from __future__ import annotations

import json
import copy
import math
from pathlib import Path
import re


def _supplied_prop_names(manifest):
    """Use supplied material's existing prop order and declared image destinations."""
    series=(manifest.get('task') or {}).get('series_material')
    if manifest.get('mode')!='adaptation' or not series:return set()
    from novel_manga.application.configuration import project_root
    from novel_manga.util import read_json
    directory=Path(series)
    if not directory.is_absolute():directory=project_root()/directory
    material=read_json(directory/'material.json',{})
    paths=set()
    for ref in material.get('reference_images',[]):
        source=Path(ref['source'])
        if not source.is_absolute():source=directory/source
        if source.is_file():paths.add(ref['path'])
    names=set()
    for index,prop in enumerate((material.get('bible') or {}).get('props',[]),1):
        folder=f'series_assets/props/prop_{index:03d}'
        image='detail.jpeg' if prop.get('closeup') and f'{folder}/detail.jpeg' in paths else 'turnaround.jpeg'
        if f'{folder}/{image}' in paths:names.add(prop['name'])
    return names


def validate(episode, manifest, work_dir):
    if not isinstance(episode, dict):
        return ['episode.json must be one object']
    task = manifest['task']
    from novel_manga.story.voice_delivery import native_speech_selected, performance_error
    profile=manifest.get('profile') or task.get('profile') or {}
    native=native_speech_selected(profile)
    script=episode.get('chapter_script')
    if not isinstance(script,dict):return ['chapter_script.shots must contain the complete episode']
    authored_policy=(script.get('profile') or {}).get('h3_speech_policy')
    shots = script.get('shots') or []
    issues = []
    own_style=work_dir/'output/style.json'
    if profile.get('sd_input_mode')=='first-frame' and own_style.is_file():
        try:style=json.loads(own_style.read_text())
        except ValueError:style=None
        if not isinstance(style,dict)or any(not isinstance(style.get(k),str)or not style[k].strip()
                                           for k in ('visual_style','render_family')):
            issues.append('output/style.json for Fast production requires nonempty visual_style and render_family strings; retain the authored art rules in the canonical style format')
    if authored_policy and profile.get('h3_speech_policy') and authored_policy!=profile['h3_speech_policy']:
        issues.append('chapter_script speech policy conflicts with the selected task policy')
    if not isinstance(shots,list) or not shots:
        return ['chapter_script.shots must contain the complete episode']
    malformed=[number for number,shot in enumerate(shots,1)if not isinstance(shot,dict)]
    if malformed:
        return [f'chapter_script.shots entries {malformed} must be shot objects; replace placeholders with the complete original episode']
    if source := manifest.get('dialogue_source'):
        from .source_dialogue import extract_markdown_dialogue, dialogue_issues
        issues.extend(dialogue_issues(shots, extract_markdown_dialogue((work_dir/source).read_text())))
    if structured := task.get('structured_script'):
        from .structured_dialogue import dialogue_issues
        try:
            issues.extend(dialogue_issues(shots, work_dir/structured, task['series_material'], task))
        except (OSError, ValueError) as error:
            issues.append(f'structured dialogue evidence cannot be validated: {error}')
    text = re.sub(r'\s+', '', '\n'.join((work_dir / p).read_text()
        for p in manifest['source_documents']))
    durations = {}
    from novel_manga.story.fields import opening_frame_error
    first_frame=any(task.get(key)=='first-frame'or task.get('profile',{}).get(key)=='first-frame'
                    for key in ('h3_input_mode','sd_input_mode'))
    supplied_props=_supplied_prop_names(manifest)
    scenes=(episode.get('spatial_plan') or {}).get('scenes') or []
    scene_locations={scene.get('scene_id'):scene.get('location') for scene in
                     (scenes if isinstance(scenes,list) else []) if isinstance(scene,dict)}
    for number, shot in enumerate(shots, 1):
        rosters={key:shot.get(key,[]) for key in ('characters','in_frame')}
        valid_rosters=True
        for key,names in rosters.items():
            if not isinstance(names,list) or any(not isinstance(name,str) or not name.strip() for name in names):
                issues.append(f'shot {number}: {key} must be a JSON list of actor names; put objects and scenery in scene_objects/props or the shot description, not actor rosters')
                valid_rosters=False
        if valid_rosters:
            declared_props={p.get('name') for p in (episode.get('production_design') or {}).get('props',[]) if isinstance(p,dict)}
            nonactors=(set(rosters['characters'])|set(rosters['in_frame'])) & declared_props
            if nonactors:
                issues.append(f'shot {number}: actor roster contains declared production_design.props {sorted(nonactors)}; put these in props/scene_objects and keep only actors in characters/in_frame')
            unknown=set(rosters['in_frame'])-set(rosters['characters'])
            if unknown:
                issues.append(f'shot {number}: in_frame entries {sorted(unknown)} are not declared actor names in characters={rosters["characters"]}; keep props/scenery/descriptive phrases out of the actor roster, or declare an actual source-supported actor in characters')
        if shot.get('index') != number:
            issues.append(f'shot {number}: consecutive index required; actual index={shot.get("index")!r}, expected {number}. After splitting, update clips and music anchors too')
        def type_issue(field, form, value):
            issues.append(f'shot {number} (index={shot.get("index")!r}): {field} must be {form}; '
                          f'got {type(value).__name__}. Preserve the authored facts when correcting this field shape.')
        for name in ('scene_id', 'location', 'purpose', 'camera', 'start_state', 'event', 'end_state'):
            value=shot.get(name)
            valid_text=isinstance(value,str) and bool(value.strip())
            purpose_lines=(name=='purpose' and isinstance(value,list) and bool(value)
                           and all(isinstance(line,str) and line.strip() for line in value))
            if not valid_text and not purpose_lines:
                type_issue(name,'a nonempty string'+(' or a nonempty list of strings' if name=='purpose' else ''),value)
        for name in ('light','scene_time','shot_scale'):
            if name not in shot:continue
            value=shot[name]
            if value is None and name in {'light','scene_time'}:continue
            if not isinstance(value,str):type_issue(name,'a string when supplied',value)
        for name in ('props','scene_objects'):
            value=shot.get(name)
            if value is not None and (not isinstance(value,list) or any(not isinstance(item,str) for item in value)):
                type_issue(name,'a JSON list of string names ([] or null clears it)',value)
        for name in ('sfx','extras'):
            value=shot.get(name)
            if value is not None and not isinstance(value,str) and not (
                    isinstance(value,list) and all(isinstance(item,str) for item in value)):
                type_issue(name,'a string or a JSON list of strings (or null)',value)
        audio_plan=shot.get('audio_plan')
        if audio_plan is not None and not isinstance(audio_plan,dict):
            type_issue('audio_plan','a JSON object (or null)',audio_plan)
        elif isinstance(audio_plan,dict):
            cue=audio_plan.get('music_cue')
            if cue is not None and not isinstance(cue,str):
                type_issue('audio_plan.music_cue','a string (or null)',cue)
        seconds = shot.get('duration_seconds')
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
            issues.append(f'shot {number}: positive duration_seconds required')
            seconds = 0
        durations[number] = seconds
        if first_frame or 'opening_frame'in shot:
            if problem:=opening_frame_error(shot.get('opening_frame')):issues.append(f'shot {number}: {problem}')
            elif valid_rosters:
                subjects=shot['opening_frame'].get('reference_subjects') or []
                location=(scene_locations.get(shot.get('scene_id')) if manifest.get('mode')=='adaptation'
                          and isinstance(shot.get('scene_id'),str) else None) or shot.get('location')
                legal=set(shot.get('characters') or [])
                if isinstance(location,str) and location:legal.add(location)
                if isinstance(shot.get('props'),list):
                    legal.update(name for name in shot['props'] if isinstance(name,str) and name in supplied_props)
                unknown=sorted(set(subjects)-legal)
                if unknown:
                    issues.append(f'shot {number}: opening_frame.reference_subjects has unregistered names {unknown}. '
                        f'Legal logical subject names for this shot: {sorted(legal)}. '
                        'Use exact names, not Image numbers, file paths or purpose labels. '
                        'The host adds each selected character\'s supplied portrait/wardrobe views and frozen style references; '
                        'do not list style as a subject. Preserve the shot facts and dialogue; omitted or empty reference_subjects is allowed.')
        for turn in shot.get('turns', []):
            if not all(turn.get(k) for k in ('speaker_name', 'text', 'delivery_mode', 'emotion')):
                issues.append(f'shot {number}: complete speaker/text/delivery/performance required')
            if turn.get('delivery_mode') not in {'visible_dialogue', 'offscreen_dialogue'}:
                issues.append(f'shot {number}: invalid delivery_mode')
            if native and (error:=performance_error(turn.get('emotion'))):
                issues.append(f'shot {number}: {error}')
        if manifest['mode'] == 'adaptation':
            quote = re.sub(r'\s+', '', str(shot.get('source_quote') or ''))
            if not quote or quote not in text:
                issues.append(f'shot {number}: source_quote must be exact original-package text')
    indexes = []
    if 'clips' not in episode and isinstance(script.get('clips'),list):
        issues.append('Found chapter_script.clips, but top-level clips is required. Move the existing array unchanged to episode.clips (a sibling of chapter_script), then remove chapter_script.clips; do not regroup or rewrite valid shots to repair this JSON path.')
    for number, clip in enumerate(episode.get('clips') or [], 1):
        ids = clip.get('shot_indexes') or []
        indexes.extend(ids)
        total = sum(durations.get(i, 0) for i in ids)
        seconds = clip.get('request_seconds', 0)
        silent_floor=(first_frame and len(ids)==1 and 0<total<4 and seconds==4
                      and not any(t.get('text') for s in shots if s.get('index')in ids for t in s.get('turns',[])))
        if (not ids or len(ids) > 6 or not isinstance(seconds, (int, float))
                or not 4 <= seconds <= 15 or (abs(total - seconds) > .01 and not silent_floor)):
            issues.append(f'clip {number}: group 1–6 shots, 4–15 seconds, matching shot total')
    if indexes != list(range(1, len(shots) + 1)) and not ('clips' not in episode and isinstance(script.get('clips'),list)):
        issues.append('clips must cover every shot exactly once in screen order')
    if task.get('world_card_seconds'):
        after = task.get('world_card_after_shot', 1)
        if not any(c.get('shot_indexes') and c['shot_indexes'][-1] == after
                   for c in episode.get('clips', [])):
            issues.append('world-card insertion must be at a generation-group boundary')
    total = sum(durations.values()) + task.get('world_card_seconds', 0) + task.get('end_card_seconds', 0)
    low, high = manifest['target_seconds']
    if not low <= total <= high:
        issues.append(f'total {total:g}s including cards must fit {low:g}–{high:g}s')
    interaction = episode.get('interaction') or {}
    options = interaction.get('options') or []
    if interaction.get('selected', 'missing') is not None:
        issues.append('interaction.selected must remain null')
    if not interaction.get('question') or len(options) < 2:
        issues.append('complete interaction question and at least two options required')
    if len({o.get('id') for o in options}) != len(options):
        issues.append('interaction option IDs must be unique')
    if any(not all(o.get(k) for k in ('id', 'label', 'reason')) for o in options):
        issues.append('each interaction option needs id, label and established reason')
    required = task.get('required_options') or []
    if required:
        actual = {o.get('id'): o for o in options}
        if set(actual) != {r['id'] for r in required}:
            issues.append('retain the required interactive destinations')
        for row in required:
            if row['label'] not in str(actual.get(row['id'], {}).get('label', '')):
                issues.append(f'interaction {row["id"]}: retain destination {row["label"]}')
    scenes = (episode.get('spatial_plan') or {}).get('scenes') or []
    if not isinstance(scenes, list):
        issues.append('spatial_plan.scenes must be a list of scene objects; keep each scene_id and its complete blockout')
        scenes = []
    if not scenes:
        issues.append('spatial_plan.scenes needs a source-backed blockout design')
    for number, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            issues.append(f'spatial scene {number}: must be a scene object, got {type(scene).__name__}')
            continue
        label = f'spatial scene {scene.get("scene_id", number)!r} (shots {scene.get("shot_indexes", [])})'
        if not scene.get('anchors'):
            issues.append(f'{label}: missing anchors')
        if 'assumptions' not in scene:
            issues.append(f'{label}: missing explicit assumptions')
        scene_shots = [shot for shot in shots if shot.get('index') in scene.get('shot_indexes', [])]
        visible = list(dict.fromkeys(name for shot in scene_shots
            for name in [*(shot.get('in_frame') if isinstance(shot.get('in_frame'),list) else []),
                         *(turn.get('speaker_name') for turn in shot.get('turns', [])
                           if turn.get('delivery_mode') == 'visible_dialogue')]
            if isinstance(name,str) and name))
        if 'placed' not in scene:
            issues.append(f'{label}: missing placed list; [] is valid when no bodies are present')
        elif not scene['placed'] and visible:
            issues.append(f'{label}: placed cannot be empty with visible bodies {visible!r}')
        if not scene.get('shot_indexes') or any(i not in durations for i in scene['shot_indexes']):
            issues.append(f'{label}: must refer to current shot indexes')
    if not episode.get('self_review'):
        issues.append('self_review of the complete creative draft is required')
    music_plan=(episode.get('chapter_script')or{}).get('music_plan')
    # Music timing indexes duration_seconds directly; a draft whose shots have not
    # yet cleared the basic duration check must keep the per-shot feedback instead
    # of crashing the validator (EP03B 2026-10-23: one missing duration killed the
    # whole validation, the author never saw which shot was broken).
    durations_ok=(all(s.get('index')==number for number,s in enumerate(shots,1))
                  and all(isinstance(s.get('duration_seconds'),(int,float))
                          and s['duration_seconds']>0 for s in shots))
    if (task.get('music_mode')=='timeline'or music_plan is not None) and durations_ok:
        from novel_manga.story.timing import music_plan_issues
        issues.extend(music_plan_issues(music_plan,shots))
    if manifest.get('mode')=='adaptation' and not issues:
        # Adaptation authors get the shot↔scene coverage feedback in-session, but
        # only once the basic per-shot checks are clean: adaptation_issues indexes
        # shot fields directly and must not crash on drafts that already carry
        # explicit basic errors (EP03C 2026-10-23: missing index).
        from .creative_material import adaptation_issues
        issues.extend(adaptation_issues(episode,manifest))
    elif task.get('production_handoff') and not issues:
        from .creative_material import design_issues
        issues.extend(design_issues(episode,manifest))
    return issues


def read_evidence(work_dir):
    reads = []
    for log in sorted((work_dir / 'attempts').glob('*/session.jsonl')):
        for line in log.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            for part in (event.get('message') or {}).get('content') or []:
                if isinstance(part, dict) and part.get('type') == 'tool_use' and part.get('name') == 'Read':
                    data = part.get('input') or {}
                    path = str(data.get('file_path') or data.get('path') or '')
                    reads.append({'path': path.removeprefix('/work/').removeprefix('./'),
                                  'offset': data.get('offset'), 'limit': data.get('limit'),
                                  'attempt': log.parent.name})
    for log in sorted((work_dir / 'attempts').glob('*/codex-events.jsonl')):
        for line in log.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get('item') or {}
            command = str(item.get('command') or '')
            if (event.get('type') != 'item.completed' or item.get('type') != 'command_execution'
                    or not re.search(r'\b(cat|sed|head|tail)\b|read_text\(|read-text\.py\b', command)):
                continue
            manifest = json.loads((work_dir/'input/manifest.json').read_text())
            declared = dict.fromkeys([*manifest['required_reads'],
                                      *manifest.get('skill_references', {}).values()])
            for path in declared:
                candidate = work_dir/path
                mention = path in command
                prefix = '.claude/skills/episode-production/'
                if path.startswith(prefix):
                    suffix = path.removeprefix(prefix)
                    snapshot = log.parent/'skill-current'/suffix
                    if ('/skill-current/'+suffix in command and snapshot.is_file()
                            and snapshot.read_bytes() == candidate.read_bytes()):
                        mention = True
                displayed = False
                if mention and candidate.suffix in {'.md', '.txt', '.json'}:
                    content = candidate.read_text().strip()
                    displayed = bool(content) and content in str(item.get('aggregated_output') or '')
                if mention and (item.get('exit_code') == 0 or displayed):
                    reads.append({'path':path,'via':'successful native file-read command','attempt':log.parent.name})
        receipt = log.parent/'attempt.json'
        images = log.parent/'attached-images.json'
        if receipt.is_file() and json.loads(receipt.read_text()).get('exit') == 0 and images.is_file():
            reads.extend({'path':r['path'],'via':'native image attachment','attempt':log.parent.name}
                         for r in json.loads(images.read_text()))
    return reads


from novel_manga.style_packs import normalized_style, export_style


def export(episode, manifest, destination, *, notes=None, style_source=None):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'owner').write_text('Unified creator / text-only creative candidate; no media generated.\n')
    if manifest.get('profile'):
        episode=copy.deepcopy(episode)
        profile=copy.deepcopy(manifest['profile'])
        episode['chapter_script'].setdefault('profile',{}).update(profile)
        (destination/'profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
    if manifest.get('selected_style'):
        if style_source is None:
            raise ValueError('export requires the selected frozen style_source')
        export_style(style_source,destination)
    shots = episode['chapter_script']['shots']
    for name, data in [('episode', episode), ('chapter_script', episode['chapter_script']),
                       ('interaction', episode['interaction']), ('spatial_plan', episode['spatial_plan']),
                       ('clip_groups', episode['clips']), ('creative-review', episode['self_review'])]:
        (destination / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    title = episode.get('title') or manifest['task'].get('title', '互动剧')
    script = [f'# {title}\n', str(episode.get('logline') or ''), '\n本稿为主创Agent创作候选，尚未制作视频。\n']
    board = [f'# {title} 完整分镜\n']
    body_seconds = sum(s['duration_seconds'] for s in shots)
    cursor = 0.
    for shot in shots:
        duration = shot['duration_seconds']
        heading = f'镜头{shot["index"]:02d}｜{cursor:g}–{cursor+duration:g}秒｜{shot["location"]}'
        script.extend([f'## {heading}\n', shot['event']])
        board.extend([f'## {heading}\n', f'目的：{shot["purpose"]}',
            f'起始：{shot["start_state"]}', f'画面与行动：{shot["event"]}',
            f'机位与运动：{shot["camera"]}', f'结果：{shot["end_state"]}'])
        if shot.get('visual_prompt'):
            board.append('视觉重点：' + str(shot['visual_prompt']))
        if shot.get('opening_frame'):
            board.append('首帧 prompt：'+shot['opening_frame']['image_prompt'])
            board.append('首帧可见人物：'+('、'.join(shot['opening_frame']['visible_characters'])or'无人或主角在镜头后'))
        if shot.get('light'):
            board.append('场景光线：' + str(shot['light']))
        for turn in shot.get('turns', []):
            mode = {'visible_dialogue': '现场对白', 'offscreen_dialogue': '画外声'}[turn['delivery_mode']]
            speech = f'{turn["speaker_name"]}（{mode}）：{turn["text"]}'
            script.append(speech)
            board.extend([f'表演先行：{turn["emotion"]}', speech])
        sound = shot.get('sfx', '')
        if isinstance(sound, list):
            sound = '、'.join(sound)
        board.extend([f'现场声：{sound}',
            f'音乐：{(shot.get("audio_plan") or {}).get("music_cue", "")}',
            f'素材依据：{shot.get("source_quote", "原创设定")}'])
        cursor += duration
        if (manifest['task'].get('world_card_seconds')
                and shot['index'] == manifest['task'].get('world_card_after_shot', 1)):
            seconds = manifest['task']['world_card_seconds']
            card = (f'## 世界介绍卡｜{cursor:g}–{cursor+seconds:g}秒\n\n'
                    + manifest['task'].get('world_card_text', title))
            script.append(card); board.append(card); cursor += seconds
    seconds = manifest['task'].get('end_card_seconds', 0)
    def choice_label(option):
        if re.match(r'^'+re.escape(option['id'])+r'\s*[｜|、.：:]', option['label']):
            return option['label']
        return f'{option["id"]} {option["label"]}'
    labels = '／'.join(choice_label(o) for o in episode['interaction']['options'])
    card = f'## 完整选择卡｜{cursor:g}–{cursor+seconds:g}秒\n\n{labels}\n\n完整保持{seconds:g}秒，尚未选择。'
    script.append(card); board.append(card)
    (destination / '剧本.md').write_text('\n\n'.join(script) + '\n')
    (destination / '分镜.md').write_text('\n\n'.join(board) + '\n')
    choices = [f'# {title} 互动选择\n', episode['interaction']['question']]
    for option in episode['interaction']['options']:
        choices.extend([f'## {choice_label(option)}\n', f'铺垫：{option["reason"]}',
                        f'下一步：{option.get("next_step", "由后续选择决定")}'])
    choices.append(f'完整选项同屏{manifest["task"].get("end_card_seconds", 3)}秒；selected=null。')
    (destination / '互动选择.md').write_text('\n\n'.join(choices) + '\n')
    spatial = ['# 白模空间设计\n', '本轮交付空间调度方案，未运行Blender或生成白模图。\n']
    for scene in episode['spatial_plan']['scenes']:
        spatial.extend([f'## {scene.get("scene_id", "场景")}\n',
                        '```json\n' + json.dumps(scene, ensure_ascii=False, indent=2) + '\n```'])
    (destination / '白模空间设计.md').write_text('\n\n'.join(spatial) + '\n')
    if notes is not None and notes.is_file():
        (destination / '创作说明.md').write_text(notes.read_text())
    (destination / 'README.md').write_text(f'# {title} 主创Agent完整创作候选\n\n'
        '[完整剧本](剧本.md) · [完整分镜](分镜.md) · [互动选择](互动选择.md) · [白模空间设计](白模空间设计.md)\n\n'
        f'正文计划{body_seconds:g}秒，世界卡{manifest["task"].get("world_card_seconds", 0)}秒，'
        f'片尾完整选择卡{manifest["task"].get("end_card_seconds", 0)}秒。\n\n'
        '原始来源与Skill读取证据保存在本次独立创作工作区。尚未采用到生产，不代表视频或声音已验收。\n')
