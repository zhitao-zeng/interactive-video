"""Image findings must be grounded in the cited author text or supplied card."""
import copy
import pytest

from novel_manga.application.agents.episode.first_frames import FirstFrameReviewUncertain, _review_from_confirmation


def example():
    payload={'contradiction_ids':[0], 'opening':{'opening':{'start_state':'A is seated.'}},
             'author_text_fields':{'opening.start_state':'A is seated.'},
             'image_prompt':'A is seated.', 'visible_characters':['A'],
             'reference_images':[{'image_number':2,'path':'actor.png','role':'character','reference_scope':'identity_portrait'},
                                 {'image_number':3,'path':'style.png','role':'style','reference_scope':'style_only_not_character_identity'}]}
    raw={'findings':[{'origin':'image','relation':'contradiction','observed':'A is seated.',
                      'expected':'A is standing.','basis':['opening.opening.start_state'], 'reason':'wrong pose'}]}
    check={'id':0,'result':'confirmed','evidence_class':'non_colour','calibration':'not_applicable',
           'author_field_path':'opening.start_state','author_quote':'A is standing.','author_subject':'A',
           'non_colour_observation':'A is seated.','non_colour_expected':'A is standing.',
           'non_colour_basis':['opening.opening.start_state'],'reference_image_number':0,'reference_use':'none',
           'evidence':'seated rather than standing'}
    return payload,raw,{'checks':[check]}


def test_invented_author_text_must_be_uncertain():
    payload,raw,confirmation=example()
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)


def test_matching_author_quote_can_confirm_non_colour_issue():
    payload,raw,confirmation=example()
    payload['opening']['opening']['start_state']='A is standing.'
    payload['author_text_fields']['opening.start_state']='A is standing.'
    review=_review_from_confirmation(payload,raw,confirmation)
    assert review['consistent'] is False


def test_reference_must_match_transmitted_image_and_scope():
    payload,raw,confirmation=example()
    check=confirmation['checks'][0]
    check.update(author_field_path='',author_quote='',non_colour_basis=['reference_images[1]'],
                 reference_image_number=3,reference_use='character_identity')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)
    check.update(non_colour_basis=['reference_images[0]'],reference_image_number=99)
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)
    check['reference_image_number']=2
    assert _review_from_confirmation(payload,raw,confirmation)['consistent'] is False


def test_source_clarification_explained_without_claiming_conflict():
    from novel_manga.application.agents.episode.first_frames import _source_issue_message
    clarification={'kind':'continuity_clarification','continuity_verified':True}
    assert _source_issue_message([clarification])=='需要作者澄清当前开场状态'
    assert _source_issue_message([clarification,{'relation':'contradiction'}])=='opening source fields conflict'


def test_text_quote_must_name_same_subject_and_match_cited_field():
    payload,raw,confirmation=example()
    payload['opening']['opening']['start_state']='B is standing.'
    payload['author_text_fields']['opening.start_state']='B is standing.'
    check=confirmation['checks'][0]
    check.update(author_quote='B is standing.',non_colour_expected='B is standing.',author_subject='B')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)
    check.update(author_field_path='image_prompt',author_quote='A is seated.',non_colour_expected='A is seated.',author_subject='A')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)


def test_normal_entry_invented_text_stops_without_grant_and_retains_receipts(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.application.agents.episode.tools import EpisodeTools
    from novel_manga.llm.config import JsonEndpoint
    from novel_manga.util import read_json
    from support.first_frame_cases import character_case
    ctx,clip,_,_=character_case(tmp_path,monkeypatch)
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:JsonEndpoint(
        'mock-review-model',('http://127.0.0.1:8789/v1',),stream=True,min_max_tokens=0))
    monkeypatch.setattr(first_frames,'build',lambda ctx,row,path:Image.new('RGB',(8,16)).save(path))
    _,raw,confirmation=example()
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**kw:copy.deepcopy(
        raw if kw['name']=='episode_first_frame' else confirmation))
    result=EpisodeTools(ctx).first_frames({})['clips'][clip['clip_id']]
    assert result['stage']=='review_incomplete' and result['retryable'] is False
    assert 'repair_grant' not in result
    candidate=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/clip['clip_id']/'candidate_01'
    saved=read_json(candidate/'review-incomplete.json')
    assert saved['initial_review_raw']==raw and saved['confirmation_raw']==confirmation
    assert not (candidate/'accepted.json').exists() and not (candidate/'rejected.json').exists()


def test_authored_colour_does_not_accept_added_colour_outside_quote():
    payload,raw,confirmation=example()
    payload['opening']['opening']['start_state']='A stands by the window.'
    payload['author_text_fields']['opening.start_state']='A stands by the window.'
    raw['findings'][0].update(observed='A wears a blue jacket.',
        expected='A stands by the window. A wears a red jacket.')
    confirmation['checks'][0].update(evidence_class='authored_explicit_colour',
        author_field_path='opening.start_state',author_quote='A stands by the window.',author_subject='A')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)


def test_reference_card_wardrobe_pointer_does_not_need_fabricated_text_quote():
    payload,raw,confirmation=example()
    payload['opening']['character_facts']=[{'name':'A','facts':{'wardrobe':{'value':'严格以综合视觉卡为准'}}}]
    payload['author_text_fields']['character_facts[0].facts.wardrobe.value']='严格以综合视觉卡为准'
    payload['reference_images'][0].update(reference_scope='wardrobe_design',name='A')
    check=confirmation['checks'][0]
    check.update(author_field_path='',author_quote='',author_subject='',
        non_colour_observation='A 穿短裤，没有外套',non_colour_expected='参考卡展示长裤及带帽外套',
        non_colour_basis=['reference_images[0]','character_facts[0].facts.wardrobe.value'],
        reference_image_number=2,reference_use='wardrobe')
    assert _review_from_confirmation(payload,raw,confirmation)['consistent'] is False


def test_authored_colour_final_expected_contains_only_verified_quote():
    payload,raw,confirmation=example()
    payload['opening']['opening']['start_state']='A wears a red jacket.'
    payload['author_text_fields']['opening.start_state']='A wears a red jacket.'
    raw['findings'][0].update(observed='A wears a blue jacket.',
        expected='A wears a red jacket. A wears red trousers too.')
    confirmation['checks'][0].update(evidence_class='authored_explicit_colour',
        author_field_path='opening.start_state',author_quote='red jacket',author_subject='jacket')
    result=_review_from_confirmation(payload,raw,confirmation)
    assert result['findings'][0]['expected']=='red jacket'
    assert 'red trousers' not in result['problems'][0]


def test_wardrobe_card_must_belong_to_same_character():
    payload,raw,confirmation=example()
    payload['opening']['character_facts']=[{'name':'A','facts':{'wardrobe':{'value':'严格以综合视觉卡为准'}}}]
    payload['author_text_fields']['character_facts[0].facts.wardrobe.value']='严格以综合视觉卡为准'
    payload['reference_images'][0].update(reference_scope='wardrobe_design',name='B')
    confirmation['checks'][0].update(author_field_path='',author_quote='',author_subject='',
        non_colour_observation='A 穿短裤',non_colour_expected='参考卡展示长裤',
        non_colour_basis=['reference_images[0]','character_facts[0].facts.wardrobe.value'],
        reference_image_number=2,reference_use='wardrobe')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)


def test_noncolour_chinese_word_mingbai_does_not_claim_a_colour():
    payload,raw,confirmation=example()
    payload['opening']['opening']['start_state']='A明白安全注意事项。'
    payload['author_text_fields']['opening.start_state']='A明白安全注意事项。'
    raw['findings'][0].update(observed='A穿蓝色外套。',expected='A明白安全注意事项。A穿红色外套。')
    confirmation['checks'][0].update(evidence_class='authored_explicit_colour',author_field_path='opening.start_state',
        author_quote='A明白安全注意事项。',author_subject='A')
    with pytest.raises(FirstFrameReviewUncertain):
        _review_from_confirmation(payload,raw,confirmation)
