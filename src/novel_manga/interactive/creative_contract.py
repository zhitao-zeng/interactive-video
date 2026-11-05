"""Validate and format a creative candidate without admitting media production."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re


def validate(episode, manifest, work_dir):
    if not isinstance(episode, dict):
        return ['episode.json must be one object']
    task = manifest['task']
    shots = (episode.get('chapter_script') or {}).get('shots') or []
    issues = []
    if not shots:
        return ['chapter_script.shots must contain the complete episode']
    text = re.sub(r'\s+', '', '\n'.join((work_dir / p).read_text()
        for p in manifest['source_documents']))
    durations = {}
    for number, shot in enumerate(shots, 1):
        if shot.get('index') != number:
            issues.append(f'shot {number}: consecutive index required')
        for name in ('scene_id', 'location', 'purpose', 'camera', 'start_state', 'event', 'end_state'):
            if not str(shot.get(name) or '').strip():
                issues.append(f'shot {number}: missing {name}')
        seconds = shot.get('duration_seconds')
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
            issues.append(f'shot {number}: positive duration_seconds required')
            seconds = 0
        durations[number] = seconds
        for turn in shot.get('turns', []):
            if not all(turn.get(k) for k in ('speaker_name', 'text', 'delivery_mode', 'emotion')):
                issues.append(f'shot {number}: complete speaker/text/delivery/performance required')
            if turn.get('delivery_mode') not in {'visible_dialogue', 'offscreen_dialogue'}:
                issues.append(f'shot {number}: invalid delivery_mode')
        if manifest['mode'] == 'adaptation':
            quote = re.sub(r'\s+', '', str(shot.get('source_quote') or ''))
            if not quote or quote not in text:
                issues.append(f'shot {number}: source_quote must be exact original-package text')
    indexes = []
    for number, clip in enumerate(episode.get('clips') or [], 1):
        ids = clip.get('shot_indexes') or []
        indexes.extend(ids)
        total = sum(durations.get(i, 0) for i in ids)
        seconds = clip.get('request_seconds', 0)
        if (not ids or len(ids) > 6 or not isinstance(seconds, (int, float))
                or not 4 <= seconds <= 15 or abs(total - seconds) > .01):
            issues.append(f'clip {number}: group 1–6 shots, 4–15 seconds, matching shot total')
    if indexes != list(range(1, len(shots) + 1)):
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
    if not scenes:
        issues.append('spatial_plan.scenes needs a source-backed blockout design')
    for scene in scenes:
        if not scene.get('anchors') or not scene.get('placed') or 'assumptions' not in scene:
            issues.append('spatial scene needs anchors, placed roles and explicit assumptions')
        if not scene.get('shot_indexes') or any(i not in durations for i in scene['shot_indexes']):
            issues.append('spatial scene must refer to current shot indexes')
    if not episode.get('self_review'):
        issues.append('self_review of the complete creative draft is required')
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


def export(episode, manifest, destination, *, notes=None):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'owner').write_text('Unified creator / text-only creative candidate; no media generated.\n')
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
