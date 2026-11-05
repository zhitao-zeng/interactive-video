import pytest
from support.first_frame_cases import case
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.agents.episode.first_frames import FirstFrameOperations


def test_rejected_summary_returns_exact_scoped_source_before_any_image_call(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    draft['frames'][0]['source_quote']='A paraphrased description instead of original words'
    atomic_write_json(path,draft);before=path.read_bytes()
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid quote must not spend image budget'))
    with pytest.raises(ValueError) as error:operation.prepare({})
    assert 'clip_01' in str(error.value) and 'empty pool' in str(error.value)
    assert 'segments.json' in str(error.value)
    assert path.read_bytes()==before


def test_reference_and_english_errors_are_reported_together_for_all_rows(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch);path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    for row in draft['frames']:
        row['image_references']=['unregistered-card.png'];row.pop('prompt_h3',None)
        row['h3']={'shots':['The empty pool stays still.'],'soundscape':'鞋底、小石、草叶、狗叫'}
    atomic_write_json(path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid batch must not generate'))
    with pytest.raises(ValueError) as error:operation.prepare({})
    text=str(error.value)
    assert 'clip_01' in text and 'clip_02' in text
    assert 'image_references' in text and 'scene.png' in text
    assert 'h3.soundscape' in text and 'English' in text
    assert not (ctx.novel_dir/'first_frames').exists()
