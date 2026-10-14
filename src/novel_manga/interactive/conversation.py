"""Compile an authored continuous exchange for the actual SD and H3 lanes.

One shot is one camera take, not one utterance. Multiple turns retain their
source order inside it. References, task history and submission remain shared
with the existing production pipeline.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from novel_manga.util import read_json, atomic_write_json


def compile_conversation_clip(clip: dict, shot: dict, *, performance: dict) -> dict:
    from novel_manga.story.h3 import compose, request_issues
    from novel_manga.application.profiles import h3_stamp

    for field in ('visual_prompt', 'end_state', 'camera', 'authored_frame'):
        if not str(shot.get(field, '')).strip():
            raise ValueError(f'Continuous exchange requires {field}')
    visible = shot.get('in_frame', [])
    if set(visible) - set(shot['characters']):
        raise ValueError('Visible cast must belong to the room')
    turns = [t for t in shot['turns'] if t.get('delivery_mode') != 'silent_action']
    if any(t['delivery_mode'] not in {'visible_dialogue', 'offscreen_dialogue'} for t in turns):
        raise ValueError('Continuous exchange supports visible or offscreen dialogue')
    if any(t['speaker_name'] not in visible for t in turns if t['delivery_mode'] == 'visible_dialogue'):
        raise ValueError('A speaking participant must be visible in this exchange')
    slots = re.findall(r'<DIALOGUE_(\d+)>', shot['authored_frame'])
    if slots != [str(i) for i in range(1, len(turns) + 1)]:
        raise ValueError('English dialogue slots must bind every turn once in order')
    result = copy.deepcopy(clip)
    pictured = [r for r in result['references'] if r['role'] != 'voice']
    cast_refs = {r['name'] for r in pictured if r['role'] == 'character'}
    if set(visible) - cast_refs:
        raise ValueError('Visible participant reference is missing')
    prop_refs = {r['name'] for r in pictured if r['role'] == 'prop'}
    if set(shot.get('props', [])) - prop_refs:
        raise ValueError('Authored persistent prop reference is missing')
    bindings = []
    for i, ref in enumerate(pictured, 1):
        if ref.get('reference_scope') == 'spatial_layout':
            bindings.append(f'@图片{i}仅用于机位、座位和物品位置，不采用灰模外观；灰模代理对应已有三维位置中的人物，不增加演员')
            continue
        scope = '身份与衣装' if ref['role'] == 'character' else '外观设计'
        bindings.append(f'{ref["name"]}采用@图片{i}的{scope}；参考拼版不作为画面构图')
    lines = [
        f'真人恋综，同一房间内一段{clip["request_seconds"]:g}秒连续交流。',
        '【参考绑定】' + '。'.join(bindings) + '。',
        '【房间在场】' + '、'.join(shot['characters']) + '。画外不等于离场。',
        '【开始状态】' + shot['visual_prompt'],
        '【本段机位】' + shot['camera'] + '。取景内人物：' + '、'.join(visible),
        '【动作与交流】' + shot.get('motion_prompt', ''),
        '按下列顺序接话，句间停顿来自回应；不为每句话另切镜，不面对观众轮流朗读。',
    ]
    for turn in turns:
        scope = '（画外声音，画内嘉宾不替它开口）' if turn['delivery_mode'] == 'offscreen_dialogue' else ''
        lines.append(f'{turn["speaker_name"]}{scope}：{turn["text"]}（{turn.get("emotion", "自然接话")}）')
    lines += ['【结束状态】' + shot['end_state'],
              '声音：' + shot.get('sfx', '安静室内底噪') + '。只说列出的对白；其他人自然静默听。',
              '保持原参考的真人画风、脸、服装和物品归属；字幕由后期添加，画面不生成文字。']
    result['prompt'] = '\n'.join(lines)
    result['prompt_chars'] = len(result['prompt'])
    # H3 consumes its own field; do not reuse the legacy multi-stage binding
    # table after collapsing the exchange into one continuous camera take.
    result['dialogue_bindings'] = [dict(t, stage=1, source_stage=shot['index']) for t in turns]
    result['shot_timing'] = [{'stage': 1, 'seconds': clip['request_seconds']}]
    stage = [('', [(t['speaker_name'], t['text'], t['delivery_mode'] == 'offscreen_dialogue', '') for t in turns])]
    direction = shot['authored_frame']
    for index, turn in enumerate(turns, 1):
        emotion = turn.get('emotion', '').strip()
        cue = performance.get(emotion, '').strip()
        if emotion and not cue:
            raise ValueError(f'Missing English performance cue: {emotion}')
        if cue:
            slot = f'<DIALOGUE_{index}>'
            direction = direction.replace(slot, f'Performance: {cue} {slot}')
    result['prompt_h3'] = compose(result, [direction], stage,
        soundscape='Quiet continuous room tone. Only the listed dialogue is spoken. No music.', authored=True)
    issues = request_issues(result)
    if issues:
        raise ValueError(f'Conversation H3 request: {issues}')
    result['prompt_h3_of'] = h3_stamp(result)
    result['authored_english_source'] = 'chapter_script.shots.authored_frame'
    result['prompt_compiler'] = 'romance_conversation_v1'
    return result


def pack_conversation(directory: Path) -> dict:
    from dataclasses import replace
    from novel_manga.application.packing.context import load_context, POLICY
    from novel_manga.application.packing.service import prepared_shots, clip_entry
    from novel_manga.story.compilation import plan_totals
    from novel_manga.application.preparation.readiness import plan_issues

    script = read_json(directory / 'chapter_script.json', {})
    context = load_context(directory, directory.parent / 'story_bible.json', None, None, None, None)
    context['compiler_options'] = replace(context['compiler_options'], max_stages=6, camera_policy='authored')
    shots = prepared_shots(script, directory, identity_data=context.get('identity_data'))
    by_index = {s['index']: s for s in shots}
    groups = read_json(directory / 'authored_groups.json', [])
    performance = read_json(directory.parent / 'performance_en.json', {})
    if [i for g in groups for i in g['shot_indexes']] != [s['index'] for s in shots]:
        raise ValueError('Conversation groups must cover source shots once in order')
    clips = []
    for number, group in enumerate(groups, 1):
        if len(group['shot_indexes']) != 1:
            raise ValueError('Author each continuous exchange as one shot with multiple turns')
        shot = by_index[group['shot_indexes'][0]]
        packed = {'kind': 'video', 'location': shot['location'], 'shots': [shot],
                  'seconds': group['request_seconds'], 'request_seconds': group['request_seconds'],
                  'shot_timing': [{'seconds': shot['duration_seconds']}]}
        clip = clip_entry(packed, f'clip_{number:02d}', context)
        # The novel packer keeps only one prop picture. Authored conversations
        # may need e.g. the offered card AND the already parked suitcases.
        # Complete only explicitly requested props, through its asset resolver.
        from novel_manga.application.packing.assets import build_references
        present = {r['name'] for r in clip['references'] if r['role'] == 'prop'}
        for name in shot.get('props', []):
            if name in present:
                continue
            refs, _, _ = build_references([], None, context['bible'], context['location_map'],
                novel_dir=directory.parent, settings=context['compiler_options'], props=[name])
            for ref in refs:
                if ref['role'] != 'prop' or ref['name'] in present:
                    continue
                ref['tag'] = '@图片' + str(1 + sum(r['role'] != 'voice' for r in clip['references']))
                clip['references'].append(ref)
                present.add(ref['name'])
        if group.get('spatial_reference'):
            path = group['spatial_reference']
            if not (directory.parent / path).is_file():
                raise FileNotFoundError(directory.parent / path)
            clip['references'].append({'role': 'location', 'name': '空间布局参考',
                'path': path, 'reference_scope': 'spatial_layout'})
        clip = compile_conversation_clip(clip, shot, performance=performance)
        clip['source_authored_segment'] = group['source_segment']
        clips.append(clip)
    plan = {'policy': POLICY, 'clips': clips,
            'limits': {'max_clip_seconds': 15, 'max_stages': 1, 'camera_policy': 'authored'},
            'totals': plan_totals(clips, shots, context)}
    issues = plan_issues(plan, script)
    if issues:
        raise ValueError(f'Conversation plan: {issues}')
    atomic_write_json(directory / 'clip_plan.json', plan)
    return plan
