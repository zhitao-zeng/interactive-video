"""Retained opening pixels survive an incomplete model review without another image."""
import copy

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames, readiness
from novel_manga.media.first_frames import require_current
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


@pytest.fixture(autouse=True)
def review_route(monkeypatch):
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'phanthy.com/deepseek-v4',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))


def truncated():
    from novel_manga.llm.client import ModelOutputTruncated
    return ModelOutputTruncated('episode_first_frame', first_frames.FIRST_FRAME_REVIEW_MAX_TOKENS)


def candidate(ctx, cid='clip_01'):
    return ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid/'candidate_01'


def live_status(ctx, outcome=None):
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    result={'adopted':True,'first_frames':first_frames.status(ctx,plan)}
    if outcome is not None:
        result['job']={'action':'first_frames','status':'completed','result':outcome}
    return result


def initial_request(ctx):
    request=readiness.automatic_request(ctx,live_status(ctx))
    assert request is not None
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{
        **request['route'],'automatic_fingerprint':request['fingerprint']}})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',request['document'])
    return request


def fail_one_review(ctx,error=None):
    def build(ctx,row,path):
        Image.new('RGB',(8,16),'blue').save(path)
    def review(ctx,path,row,contract):
        if row['clip_id']=='clip_01':raise error or truncated()
        return {'findings':[]}
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=review)
    with pytest.raises(ValueError,match='JSON truncated' if error is None else str(error)):
        operation.prepare({})
    outcome={'ok':False,**operation.partial_outcome}
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
    return outcome


def test_truncation_keeps_candidate_and_review_stage_without_content_grant(tmp_path,monkeypatch):
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    outcome=fail_one_review(ctx)
    failed=outcome['clips']['clip_01']
    assert failed['stage']=='review_incomplete'
    assert failed['candidate']=='candidate_01'
    assert 'repair_grant' not in failed
    saved=read_json(candidate(ctx)/'review-incomplete.json')
    assert saved['review_configuration']['max_tokens']==1800
    assert saved['input']['source_contract']
    assert (candidate(ctx)/'opening.png').is_file()
    assert not (candidate(ctx)/'rejected.json').exists()
    state=live_status(ctx,outcome)
    row=state['first_frames']['clips']['clip_01']
    assert row['stage']=='review_incomplete'
    assert 'review incomplete' in row['issue']
    assert 'repair_grant' not in row and 'repair_origin' not in row
    route=readiness.next_action(ctx,state)
    assert route['action']=='first_frames'
    assert 'review incomplete' in route['blocked']['clip_01']
    assert 'source_input_conflicts' not in route
    assert state['first_frames']['clips']['clip_02']['current'] is True


def test_changed_review_budget_reuses_only_failed_image_once_and_keeps_accepted_clip(tmp_path,monkeypatch):
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    first=initial_request(ctx)
    assert len(first['document']['frames'])==2
    outcome=fail_one_review(ctx)
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    accepted=copy.deepcopy(plan['clips'][1])
    pixels=(candidate(ctx)/'opening.png').read_bytes()
    # clip_02 passing and shrinking the target set cannot repeat this failed review.
    assert readiness.automatic_request(ctx,live_status(ctx,outcome)) is None
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',8000)
    request=readiness.automatic_request(ctx,live_status(ctx,outcome))
    assert request['document']['frames']==[{
        'clip_id':'clip_01','extra_references':[],'reuse_candidate':'candidate_01'}]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',request['document'])
    def no_image(*a):raise AssertionError('review recovery must reuse the saved image')
    operation=first_frames.FirstFrameOperations(ctx,builder=no_image,checker=lambda *a:{'findings':[]})
    assert operation.prepare({})['ok'] is True
    recovered=read_json(ctx.episode_dir/'clip_plan.json')['clips']
    assert recovered[1]==accepted
    for clip in recovered:require_current(ctx,clip)
    assert (candidate(ctx)/'opening.png').read_bytes()==pixels
    assert len(list(candidate(ctx).parent.glob('candidate_*')))==1


def test_second_incomplete_review_is_not_replayed_with_unchanged_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    outcome=fail_one_review(ctx)
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',8000)
    request=readiness.automatic_request(ctx,live_status(ctx,outcome))
    atomic_write_json(ctx.work_dir/'output/first-frames.json',request['document'])
    outcome=fail_one_review(ctx)
    assert readiness.automatic_request(ctx,live_status(ctx,outcome)) is None
    assert len(list(candidate(ctx).parent.glob('candidate_*')))==1


def test_saved_image_without_review_is_projected_as_reuse_before_generation(tmp_path,monkeypatch):
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    fail_one_review(ctx)
    (candidate(ctx)/'review-incomplete.json').unlink()
    (ctx.work_dir/'input/latest-first-frames.json').unlink()
    state=live_status(ctx)
    assert state['first_frames']['clips']['clip_01']['stage']=='review_incomplete'
    request=readiness.automatic_request(ctx,state)
    assert request['document']['frames'][0]['reuse_candidate']=='candidate_01'


def test_actual_old_truncation_receipt_resumes_only_after_review_configuration_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    outcome=fail_one_review(ctx)
    (candidate(ctx)/'review-incomplete.json').unlink()
    failure={'ok':False,'error':'ValueError: episode_first_frame: JSON truncated at 1800 output tokens',
             'retryable':False}
    outcome={'ok':False,'clips':{'clip_01':failure},**failure}
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',outcome)
    # The actual stopped receipt overwrote its earlier automatic fingerprint.
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{'action':'first_frames'}})
    assert readiness.automatic_request(ctx,live_status(ctx,outcome)) is None
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',8000)
    request=readiness.automatic_request(ctx,live_status(ctx,outcome))
    assert request['document']['frames'][0]['reuse_candidate']=='candidate_01'
    assert not (candidate(ctx)/'review-incomplete.json').exists() # status remains read-only


def test_plain_value_error_is_not_a_retryable_model_review_failure(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    compiled=first_frames.compile_opening(ctx,clips[0],{'clip_id':'clip_01'})
    failure=first_frames.failure_result(compiled,ValueError('invalid source'),candidate='candidate_01')
    assert failure['retryable'] is False
    assert 'review_incomplete' not in failure and 'stage' not in failure
    initial_request(ctx)
    outcome=fail_one_review(ctx,ValueError('invalid source'))
    assert readiness.automatic_request(ctx,live_status(ctx,outcome)) is None


def test_ineffective_budget_or_reasoning_setting_change_does_not_repeat_review(tmp_path,monkeypatch):
    from novel_manga.llm.config import JsonEndpoint
    controls={'reasoning':'low'}
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'phanthy.com/deepseek-v4',('http://127.0.0.1:8789/v1',),stream=True,
        min_max_tokens=16000,reasoning=controls['reasoning']))
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',1800,raising=False)
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    outcome=fail_one_review(ctx)
    monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',8000)
    controls['reasoning']='high' # json_policy's review effort still overrides this transport fallback.
    assert readiness.automatic_request(ctx,live_status(ctx,outcome)) is None


def test_review_policy_change_can_recheck_the_same_incomplete_candidate(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    initial_request(ctx)
    outcome=fail_one_review(ctx)
    monkeypatch.setattr(first_frames,'REVIEW_POLICY',first_frames.REVIEW_POLICY+'-changed')
    request=readiness.automatic_request(ctx,live_status(ctx,outcome))
    assert request['document']['frames']==[{
        'clip_id':'clip_01','extra_references':[],'reuse_candidate':'candidate_01'}]
