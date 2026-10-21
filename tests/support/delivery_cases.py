"""Shared delivery/final-cut test factories (moved out of test modules for the
tests-use-support rule; behavior unchanged)."""
import json
from types import SimpleNamespace

import pytest

from novel_manga.util import atomic_write_json


@pytest.fixture
def labelled_delivery(tmp_path, monkeypatch):
    from novel_manga.interactive import delivery
    episode = tmp_path/'episode'; episode.mkdir()
    work = episode/'work'; work.mkdir()
    assembled = episode/'assembled.mp4'; assembled.write_bytes(b'assembled with dialogue')
    ass = episode/'dialogue.ass'; ass.write_text('approved dialogue')
    (episode/'interaction.json').write_text(json.dumps({'seconds': 3, 'question': 'choose', 'options': []}))
    (episode/'overlays.json').write_text(json.dumps([{'index': 2, 'text': '仍在池室', 'placement': 'panel'}]))
    shots = [{'index': 1, 'duration_seconds': 6}, {'index': 2, 'duration_seconds': 9},
             *[{'index': i, 'duration_seconds': 8} for i in range(3, 23)],
             {'index': 23, 'duration_seconds': 10}]
    clips = [{'clip_id': 'clip_01', 'shot_indexes': [1, 2], 'request_seconds': 15},
             *[{'clip_id': f'clip_{i-1:02d}', 'shot_indexes': [i], 'request_seconds': s['duration_seconds']}
               for i, s in enumerate(shots[2:], 3)]]
    timeline = []; cursor = 0.0
    for i, clip in enumerate(clips):
        end = cursor + clip['request_seconds'] + (.2 if i == 0 else 0)
        timeline.append({'unit_id': clip['clip_id'], 'start': cursor, 'end': end})
        cursor = end
    ctx = SimpleNamespace(episode_dir=episode, work=work, script={'shots': shots}, clip_plan={'clips': clips},
                          provider=SimpleNamespace(short_edge=768), profile={'resolution': '768p'},
                          settings=SimpleNamespace(width=1080, height=1920, font_path=tmp_path/'font'))
    report = {'assembly': {'final_video': str(assembled), 'ass': str(ass), 'timeline': timeline, 'thin_passed': True},
              'quality_review': {'passed': True, 'content_passed': True}, 'clips': []}
    commands = []
    monkeypatch.setattr(delivery, 'inspect_media', lambda *a, **k: {'passed': True, 'checks': {}})
    monkeypatch.setattr(delivery, '_card', lambda *a: None)
    monkeypatch.setattr(delivery, 'run', lambda command: commands.append(command))
    monkeypatch.setattr(delivery.subprocess, 'run',
                        lambda command, **kw: commands.append(command) or SimpleNamespace(returncode=0, stderr=''))
    def probe(command, **kw):
        frames = '4705' if command[-1].endswith('/episode01.mp4') else '4630'
        return json.dumps({'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
             'r_frame_rate': '25/1', 'nb_frames': frames},
            {'codec_type': 'audio', 'codec_name': 'aac'}]})
    monkeypatch.setattr(delivery.subprocess, 'check_output', probe)
    return SimpleNamespace(ctx=ctx, report=report, commands=commands, destination=tmp_path/'delivery')


def episode(tmp_path):
    directory = tmp_path/'book'/'book_1'; directory.mkdir(parents=True)
    movie = directory/'movie.mp4'; movie.write_bytes(b'movie')
    clip = {'clip_id': 'a', 'kind': 'video', 'shot_indexes': [1], 'references': []}
    plan = {'clips': [clip]}
    atomic_write_json(directory/'clip_plan.json', plan)
    atomic_write_json(directory/'chapter_script.json', {'shots': []})
    atomic_write_json(directory/'episode_review.json', {'clips': {'a': {'severity': 'pass'}}, 'feedback': {}})
    media = {'clips': [{'clip_id': 'a', 'selected': {'video': str(movie), 'chunks': []}}],
             'assembly': {'final_video': str(movie), 'thin_passed': True,
                          'timeline': [{'unit_id': 'a', 'start': 0, 'end': 9}]}}
    ctx = SimpleNamespace(episode_dir=directory, clip_plan=plan, script={'shots': []}, bible=None,
                          settings=SimpleNamespace(width=1080, height=1920, font_path='/font'))
    return SimpleNamespace(context=ctx), media, movie
