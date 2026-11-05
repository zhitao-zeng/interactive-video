import copy
import json
from pathlib import Path

import pytest

from novel_manga.interactive import creation, creative_contract
from novel_manga.interactive.structured_dialogue import dialogue_issues, source_rows
from novel_manga.util import atomic_write_json, read_json


ARCHIVE = Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022/.runtime/zhiyao-batch/mini-EP04C_W10_失物海.zip')
MATERIAL = Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022/.runtime/zhiyao-base/material')
HISTORICAL_EPISODE = Path('/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-w10-clean-v21-20261025/.runtime/autonomous-w10-codex56-v21-20261025/run/zhiyao-auto-v21-EP04C_W10_失物海/creator/sandbox/output/episode.json')


def task():
    return {
        'mode': 'adaptation', 'project': 'zhiyao',
        'source_archive': str(ARCHIVE), 'series_material': str(MATERIAL),
        'source_script': 'pkg/episode/01-完整剧本.md',
        'structured_script': 'input/package/pkg/episode/02-结构化剧本.json',
        'target_seconds': [180, 300], 'brief': '逐字绑定完整剧本；保留全部说话人、台词和事件顺序。',
        'video_lane': 'h3', 'production_handoff': True,
    }


def prepare(tmp_path):
    path = tmp_path/'task.json'
    atomic_write_json(path, task())
    return creation.prepare(path, tmp_path/'creator')


def test_real_prepare_and_historical_episode_bind_all_structured_speakers(tmp_path):
    manifest = prepare(tmp_path)
    work = manifest['work_dir'] if 'work_dir' in manifest else tmp_path/'creator/sandbox'
    episode = copy.deepcopy(read_json(HISTORICAL_EPISODE))
    for shot in episode['chapter_script']['shots']:
        if 'opening_frame' in shot:
            shot['opening_frame']['reference_subjects'] = []
    assert creative_contract.validate(episode, manifest, work) == []
    protected = read_json(work/'input/protected-structured-dialogue.json')
    assert len(protected) == 60
    assert {row['speaker'] for row in protected} == {'C01', 'C046'}


def test_structured_dialogue_rejects_speaker_substitution_missing_and_added_text(tmp_path):
    manifest = prepare(tmp_path)
    work = tmp_path/'creator/sandbox'
    episode = read_json(HISTORICAL_EPISODE)
    shots = episode['chapter_script']['shots']
    source = work/'input/package/pkg/episode/02-结构化剧本.json'
    assert dialogue_issues(shots, source, MATERIAL, manifest['task']) == []
    for mutation in ('speaker', 'missing', 'added'):
        changed = copy.deepcopy(shots)
        if mutation == 'speaker':
            changed[0]['turns'][0]['speaker_name'] = '潮叔'
        elif mutation == 'missing':
            changed[0]['turns'].pop(0)
        else:
            changed[0]['turns'][0]['text'] += '新增一句。'
        issues = dialogue_issues(changed, source, MATERIAL, manifest['task'])
        assert len(issues) == 1 and 'structured dialogue mismatch' in issues[0]


def test_structured_dialogue_allows_source_turn_split_across_shots(tmp_path):
    manifest = prepare(tmp_path)
    work = tmp_path/'creator/sandbox'
    shots = copy.deepcopy(read_json(HISTORICAL_EPISODE)['chapter_script']['shots'])
    source = work/'input/package/pkg/episode/02-结构化剧本.json'
    first = shots[0]['turns'][0]
    cut = len(first['text'])//2
    left, right = first['text'][:cut], first['text'][cut:]
    first['text'] = left
    shots.insert(1, {**copy.deepcopy(shots[0]), 'index': 2,
                     'turns': [{**first, 'text': right}]})
    for number, shot in enumerate(shots, 1):
        shot['index'] = number
    assert dialogue_issues(shots, source, MATERIAL, manifest['task']) == []


def test_structured_selection_and_mapping_fail_closed(tmp_path):
    document = {'episodes': [
        {'materialId': 'one', 'scenes': [{'sceneId': 's', 'flow': [{'speaker': 'C99', 'line': 'x'}]}]},
        {'materialId': 'two', 'scenes': [{'sceneId': 's', 'flow': [{'speaker': 'C99', 'line': 'y'}]}]},
    ]}
    path = tmp_path/'structured.json'
    atomic_write_json(path, document)
    with pytest.raises(ValueError, match='multiple episodes'):
        source_rows(path, {})
    assert source_rows(path, {'materialId': 'two'})[0]['text'] == 'y'
    with pytest.raises(ValueError, match='unambiguous'):
        dialogue_issues([], path, MATERIAL, {'materialId': 'two'})
