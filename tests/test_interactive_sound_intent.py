import copy
import json
from pathlib import Path

from novel_manga.interactive.authored import prepare_authored, pack_authored
from novel_manga.application.profiles import h3_stamp
from novel_manga.util import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ('【阶段1】梁舟站在门边。声音：中文普通话，压低声音且迟疑，梁舟开口说：{谁在那里？}'
          '结束时：门打开。画面呈现')


def scene_clip(**extra):
    return {'request_seconds': 10, 'scene_ids': ['s1'], 'shot_timing': [{'seconds': 10, 'cut': ''}],
            'references': [{'role': 'character', 'name': '梁舟',
                            'path': 'series_assets/characters/c1/turnaround.jpeg'}], **extra}


def test_authored_sound_and_sign_survive_the_final_sd_request(tmp_path):
    material = json.loads((ROOT/'projects/adventure/laughing-country/material.json').read_text())
    material['script']['shots'][2]['native_text'] = ['笑之国法律', '公共场所保持微笑']
    material['script']['shots'][2]['audio_plan'] = {'music_cue': '前半轻音乐，看到规则后收低；对白优先'}
    project = tmp_path/'input';project.mkdir()
    atomic_write_json(project/'material.json', material)
    (project/'source.md').write_text('原文及已批准的声音与实体告示牌说明')
    episode = prepare_authored(project, tmp_path/'trial')
    clip = pack_authored(episode)['clips'][1]
    assert '「笑之国法律」' in clip['prompt']
    assert '仅允许上述指定实体表面文字和数字' in clip['prompt']
    assert '画面中不出现任何文字' not in clip['prompt']
    assert '前半轻音乐，看到规则后收低' in clip['prompt']
    assert '无背景音乐。' not in clip['prompt']
    assert clip['spoken_text'] == material['script']['shots'][2]['turns'][0]['text']
    assert clip['music_cues'][0]['stage'] == 1


def test_h3_translates_music_without_changing_bound_dialogue(monkeypatch):
    from novel_manga.application.rendering import h3
    clip = scene_clip(render_family='photo', prompt=PROMPT)
    clip['music_cues'] = [{'stage': 1, 'cue': '细小弦乐进入，低于对白，句尾淡出'}]
    def write(parts, schema, **kwargs):
        assert 'music' in schema['required']
        assert '细小弦乐进入' in parts[0]['text']
        return {'shots': ['<Subject 1> waits by a door. Low and hesitant delivery. <DIALOGUE_1>'],
                'soundscape': 'None.', 'music': 'Shot 1: soft strings enter below dialogue and fade at the sentence end.',
                'conflicts': []}
    monkeypatch.setattr(h3, 'ask_json', write)
    assert h3.convert(clip)
    assert 'non_diegetic_music:\nShot 1: soft strings' in clip['prompt_h3']
    assert clip['prompt_h3'].count('<d>[Chinese] 谁在那里?</d>') == 1
    assert not h3.request_issues(clip)


def test_missing_authored_music_does_not_publish_an_incomplete_h3_request(monkeypatch):
    from novel_manga.application.rendering import h3
    clip = scene_clip(render_family='photo', prompt=PROMPT)
    clip['music_cues'] = [{'stage': 1, 'cue': '轻音乐'}]
    monkeypatch.setattr(h3, 'ask_json', lambda *a, **k: {
        'shots': ['<Subject 1> waits. <DIALOGUE_1>'], 'soundscape': 'None.', 'conflicts': []})
    assert not h3.convert(clip, tries=1)
    assert not clip.get('prompt_h3')


def test_a_changed_score_requires_a_new_h3_translation():
    clip = scene_clip(render_family='photo', prompt=PROMPT)
    other = copy.deepcopy(clip)
    other['music_cues'] = [{'stage': 1, 'cue': '无背景音乐，留下脚步'}]
    assert h3_stamp(clip) != h3_stamp(other)
