"""All movie evidence survives bounded parallel group review and failures."""
import json
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import final_cut, independent
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def movie(tmp_path, monkeypatch):
    directory = tmp_path/'book'/'book_1'
    directory.mkdir(parents=True)
    video = directory/'movie.mp4'
    video.write_bytes(b'current movie')
    clips = [{'clip_id':f'c{i:02d}', 'kind':'video', 'shot_indexes':[i+1], 'references':[]}
             for i in range(45)]
    plan = {'clips':clips}
    script = {'shots':[{'index':i+1} for i in range(45)]}
    atomic_write_json(directory/'clip_plan.json', plan)
    atomic_write_json(directory/'chapter_script.json', script)
    media = {'clips':[{'clip_id':c['clip_id'], 'selected':{'video':str(video)}} for c in clips],
             'assembly':{'final_video':str(video), 'timeline':[
                 {'unit_id':c['clip_id'], 'start':9*i, 'end':9*(i+1)} for i, c in enumerate(clips)]}}
    runner = SimpleNamespace(context=SimpleNamespace(episode_dir=directory, clip_plan=plan, script=script, bible=None))
    monkeypatch.setattr(final_cut.subprocess, 'check_output', lambda *a, **k:json.dumps({'format':{'duration':405}}))
    monkeypatch.setattr(final_cut.subprocess, 'run', lambda cmd, **k:Path(cmd[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model, 'image_part', lambda path, *a:{'type':'text', 'text':'image:'+path.stem})
    return runner, media, directory


def group_number(parts):
    return int(parts[1]['text'].rsplit('-', 1)[-1])//(final_cut.MAX_IMAGES-2)+1


def answer(group):
    return {'passed':True, 'observations':[f'group {group} observed'], 'issues':[]}


def test_all_135_frames_and_23_overlapping_groups_run_at_most_two_at_once(movie, monkeypatch):
    runner, media, directory = movie
    first_started, second_finished = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0
    completed, supplied = [], {}
    main_thread = threading.get_ident()
    writer = final_cut.atomic_write_json

    def save(path, value):
        assert threading.get_ident() == main_thread
        writer(path, value)

    def judge(parts, schema, **kwargs):
        nonlocal active, peak
        group = group_number(parts)
        indexes = [int(p['text'].rsplit('-', 1)[-1]) for p in parts if p.get('text', '').startswith('image:')]
        with lock:
            active += 1
            peak = max(peak, active)
            supplied[group] = indexes
        try:
            assert 0 < kwargs['timeout'] <= 300
            if group == 1:
                first_started.set()
                assert second_finished.wait(2), 'second group never overlapped the first'
            elif group == 2:
                assert first_started.wait(2)
                with lock:
                    completed.append(group)
                second_finished.set()
                return answer(group)
            with lock:
                completed.append(group)
            return answer(group)
        finally:
            with lock:
                active -= 1

    combined = []
    def combine(directory, final):
        assert active == 0 and len(final['groups']) == 23
        combined.append(True)
        return final

    monkeypatch.setattr(final_cut, 'atomic_write_json', save)
    monkeypatch.setattr(final_cut.model, 'ask_json', judge)
    monkeypatch.setattr(independent, 'combine', combine)
    result = final_cut.review_assembly(runner, media)
    assert result['complete'] and result['passed'] and not result['errors']
    assert peak == 2 and completed[:2] == [2, 1]
    assert len(result['frames']) == 135 and len(supplied) == 23
    for group, start in enumerate(range(0, 135, final_cut.MAX_IMAGES-2), 1):
        assert supplied[group] == list(range(start, min(start+final_cut.MAX_IMAGES, 135)))
    assert [r['group'] for r in result['responses']] == list(range(1, 24))
    assert [r['observations'] for r in result['groups']] == [answer(g)['observations'] for g in range(1, 24)]
    assert combined == [True]
    assert read_json(directory/'final_cut_review.json') == result


def test_immediate_failure_does_not_admit_an_unstarted_group(movie, monkeypatch):
    runner, media, _ = movie
    calls = []

    class ImmediatePool:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def submit(self, function, *args):
            # Deterministically represent the first worker finishing before submit returns.
            future = Future()
            future.set_result(function(*args))
            return future

    def judge(parts, *args, **kwargs):
        calls.append(group_number(parts))
        raise TimeoutError('first group immediately unavailable')

    monkeypatch.setattr(final_cut, 'ThreadPoolExecutor', ImmediatePool)
    monkeypatch.setattr(final_cut.model, 'ask_json', judge)
    result = final_cut.review_assembly(runner, media)
    assert calls == [1]
    assert not result['complete'] and not result['passed'] and result['errors']


def test_parallel_groups_still_share_the_existing_model_resource_slot(movie, monkeypatch, tmp_path):
    from novel_manga.llm import resources, transport
    runner, media, _ = movie
    base = 'http://movie-review.invalid/v1'
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', '')
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([{'base':base, 'slots':1}]))
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(tmp_path/'model-slots'))
    monkeypatch.setattr(final_cut.model, 'endpoint_key', lambda:'')
    monkeypatch.setattr(final_cut.model, 'endpoint_order', lambda *a:[base])
    monkeypatch.setattr(transport, 'streaming_wanted', lambda:False)
    monkeypatch.setattr(transport, '_meter', lambda *a:None)
    original_slot = resources.model_slot
    two_waiting = threading.Event()
    lock = threading.Lock()
    admitted = active = peak = 0

    @contextmanager
    def slot(url):
        nonlocal admitted
        with lock:
            admitted += 1
            if admitted == 2:two_waiting.set()
        with original_slot(url):
            yield

    def respond(client, url, headers, payload, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            assert two_waiting.wait(2), 'second group never reached model admission'
            group = group_number(payload['messages'][0]['content'])
            body = {'choices':[{'message':{'content':json.dumps(answer(group))}, 'finish_reason':'stop'}]}
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:body)
        finally:
            with lock:active -= 1

    monkeypatch.setattr(transport, 'model_slot', slot)
    monkeypatch.setattr(transport, 'send_json', respond)
    result = final_cut.review_assembly(runner, media)
    assert result['complete'] and result['passed']
    assert admitted == 23 and peak == 1


@pytest.mark.parametrize('failed_group', [1, 2])
@pytest.mark.parametrize('failure', ['service', 'protocol', 'correction_service'])
def test_failure_drains_started_groups_preserving_raw_without_starting_more(movie, monkeypatch, failed_group, failure):
    runner, media, directory = movie
    both_started = threading.Barrier(2)
    failed, sibling_finished = threading.Event(), threading.Event()
    calls, siblings = [], []
    lock = threading.Lock()
    bad = {'passed':False, 'observations':['raw contradictory response'], 'issues':[]}

    def judge(parts, schema, **kwargs):
        group = group_number(parts)
        with lock:
            calls.append(group)
            revision = calls.count(group)
        assert group in {1, 2}, 'new group admitted after failure'
        if revision == 1:
            both_started.wait(2)
        if group == failed_group:
            if failure == 'service':
                failed.set()
                raise TimeoutError('review provider unavailable')
            if revision == 2:
                assert '格式校验失败' in parts[-1]['text']
                failed.set()
                if failure == 'correction_service':
                    raise TimeoutError('correction provider unavailable')
            return bad.copy()
        assert failed.wait(2)
        siblings.append(group)
        sibling_finished.set()
        return answer(group)

    def combine(directory, final):
        assert sibling_finished.is_set()
        assert not final['complete'] and not final['passed']
        return final

    monkeypatch.setattr(final_cut.model, 'ask_json', judge)
    monkeypatch.setattr(independent, 'combine', combine)
    result = final_cut.review_assembly(runner, media)
    assert not result['complete'] and not result['passed'] and result['errors']
    assert set(calls) == {1, 2} and siblings == [3-failed_group]
    assert result['groups'] == [answer(3-failed_group)]
    assert [r['group'] for r in result['responses']] == sorted(r['group'] for r in result['responses'])
    if failure in {'protocol', 'correction_service'}:
        raw = [r for r in result['responses'] if r['group'] == failed_group]
        assert len(raw) == (2 if failure == 'protocol' else 1)
        assert all(r['answer'] == bad and r['protocol_error'] for r in raw)
    else:
        assert len(result['responses']) == 1
    assert read_json(directory/'final_cut_review.json') == result
