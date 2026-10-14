from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from novel_manga.media import generation


def test_two_clips_can_prepare_the_same_voice_sample(tmp_path, monkeypatch):
    source=tmp_path/'voice.wav'
    source.write_bytes(b'original')
    both_trimming=Barrier(2)

    def encode(command):
        Path(command[-1]).write_bytes(b'trimmed')
        both_trimming.wait(timeout=5)

    monkeypatch.setattr(generation,'run',encode)
    with ThreadPoolExecutor(2) as pool:
        paths=list(pool.map(lambda _: generation.trimmed_voice(source,7.5),range(2)))
    assert paths[0]==paths[1]
    assert paths[0].read_bytes()==b'trimmed'
    assert list(paths[0].parent.iterdir())==[paths[0]]
