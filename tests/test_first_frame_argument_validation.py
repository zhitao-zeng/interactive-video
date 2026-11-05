import pytest

from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


def test_authored_conflicts_do_not_hide_other_preflight_errors_in_same_batch(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    for clip in clips:
        clip['shots'][0]['opening_frame']={'image_prompt':'Approved opening at an empty pool',
            'visible_characters':['A'],'reference_subjects':['A']}
    plan_path=ctx.episode_dir/'clip_plan.json';atomic_write_json(plan_path,{'clips':clips})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    for row in draft['frames']:
        row['source_quote']='Preserve the approved source dialogue and action.'
        row['image_references']=['unregistered.png']
        row.pop('prompt_h3',None)
        row['h3']={'shots':['The pool is still.'],'soundscape':'中文环境声'}
    atomic_write_json(draft_path,draft)
    protected={p:p.read_bytes() for p in (draft_path,plan_path)}
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('preflight must spend no image capacity'))
    with pytest.raises(ValueError):operation.prepare({})
    result=operation.partial_outcome
    assert result['validation_targets']==['clip_01','clip_02']
    assert result['validated_clips']==[]
    for cid in result['validation_targets']:
        assert set(result['invalid_fields'][cid])=={'image_prompt','visible_characters','source_quote','image_references','h3'}
        errors=' '.join(result['argument_errors'][cid])
        assert 'input repair' in errors and 'source_quote must quote' in errors
        assert 'empty pool' in errors and 'registered source cards' in errors and 'English' in errors
    assert all(p.read_bytes()==value for p,value in protected.items())
    assert not (ctx.novel_dir/'first_frames').exists()


def test_bad_row_field_types_still_report_independent_quote_and_other_rows(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    draft['frames'][0].update(image_references=None,source_quote='Not an exact quote.')
    draft['frames'][1]['source_quote']='Another summary.'
    atomic_write_json(draft_path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid batch'))
    with pytest.raises(ValueError):operation.prepare({})
    assert set(operation.partial_outcome['invalid_fields']['clip_01'])=={'image_references','source_quote'}
    assert operation.partial_outcome['invalid_fields']['clip_02']==['source_quote']
    assert operation.partial_outcome['validation_targets']==['clip_01','clip_02']


def test_correcting_authored_fields_leaves_only_quote_failure_without_rewriting_source(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    for clip in clips:
        clip['shots'][0]['opening_frame']={'image_prompt':'Approved opening at an empty pool',
            'visible_characters':[],'reference_subjects':[]}
    plan_path=ctx.episode_dir/'clip_plan.json';atomic_write_json(plan_path,{'clips':clips})
    before=plan_path.read_bytes()
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    for row in draft['frames']:
        row.pop('image_prompt');row.pop('visible_characters')
        row['source_quote']='Keep the original source.'
    atomic_write_json(draft_path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('quote failure still blocks'))
    with pytest.raises(ValueError):operation.prepare({})
    assert operation.partial_outcome['invalid_fields']=={'clip_01':['source_quote'],'clip_02':['source_quote']}
    assert operation.partial_outcome['validated_clips']==[]
    assert plan_path.read_bytes()==before


def test_duplicate_references_and_missing_h3_reuse_current_valid_request(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    clips[0]['prompt_h3']='existing complete H3 request'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0]
    row['image_references']=['scene.png','scene.png']
    row.pop('h3',None);row.pop('prompt_h3',None)
    atomic_write_json(draft_path,{'frames':[row]})
    captured=[]
    def build(c,r,p):captured.append(r);Image.new('RGB',(8,16)).save(p)
    result=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok']
    assert captured[0]['image_references']==['scene.png']
    assert captured[0]['prompt_h3']=='existing complete H3 request'


def test_missing_source_quote_binds_exact_clip_source(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0];row.pop('source_quote')
    atomic_write_json(draft_path,{'frames':[row]})
    captured=[]
    def build(c,r,p):captured.append(r);Image.new('RGB',(8,16)).save(p)
    result=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok'] and captured[0]['source_quote']=='empty pool'


def test_missing_source_quote_cannot_bind_empty_source(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    clips[0]['segment_ids']=['missing']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0];row.pop('source_quote')
    atomic_write_json(draft_path,{'frames':[row]})
    with pytest.raises(ValueError,match='source_quote requires text'):
        FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('empty source must not generate')).prepare({})


def test_missing_h3_without_existing_valid_request_still_blocks(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    clips[0].pop('prompt_h3',None)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0];row.pop('h3',None);row.pop('prompt_h3',None)
    atomic_write_json(draft_path,{'frames':[row]})
    with pytest.raises(ValueError,match='H3 directions require'):
        FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid request must not generate')).prepare({})
