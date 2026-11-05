from PIL import Image

from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
from novel_manga.util import read_json
from support.first_frame_cases import case


def test_pause_before_dispatch_allocates_no_candidates(tmp_path, monkeypatch):
    ctx, _ = case(tmp_path, monkeypatch)
    pause = tmp_path/'pause-dispatch'
    pause.write_text('pause')
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE', str(pause))
    calls = []
    operation = FirstFrameOperations(ctx, builder=lambda *a: calls.append(a),
                                    checker=lambda *a: {'consistent': True, 'problems': []})
    result = operation.prepare({})
    assert calls == []
    assert result['paused'] is True and result['ok'] is False
    assert result['pending_clips'] == ['clip_01', 'clip_02']
    assert not (ctx.novel_dir/'first_frames').exists()


def test_pause_drains_started_work_and_resume_reuses_its_image(tmp_path, monkeypatch):
    ctx, _ = case(tmp_path, monkeypatch)
    pause = tmp_path/'pause-dispatch'
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE', str(pause))
    monkeypatch.setenv('NOVEL_MEDIA_WORKERS', '1')
    calls = []

    def build(ctx, row, output):
        calls.append(row['clip_id'])
        Image.new('RGB', (8, 16)).save(output)
        if row['clip_id'] == 'clip_01':
            pause.write_text('stop new dispatch; retain completed work')

    operation = FirstFrameOperations(ctx, builder=build,
                                    checker=lambda *a: {'consistent': True, 'problems': []})
    result = operation.prepare({})
    assert calls == ['clip_01']
    assert result['paused'] is True and result['ok'] is False
    assert result['pending_clips'] == ['clip_02']
    assert result['changed'] == ['clip_01']
    root = ctx.novel_dir/'first_frames/book_1'
    accepted = root/'clip_01/candidate_01'
    preserved = {p: p.read_bytes() for p in accepted.rglob('*') if p.is_file()}
    assert not (root/'clip_02').exists()
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']['review']['consistent']

    pause.unlink()
    resumed = operation.prepare({})
    assert resumed['ok'] is True
    assert calls == ['clip_01', 'clip_02']
    assert all(path.read_bytes() == data for path, data in preserved.items())
    assert len(list(root.glob('*/candidate_*'))) == 2
