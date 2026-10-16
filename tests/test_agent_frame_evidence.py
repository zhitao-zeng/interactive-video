"""Candidate-only export regression; no decoder, model, container or production writes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.review.storage import take_identity


CANDIDATE = Path(__file__).resolve().parents[1]


@pytest.fixture
def agent(monkeypatch):
    path = CANDIDATE/'src/novel_manga/application/repair/agent.py'
    spec = importlib.util.spec_from_file_location('agent_frame_evidence_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'project_root', lambda: CANDIDATE)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def setup_episode(tmp_path):
    directory = tmp_path/'book/book_1'
    current = directory/'work/clips/clip_09/attempt_01/clip.mp4'
    historical = directory/'repair_history/takes/clip_09/old/clip.mp4'
    for index, video in enumerate((current, historical)):
        video.parent.mkdir(parents=True)
        video.write_bytes(f'fake take {index}'.encode())
        write(video.parent/'request.json', {'prompt': f'request {index}'})
        write(video.parent/'asr.json', {'hypothesis': f'ASR {index}'})
    clip = {'clip_id': 'clip_09', 'repair_take': 2, 'references': [], 'shot_indexes': [1],
            'prompt_h3': '\n\n'.join(f'{name}:\nNone.' for name in (
                'subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                'overall_soundscape', 'non_diegetic_music'))}
    media = {'clips': [{'clip_id': 'clip_09', 'selected': {'video': str(current)}}]}
    write(directory/'thin_media_report.json', media)
    write(directory/'chapter_script.json', {'shots': [{'index': 1, 'event': 'original event'}]})
    frames = directory/'work/review/clip_09'
    frames.mkdir(parents=True)
    for i in range(1, 6):
        (frames/f'frame_{i}.jpg').write_bytes(f'existing frame {i}'.encode())
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    write(frames/'frame-evidence.json', {'video': str(current), 'take': take_identity(current),
        'frames': [{'path': str(frames/f'frame_{i}.jpg'), 'seconds': i} for i in range(1, 4)]})
    return directory, current, historical, clip, media


def mock_host_frames(agent, monkeypatch):
    calls = []
    def extract(video, target, count):
        calls.append((video, target, count))
        target.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(1, count + 1):
            frame = target/f'frame_{i}.jpg'
            frame.write_bytes(f'extracted frame {i}'.encode())
            frames.append(frame)
        return frames
    monkeypatch.setattr(agent, 'clip_frames', extract)
    return calls




def test_three_frame_review_does_not_reuse_a_stale_fourth_frame_with_plausible_mtime(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    stale = directory/'work/review/clip_09/frame_4.jpg'
    stale.write_bytes(b'frame from a different earlier review')
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    assert current.stat().st_mtime_ns <= stale.stat().st_mtime_ns <= (directory/'episode_review.json').stat().st_mtime_ns
    monkeypatch.setattr(agent, 'clip_frames', lambda *a: pytest.fail('valid three-frame review should be reused'))
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, tmp_path/'run/input/take-1/frames')
    assert provenance == 'same_take_review_report_supported'
    assert [p.name for p in frames] == ['frame_1.jpg', 'frame_2.jpg', 'frame_3.jpg']


def test_same_review_exports_the_late_fourth_action_frame(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    cache = directory/'work/review/clip_09'
    write(cache/'frame-evidence.json', {'video': str(current), 'take': take_identity(current),
        'frames': [{'path': str(cache/f'frame_{i}.jpg'), 'seconds': i * 3} for i in range(1, 5)]})
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    monkeypatch.setattr(agent, 'clip_frames', lambda *a: pytest.fail('use all four supported frames'))
    target = tmp_path/'run/input/frames'
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, target)
    assert provenance == 'same_take_review_report_supported'
    assert [p.name for p in frames] == [f'frame_{i}.jpg' for i in range(1, 5)]
    assert json.loads((target/'frame-evidence.json').read_text())['frames'][-1]['seconds'] == 12


def test_legacy_review_without_frame_list_extracts_tail_instead_of_guessing(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    (directory/'work/review/clip_09/frame-evidence.json').unlink()
    calls = mock_host_frames(agent, monkeypatch)
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, tmp_path/'input/frames')
    assert provenance == 'host_uniform_10_to_90_percent'
    assert len(frames) == 4 and len(calls) == 1


@pytest.mark.parametrize('problem', ['different_take', 'missing_frame', 'old_frame', 'postmix_video', 'review_error'])
def test_unconfirmed_review_frames_are_extracted_only_inside_run_copy(tmp_path, monkeypatch, agent, problem):
    directory, current, _, _, _ = setup_episode(tmp_path)
    review_path = directory/'episode_review.json'
    review = json.loads(review_path.read_text())
    row = review['clips']['clip_09']
    cache = directory/'work/review/clip_09'
    if problem == 'different_take':
        row['take'][0] += 1
    elif problem == 'missing_frame':
        (cache/'frame_3.jpg').unlink()
    elif problem == 'old_frame':
        old = current.stat().st_mtime_ns - 1_000_000
        os.utime(cache/'frame_1.jpg', ns=(old, old))
    elif problem == 'postmix_video':
        row['video'] = str(current.parent/'postmix/clip.mp4')
    else:
        row['severity'] = 'review_error'
    write(review_path, review)
    cached_before = {p: p.read_bytes() for p in cache.iterdir()}
    calls = mock_host_frames(agent, monkeypatch)
    target = tmp_path/'sandbox-runs/run/input/take-1/frames'
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, target)
    assert provenance == 'host_uniform_10_to_90_percent'
    assert calls == [(current, target, 4)] and len(frames) == 4
    assert {p: p.read_bytes() for p in cache.iterdir()} == cached_before


def test_propose_hands_current_and_history_frames_to_sandbox(tmp_path,monkeypatch,agent):
    directory,current,historical,clip,media=setup_episode(tmp_path)
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    mock_host_frames(agent,monkeypatch)
    config={'runs_root':str(tmp_path/'runs')}
    def execute(name,**kwargs):
        work=Path(config['runs_root'])/name
        evidence=json.loads((work/'input/evidence.json').read_text())
        assert kwargs['timeout']==600
        takes=evidence['takes']
        assert [t['current'] for t in takes]==[True,False]
        assert evidence['primary_reading']['current_frames'] == takes[0]['frames']
        assert evidence['primary_reading']['request'] == 'input/prompt.txt'
        assert len(takes[0]['frames'])==3
        assert takes[0]['frames'][-1]['seconds']==3
        assert len(takes[1]['frames'])==4
        for t in takes:
            assert all((work/f['path']).is_file() for f in t['frames'])
            assert (work/t['request']).is_file() and (work/t['asr']).is_file()
        import subprocess,sys
        answer=json.loads(subprocess.check_output([sys.executable,str(work/'tools/video_frames.py'),'take-1','--seconds','2.2']))
        assert answer['frames'][0]['seconds']==2
        assert answer['selection']=='nearest supplied frame'
        write(work/'output/decision.json',{'action':'stop','reason':'fixture','evidence':['input/evidence.json']})
        return SimpleNamespace(ok=True,produced=['decision.json'],attempt='test')
    monkeypatch.setattr(agent,'run_agent',execute)
    assert agent.propose(directory,clip,{},config=config)['action']=='stop'
    assert all(p.read_bytes()==data for p,data in before.items())


def test_export_skips_only_the_archived_copy_of_the_same_managed_render(tmp_path, monkeypatch, agent):
    directory, current, historical, clip, _ = setup_episode(tmp_path)
    marker = {'clip_id': clip['clip_id'], 'take': 3}
    for video in (current, historical):
        write(video.parent/'request.json', {'prompt': 'current request', 'managed_generation': marker})
    previous = directory/'repair_history/takes/clip_09/previous/clip.mp4'
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b'previous independent take')
    write(previous.parent/'request.json', {'prompt': 'previous request',
                                         'managed_generation': {**marker, 'take': 2}})
    write(previous.parent/'asr.json', {'hypothesis': 'previous ASR'})
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    calls = mock_host_frames(agent, monkeypatch)
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(name, **kwargs):
        work = Path(config['runs_root'])/name
        evidence = json.loads((work/'input/evidence.json').read_text())
        assert evidence['duplicate_generation_copies_skipped'] == 1
        assert [t['current'] for t in evidence['takes']] == [True, False]
        assert [(work/t['path']/'clip.mp4').read_bytes() for t in evidence['takes']] == [
            current.read_bytes(), previous.read_bytes()]
        assert len(evidence['primary_reading']['current_frames']) == 3
        assert len(evidence['takes'][1]['frames']) == 4
        assert kwargs['timeout'] == 600
        write(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture',
                                          'evidence': ['input/evidence.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], attempt='test')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert agent.propose(directory, clip, {}, config=config)['action'] == 'stop'
    assert len(calls) == 1 and calls[0][0] == previous
    assert all(p.read_bytes() == data for p, data in before.items())


def test_unmarked_historical_takes_remain_available(tmp_path, agent):
    directory, current, historical, _, _ = setup_episode(tmp_path)
    write(current.parent/'request.json', {'managed_generation': {'take': 1}})
    videos, skipped = agent.unique_takes([current, historical])
    assert videos == [current, historical] and skipped == 0


def test_task_repair_options_preserve_the_callers_config_and_full_evidence(tmp_path, monkeypatch, agent):
    directory, _, _, clip, _ = setup_episode(tmp_path)
    config = {'runs_root': str(tmp_path/'runs'), 'extra_body': {'request_option': 'preserved'},
              'repair_extra_body': {'thinking': {'type': 'disabled'}}}
    before = copy.deepcopy(config)
    mock_host_frames(agent, monkeypatch)
    def execute(name, **kwargs):
        assert kwargs['config']['extra_body'] == {
            'request_option': 'preserved', 'thinking': {'type': 'disabled'}}
        assert kwargs['timeout'] == 600
        work = Path(config['runs_root'])/name
        evidence = json.loads((work/'input/evidence.json').read_text())
        assert len(evidence['takes']) == 2
        prompt = (work/'prompt.txt').read_text()
        assert 'input/primary.json' in prompt and 'complete prompt.txt together in one tool turn' in prompt
        write(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture',
                                          'evidence': ['input/evidence.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], attempt='test')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert agent.propose(directory, clip, {}, config=config)['action'] == 'stop'
    assert config == before


def test_primary_input_exposes_actual_current_frames_before_full_episode_evidence(tmp_path, monkeypatch, agent):
    directory, current, _, clip, _ = setup_episode(tmp_path)
    clip['segment_ids'] = ['seg_1']
    shots = [{'index': 1, 'segment_id': 'seg_1', 'event': 'current event'},
             {'index': 2, 'segment_id': 'seg_2', 'event': 'next event'},
             {'index': 3, 'segment_id': 'seg_3', 'event': 'unrelated event'}]
    segments = [{'segment_id': f'seg_{i}', 'text': f'original passage {i}'} for i in range(1, 4)]
    write(directory/'chapter_script.json', {'shots': shots})
    write(directory/'segments.json', segments)
    for role, path in [('character', 'character.jpeg'), ('voice', 'voice.wav')]:
        (directory.parent/path).write_bytes(f'original {role} reference'.encode())
        clip['references'].append({'role': role, 'path': path, 'name': 'person'})
    config = {'runs_root': str(tmp_path/'runs')}
    verdict = {'severity': 'fail', 'video': str(current), 'take': take_identity(current)}
    mock_host_frames(agent, monkeypatch)
    def execute(name, **kwargs):
        work = Path(config['runs_root'])/name
        primary = json.loads((work/'input/primary.json').read_text())
        evidence = json.loads((work/'input/evidence.json').read_text())
        assert next(iter(primary)) == 'current_frames'
        assert primary['current_frames'] == evidence['takes'][0]['frames']
        assert primary['current_frames'][-1]['seconds'] == 3
        for row in primary['current_frames']:
            assert (work/row['path']).read_bytes() == (
                directory/'work/review/clip_09'/Path(row['path']).name).read_bytes()
        assert primary['current_request'] == evidence['takes'][0]['request']
        assert (work/primary['current_request']).read_bytes() == (current.parent/'request.json').read_bytes()
        assert (work/primary['request']).read_text() == clip['prompt_h3']
        assert primary['shots_and_neighbours'] == shots[:2]
        assert primary['source_passages'] == segments[:2]
        assert primary['references'] == evidence['references']
        assert primary['verdict'] == verdict
        assert evidence['segments'] == segments and len(evidence['takes']) == 2
        prompt = (work/'prompt.txt').read_text()
        assert prompt.startswith('In your first parallel Read tool batch, read input/primary.json and open these current-take JPEGs: ')
        assert all('/work/'+row['path'] in prompt for row in primary['current_frames'])
        assert 'evidence.json retains complete history' in prompt
        write(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture',
                                          'evidence': ['input/primary.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], attempt='test')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert agent.propose(directory, clip, verdict, config=config)['action'] == 'stop'



def test_romance_package_and_approved_editorial_are_real_sandbox_inputs(tmp_path, monkeypatch, agent):
    directory, _, _, clip, _ = setup_episode(tmp_path)
    original = tmp_path/'original-package/script.json'
    editorial = tmp_path/'approved-editorial.md'
    write(original, {'fact': 'four guests and an undecided ending'})
    editorial.write_text('Approved cold open and chronological reunion.\n')
    write(directory/'source_trace.json', {
        'original_package': str(original), 'approved_editorial': str(editorial)})
    mock_host_frames(agent, monkeypatch)
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(name, **kwargs):
        work = Path(config['runs_root'])/name
        evidence = json.loads((work/'input/evidence.json').read_text())
        for key, source in [('original_package', original), ('approved_editorial', editorial)]:
            target = work/'input/source_documents'/(key+source.suffix)
            assert target.read_bytes() == source.read_bytes()
            assert str(target.relative_to(work)) in evidence['source_documents']
        write(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture',
                                          'evidence': ['input/evidence.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], attempt='test')
    monkeypatch.setattr(agent, 'run_agent', execute)
    assert agent.propose(directory, clip, {}, config=config)['action'] == 'stop'


def test_unselected_failed_take_is_exported_from_current_verdict(tmp_path, monkeypatch, agent):
    directory, current, historical, clip, _ = setup_episode(tmp_path)
    write(directory/'thin_media_report.json', {'clips': [
        {'clip_id': clip['clip_id'], 'selected': None, 'attempts': [
            {'video': str(current), 'passed': False, 'issues': ['excess_unplanned_speech']}]}]})
    mock_host_frames(agent, monkeypatch)
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(name, **kwargs):
        work = Path(config['runs_root'])/name
        evidence = json.loads((work/'input/evidence.json').read_text())
        assert [t['current'] for t in evidence['takes']] == [True, False]
        assert (work/evidence['takes'][0]['path']/'clip.mp4').read_bytes() == current.read_bytes()
        assert len(evidence['takes'][0]['frames']) == 3
        assert (work/evidence['takes'][0]['asr']).is_file()
        write(work/'output/decision.json', {'action': 'stop', 'reason': 'fixture',
                                          'evidence': ['input/evidence.json']})
        return SimpleNamespace(ok=True, produced=['decision.json'], attempt='test')
    monkeypatch.setattr(agent, 'run_agent', execute)
    verdict = {'video': str(current), 'take': take_identity(current), 'severity': 'fail'}
    assert agent.propose(directory, clip, verdict, config=config)['action'] == 'stop'


def test_actual_video_fallback_has_readable_images_and_timestamps(tmp_path,agent):
    import subprocess
    from PIL import Image
    video=tmp_path/'actual.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=64x96:r=25:d=2',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
    target=tmp_path/'run/frames'
    frames,origin=agent.provided_frames(tmp_path,'x',video,target)
    assert len(frames)==4 and origin=='host_uniform_10_to_90_percent'
    record=json.loads((target/'frame-evidence.json').read_text())
    assert record['frames'][0]['seconds']==pytest.approx(.2)
    assert record['frames'][-1]['seconds']==pytest.approx(1.8)
    for f in frames:
        with Image.open(f) as image:assert image.height>image.width
