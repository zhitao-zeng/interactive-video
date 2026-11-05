"""Current source facts and explicit visual contradictions in one first-frame review."""
import copy

from PIL import Image
import pytest

from novel_manga.application.agents.episode.first_frames import FirstFrameOperations, check
from novel_manga.media.first_frames import current_contract, require_current, source_asset_state
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case, character_case, fixture_book


def finding(relation='contradiction'):
    return {'relation': relation, 'observed': 'bare feet and shorts',
            'expected': 'grey long trousers and grey hiking shoes',
            'basis': ['character_facts[0].facts.wardrobe'], 'reason': 'visible clothing is incompatible'}


def test_contract_carries_current_look_and_opening_equipment_without_requiring_a_face(tmp_path, monkeypatch):
    ctx, clip, row, _ = character_case(tmp_path, monkeypatch)
    contract = current_contract(ctx, clip, row['image_references'])
    facts = contract['character_facts']
    assert [r['name'] for r in facts] == ['A']
    assert facts[0]['facts']['wardrobe'] == {'value': 'grey long trousers and grey hiking shoes',
                                           'source': 'story_bible.json:characters[0].wardrobe'}
    assert contract['opening']['device_state'] == 'headlamp stays inside the backpack'
    assert contract['opening']['in_frame'] == []


def test_current_phase_replaces_base_wardrobe_and_keeps_the_fact_source(tmp_path, monkeypatch):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    atomic_write_json(ctx.novel_dir/'series_assets/phases.json',{'characters':{'A':[
        {'from':1,'to':2,'base_costume':'blue uniform','hair':''}]}})
    facts=current_contract(ctx,clip,row['image_references'])['character_facts'][0]['facts']
    assert facts['wardrobe']=={'value':'blue uniform',
        'source':'series_assets/phases.json:characters.A[0].base_costume'}
    assert 'hair'not in facts


def test_check_requests_traceable_findings_from_same_images_and_current_facts(tmp_path, monkeypatch):
    ctx, clip, row, _ = character_case(tmp_path, monkeypatch)
    captured = {}
    answer = {'consistent': True, 'problems': [], 'observations': ['bare feet'], 'findings': [finding()]}
    def ask(parts, schema, **kwargs):
        captured.update(parts=parts, schema=schema, kwargs=kwargs)
        return answer
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    monkeypatch.setattr('novel_manga.llm.client.image_part', lambda p, size: {'path': str(p)})
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings', lambda: None)
    verdict=check(ctx, ctx.novel_dir/'opening.png', row,
                  current_contract(ctx, clip, row['image_references']))
    assert verdict['raw']==answer
    assert verdict['consistent']is False
    prompt = captured['parts'][0]['text']
    assert 'grey long trousers' in prompt and 'headlamp stays inside' in prompt
    assert '可见部位' in prompt and 'underspecified' in prompt
    schema = captured['schema']['properties']['findings']['items']['properties']
    assert set(schema['relation']['enum']) == {'contradiction', 'underspecified', 'compatible'}
    assert {'observed', 'expected', 'basis', 'reason'} <= set(schema)
    assert set(captured['schema']['properties'])=={'observations','findings'}
    assert len(captured['parts']) == 4


@pytest.mark.parametrize('relation,accepted', [('contradiction', False), ('underspecified', True), ('compatible', True)])
def test_explicit_finding_controls_adoption_and_render_even_when_top_level_passes(tmp_path, monkeypatch, relation, accepted):
    ctx, clip, row, _ = character_case(tmp_path, monkeypatch)
    raw = {'consistent': True, 'problems': [], 'observations': ['only a lower leg is visible'],
           'findings': [finding(relation)]}
    candidate = ctx.novel_dir/'first_frames/book_1/clip_01/candidate_01'
    legacy_contract=current_contract(ctx,clip,row['image_references'])
    legacy_contract.pop('character_facts');legacy_contract['opening'].pop('device_state')
    legacy={k:row[k] for k in ('image_prompt','image_references','visible_characters')}
    legacy.update(source_contract=legacy_contract,source_assets=source_asset_state(ctx,row['image_references']))
    atomic_write_json(candidate/'input.json',legacy)
    Image.new('RGB',(8,16)).save(candidate/'opening.png')
    old_raw={'consistent':True,'observations':['shorts and bare feet'],'problems':[]}
    atomic_write_json(candidate/'review.json',old_raw)
    clip['first_frame']={'path':str((candidate/'opening.png').relative_to(ctx.novel_dir)),
                         'source_contract':legacy_contract,'review':old_raw}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    require_current(ctx,clip)  # Legacy acceptance is not retroactively certified against new facts.
    queued = []
    result = FirstFrameOperations(ctx, builder=lambda *a: pytest.fail('Retain the paid pixels'),
                                  checker=lambda *a: copy.deepcopy(raw), on_accepted=queued.append).prepare({})
    assert result['ok'] is accepted and queued == (['clip_01'] if accepted else [])
    candidate = ctx.novel_dir/'first_frames/book_1/clip_01/candidate_01'
    assert read_json(candidate/'review.json') == old_raw
    if accepted:
        require_current(ctx, read_json(ctx.episode_dir/'clip_plan.json')['clips'][0])
    else:
        rejected=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
        assert rejected['first_frame']['path']==clip['first_frame']['path']
        with pytest.raises(ValueError,match='not approved'):
            require_current(ctx,rejected)
        # A retained frame cannot evade the same gate with its old top-level pass.
        clip['first_frame'] = {'path': str((candidate/'opening.png').relative_to(ctx.novel_dir)),
                               'review': raw, 'source_contract': current_contract(ctx, clip, row['image_references'])}
        with pytest.raises(ValueError, match='not approved'):
            require_current(ctx, clip)


@pytest.mark.parametrize('image_already_exists', [True, False])
def test_saved_image_is_reviewed_with_new_facts_without_rebilling_or_destroying_raw_history(tmp_path, monkeypatch, image_already_exists):
    ctx, clip, row, bible = character_case(tmp_path, monkeypatch)
    contract = current_contract(ctx, clip, row['image_references'])
    contract.pop('character_facts', None)
    contract['opening'].pop('device_state')
    legacy = {k: row[k] for k in ('image_prompt', 'image_references', 'visible_characters')}
    legacy.update(source_contract=contract, source_assets=source_asset_state(ctx, row['image_references']))
    root = ctx.novel_dir/'first_frames/book_1/clip_01'
    old = root/'candidate_01'
    atomic_write_json(old/'input.json', legacy)
    if image_already_exists:Image.new('RGB', (8, 16)).save(old/'opening.png')
    atomic_write_json(old/'opening.png.task.json', {'task_id': 'paid-task'})
    raw = {'consistent': True, 'observations': ['shorts and bare feet'], 'problems': []}
    atomic_write_json(old/'review.json', raw)
    for i in (2, 3):
        atomic_write_json(root/f'candidate_{i:02d}'/'input.json', {'older_variant': i})
    protected = {p: p.read_bytes() for p in old.iterdir() if p.is_file()}
    calls = []
    def checker(ctx, path, row, contract):
        calls.append(copy.deepcopy(contract))
        relation='compatible' if contract['character_facts'][0]['facts']['wardrobe']['value']==bible['characters'][0]['wardrobe'] and len(calls)==1 else 'contradiction'
        return {'consistent': True, 'problems': [], 'findings': [finding(relation)]}
    built=[]
    def no_build(*a):
        ctx, row, path=a
        assert not image_already_exists and not built and path==old/'opening.png'
        assert read_json(path.with_suffix('.png.task.json'))=={'task_id':'paid-task'}
        built.append(path)
        Image.new('RGB',(8,16)).save(path)
    op = lambda: FirstFrameOperations(ctx, builder=no_build, checker=checker).prepare({})
    assert op()['ok'] and len(calls) == 1
    assert calls[0]['character_facts'][0]['facts']['wardrobe']['value'] == bible['characters'][0]['wardrobe']
    assert op()['ok'] and len(calls) == 1
    selected = read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    require_current(ctx, selected)
    bible['characters'][0]['wardrobe'] = 'blue long trousers and blue shoes'
    atomic_write_json(ctx.novel_dir/'story_bible.json', bible)
    with pytest.raises(ValueError, match='stale'):
        require_current(ctx, selected)
    assert not op()['ok'] and len(calls) == 2
    with pytest.raises(ValueError, match='not approved'):
        require_current(ctx, read_json(ctx.episode_dir/'clip_plan.json')['clips'][0])
    assert not op()['ok'] and len(calls) == 2
    assert len(list(root.glob('candidate_*'))) == 3
    assert all(p.read_bytes() == data for p, data in protected.items())
    assert len(list((old/'reviews').glob('*.json'))) == 2
    # A changed opening is a different image request, even if its prompt was left unchanged.
    selected = read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    selected['shots'][0]['camera'] = 'rear-facing camera'
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [selected]})
    assert not op()['ok'] and len(calls) == 2


def test_fast_uses_same_rejection_and_rechecks_retained_pixels_after_fact_change(tmp_path, monkeypatch):
    
    from novel_manga.interactive.sd_first_frames import prepare
    book,episode=fixture_book(tmp_path)
    monkeypatch.setattr('novel_manga.style_packs.frame_style',lambda novel:{})
    monkeypatch.setattr('novel_manga.style_packs.first_frame_prompt',lambda text,style:text)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',lambda *a:{})
    calls=[]
    def build(ctx,row,path):
        calls.append('image')
        Image.new('RGB',(8,16)).save(path)
        atomic_write_json(path.with_suffix('.png.task.json'),{'task_id':'fast-paid'})
    def checker(ctx,path,row,contract):
        calls.append('review')
        return {'consistent':True,'problems':[],
                'findings':[finding()] if contract['character_facts'] else []}
    prepare(tmp_path,book,builder=build,checker=checker)
    old=book/'first_frames'/episode.name/'clip_01/candidate_01'
    # An existing source had device_state, while the historical audit did not store it.
    script=read_json(episode/'chapter_script.json')
    script['shots'][0]['device_state']='lamp is inside backpack'
    atomic_write_json(episode/'chapter_script.json',script)
    saved=read_json(old/'sd-input.json');saved['source_contract'].pop('character_facts')
    atomic_write_json(old/'sd-input.json',saved)
    plan=read_json(episode/'clip_plan.json');frame=plan['clips'][0]['first_frame']
    frame['source_contract'].pop('character_facts');frame.pop('review_record')
    atomic_write_json(episode/'clip_plan.json',plan)
    protected={p:p.read_bytes() for p in old.iterdir() if p.is_file()}
    atomic_write_json(book/'story_bible.json',{'characters':[{'name':'A','wardrobe':'long trousers'}]})
    ctx=type('Context',(),{'novel_dir':book,'episode_dir':episode})()
    require_current(ctx,plan['clips'][0])
    with pytest.raises(ValueError,match='review rejected'):
        prepare(tmp_path,book,builder=build,checker=checker)
    with pytest.raises(ValueError,match='review rejected'):
        prepare(tmp_path,book,builder=build,checker=checker)
    assert calls==['image','review','review']
    assert all(p.read_bytes()==value for p,value in protected.items())
    assert len(list(old.parent.glob('candidate_*')))==1
    clip=read_json(episode/'clip_plan.json')['clips'][0]
    with pytest.raises(ValueError,match='not approved'):
        require_current(ctx,clip)


def test_rejected_alternative_preserves_a_different_current_selection(tmp_path,monkeypatch):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    Image.new('RGB',(8,16)).save(ctx.novel_dir/'selected.png')
    clip['first_frame']={'path':'selected.png','review':{'consistent':True,'problems':[]},
        'source_contract':current_contract(ctx,clip,row['image_references'])}
    selected=copy.deepcopy(clip['first_frame'])
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    result=FirstFrameOperations(ctx,builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),
        checker=lambda *a:{'consistent':True,'problems':[],'findings':[finding()]}).prepare({})
    assert not result['ok']
    current=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    assert current['first_frame']==selected
    require_current(ctx,current)


def test_fast_pending_image_keeps_original_request_when_review_facts_are_added(tmp_path, monkeypatch):
    
    from novel_manga.interactive.sd_first_frames import prepare
    book, episode = fixture_book(tmp_path)
    monkeypatch.setattr('novel_manga.style_packs.frame_style', lambda novel: {})
    monkeypatch.setattr('novel_manga.style_packs.first_frame_prompt', lambda text, style: text)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources', lambda *a: {})
    sent = []
    submitted = []
    reviews = []

    def build(ctx, row, path):
        sent.append(row['request_prompt'])
        task = path.with_suffix('.png.task.json')
        if not task.exists():
            submitted.append('already-paid-task')
            atomic_write_json(task, {'task_id': submitted[-1]})
            raise TimeoutError('supplier task still running')
        Image.new('RGB', (8, 16)).save(path)

    def check(ctx, path, row, contract):
        reviews.append(contract['character_facts'])
        return {'consistent': True, 'problems': [], 'findings': []}

    with pytest.raises(TimeoutError, match='still running'):
        prepare(tmp_path, book, builder=build, checker=check)
    atomic_write_json(book/'story_bible.json', {'characters': [{'name': 'A', 'wardrobe': 'long trousers'}]})
    prepare(tmp_path, book, builder=build, checker=check)
    assert sent[0] == sent[1]
    assert len(submitted) == 1 and len(reviews) == 1 and reviews[0]
    assert len(list((book/'first_frames'/episode.name/'clip_01').glob('candidate_*'))) == 1


@pytest.mark.parametrize('relation,accepted',[('compatible',True),('contradiction',False)])
def test_explicit_reuse_reviews_saved_pixels_after_camera_change_without_new_image_capacity(tmp_path,monkeypatch,relation,accepted):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    calls=[]
    def checker(ctx,path,row,contract):
        calls.append(copy.deepcopy(contract))
        return {'consistent':True,'problems':[],'findings':[] if len(calls)==1 else [finding(relation)]}
    FirstFrameOperations(ctx,builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),checker=checker).prepare({})
    root=ctx.novel_dir/'first_frames/book_1/clip_01';old=root/'candidate_01'
    atomic_write_json(old/'opening.png.task.json',{'task_id':'paid-original'})
    for i in (2,3):atomic_write_json(root/f'candidate_{i:02d}'/'input.json',{'old_variant':i})
    protected={p:p.read_bytes() for p in old.rglob('*') if p.is_file()}
    plan=read_json(ctx.episode_dir/'clip_plan.json');plan['clips'][0]['shots'][0]['camera']='stable selfie camera'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    def no_build(*args):pytest.fail('explicit reuse never buys another image')
    op=lambda:FirstFrameOperations(ctx,builder=no_build,checker=checker).prepare({})
    assert not op()['ok'] and len(calls)==1  # Normal source-change matching stays strict.
    row['reuse_candidate']='candidate_01'
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    result=op()
    assert result['ok'] is accepted and len(calls)==2
    assert calls[-1]['opening']['camera']=='stable selfie camera'
    assert calls[-1]['character_facts'][0]['facts']['wardrobe']['value']=='grey long trousers and grey hiking shoes'
    assert len(list(root.glob('candidate_*')))==3
    # Current accepted.json may advance; immutable request, image, receipt and raw reviews do not.
    assert all(p.read_bytes()==value for p,value in protected.items() if p.name!='accepted.json')
    selected=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    if accepted:require_current(ctx,selected)
    else:
        with pytest.raises(ValueError,match='not approved'):require_current(ctx,selected)
    assert op()['ok'] is accepted and len(calls)==2


@pytest.mark.parametrize('requested',['../other/candidate_01','candidate_99',None])
def test_explicit_reuse_requires_an_existing_image_owned_by_the_selected_clip(tmp_path,monkeypatch,requested):
    ctx,_,row,_=character_case(tmp_path,monkeypatch)
    row['reuse_candidate']=requested
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    with pytest.raises(ValueError,match='existing generated candidate'):
        FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no paid image'),
            checker=lambda *a:pytest.fail('no external review')).prepare({})
    assert not list((ctx.novel_dir/'first_frames/book_1/clip_01').glob('candidate_*'))


@pytest.mark.parametrize('relation,accepted',[('underspecified',True),('compatible',True),('contradiction',False)])
def test_relations_override_free_summary_and_raw_is_saved_independently(tmp_path,monkeypatch,relation,accepted):
    ctx,clip,row,_=character_case(tmp_path,monkeypatch)
    uncertain=finding('underspecified')
    uncertain.update(observed='phone and selfie stick are outside the frame',expected='phone remains mounted',
                     reason='mounting is not visible and cannot be confirmed')
    raw={'observations':['only wrist strap is visible'],
         'findings':[uncertain,finding(relation)],'consistent':False,
         'problems':['phone and stick are not shown; mounting cannot be confirmed']}
    result=FirstFrameOperations(ctx,builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),
        checker=lambda *a:copy.deepcopy(raw)).prepare({})
    assert result['ok']is accepted
    candidate=ctx.novel_dir/'first_frames/book_1/clip_01/candidate_01'
    saved=read_json(candidate/'reviews/review_001.json')
    assert saved['raw']==raw and read_json(candidate/'review.json')==raw
    assert saved['review']['consistent']is accepted
    assert saved['review']['problems']==([]if accepted else [finding()['reason']])
    if accepted:
        selected=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
        require_current(ctx,selected)
        selected['first_frame']['review']=raw
        require_current(ctx,selected)  # Rendering interprets old mixed structured reports consistently.
    cached=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no new image'),
        checker=lambda *a:pytest.fail('same evidence is cached')).prepare({})
    assert cached['ok']is accepted


@pytest.mark.parametrize('raw,accepted',[
    ({'consistent':True,'problems':[]},True),
    ({'consistent':False,'problems':[]},False),
    ({'consistent':True,'problems':['actual legacy failure']},False)])
def test_legacy_review_without_findings_keeps_its_original_decision(tmp_path,monkeypatch,raw,accepted):
    ctx,_,_,_=character_case(tmp_path,monkeypatch)
    result=FirstFrameOperations(ctx,builder=lambda ctx,row,path:Image.new('RGB',(8,16)).save(path),
        checker=lambda *a:copy.deepcopy(raw)).prepare({})
    assert result['ok']is accepted
    record=read_json(ctx.novel_dir/'first_frames/book_1/clip_01/candidate_01/reviews/review_001.json')
    assert record['raw']==record['review']==raw

