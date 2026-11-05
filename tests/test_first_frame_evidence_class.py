"""A confirmed issue needs usable evidence, not an uncalibrated color guess."""
import copy
from dataclasses import replace

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.llm.config import JsonEndpoint
from novel_manga.util import atomic_write_json,read_json
from support.first_frame_cases import character_case


def context(tmp_path, monkeypatch):
    ctx, clip, _, _ = character_case(tmp_path, monkeypatch)
    compiled = first_frames.compile_opening(ctx, clip, {'clip_id':clip['clip_id']})
    target = ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    target.mkdir(parents=True)
    Image.new('RGB',(8,16),'blue').save(target/'opening.png')
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda path,size:{'type':'image','path':str(path),'size':size})
    return ctx, clip, compiled, target


def finding(observed, expected, reason, origin='image', basis=None):
    return {'origin':origin,'relation':'contradiction','observed':observed,
            'expected':expected,'reason':reason,'basis':basis or ['reference_images[1]']}


def decision(index, result='confirmed', category='non_colour', **values):
    return {'id':index,'result':result,'evidence_class':category,'calibration':'unknown_or_unverified',
        'author_field_path':'','author_quote':'','author_subject':'',
        'non_colour_observation':'two bags are visible',
        'non_colour_expected':'only one authored bag',
        'non_colour_basis':['reference_images[1]'],
        'reference_image_number':3,'reference_use':'wardrobe',
        'evidence':'The image and the stated requirement have been checked.',**values}


def run_confirmation(monkeypatch, ctx, compiled, target, raw, checks):
    calls=[]
    def ask(parts,schema,**kw):
        calls.append((parts,schema,kw))
        return copy.deepcopy({'checks':checks})
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=first_frames.confirm_findings(ctx,target/'opening.png',compiled,None,raw)
    assert len(calls)==1 and calls[0][2]['name']=='episode_first_frame_review_confirmation'
    return result


def test_uncalibrated_reference_colour_is_uncertain_even_when_model_claims_calibration(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[finding('light trousers','reference dark trousers','colour mismatch')]}
    checks=[decision(0,category='reference_colour_only',calibration='verified_shared')]
    with pytest.raises(first_frames.FirstFrameReviewUncertain) as caught:
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)
    assert caught.value.first_frame_initial_review==raw
    assert caught.value.first_frame_confirmation_raw=={'checks':checks}
    assert caught.value.first_frame_confirmation_effective[0]['result']=='uncertain'


def test_normal_entry_with_reference_colour_alone_is_incomplete_not_retake_or_pass(tmp_path,monkeypatch):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,row,path:Image.new('RGB',(8,16)).save(path))
    raw={'observations':[],'findings':[finding('light trousers','reference dark trousers','colour mismatch')]}
    checks={'checks':[decision(0,category='reference_colour_only',calibration='verified_shared')]}
    calls=[]
    def ask(parts,schema,**kw):
        calls.append(kw['name'])
        return copy.deepcopy(raw if kw['name']=='episode_first_frame' else checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    outcome=EpisodeTools(ctx).first_frames({})
    result=outcome['clips'][clip['clip_id']]
    assert not outcome['ok'] and result['stage']=='review_incomplete'
    assert result['retryable'] is False and 'repair_grant' not in result
    target=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    receipt=read_json(target/'review-incomplete.json')
    assert receipt['initial_review_raw']==raw and receipt['confirmation_raw']==checks
    assert receipt['confirmation_effective'][0]['result']=='uncertain'
    assert not (target/'accepted.json').exists() and not (target/'rejected.json').exists()
    assert calls==['episode_first_frame','episode_first_frame_review_confirmation']


def test_mixed_confirmed_issue_uses_only_verified_noncolour_reason_and_retains_raw(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[
        finding('shorts and a brown bag','long trousers and black bag','shorts and bag colour differ'),
        finding('light trousers','dark trousers','trouser colour differs')]}
    checks=[decision(0,category='mixed_or_unclear',
        non_colour_observation='The person wears shorts.',
        non_colour_expected='The wardrobe reference shows long trousers.',
        non_colour_basis=['reference_images[1]']),
        decision(1,category='reference_colour_only')]
    before=copy.deepcopy(raw)
    review,original=run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)
    assert raw==before and original=={'checks':checks}
    assert review['consistent'] is False and len(review['findings'])==1
    assert 'shorts' in review['problems'][0] and 'colour' not in review['problems'][0]
    assert review['confirmation_effective'][1]['result']=='uncertain'


def test_mixed_issue_without_traceable_noncolour_evidence_is_incomplete(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[finding('shorts and brown bag','long trousers and black bag','mixed mismatch')]}
    checks=[decision(0,category='mixed_or_unclear',non_colour_basis=['reference_images[99]'])]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


def test_first_pass_model_observation_is_not_itself_a_source_basis(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[finding('shorts','long trousers','mismatch')]}
    checks=[decision(0,non_colour_observation='shorts',non_colour_expected='long trousers',
                     non_colour_basis=['original_initial_review.findings[0].observed'])]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


def test_dismissed_mixed_issue_does_not_hide_unresolved_colour(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[finding('shorts and brown bag','long trousers and black bag','mixed mismatch')]}
    checks=[decision(0,result='dismissed',category='mixed_or_unclear')]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


@pytest.mark.parametrize('valid',[True,False])
def test_authored_colour_requires_source_quote_and_matching_subject(tmp_path,monkeypatch,valid):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A red sign is visible.'
    compiled=replace(compiled,contract=contract)
    raw={'observations':[],'findings':[finding('A blue sign is visible.',
        'A red sign is visible.','The sign has the wrong colour')]}
    checks=[decision(0,category='authored_explicit_colour',
        author_field_path='opening.start_state',author_quote='red sign',
        author_subject='sign' if valid else 'pants')]
    if not valid:
        with pytest.raises(first_frames.FirstFrameReviewUncertain):
            run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)
    else:
        review,_=run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)
        assert review['consistent'] is False
        assert review['findings'][0]['expected']=='red sign'
        assert review['problems']==['A blue sign is visible.；red sign']


def test_source_colour_without_two_source_field_evidence_is_not_granted(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A red sign is visible.'
    compiled=replace(compiled,contract=contract)
    raw={'observations':[],'findings':[finding('A blue sign is visible.',
        'A red sign is visible.','The source colors conflict','source')]}
    checks=[decision(0,category='authored_explicit_colour',
        author_field_path='author_text_fields.opening.start_state',author_quote='red sign',author_subject='sign')]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


def test_authored_colour_quote_must_also_be_in_this_findings_expected_text(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A red sign and blue sign are visible.'
    compiled=replace(compiled,contract=contract)
    raw={'observations':[],'findings':[finding('The red sign looks blue.',
        'A blue sign is required.','The sign has the wrong colour')]}
    checks=[decision(0,category='authored_explicit_colour',
        author_field_path='opening.start_state',author_quote='red sign',author_subject='sign')]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


def test_source_origin_is_not_rewritten_by_noncolour_classification(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A is walking.'
    contract['opening']['opening_frame']['image_prompt']='A remains still.'
    compiled=replace(compiled,contract=contract)
    source_basis=['opening.opening.start_state','opening.opening.opening_frame.image_prompt']
    raw={'observations':[],'findings':[finding('A is walking.','A remains still.','source fields conflict','source',source_basis)]}
    checks=[decision(0,non_colour_observation='A is walking.',non_colour_expected='A remains still.',
                     non_colour_basis=['author_text_fields.opening.start_state','author_text_fields.opening.opening_frame.image_prompt'])]
    review,_=run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)
    assert review['consistent'] is False and review['findings'][0]['origin']=='source'
    assert review['findings'][0]['basis']==source_basis


def test_source_confirmation_cannot_replace_the_authorized_source_fields(tmp_path,monkeypatch):
    ctx,_,compiled,target=context(tmp_path,monkeypatch)
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A is walking.'
    contract['opening']['opening_frame']['image_prompt']='A remains still.'
    contract['opening']['location']='A is inside.'
    compiled=replace(compiled,contract=contract)
    raw={'observations':[],'findings':[finding('A is walking.','A remains still.','source fields conflict','source',
        ['opening.opening.start_state','opening.opening.opening_frame.image_prompt'])]}
    checks=[decision(0,non_colour_observation='A is walking.',non_colour_expected='A is inside.',
                     non_colour_basis=['opening.opening.start_state','opening.opening.location'])]
    with pytest.raises(first_frames.FirstFrameReviewUncertain):
        run_confirmation(monkeypatch,ctx,compiled,target,raw,checks)


@pytest.mark.parametrize('latest_incomplete',[False,True])
def test_accepted_review_does_not_reuse_older_rejection_unless_newer_review_is_incomplete(
        tmp_path,monkeypatch,latest_incomplete):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    operation=first_frames.FirstFrameOperations(ctx,
        builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),
        checker=lambda *args:{'findings':[]})
    assert operation.prepare({})['ok'] is True
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    candidate=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    rejection={'error':'earlier image rejection','review':{'findings':[
        {'origin':'image','relation':'contradiction','reason':'earlier defect'}]}}
    atomic_write_json(candidate/'rejected.json',rejection)
    if latest_incomplete:
        atomic_write_json(candidate/'review-incomplete.json',{'error':'new review unfinished'})
    row=first_frames.status(ctx,plan)['clips'][clip['clip_id']]
    assert read_json(candidate/'rejected.json')==rejection
    assert bool(row.get('repair_grant')) is latest_incomplete
    if latest_incomplete:assert row['repair_grant']['operation']=='retake_candidate'
