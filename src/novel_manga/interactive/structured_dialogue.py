"""Bind structured source-flow speaker IDs and dialogue to the creative draft."""
from __future__ import annotations

from itertools import zip_longest
import json
from pathlib import Path
import re

from novel_manga.util import read_json


def _episode(document, task):
    episodes = document.get('episodes')
    if not isinstance(episodes, list) or not episodes:
        if isinstance(document.get('scenes'), list):
            return document
        raise ValueError('structured screenplay needs a nonempty episodes or scenes array')
    selectors = {key: task.get(key) for key in ('materialId', 'material_id', 'videoId', 'video_id', 'ep') if task.get(key) is not None}
    if selectors:
        key, value = next(iter(selectors.items()))
        normalized = key.replace('_id', 'Id').replace('_', '')
        candidates = [row for row in episodes if row.get(normalized) == value]
        if len(candidates) == 1:
            return candidates[0]
        raise ValueError(f'structured screenplay selection {key}={value!r} matches {len(candidates)} episodes')
    if len(episodes) != 1:
        raise ValueError('structured screenplay has multiple episodes; select materialId, videoId or ep explicitly')
    return episodes[0]


def source_rows(path, task=None):
    document = read_json(Path(path), {})
    episode = _episode(document, task or {})
    rows = []
    for scene in episode.get('scenes') or []:
        for number, flow in enumerate(scene.get('flow') or [], 1):
            if 'speaker' not in flow:
                continue
            speaker = flow.get('speaker')
            text = flow.get('line')
            if not isinstance(speaker, str) or not speaker.strip() or not isinstance(text, str) or not text:
                raise ValueError(f"structured dialogue {scene.get('sceneId')} flow {number} needs speaker and line")
            rows.append({'speaker': speaker, 'text': text, 'scene_id': scene.get('sceneId'), 'flow_index': number})
    if not rows:
        raise ValueError('structured screenplay has no scenes[].flow[].speaker dialogue')
    return rows


def speaker_names(rows, series_material):
    from novel_manga.application.configuration import project_root
    directory = Path(series_material)
    directory = directory if directory.is_absolute() else project_root()/directory
    material = read_json(directory/'material.json', {})
    characters = (material.get('bible') or {}).get('characters') or []
    names = {row.get('name') for row in characters}
    mapping = {}
    fixed = []
    for character in characters:
        evidence = json.dumps(character, ensure_ascii=False)
        indexed = re.findall(r'角色索引\s*(C\d+)', evidence)
        for speaker in indexed:
            if speaker in mapping and mapping[speaker] != character.get('name'):
                raise ValueError(f'series material maps {speaker} to multiple characters')
            mapping[speaker] = character.get('name')
        if str(character.get('role') or '').startswith('固定') and not indexed:
            fixed.append(character.get('name'))
    unresolved = sorted({row['speaker'] for row in rows} - mapping.keys() - names)
    if unresolved:
        if unresolved == ['C01'] and len(fixed) == 1:
            mapping['C01'] = fixed[0]
        else:
            raise ValueError(f'cannot bind structured speaker IDs {unresolved} to an unambiguous series character')
    return mapping


def dialogue_issues(shots, source, series_material, task=None):
    rows = source_rows(source, task)
    mapping = speaker_names(rows, series_material)
    expected = [(mapping.get(row['speaker'], row['speaker']), char, row)
                for row in rows for char in row['text'] if not char.isspace()]
    actual = [(turn.get('speaker_name'), char, shot, number)
              for shot in shots for number, turn in enumerate(shot.get('turns') or [], 1)
              for char in turn.get('text', '') if not char.isspace()]
    for position, (wanted, found) in enumerate(zip_longest(expected, actual), 1):
        if wanted is not None and found is not None and wanted[:2] == found[:2]:
            continue
        if wanted:
            speaker, _, row = wanted
            expected_text = f"{row['scene_id']} flow {row['flow_index']} {speaker}: {row['text'][:60]!r}"
        else:
            expected_text = 'end of structured source dialogue'
        if found:
            speaker, _, shot, number = found
            turn = shot['turns'][number - 1]
            actual_text = f"shot {shot.get('index')} turn {number} {speaker}: {turn.get('text', '')[:60]!r}"
        else:
            actual_text = 'end of candidate dialogue'
        return [f'structured dialogue mismatch at character {position}: expected {expected_text}; actual {actual_text}. '
                'Preserve every source speaker, word and order; splitting a source turn across shots is allowed']
    return []
