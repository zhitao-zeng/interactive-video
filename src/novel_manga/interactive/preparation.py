"""Bind approved external episode material to the existing production contracts."""
from pathlib import Path
import json
from PIL import Image
from novel_manga.util import atomic_write_json


def prepare_episode(project: Path, output: Path, package: Path) -> Path:
    episode = json.loads((project / 'episode01.json').read_text())
    bible = json.loads((project / 'bible.json').read_text())
    shots = episode['shots']
    total = sum(s['duration_seconds'] for s in shots)
    if abs(total + episode['interaction']['seconds'] - episode['target_seconds']) > .01:
        raise ValueError('Authored body plus interaction must match the target duration')
    ids = [s['id'] for s in shots]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate authored shot IDs')
    output.mkdir(parents=True, exist_ok=True)
    directory = output / (output.name + '_1')
    directory.mkdir(exist_ok=True)
    if (directory / 'thin_media_report.json').exists():
        raise FileExistsError('Episode already rendered; keep its adopted preparation and history')
    (output / 'owner').write_text('Codex / interactive adventure demo / authorized 2026-10-12\n')
    profile = {'style': 'modern-live', 'frame': '9:16', 'tier': 'quality', 'genre': 'generic',
               'resolution': '720p', 'expressions': False, 'two_views': 'off', 'episode_shape': 'fixed',
               'speech_gate': 'enforce', 'props': False}
    atomic_write_json(output / 'profile.json', profile)
    atomic_write_json(output / 'story_bible.json', bible)
    segments, prepared, trace = [], [], []
    for index, shot in enumerate(shots, 1):
        source = '。'.join(filter(None, [shot['visual'], shot['action'], shot['dialogue']]))
        segment_id = f'seg_{index}'
        segments.append({'segment_id': segment_id, 'text': source})
        visible = ['林野'] if shot['visible'] else []
        turns = ([{'speaker_name': '林野', 'delivery_mode': 'visible_dialogue' if shot.get('on_camera') else 'offscreen_dialogue',
                   'text': shot['dialogue'], 'emotion': '镇定自然，成年男性中低音，轻微呼吸感'}]
                 if shot['dialogue'] else [{'speaker_name': '', 'delivery_mode': 'silent_action', 'text': shot['action']}])
        prepared.append({'index': index, 'origin_index': index, 'shot_id': shot['id'], 'label': shot['id'], 'segment_id': segment_id,
                         'source_quote': source, 'location': shot['location'], 'scene_id': shot['location'],
                         'scene_time': '阴天午后，连续时间', 'characters': visible, 'in_frame': visible,
                         'extras': [], 'listeners': [], 'wears': {}, 'props': [],
                         'visual_prompt': shot['visual'], 'motion_prompt': shot['action'],
                         'start_state': shot['visual'], 'end_state': shot['end'],
                         'camera': shot['camera'], 'light': '阴天自然散射光，柔和低反差，真实肤色',
                         'shot_scale': shot['scale'], 'duration_seconds': shot['duration_seconds'],
                         'sfx': shot['sfx'], 'avoid': '额外人物、古装、超自然光效、错误可读文字',
                         'turns': turns})
        trace.append({'shot_id': shot['id'], 'segment_id': segment_id, 'original_shots': shot['source_shots'],
                      'adaptation': 'expanded observation and verification; no new hidden truth'})
    script = {'video_title': episode['title'], 'source_title': '第一集 地图上不存在的村庄', 'episode_index': 1,
              'profile': profile, 'hook': episode['hook'], 'summary': episode['summary'],
              'shots': prepared, 'skipped_segments': []}
    atomic_write_json(directory / 'chapter_script.json', script)
    atomic_write_json(directory / 'segments.json', segments)
    atomic_write_json(directory / 'source_trace.json', {'original': str(package / episode['source']), 'shots': trace})
    atomic_write_json(directory / 'interaction.json', episode['interaction'])
    atomic_write_json(directory / 'overlays.json', [{'index': i, **s['overlay']} for i, s in enumerate(shots, 1) if s.get('overlay')])
    (directory / 'source.txt').write_text('\n\n'.join(s['text'] for s in segments))
    # Import existing identity pixels. This is a format conversion, not a redraw.
    card = output / 'series_assets/characters/character_001/turnaround.jpeg'
    card.parent.mkdir(parents=True, exist_ok=True)
    if not card.exists():
        with Image.open(package / '角色资产/林野-身份基准-v1.0.png') as image:
            image.convert('RGB').save(card, quality=97)
        atomic_write_json(card.with_suffix('.jpeg.origin.json'), {'kind': 'supplied-reference', 'reference_scope':'identity_portrait',
                          'source': '角色资产/林野-身份基准-v1.0.png', 'package': str(package)})
    return directory
