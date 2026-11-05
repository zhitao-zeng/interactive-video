import pytest
from support.first_frame_cases import case
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.agents.episode.first_frames import FirstFrameOperations


def test_first_frame_quote_is_not_received_or_persisted(tmp_path,monkeypatch):
    from PIL import Image
    ctx,_=case(tmp_path,monkeypatch)
    path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    draft['frames'][0]['source_quote']='A paraphrased description instead of original words'
    atomic_write_json(path,draft)
    captured=[]
    def build(context,row,output):
        captured.append(row)
        Image.new('RGB',(8,16)).save(output)
    result=FirstFrameOperations(ctx,builder=build,
        checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok'] and 'source_quote' not in captured[0]
    saved=read_json(ctx.novel_dir/'first_frames/book_1/clip_01/candidate_01/input.json')
    assert 'source_quote' not in saved


def test_reference_and_extra_reference_errors_are_reported_together_for_all_rows(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch);path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    for row in draft['frames']:
        row['image_references']=['unregistered-card.png']
        row['extra_references']=['also-unregistered.png']
    atomic_write_json(path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid batch must not generate'))
    with pytest.raises(ValueError) as error:operation.prepare({})
    text=str(error.value)
    assert 'clip_01' in text and 'clip_02' in text
    assert 'image_references' in text and 'scene.png' in text
    assert 'extra_references' in text and 'current source image paths' in text
    assert not (ctx.novel_dir/'first_frames').exists()


def test_partial_preflight_reports_validated_rows_without_generating_images(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch);path=ctx.work_dir/'output/first-frames.json';draft=read_json(path)
    draft['frames'][0].pop('extra_references')  # optional, no extra video card selected
    draft['frames'][1]['extra_references']=['not-an-original-reference.png']
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
    finding={'origin':'image','observed':'外套为红色','expected':'灰色外套','basis':['opening.opening.wears'],
        'relation':'contradiction','reason':'当前衣服颜色与开场要求不一致'}
    confirmation={'checks':[{'id':0,'result':'confirmed','evidence_class':'authored_explicit_colour',
        'calibration':'not_applicable','author_field_path':'opening.wears.旅人','author_quote':'灰色外套',
        'author_subject':'外套','non_colour_observation':'','non_colour_expected':'',
        'non_colour_basis':[],'reference_image_number':0,'reference_use':'none',
        'evidence':'当前开场明确写了灰色外套，图中外套为红色'}]}
    def ask(parts,*args,**kwargs):
        captured.append(parts)
        return {'observations':[],'findings':[finding]} if kwargs['name']=='episode_first_frame' else confirmation
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
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
