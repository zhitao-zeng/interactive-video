"""First-frame contradictions need evidence-preserving independent confirmation."""
import copy
import json

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames, readiness
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.llm.client import ModelOutputMalformed
from novel_manga.media import first_frames as media_first_frames
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case, character_case


def finding(observed, expected, reason):
    return {'origin':'image','relation':'contradiction','observed':observed,
            'expected':expected,'basis':['opening.opening.start_state'],'reason':reason}


def classified(raw,checks):
    """Supply the production evidence fields for earlier non-colour fixtures."""
    for row in checks['checks']:
        item=raw['findings'][row['id']]
        row.update(evidence_class='non_colour',calibration='not_applicable',
            author_field_path='',author_quote='',author_subject='',
            non_colour_observation=item['observed'],non_colour_expected=item['expected'],
            non_colour_basis=list(item['basis']) if item['origin']=='source' else ['reference_images[1]'],
            reference_image_number=0 if item['origin']=='source' else 3,
            reference_use='none' if item['origin']=='source' else 'wardrobe')
    return checks


def actionable(item):
    return {**item,'basis':item['basis'] if item['origin']=='source' else ['reference_images[1]'],
            'reason':item['observed']+'；'+item['expected']}


def evidence_findings():
    return [
        finding('A hand holds a rod resting against a step','The rod rests against the step',
                'Holding the rod violates resting against the step'),
        finding('A hook touches the bag strap','The camera endpoint touches the strap',
                'The endpoint has already happened'),
        finding('One backpack is in the water and one is on her shoulders',
                'Her single lost backpack is in the water','A second backpack was added'),
        finding('The lamp chamber is red but has no red sign',
                'A red sign is on the shade','The red sign is missing'),
        finding('The trousers look light under outdoor lighting',
                'The card shows dark grey-green trousers','The trousers are a different colour'),
    ]


def context_and_row(tmp_path, monkeypatch):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    compiled=first_frames.compile_opening(ctx,clip,{'clip_id':clip['clip_id']})
    target=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    target.mkdir(parents=True)
    Image.new('RGB',(8,16),'blue').save(target/'opening.png')
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda path,size:{'type':'image','path':str(path),'size':size})
    return ctx,clip,compiled,target


def test_independent_confirmation_same_pixels_references_and_mixed_findings(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    original={'observations':['visible rod, bags, lamp and trousers'],'findings':evidence_findings()}
    checks={'checks':[{'id':i,'result':result,'evidence':'Reviewed actual image and current requirement'}
        for i,result in enumerate(['dismissed','dismissed','confirmed','confirmed','dismissed'])]}
    classified(original,checks)
    calls=[]
    def ask(parts,schema,**kwargs):
        calls.append((copy.deepcopy(parts),kwargs['name']))
        return copy.deepcopy(original if len(calls)==1 else checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    answer=first_frames.check(ctx,target/'opening.png',compiled)
    assert [x[1] for x in calls]==['episode_first_frame','episode_first_frame_review_confirmation']
    assert calls[0][0][1:]==calls[1][0][1:]
    assert calls[0][0][1]['path']==str(target/'opening.png')
    assert [x['path'] for x in calls[0][0][2:]]==[str(ctx.novel_dir/p) for p in compiled.row['image_references']]
    assert answer['raw']==original and answer['confirmation']==checks
    assert answer['consistent'] is False
    assert answer['findings']==[actionable(original['findings'][2]),actionable(original['findings'][3])]
    assert answer['problems']==[actionable(original['findings'][i])['reason'] for i in (2,3)]


def test_all_uncertain_confirmation_is_incomplete_and_grants_no_retake(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    original={'observations':[],'findings':[evidence_findings()[2]]}
    checks={'checks':[{'id':0,'result':'uncertain','evidence':'Cannot see the object clearly'}]}
    classified(original,checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:
                        copy.deepcopy(original if kw['name']=='episode_first_frame' else checks))
    with pytest.raises(first_frames.FirstFrameReviewUncertain) as caught:
        first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(target/'review-incomplete.json')
    assert saved['initial_review_raw']==original and saved['confirmation_raw']==checks
    assert not (target/'rejected.json').exists() and not (target/'accepted.json').exists()
    failure=first_frames.failure_result(compiled,caught.value,candidate='candidate_01')
    assert failure['stage']=='review_incomplete' and failure['retryable'] is False
    assert 'service_interrupted' not in failure and 'repair_grant' not in failure


def test_confirmed_plus_uncertain_reports_only_confirmed_as_actionable(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[4],evidence_findings()[2]]}
    checks={'checks':[{'id':0,'result':'uncertain','evidence':'Colour under light is unclear'},
                      {'id':1,'result':'confirmed','evidence':'Two distinct bags are visible'}]}
    classified(raw,checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:
                        copy.deepcopy(raw if kw['name']=='episode_first_frame' else checks))
    review,reviewed,record=first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(record)
    assert reviewed and saved['raw']==raw and saved['confirmation']==checks
    assert review['findings']==[actionable(raw['findings'][1])] and review['problems']==[actionable(raw['findings'][1])['reason']]
    assert review['consistent'] is False


def test_lighting_ambiguous_colour_does_not_obscure_confirmed_bag_and_style_defects(tmp_path,monkeypatch):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,compiled,path:Image.new('RGB',(8,16)).save(path))
    style=finding('A visibly strapless dress','A hooded jacket with sleeves','The clothing style is incompatible')
    raw={'observations':[],'findings':[evidence_findings()[4],evidence_findings()[2],style]}
    checks={'checks':[{'id':0,'result':'uncertain','evidence':'Light and white balance differ'},
                      {'id':1,'result':'confirmed','evidence':'Two distinct backpacks are visible'},
                      {'id':2,'result':'confirmed','evidence':'A strapless dress has no hood or sleeves'}]}
    classified(raw,checks)
    calls=[]
    def ask(parts,schema,**kw):
        calls.append((kw['name'],parts[0]['text']))
        return copy.deepcopy(raw if kw['name']=='episode_first_frame' else checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    outcome=EpisodeTools(ctx).first_frames({})
    assert not outcome['ok']
    assert [item[0] for item in calls]==['episode_first_frame','episode_first_frame_review_confirmation']
    assert '白平衡' in calls[1][1] and '款式' in calls[1][1]
    rejected=outcome['clips'][clip['clip_id']]
    assert rejected['problems']==[actionable(raw['findings'][i])['reason'] for i in (1,2)]
    assert rejected['repair_grant']['operation']=='retake_candidate'
    target=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    record=read_json(target/'reviews/review_001.json')
    assert record['raw']==raw and record['confirmation']==checks
    assert record['review']['findings']==[actionable(raw['findings'][1]),actionable(raw['findings'][2])]


def test_independently_confirmed_major_colour_conflict_is_not_automatically_waived(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    from dataclasses import replace
    contract=copy.deepcopy(compiled.contract)
    contract['opening']['start_state']='A blue jacket is required.'
    compiled=replace(compiled,contract=contract)
    raw={'observations':[],'findings':[finding('A bright red jacket','A blue jacket',
        'The visible colour conflict cannot be explained by lighting')]}
    checks={'checks':[{'id':0,'result':'confirmed',
        'evidence':'The vivid red and blue differ across colour families under comparable light'}]}
    classified(raw,checks)
    checks['checks'][0].update(evidence_class='authored_explicit_colour',
        author_field_path='opening.start_state',author_quote='blue jacket',author_subject='jacket')
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:copy.deepcopy(checks))
    review,confirmation=first_frames.confirm_findings(ctx,target/'opening.png',compiled,None,raw)
    assert confirmation==checks and review['consistent'] is False
    assert review['findings'][0]['expected']=='blue jacket'
    assert review['findings'][0]['basis']==['opening.start_state']
    assert review['problems']==['A bright red jacket；blue jacket']


def test_normal_entry_with_only_uncertainty_stops_without_retake_or_pass(tmp_path,monkeypatch):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,compiled,path:Image.new('RGB',(8,16)).save(path))
    raw={'observations':[],'findings':[evidence_findings()[2]]}
    checks={'checks':[{'id':0,'result':'uncertain','evidence':'The second object is obscured'}]}
    classified(raw,checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:
                        copy.deepcopy(raw if kw['name']=='episode_first_frame' else checks))
    outcome=EpisodeTools(ctx).first_frames({})
    result=outcome['clips'][clip['clip_id']]
    assert outcome['ok'] is False and result['stage']=='review_incomplete'
    assert result['retryable'] is False and 'repair_grant' not in result and 'service_interrupted' not in result
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    assert not plan['clips'][0].get('first_frame')
    status=first_frames.status(ctx,plan)['clips'][clip['clip_id']]
    assert status['stage']=='review_incomplete' and status['review_incomplete']['retryable'] is False
    assert 'repair_grant' not in status and 'repair_origin' not in status


def test_repeated_incomplete_review_keeps_earlier_raw_evidence_in_existing_receipt(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[2]]}
    first={'checks':[{'id':0,'result':'uncertain','evidence':'Obscured'}]}
    second={'checks':[{'id':0,'result':'uncertain','evidence':'Still obscured'}]}
    classified(raw,first);classified(raw,second)
    confirmations=iter([first,second])
    def ask(*args,**kw):
        return copy.deepcopy(raw if kw['name']=='episode_first_frame' else next(confirmations))
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    for _ in range(2):
        with pytest.raises(first_frames.FirstFrameReviewUncertain):
            first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(target/'review-incomplete.json')
    assert saved['confirmation_raw']==second and saved['initial_review_raw']==raw
    assert len(saved['history'])==1
    assert saved['history'][0]['confirmation_raw']==first and saved['history'][0]['initial_review_raw']==raw
    assert not list((target/'reviews').glob('review_*.json'))


def test_no_contradiction_needs_no_second_review(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':['only a hand visible'],'findings':[
        {**evidence_findings()[0],'relation':'underspecified'}]}
    calls=[]
    def ask(*args,**kwargs):calls.append(kwargs['name']);return copy.deepcopy(raw)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    answer=first_frames.check(ctx,target/'opening.png',compiled)
    assert calls==['episode_first_frame'] and answer['consistent'] is True
    assert answer['raw']==raw and 'confirmation' not in answer


def test_direct_confirmation_of_retained_initial_review(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[0],evidence_findings()[2]]}
    before=copy.deepcopy(raw);checks={'checks':[
        {'id':0,'result':'dismissed','evidence':'A supported rod may still be held'},
        {'id':1,'result':'confirmed','evidence':'Two distinct backpacks are visible'}]}
    classified(raw,checks)
    calls=[]
    def ask(parts,schema,**kw):calls.append(kw['name']);return copy.deepcopy(checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    review,receipt=first_frames.confirm_findings(ctx,target/'opening.png',compiled,None,raw)
    assert calls==['episode_first_frame_review_confirmation']
    assert raw==before and receipt==checks and review['findings']==[actionable(raw['findings'][1])]


@pytest.mark.parametrize('keep_true_defect',[False,True])
def test_normal_first_frames_entry_uses_confirmed_verdict(tmp_path,monkeypatch,keep_true_defect):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,compiled,path:Image.new('RGB',(8,16)).save(path))
    raw={'observations':[],'findings':[evidence_findings()[0],evidence_findings()[2]]}
    checks={'checks':[{'id':0,'result':'dismissed','evidence':'Holding can coexist with resting'},
        {'id':1,'result':'confirmed' if keep_true_defect else 'dismissed',
         'evidence':'Two objects are visible' if keep_true_defect else 'Only one object is visible'}]}
    classified(raw,checks)
    calls=[]
    def ask(*args,**kw):calls.append(kw['name']);return copy.deepcopy(raw if len(calls)==1 else checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    answer=EpisodeTools(ctx).first_frames({})
    assert answer['ok'] is (not keep_true_defect)
    target=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    record=read_json(target/'reviews/review_001.json')
    assert record['raw']==raw and record['confirmation']==checks
    assert record['review']['findings']==([actionable(raw['findings'][1])] if keep_true_defect else [])
    if keep_true_defect:
        rejection=read_json(target/'rejected.json')
        assert rejection['problems']==[actionable(raw['findings'][1])['reason']]
        assert rejection['repair_grant']['operation']=='retake_candidate'
    else:
        assert not (target/'rejected.json').exists()
        assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']['review']['consistent'] is True


def test_confirmed_source_conflict_retains_input_grant_origin(tmp_path,monkeypatch):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    clip['shots'][0]['start_state']='A is walking.'
    clip['shots'][0]['opening_frame']['image_prompt']='A remains still.'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    draft=read_json(ctx.work_dir/'output/first-frames.json')
    draft['frames'][0]['image_prompt']='A remains still.'
    atomic_write_json(ctx.work_dir/'output/first-frames.json',draft)
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,compiled,path:Image.new('RGB',(8,16)).save(path))
    conflict={'origin':'source','relation':'contradiction','observed':'A is walking.',
        'expected':'A remains still.','reason':'The authored start states conflict.',
        'basis':['opening.opening.start_state','opening.opening.opening_frame.image_prompt']}
    raw={'observations':[],'findings':[conflict]}
    checks={'checks':[{'id':0,'result':'confirmed','evidence':'The two authored start requirements conflict'}]}
    classified(raw,checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:
                        copy.deepcopy(raw if kw['name']=='episode_first_frame' else checks))
    result=EpisodeTools(ctx).first_frames({})['clips'][clip['clip_id']]
    assert result['ok'] is False and result['repair_origin']=='source'
    assert result['repair_grant']['operation']=='input'
    assert result['review']['findings']==[actionable(conflict)]


def test_record_preserves_raw_and_confirmation_and_cache_never_reruns(tmp_path,monkeypatch):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[0],evidence_findings()[2]]}
    checks={'checks':[{'id':0,'result':'dismissed','evidence':'Holding and resting can coexist'},
                      {'id':1,'result':'confirmed','evidence':'Two distinct bags are visible'}]}
    classified(raw,checks)
    calls=[]
    def ask(*args,**kwargs):calls.append(kwargs['name']);return copy.deepcopy(raw if len(calls)==1 else checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    review,reviewed,record=first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(record)
    assert reviewed and saved['raw']==raw and saved['confirmation']==checks
    assert saved['review']['findings']==[actionable(raw['findings'][1])] and review['consistent'] is False
    row,contract,images=first_frames._review_context(ctx,compiled,None)
    payload=first_frames._confirmation_payload(row,contract,images,saved['raw'])
    assert first_frames._review_from_confirmation(payload,saved['raw'],saved['confirmation'])==saved['review']
    assert read_json(target/'review.json')==raw
    again,new,_=first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    assert not new and again==review and len(calls)==2


@pytest.mark.parametrize('invalid',[{'checks':[]},
    {'checks':[{'id':0,'result':'dismissed','evidence':'x'},{'id':0,'result':'dismissed','evidence':'x'}]},
    {'checks':[{'id':0,'result':'pass','evidence':'x'}]}])
def test_confirmation_protocol_failure_retains_image_and_raw_without_content_rejection(tmp_path,monkeypatch,invalid):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[2]]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:
                        copy.deepcopy(raw if kw['name']=='episode_first_frame' else invalid))
    with pytest.raises(ModelOutputMalformed) as caught:
        first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(target/'review-incomplete.json')
    assert saved['initial_review_raw']==raw and saved['confirmation_raw']==invalid
    assert (target/'opening.png').is_file() and not (target/'rejected.json').exists()
    assert not (target/'accepted.json').exists() and not (target/'review.json').exists()
    failure=first_frames.failure_result(compiled,caught.value,candidate='candidate_01')
    assert failure['stage']=='review_incomplete' and failure['retryable'] is True
    assert failure.get('repair_grant',{}).get('operation')!='retake_candidate'


@pytest.mark.parametrize('error,retryable',[(TimeoutError('temporary network timeout'),True),
    (RuntimeError('service returned HTTP 401: unauthorized'),False)])
def test_confirmation_service_failure_preserves_initial_evidence_and_service_routing(tmp_path,monkeypatch,error,retryable):
    ctx,_,compiled,target=context_and_row(tmp_path,monkeypatch)
    raw={'observations':[],'findings':[evidence_findings()[2]]}
    def ask(*args,**kw):
        if kw['name']=='episode_first_frame':return copy.deepcopy(raw)
        raise error
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    with pytest.raises(type(error)) as caught:
        first_frames.review_candidate(ctx,target/'opening.png',compiled,first_frames.check)
    saved=read_json(target/'review-incomplete.json')
    assert saved['initial_review_raw']==raw and saved['retryable'] is retryable
    assert not (target/'rejected.json').exists() and not (target/'accepted.json').exists()
    failure=first_frames.failure_result(compiled,caught.value,candidate='candidate_01')
    assert failure['stage']=='review_incomplete' and failure['retryable'] is retryable
    assert failure.get('repair_grant',{}).get('operation')!='retake_candidate'


@pytest.mark.parametrize('error',[TimeoutError('temporary network timeout'),
    RuntimeError('service returned HTTP 503: service unavailable'),
    ModelOutputMalformed('episode_first_frame_review_confirmation')])
def test_cold_resume_after_retryable_confirmation_service_error_reuses_original_image_without_generation(tmp_path,monkeypatch,error):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    from novel_manga.llm.config import JsonEndpoint
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    builds=[]
    def build(ctx,compiled,path):
        builds.append(path);Image.new('RGB',(8,16),'blue').save(path)
    monkeypatch.setattr(first_frames,'build',build)
    raw={'observations':[],'findings':[evidence_findings()[0]]}
    checks={'checks':[{'id':0,'result':'dismissed','evidence':'Holding can coexist with resting'}]}
    classified(raw,checks)
    calls=[]
    def ask(*args,**kw):
        calls.append(kw['name'])
        if kw['name']=='episode_first_frame':return copy.deepcopy(raw)
        if calls.count('episode_first_frame_review_confirmation')==1:
            raise error
        return copy.deepcopy(checks)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    outcome=EpisodeTools(ctx).first_frames({})
    failed=outcome['clips'][clip['clip_id']]
    assert outcome['ok'] is False and failed['stage']=='review_incomplete'
    assert failed['retryable'] is True and failed.get('service_interrupted')
    target=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    pixels=(target/'opening.png').read_bytes()
    saved=read_json(target/'review-incomplete.json')
    assert saved['initial_review_raw']==raw and saved['retryable'] is True
    assert saved.get('service_interrupted')
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    ff=first_frames.status(ctx,plan)
    row=ff['clips'][clip['clip_id']]
    assert row['stage']=='review_incomplete' and row['review_incomplete']['retryable'] is True
    assert row['review_incomplete']['service_interrupted']
    grant=first_frames.current_frame_grant(ctx,clip)
    assert grant['operation']=='reuse_candidate' and grant['evidence']['candidate']=='candidate_01'
    with pytest.raises(ValueError,match='retake_candidate is not authorized'):
        first_frames.validate_frame_operation({'retake_candidate':{'candidate':'candidate_01','reason':'retry'}},grant)
    request=readiness.automatic_request(ctx,{'adopted':True,'first_frames':ff})
    assert request is not None
    assert request['document']['frames']==[{'clip_id':clip['clip_id'],
        'extra_references':[],'reuse_candidate':'candidate_01'}]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',request['document'])
    monkeypatch.setattr(first_frames,'build',lambda *a:pytest.fail('cold review recovery must not regenerate'))
    recovered=EpisodeTools(ctx).first_frames({})
    assert recovered['ok'] is True and len(builds)==1
    assert (target/'opening.png').read_bytes()==pixels
    assert len(list(target.parent.glob('candidate_*')))==1
    assert read_json(target/'review-incomplete.json')['initial_review_raw']==raw
    record=read_json(target/'reviews/review_001.json')
    assert record['raw']==raw and record['confirmation']==checks and record['review']['consistent'] is True


@pytest.mark.parametrize('kind',['unauthorized','quota','uncertain','truncated','unknown'])
def test_cold_resume_does_not_retry_nontransient_confirmation_failure(tmp_path,monkeypatch,kind):
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    from novel_manga.llm.config import JsonEndpoint
    from novel_manga.llm.client import ModelOutputTruncated
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,compiled,path:Image.new('RGB',(8,16)).save(path))
    raw={'observations':[],'findings':[evidence_findings()[2]]}
    def ask(*args,**kw):
        if kw['name']=='episode_first_frame':return copy.deepcopy(raw)
        if kind=='unauthorized':raise RuntimeError('service returned HTTP 401: unauthorized')
        if kind=='quota':raise RuntimeError('service returned HTTP 402: payment required')
        if kind=='truncated':raise ModelOutputTruncated('episode_first_frame_review_confirmation',8000)
        if kind=='unknown':raise AssertionError('unknown bug')
        return classified(raw,{'checks':[{'id':0,'result':'uncertain','evidence':'Object obscured'}]})
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    outcome=EpisodeTools(ctx).first_frames({})
    assert outcome['ok'] is False
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    ff=first_frames.status(ctx,plan);row=ff['clips'][clip['clip_id']]
    assert row['stage']=='review_incomplete' and row['review_incomplete']['retryable'] is False
    assert 'repair_grant' not in row and 'repair_origin' not in row
    assert readiness.automatic_request(ctx,{'adopted':True,'first_frames':ff}) is None
    assert len(list((ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']).glob('candidate_*')))==1
    if kind in {'unauthorized','quota','unknown'}:
        monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',
                            first_frames.FIRST_FRAME_REVIEW_MAX_TOKENS+1)
        changed=first_frames.status(ctx,plan)
        assert changed['clips'][clip['clip_id']]['review_incomplete']['retryable'] is False
        assert readiness.automatic_request(ctx,{'adopted':True,'first_frames':changed}) is None
    if kind=='truncated':
        monkeypatch.setattr(first_frames,'FIRST_FRAME_REVIEW_MAX_TOKENS',
                            first_frames.FIRST_FRAME_REVIEW_MAX_TOKENS+1)
        changed=first_frames.status(ctx,plan)
        assert changed['clips'][clip['clip_id']]['review_incomplete']['retryable'] is True
        request=readiness.automatic_request(ctx,{'adopted':True,'first_frames':changed})
        assert request['document']['frames'][0]['reuse_candidate']=='candidate_01'


@pytest.mark.parametrize('old',[
    'first-frame-explicit-relations-v7-authored-prop-instances',
    'first-frame-explicit-relations-v8-independent-confirmation',
    'first-frame-explicit-relations-v9-lighting-aware-confirmation'])
def test_prior_accepted_frame_remains_current_after_review_policy_change(tmp_path,monkeypatch,old):
    ctx,clips=case(tmp_path,monkeypatch)
    current=media_first_frames.REVIEW_POLICY
    monkeypatch.setattr(media_first_frames,'REVIEW_POLICY',old)
    monkeypatch.setattr(first_frames,'REVIEW_POLICY',old)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01'}]})
    result=first_frames.FirstFrameOperations(ctx,builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),
        checker=lambda *args:{'observations':[],'findings':[]}).prepare({})
    assert result['ok'] is True
    plan=read_json(ctx.episode_dir/'clip_plan.json');approved=copy.deepcopy(plan['clips'][0])
    assert approved['first_frame']['review']['consistent'] is True
    monkeypatch.setattr(media_first_frames,'REVIEW_POLICY',current)
    monkeypatch.setattr(first_frames,'REVIEW_POLICY',current)
    media_first_frames.require_current(ctx,approved)
    assert first_frames.status(ctx,plan)['clips']['clip_01']['current'] is True
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]==approved
    assert len(list((ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_01').glob('candidate_*')))==1
