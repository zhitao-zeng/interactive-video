import pytest
from PIL import Image

from novel_manga.application.agents.episode import first_frames
from novel_manga.media.first_frames import current_contract
from novel_manga.util import atomic_write_json,read_json
from support.first_frame_cases import case


def finding(origin):
    return {'origin':origin,'relation':'contradiction','observed':'Source shot_scale says two-shot',
        'expected':'Source opening_frame says actor alone',
        'basis':['opening.opening.shot_scale','opening.opening.opening_frame.image_prompt'],
        'reason':'These source requirements cannot hold simultaneously.'}


@pytest.mark.parametrize('origin',['source','image'])
def test_retake_routes_source_conflict_to_input_before_allocating_image(tmp_path,monkeypatch,origin):
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft=read_json(ctx.work_dir/'output/first-frames.json');row=draft['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    builds=[]
    def build(c,r,p):builds.append(p);Image.new('RGB',(8,16)).save(p)
    checker=lambda *a:{'observations':[],'findings':[finding(origin)]}
    operation=lambda:first_frames.FirstFrameOperations(ctx,builder=build,checker=checker).prepare({})
    result=operation()
    assert not result['ok']
    root=ctx.novel_dir/'first_frames/book_1/clip_01'
    candidate=root/'candidate_01'
    protected={p:p.read_bytes() for p in candidate.rglob('*') if p.is_file()}
    row['retake_candidate']={'candidate':'candidate_01','reason':'Try a different image'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    if origin=='source':
        assert result['clips']['clip_01']['repair_origin']=='source'
        status=result['first_frames']['clips']['clip_01']
        assert 'input repair' in status['next_action'] and 'not retake' in status['next_action']
        assert status['last_rejection']['source_conflicts']==[finding('source')]
        with pytest.raises(ValueError,match='source input.*input repair'):operation()
        assert len(builds)==1 and len(list(root.glob('candidate_*')))==1
        assert not (candidate/'retake-decision.json').exists()
        assert all(p.read_bytes()==value for p,value in protected.items())
    else:
        assert not operation()['ok']
        assert len(builds)==2 and len(list(root.glob('candidate_*')))==2


def test_real_review_requests_source_origin_with_exact_source_evidence(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    captured={}
    def ask(parts,schema,**kwargs):
        captured.update(parts=parts,schema=schema)
        return {'observations':[],'findings':[finding('source')]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda p,*a:{'path':str(p)})
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:None)
    result=first_frames.check(ctx,ctx.novel_dir/'opening.png',row,current_contract(ctx,clips[0],row['image_references']))
    schema=captured['schema']['properties']['findings']['items']['properties']
    assert schema['origin']['enum']==['source','image']
    instructions=captured['parts'][0]['text']
    assert '源字段彼此矛盾' in instructions and '至少两个' in instructions
    assert not result['consistent']


def test_old_review_policy_requires_same_image_recheck_before_retake(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    policy=first_frames.REVIEW_POLICY
    monkeypatch.setattr(first_frames,'REVIEW_POLICY','first-frame-explicit-relations-v4-opening-time')
    old=finding('source');old.pop('origin')
    first_frames.FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
        checker=lambda *a:{'observations':[],'findings':[old]}).prepare({})
    monkeypatch.setattr(first_frames,'REVIEW_POLICY',policy)
    row['retake_candidate']={'candidate':'candidate_01','reason':'Try again'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    with pytest.raises(ValueError,match='reuse_candidate'):
        first_frames.FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('old review cannot authorize more images'),
            checker=lambda *a:pytest.fail('caller must explicitly reuse existing image')).prepare({})
    assert len(list((ctx.novel_dir/'first_frames/book_1/clip_01').glob('candidate_*')))==1


def test_adopted_source_change_requests_recheck_instead_of_another_input_revision(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    clips[0]['shots'][0]['shot_scale']='medium two-shot'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    first_frames.FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
        checker=lambda *a:{'observations':[],'findings':[finding('source')]}).prepare({})
    clips[0]['shots'][0]['shot_scale']='single-person medium shot'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    status=first_frames.status(ctx,{'clips':[clips[0]]})['clips']['clip_01']
    assert status.get('repair_origin')!='source'
    assert "reuse_candidate:'candidate_01'" in status['next_action']
    assert 'source changed' in status['next_action']
