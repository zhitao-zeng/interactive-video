"""Exact-time reads must decode a new frame from the selected take."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.repair import video_frames


def evidence(tmp_path, paths=('input/take-1',)):
    rows = []
    for path in paths:
        directory = tmp_path / path
        directory.mkdir(parents=True)
        (directory / 'clip.mp4').write_bytes(b'local take')
        rows.append({'path': path, 'current': True,
                     'frames': [{'path': path+'/frames/frame_1.jpg', 'seconds': 2.0}]})
    (tmp_path / 'input/evidence.json').write_text(json.dumps({'takes': rows}))
    return rows


def test_requested_time_decodes_instead_of_returning_nearest_supplied_frame(tmp_path, monkeypatch):
    evidence(tmp_path)
    calls = []
    def decode(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b'new decoded JPEG')
        return SimpleNamespace(returncode=0, stderr='showinfo n:0 pts_time:2.125 pos:1')
    monkeypatch.setattr(video_frames.subprocess, 'run', decode)
    monkeypatch.setattr(video_frames.shutil, 'which', lambda _: '/existing/ffmpeg')
    result = video_frames.inspect(tmp_path, 'take-1', 2.1)
    assert result['requested_seconds'] == 2.1
    assert result['actual_seconds'] == 2.125
    assert result['frames'][0]['seconds'] == 2.125
    assert result['frames'][0]['path'].startswith('output/frames/')
    assert (tmp_path / result['frames'][0]['path']).read_bytes() == b'new decoded JPEG'
    assert str(tmp_path / 'input/take-1/clip.mp4') in calls[0]
    assert json.loads((tmp_path / 'output/frame_requests.jsonl').read_text())['source_video'] == 'input/take-1/clip.mp4'


def test_batch_reader_requires_qualified_take_when_basenames_are_ambiguous(tmp_path):
    evidence(tmp_path, ('input/clip_a/take-1', 'input/clip_b/take-1'))
    with pytest.raises(ValueError, match='ambiguous'):
        video_frames.inspect(tmp_path, 'take-1')
    assert video_frames.inspect(tmp_path, 'clip_b/take-1')['video'] == 'input/clip_b/take-1/clip.mp4'


def test_failed_extraction_never_returns_an_old_jpeg_or_a_supplied_frame(tmp_path, monkeypatch):
    evidence(tmp_path)
    monkeypatch.setattr(video_frames.shutil, 'which', lambda _: '/existing/ffmpeg')
    monkeypatch.setattr(video_frames.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stderr='no frames'))
    with pytest.raises(RuntimeError, match='extraction'):
        video_frames.inspect(tmp_path, 'take-1', 90.0)


def test_listing_still_works_without_a_decoder(tmp_path):
    evidence(tmp_path)
    assert video_frames.inspect(tmp_path, 'take-1')['selection'] == 'all supplied frames'
