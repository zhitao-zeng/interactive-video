import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from test_first_take_stream import case
from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
from novel_manga.util import atomic_write_json, read_json


def test_images_overlap_and_fast_clip_is_adopted_before_slow_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS', '2')
    ctx, _ = case(tmp_path, monkeypatch)
    owner = threading.get_ident()
    slow_started = threading.Event()
    fast_accepted = threading.Event()
    queued = []
    active = 0
    maximum = 0
    lock = threading.Lock()

    def build(ctx, row, path):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        if row['clip_id'] == 'clip_01':
            slow_started.set()
            assert fast_accepted.wait(3), 'ready sibling must not wait for clip_01'
        else:
            assert slow_started.wait(3)
        Image.new('RGB', (8, 16)).save(path)
        with lock:
            active -= 1

    def accepted(cid):
        assert threading.get_ident() == owner
        frame = next(c for c in read_json(ctx.episode_dir/'clip_plan.json')['clips'] if c['clip_id'] == cid)['first_frame']
        assert (ctx.novel_dir/frame['path']).parent.joinpath('accepted.json').is_file()
        queued.append(cid)
        if cid == 'clip_02':
            fast_accepted.set()

    result = FirstFrameOperations(ctx, builder=build,
        checker=lambda *a: {'consistent': True, 'problems': []}, on_accepted=accepted).prepare({})
    assert result['ok'] and maximum == 2
    assert queued == ['clip_02', 'clip_01']
    assert result['changed'] == ['clip_01', 'clip_02']


def test_error_drains_started_images_and_does_not_allocate_unstarted_clip(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS', '2')
    ctx, clips = case(tmp_path, monkeypatch)
    third = copy.deepcopy(clips[1]); third['clip_id'] = 'clip_03'
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [*clips, third]})
    draft = read_json(ctx.work_dir/'output/first-frames.json')
    row = copy.deepcopy(draft['frames'][1]); row['clip_id'] = 'clip_03'
    draft['frames'].append(row)
    atomic_write_json(ctx.work_dir/'output/first-frames.json', draft)
    started = threading.Event()
    failed = threading.Event()
    queued = []

    def build(ctx, row, path):
        if row['clip_id'] == 'clip_01':
            started.set()
            assert failed.wait(3)
        else:
            assert row['clip_id'] == 'clip_02'
            assert started.wait(3)
        Image.new('RGB', (8, 16)).save(path)
        atomic_write_json(path.with_suffix('.png.task.json'), {'task_id': row['clip_id']})

    def check(ctx, path, row, contract):
        if row['clip_id'] == 'clip_02':
            failed.set()
            raise RuntimeError('review service 503')
        return {'consistent': True, 'problems': []}

    operation = FirstFrameOperations(ctx, builder=build, checker=check, on_accepted=queued.append)
    with pytest.raises(RuntimeError, match='503'):
        operation.prepare({})
    assert queued == ['clip_01']
    assert operation.partial_outcome['clips']['clip_01']['ok']
    assert '503' in operation.partial_outcome['clips']['clip_02']['error']
    root = ctx.novel_dir/'first_frames/book_1'
    assert not (root/'clip_03/candidate_01').exists()
    task = root/'clip_02/candidate_01/opening.png.task.json'
    assert read_json(task)['task_id'] == 'clip_02'
    assert read_json(ctx.work_dir/'input/clip_plan.json')['clips'][0]['first_frame']
    # Resuming the same candidates reviews the existing image, without a new builder call.
    def resume_build(ctx, row, path):
        assert row['clip_id'] == 'clip_03'
        Image.new('RGB', (8, 16)).save(path)
    result = FirstFrameOperations(ctx, builder=resume_build,
        checker=lambda *a: {'consistent': True, 'problems': []}).prepare({})
    assert result['ok'] and read_json(task)['task_id'] == 'clip_02'
    assert all(len(list((root/cid).glob('candidate_*'))) == 1 for cid in ('clip_01', 'clip_02', 'clip_03'))


@pytest.mark.parametrize('workers', ['0', '9', 'bad'])
def test_image_worker_setting_is_validated_before_allocating_candidates(tmp_path, monkeypatch, workers):
    ctx, _ = case(tmp_path, monkeypatch)
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS', workers)
    with pytest.raises(ValueError):
        FirstFrameOperations(ctx).prepare({})
    assert not (ctx.novel_dir/'first_frames').exists()


def test_error_finishing_during_adoption_stops_next_dispatch(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import first_frames
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS', '2')
    ctx, clips = case(tmp_path, monkeypatch)
    third=copy.deepcopy(clips[1]);third['clip_id']='clip_03'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[*clips,third]})
    draft=read_json(ctx.work_dir/'output/first-frames.json')
    row=copy.deepcopy(draft['frames'][1]);row['clip_id']='clip_03';draft['frames'].append(row)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',draft)
    second_started=threading.Event();adopting=threading.Event();futures={}
    class Pool(ThreadPoolExecutor):
        def submit(self,fn,*task):
            future=super().submit(fn,*task)
            futures[task[0]['clip_id']]=future
            return future
    monkeypatch.setattr(first_frames,'ThreadPoolExecutor',Pool)
    def build(ctx,row,path):
        if row['clip_id']=='clip_01':assert second_started.wait(3)
        if row['clip_id']=='clip_02':second_started.set()
        Image.new('RGB',(8,16)).save(path)
    def check(ctx,path,row,contract):
        if row['clip_id']=='clip_02':
            assert adopting.wait(3)
            raise RuntimeError('review 503 during adoption')
        return {'consistent':True,'problems':[]}
    def accepted(cid):
        if cid=='clip_01':
            adopting.set()
            with pytest.raises(RuntimeError,match='503'):
                futures['clip_02'].result(timeout=3)
    operation=FirstFrameOperations(ctx,builder=build,checker=check,on_accepted=accepted)
    with pytest.raises(RuntimeError,match='503'):operation.prepare({})
    assert not (ctx.novel_dir/'first_frames/book_1/clip_03/candidate_01').exists()
