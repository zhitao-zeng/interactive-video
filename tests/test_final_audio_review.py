import json
from pathlib import Path
import subprocess

from novel_manga.application.review import final_audio


def _movie(path, seconds=65, frequency=440):
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    f'sine=frequency={frequency}:duration={seconds}', '-f', 'lavfi', '-i',
                    f'color=c=black:s=64x64:r=25:d={seconds}', '-shortest', '-c:v', 'libx264',
                    '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path


def _episode(path):
    path.mkdir(parents=True)
    (path / 'chapter_script.json').write_text(json.dumps({'music_plan': {'cues': []}, 'shots': []}))
    (path / 'clip_plan.json').write_text(json.dumps({'clips': [
        {'clip_id': 'clip-a', 'start': 0, 'end': 32}, {'clip_id': 'clip-b', 'start': 32, 'end': 65}]}))
    (path / 'timeline.json').write_text(json.dumps({'timeline': [
        {'clip_id': 'clip-a', 'start': 0, 'end': 32}, {'clip_id': 'clip-b', 'start': 32, 'end': 65}]}))
    (path / 'music_policy.json').write_text(json.dumps({'intent': 'quiet'}))
    (path / 'source.txt').write_text('门外传来三下敲击。')
    (path / 'thin_media_report.json').write_text(json.dumps({'status': 'complete', 'timing': {'render': 1},
        'final_cut_review': {'issues': ['old history']}, 'assembly': {'timeline': [
            {'unit_id': 'clip-a', 'role': 'dialogue', 'start': 0, 'end': 32},
            {'unit_id': 'clip-b', 'role': 'dialogue', 'start': 32, 'end': 65}]}}))


def _runtime(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights.bin').write_bytes(b'weights-v1')
    source = tmp_path / 'eureka-source'
    source.mkdir()
    return {'python': 'python', 'audio_source_root': str(source), 'audio_model_dir': str(model)}


def _fake_worker(monkeypatch, *, fail_after_first=False):
    calls = []

    def run(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text())
        assert set(job['runtime']) == {'python', 'audio_source_root', 'audio_model_dir'}
        assert all(Path(row['audio']).is_absolute() and not row['audio'].startswith('file:')
                   for row in job['jobs'])
        calls.append(job['jobs'])
        jobs = job['jobs'][:1] if fail_after_first else job['jobs']
        for row in jobs:
            Path(row['output']).write_text(json.dumps({'response': f"sound in {row['source_windows']}"}))
        return subprocess.CompletedProcess(command, 1 if fail_after_first else 0, 'done', 'stopped')

    monkeypatch.setattr(final_audio, '_run_worker', lambda runtime, root, job_path: run(['worker', str(job_path)]))
    return calls


def test_full_movie_windows_judgment_and_cache_identity(tmp_path, monkeypatch):
    ep = tmp_path / 'episode'
    _episode(ep)
    movie = _movie(tmp_path / 'final.mp4')
    runtime = _runtime(tmp_path)
    worker_calls = _fake_worker(monkeypatch)
    decode_calls = []
    original_run = subprocess.run
    original_decode = final_audio._decode
    original_duration = final_audio._duration

    def count_ffmpeg(command, **kwargs):
        if command and command[0] == 'ffmpeg' and '-f' in command and 'f32le' in command:
            decode_calls.append(command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(final_audio.subprocess, 'run', count_ffmpeg)
    judgements = []

    def judge(facts, windows):
        judgements.append((facts, windows))
        assert windows[0]['start'] == 0
        assert windows[-1]['end'] >= 64.9
        assert all(row['end'] - row['start'] <= 30 for row in windows)
        return {'summary': 'Only the scripted knocks are present.', 'issues': []}

    monkeypatch.setattr(final_audio, '_judge', judge)
    first = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert first['complete'] and first['passed']
    assert first['movie'] == str(movie.resolve())
    assert len(first['coverage']) == 3
    assert first['coverage'][0]['start'] == 0
    assert first['coverage'][-1]['end'] >= 64.9
    assert len(decode_calls) == 1 and len(worker_calls) == 1
    assert all(row['start'] < row['end'] for row in first['coverage'])
    # Simulate normal finish saving status/history after the review, and replacing its latest receipt.
    report = json.loads((ep / 'thin_media_report.json').read_text())
    report['status'] = 'finished-again'
    report['timing'] = {'render': 2, 'review': 7}
    report['final_audio_review'] = {'status': 'round-trip metadata only'}
    (ep / 'thin_media_report.json').write_text(json.dumps(report))
    (ep / 'final_audio_review.json').write_text(json.dumps({'movie': str(movie.resolve()),
        'complete': False, 'passed': False, 'issues': [], 'errors': ['finish placeholder']}))
    monkeypatch.setattr(final_audio, '_duration', lambda movie: (_ for _ in ()).throw(AssertionError('duration rerun')))
    monkeypatch.setattr(final_audio, '_decode', lambda *a: (_ for _ in ()).throw(AssertionError('decode rerun')))
    monkeypatch.setattr(final_audio, '_run_worker', lambda *a: (_ for _ in ()).throw(AssertionError('worker rerun')))
    monkeypatch.setattr(final_audio, '_judge', lambda *a: (_ for _ in ()).throw(AssertionError('judge rerun')))
    cached = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert cached['passed'] and cached['policy'] == final_audio.POLICY
    assert len(decode_calls) == 1 and len(worker_calls) == 1 and len(judgements) == 1

    # Authored dialogue/music intent changes rerun the text judgment only.
    monkeypatch.setattr(final_audio, '_judge', judge)
    monkeypatch.setattr(final_audio, '_duration', original_duration)
    monkeypatch.setattr(final_audio, '_decode', original_decode)
    monkeypatch.setattr(final_audio, '_run_worker', lambda runtime, root, job_path: worker_calls.append([]))
    script = json.loads((ep / 'chapter_script.json').read_text())
    script['music_plan']['native_bgm'] = 'soft restrained pad'
    (ep / 'chapter_script.json').write_text(json.dumps(script))
    changed_source = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert changed_source['complete'] and len(judgements) == 2
    assert len(decode_calls) == 1 and len(worker_calls) == 1

    def fill_windows(runtime, root, job_path):
        job = json.loads(Path(job_path).read_text())
        worker_calls.append(job['jobs'])
        for row in job['jobs']:
            Path(row['output']).write_text(json.dumps({'response': 'sound evidence'}))
        return subprocess.CompletedProcess(['worker'], 0, '', '')

    monkeypatch.setattr(final_audio, '_run_worker', fill_windows)

    # A changed final movie gets a separate decoded/window cache.
    other_movie = _movie(tmp_path / 'other-final.mp4', frequency=550)
    other = final_audio.review_audio(tmp_path, ep, other_movie, runtime=runtime)
    assert other['movie'] == str(other_movie.resolve()) and len(decode_calls) == 2
    assert len(worker_calls) == 2

    # A model file change invalidates the window and judgment cache.
    (Path(runtime['audio_model_dir']) / 'weights.bin').write_bytes(b'weights-v2-changed')
    changed_model = final_audio.review_audio(tmp_path, ep, other_movie, runtime=runtime)
    assert changed_model['complete'] and len(worker_calls) == 3 and len(decode_calls) == 3


def test_interrupted_windows_resume_without_redecode(tmp_path, monkeypatch):
    ep = tmp_path / 'episode'
    _episode(ep)
    movie = _movie(tmp_path / 'final.mp4')
    runtime = _runtime(tmp_path)
    calls = _fake_worker(monkeypatch, fail_after_first=True)
    decode_calls = []
    original_run = subprocess.run

    def count_ffmpeg(command, **kwargs):
        if command and command[0] == 'ffmpeg' and '-f' in command and 'f32le' in command:
            decode_calls.append(command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(final_audio.subprocess, 'run', count_ffmpeg)
    failed = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert not failed['complete'] and failed['errors']
    assert failed['coverage'] and len(calls[0]) == 3
    # Run the existing worker stub for only missing windows; completed window JSON is reused.
    def resume_worker(runtime, root, job_path):
        job = json.loads(Path(job_path).read_text())
        calls.append(job['jobs'])
        for row in job['jobs']:
            Path(row['output']).write_text(json.dumps({'response': 'resumed window'}))
        return subprocess.CompletedProcess(['worker'], 0, '', '')

    monkeypatch.setattr(final_audio, '_run_worker', resume_worker)
    monkeypatch.setattr(final_audio, '_judge', lambda facts, windows: {'summary': 'Reviewed.', 'issues': []})
    resumed = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert resumed['complete'] and resumed['passed']
    assert len(decode_calls) == 1
    assert len(calls[1]) == 2


def test_missing_windows_and_bad_judgment_never_pass(tmp_path, monkeypatch):
    ep = tmp_path / 'episode'
    _episode(ep)
    movie = _movie(tmp_path / 'final.mp4')
    runtime = _runtime(tmp_path)
    _fake_worker(monkeypatch, fail_after_first=True)
    result = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert not result['complete'] and not result['passed']

    # Supply the missing rows, then make the task-selected text judge return an invalid shape.
    def fill_worker(runtime, root, job_path):
        job = json.loads(Path(job_path).read_text())
        for row in job['jobs']:
            Path(row['output']).write_text(json.dumps({'response': 'some sound'}))
        return subprocess.CompletedProcess(['worker'], 0, '', '')

    monkeypatch.setattr(final_audio, '_run_worker', fill_worker)
    monkeypatch.setattr(final_audio, '_judge', lambda facts, windows: {'summary': 'bad', 'issues': 'bad'})
    result = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert not result['complete'] and not result['passed'] and result['errors']
    review_records = sorted((ep / 'work' / 'final-audio-review').rglob('review_*.json'))
    assert len(review_records) == 1
    bad_record = json.loads(review_records[0].read_text())
    assert bad_record['raw_judgment'] == {'summary': 'bad', 'issues': 'bad'}
    assert bad_record['validation'] == 'rejected'

    monkeypatch.setattr(final_audio, '_judge', lambda facts, windows: {'summary': 'A concrete audio fault.',
        'issues': [{'start': 33, 'end': 35, 'problem': 'A loud impact conflicts with the quiet action.',
                    'evidence': 'Eureka window 1 describes a sharp impact.',
                    'source_basis': 'The authored sound plan calls for quiet.'}]})
    result = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert result['complete'] and not result['passed']
    assert result['issues'][0]['clip_ids'] == ['clip-b']
    assert result['issues'][0]['start'] == 33 and result['issues'][0]['end'] == 35
    assert len(list((ep / 'work' / 'final-audio-review').rglob('review_*.json'))) == 2

    # A valid ending issue outside native clips remains a final-movie global issue.
    report = json.loads((ep / 'thin_media_report.json').read_text())
    report['assembly']['timeline'][-1]['end'] = 60
    (ep / 'thin_media_report.json').write_text(json.dumps(report))
    monkeypatch.setattr(final_audio, '_judge', lambda facts, windows: {'summary': 'Ending problem.',
        'issues': [{'start': 64, 'end': 64.5, 'problem': 'Abrupt audio cutoff.', 'evidence': 'The final window cuts off.',
                    'source_basis': 'The authored ending specifies a sustained room tone.'}]})
    result = final_audio.review_audio(tmp_path, ep, movie, runtime=runtime)
    assert result['complete'] and not result['passed']
    assert result['issues'][0]['clip_ids'] == []
    assert result['issues'][0]['scope'] == 'final_movie_global'


def test_config_failure_is_unfinished(tmp_path):
    ep = tmp_path / 'episode'
    _episode(ep)
    movie = _movie(tmp_path / 'final.mp4')
    result = final_audio.review_audio(tmp_path, ep, movie, runtime={})
    assert not result['complete'] and not result['passed'] and result['errors']
