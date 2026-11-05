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


def test_partial_preflight_reports_validated_rows_without_generating_images(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch);path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    draft['frames'][0].pop('extra_references')  # optional, no extra video card selected
    draft['frames'][1]['source_quote']='not the source'
    atomic_write_json(path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('not admitted'))
    with pytest.raises(ValueError):operation.prepare({})
    assert operation.partial_outcome['validated_clips']==['clip_01']
    assert operation.partial_outcome['validation_targets']==['clip_01','clip_02']
    assert 'no images generated' in operation.partial_outcome['validation_scope']


def test_review_separates_opening_from_camera_endpoint_without_dropping_initial_geometry(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import check
    from novel_manga.media.first_frames import current_contract
    import json
    ctx,clips=case(tmp_path,monkeypatch)
    clip=clips[0]
    clip['shots'][0].update(start_state='旅人仍被扶住，长杆尚未触水。',
        camera='近景，侧前方起拍，人物与杆保持同轴。 | actor_path: 扶住 → 松手; endpoint: 松手，长杆触水; hold: 停住。',
        shot_scale='近景',wears={'旅人':'灰色外套'})
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    row['image_prompt']='时间零点旅人被扶住，准备接杆。'
    contract=current_contract(ctx,clip,row['image_references'])
    captured=[]
    finding={'observed':'外套为红色','expected':'灰色外套','basis':['opening.opening.wears'],
        'relation':'contradiction','reason':'当前衣服颜色与开场要求不一致'}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda parts,*a,**k:captured.append(parts) or {'observations':[],'findings':[finding]})
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda p,*a:{'path':str(p)})
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:None)
    result=check(ctx,ctx.novel_dir/'opening.png',row,contract)
    prompt=captured[0][0]['text'];instructions,payload=prompt.split('\n',1)
    assert '只审时间零点t=0' in instructions
    assert 'actor_path箭头右侧' in instructions and '不是首帧必须完成的动作' in instructions
    assert '不是独立JSON字段' in instructions
    assert json.loads(payload)['opening']['opening']==contract['opening']
    assert json.loads(payload)['image_prompt']==row['image_prompt']
    assert [part['path'] for part in captured[0][1:]]==[str(ctx.novel_dir/'opening.png'),str(ctx.novel_dir/'scene.png')]
    assert result['consistent'] is False  # Real opening-state defects still fail.


@pytest.mark.parametrize('relation',['compatible','contradiction'])
def test_changed_review_policy_reuses_full_budget_pixels_and_preserves_old_evidence(tmp_path,monkeypatch,relation):
    import shutil
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames as operations
    ctx,clips=case(tmp_path,monkeypatch)
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    policy=operations.REVIEW_POLICY
    monkeypatch.setattr(operations,'REVIEW_POLICY','first-frame-explicit-relations-v3-appearance-scope')
    old={'observations':['still supporting'],'findings':[{'observed':'still supporting','expected':'already released',
        'basis':['opening.opening.endpoint'],'relation':'contradiction','reason':'later endpoint incorrectly required at t=0'}]}
    first=FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),checker=lambda *a:old).prepare({})
    assert not first['ok']
    root=ctx.novel_dir/'first_frames/book_1/clip_01'
    candidate=root/'candidate_01'
    atomic_write_json(candidate/'opening.png.task.json',{'task_id':'original-paid-image'})
    for number in (2,3):shutil.copytree(candidate,root/f'candidate_{number:02d}')
    candidate=root/'candidate_03'
    protected={p:p.read_bytes() for p in candidate.rglob('*') if p.is_file()}
    monkeypatch.setattr(operations,'REVIEW_POLICY',policy)
    status=operations.status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))['clips']['clip_01']
    assert status['remaining_image_candidates']==0
    assert status['review_policy_changed']['current']==policy
    assert "reuse_candidate:'candidate_03'" in status['next_action']
    assert 'single-row frames request' in status['next_action']
    row['reuse_candidate']='candidate_03'
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    calls=[]
    new={'observations':['current pixels'],'findings':[{'observed':'current pixels','expected':'opening state',
        'basis':['opening.opening.start_state'],'relation':relation,'reason':'current source compared at t=0'}]}
    def checker(*args):calls.append(args);return new
    op=lambda:FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no new image capacity'),checker=checker).prepare({})
    result=op()
    assert result['ok'] is (relation=='compatible')
    assert len(calls)==1 and len(list(root.glob('candidate_*')))==3
    assert all(p.read_bytes()==value for p,value in protected.items() if p.name!='rejected.json')
    assert read_json(candidate/'reviews/review_001.json')['raw']==old
    assert read_json(candidate/'reviews/review_002.json')['raw']==new
    assert op()['ok'] is (relation=='compatible') and len(calls)==1


def test_changed_review_policy_does_not_invalidate_current_accepted_openings(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames as operations
    from novel_manga.media.first_frames import require_current
    ctx,_=case(tmp_path,monkeypatch)
    policy=operations.REVIEW_POLICY
    monkeypatch.setattr(operations,'REVIEW_POLICY','first-frame-explicit-relations-v3-appearance-scope')
    FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
        checker=lambda *a:{'observations':[],'findings':[]}).prepare({})
    monkeypatch.setattr(operations,'REVIEW_POLICY',policy)
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    status=operations.status(ctx,plan)
    for clip in plan['clips']:
        require_current(ctx,clip)
        assert status['clips'][clip['clip_id']]['current']
        assert 'review_policy_changed' not in status['clips'][clip['clip_id']]
