"""A grounded adjacent-shot ambiguity asks the existing author, without a retake."""
import copy
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from novel_manga.application.agents.episode import opening_source
from novel_manga.application.agents.episode.input_contracts import opening_grant, validate_grant
from novel_manga.application.agents.episode.arguments import RepairArgumentError
from novel_manga.media.first_frames import opening_contract
from novel_manga.util import atomic_write_json, read_json


def case(tmp_path):
    book=tmp_path/'book';episode=book/'book_1';run=tmp_path/'run'
    episode.mkdir(parents=True);run.mkdir()
    atomic_write_json(book/'profile.json',{'h3_input_mode':'first-frame'})
    shots=[
        {'index':5,'scene_id':'scene_a','start_state':'A holds an item.',
         'event':'A puts it on the table.','end_state':'The item remains open on the table.'},
        {'index':6,'scene_id':'scene_a','scene_time':'early morning',
         'scene_transition':'the previous scene entered before this shot',
         'start_state':'A closes the item but does not wear it.',
         'event':'B reaches for the lamp.','end_state':'B pauses before touching the lamp.',
         'opening_frame':{'image_prompt':'A closes the item but does not wear it.',
                          'visible_characters':['A'],'reference_subjects':['A']}},
        {'index':7,'scene_id':'scene_a','scene_time':'day','scene_transition':'continuous',
         'start_state':'A stands beside the lamp.',
         'event':'A speaks to B.','end_state':'A looks toward the sea.',
         'opening_frame':{'image_prompt':'A stands beside the lamp; equipment follows the scene.',
                          'visible_characters':['A'],'reference_subjects':['A']}},
        {'index':8,'scene_id':'scene_b','start_state':'A arrives at a new location.',
         'event':'A enters.','end_state':'A is inside.',
         'opening_frame':{'image_prompt':'A arrives at a new location.',
                          'visible_characters':['A'],'reference_subjects':['A']}}]
    ref={'role':'character','name':'A','path':'actor.png'}
    Image.new('RGB',(8,16)).save(book/'actor.png')
    atomic_write_json(book/'story_bible.json',{'characters':[{'name':'A'}],'props':[]})
    atomic_write_json(run/'catalog.json',[ref])
    clips=[{'clip_id':f'clip_{i:02d}','kind':'video','shot_indexes':[i],
            'references':[ref],'shots':[copy.deepcopy(next(x for x in shots if x['index']==i))]}
           for i in (6,7,8)]
    atomic_write_json(episode/'chapter_script.json',{'shots':shots})
    atomic_write_json(episode/'clip_plan.json',{'clips':clips})
    ctx=SimpleNamespace(novel_dir=book,episode_dir=episode,run_dir=run,work_dir=run/'sandbox')
    return ctx,shots,{'clips':clips}


def prepared(plan,shots):
    byindex={shot['index']:shot for shot in shots}
    return [({'clip_id':clip['clip_id']},
             opening_contract(clip,[],shots=[byindex[clip['shot_indexes'][0]]]),[])
            for clip in plan['clips']]


def clarification():
    return {'kind':'continuity_clarification','origin':'source','relation':'underspecified',
            'basis':['opening.start_state','previous_shot_context.start_state'],
            'observed':'A stands beside the lamp.',
            'expected':'A closes the item but does not wear it.',
            'reason':'Maybe A carries the item in her hands; require that invented alternative.'}


def contradiction():
    return {'origin':'source','relation':'contradiction','observed':'A stands',
            'expected':'A sits','basis':['opening.start_state','opening.camera'],
            'reason':'Two current source states are mutually exclusive.'}


def test_previous_context_is_same_scene_adjacent_and_can_use_candidate_script(tmp_path):
    ctx,shots,plan=case(tmp_path);clips={c['clip_id']:c for c in plan['clips']}
    before=opening_source.previous_shot_context(ctx,clips['clip_07'])
    assert before=={'shot_index':6,'scene_id':'scene_a',
                   **{name:shots[1][name] for name in ('scene_time','start_state','event','end_state')}}
    assert 'scene_transition' not in before
    assert opening_source.current_time_context(ctx,clips['clip_07'])=={
        'scene_time':'day','scene_transition':'continuous'}
    assert 'scene_time' not in opening_source.previous_shot_context(ctx,clips['clip_06'])
    assert opening_source.previous_shot_context(ctx,clips['clip_08']) is None
    candidate=copy.deepcopy(shots)
    candidate[1]['start_state']='A explicitly changes the item state.'
    candidate[2]['start_state']='A stands after the changed state.'
    assert opening_source.previous_shot_context(ctx,clips['clip_07'],shots=candidate)['start_state']==candidate[1]['start_state']
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots'][1]==shots[1]


def test_grounded_clarification_preserves_raw_but_routes_neutral_cited_feedback(tmp_path,monkeypatch):
    ctx,shots,plan=case(tmp_path);calls=[]
    def ask(parts,schema,**kwargs):
        calls.append((parts,kwargs))
        payload=json.loads(parts[0]['text'].rsplit('\n',1)[1])
        assert payload['clips'][1]['previous_shot_context']['start_state']==shots[1]['start_state']
        return {'reviews':[{'clip_id':'clip_06','findings':[]},
                           {'clip_id':'clip_07','findings':[clarification()]},
                           {'clip_id':'clip_08','findings':[]}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    reviews=opening_source.review_batch(ctx,prepared(plan,shots))
    assert len(calls)==1 and all(p['type']=='text' for p in calls[0][0])
    item=reviews['clip_07']['findings'][0]
    assert item['continuity_verified'] is True
    assert item['relation']=='underspecified' and item['kind']=='continuity_clarification'
    assert 'A closes the item but does not wear it.' in item['reason']
    assert 'A stands beside the lamp.' in item['reason']
    assert 'carries the item in her hands' not in item['reason']
    assert reviews['clip_07']['raw_preflight']['findings'][0]['reason']==clarification()['reason']
    assert opening_source.review_batch(ctx,prepared(plan,shots))==reviews and len(calls)==1
    current=prepared(plan,shots)[1][1]['opening']
    assert opening_source.current_review(ctx,'clip_07',current)==reviews['clip_07']
    assert opening_source.current_review(ctx,'clip_07',current,None,None) is None


def test_real_status_and_grant_find_cached_source_clarification(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import first_frames
    ctx,shots,plan=case(tmp_path)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[clarification()]},
        {'clip_id':'clip_08','findings':[]}]})
    opening_source.review_batch(ctx,prepared(plan,shots))
    clip=plan['clips'][1]
    state=first_frames.status(ctx,plan)
    row=state['clips']['clip_07']
    assert row['repair_origin']=='source'
    assert row['repair_grant']['operation']=='input'
    assert row['repair_grant']['shot_indexes']==[7]
    assert row['repair_grant']['shot_fields']==['opening_frame','start_state']
    assert 'carries the item in her hands' not in str(row)
    grant=first_frames.current_frame_grant(ctx,clip)
    assert grant['operation']=='input' and grant['shot_indexes']==[7]


@pytest.mark.parametrize('change',[
    {'basis':['opening.camera','previous_shot_context.start_state']},
    {'basis':['opening.start_state','previous_shot_context.missing']},
    {'basis':['previous_shot_context.start_state','opening.start_state']},
    {'relation':'contradiction'},
    {'origin':'image'},
])
def test_invalid_continuity_evidence_is_rejected_before_cache(tmp_path,monkeypatch,change):
    ctx,shots,plan=case(tmp_path);bad={**clarification(),**change}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[bad]},
        {'clip_id':'clip_08','findings':[]}]})
    with pytest.raises(ValueError):opening_source.review_batch(ctx,prepared(plan,shots))
    assert not (ctx.run_dir/'opening-source-checks.json').exists()


@pytest.mark.parametrize('copied',[
    {'observed':'','expected':''},
    {'observed':'incorrect current copy','expected':'incorrect previous copy'},
])
def test_host_supplies_exact_selected_fields_even_if_model_copies_badly(tmp_path,monkeypatch,copied):
    ctx,shots,plan=case(tmp_path);raw={**clarification(),**copied}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[raw]},
        {'clip_id':'clip_08','findings':[]}]})
    review=opening_source.review_batch(ctx,prepared(plan,shots))['clip_07']
    assert review['raw_preflight']['findings'][0]==raw
    finding=review['findings'][0]
    assert finding['observed']==shots[2]['start_state']
    assert finding['expected']==shots[1]['start_state']
    assert shots[2]['start_state'] in finding['reason'] and shots[1]['start_state'] in finding['reason']
    assert 'incorrect' not in finding['reason']


def test_clarification_grant_only_current_opening_and_start_and_never_other_underspecified(tmp_path):
    _,_,plan=case(tmp_path);clip=plan['clips'][1]
    assert opening_grant(clip,[clarification()],{}) is None
    generic={**clarification(),'kind':'other','continuity_verified':True}
    assert opening_grant(clip,[generic],{}) is None
    verified={**clarification(),'continuity_verified':True}
    grant=opening_grant(clip,[verified],{})
    assert grant.operation=='input' and grant.shot_indexes==(7,)
    assert grant.shot_fields=={'opening_frame','start_state'}
    validate_grant({'shots':[{'index':7,'start_state':'author decision',
                               'opening_frame':{'image_prompt':'author decision'}}]},grant)
    with pytest.raises(RepairArgumentError):validate_grant({'shots':[{'index':6,'start_state':'change previous'}]},grant)
    with pytest.raises(RepairArgumentError):validate_grant({'shots':[{'index':7,'turns':[]}]},grant)


def test_pending_context_is_targeted_read_only_and_candidate_aware(tmp_path,monkeypatch):
    ctx,shots,plan=case(tmp_path)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[]},
        {'clip_id':'clip_08','findings':[]}]})
    opening_source.review_batch(ctx,prepared(plan,shots))
    cache=ctx.run_dir/'opening-source-checks.json';original=cache.read_bytes()
    assert opening_source.pending_context(ctx,plan)=={}
    candidate=copy.deepcopy(shots);candidate[1]['event']='B moves before the current shot.'
    result=opening_source.pending_context(ctx,plan,shots=candidate)
    assert set(result)=={'clip_07'}
    assert result['clip_07']['previous_shot_context']['event']==candidate[1]['event']
    assert cache.read_bytes()==original
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots']==shots
    candidate=copy.deepcopy(shots);candidate[2]['scene_time']='night'
    assert set(opening_source.pending_context(ctx,plan,shots=candidate))=={'clip_07'}
    assert opening_source.pending_context(ctx,plan,shots=candidate)['clip_07']['current_time_context']['scene_time']=='night'
    candidate=copy.deepcopy(shots);candidate[2]['scene_transition']='after a jump in time'
    assert set(opening_source.pending_context(ctx,plan,shots=candidate))=={'clip_07'}
    candidate=copy.deepcopy(shots);candidate[1]['scene_time']='evening'
    assert set(opening_source.pending_context(ctx,plan,shots=candidate))=={'clip_06','clip_07'}
    candidate=copy.deepcopy(shots);candidate[1]['scene_transition']='different entry into the previous shot'
    assert set(opening_source.pending_context(ctx,plan,shots=candidate))=={'clip_06'}


def test_same_candidate_changes_both_adjacent_shots_without_reading_old_disk(tmp_path,monkeypatch):
    ctx,shots,plan=case(tmp_path);requests=[]
    def ask(parts,schema,**kwargs):
        payload=json.loads(parts[0]['text'].rsplit('\n',1)[1]);requests.append(payload)
        return {'reviews':[{'clip_id':row['clip_id'],'findings':[]} for row in payload['clips']]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    opening_source.review_batch(ctx,prepared(plan,shots))
    candidate=copy.deepcopy(shots)
    candidate[1]['start_state']='The author explicitly changes the previous shot.'
    candidate[2]['start_state']='The author explicitly changes the current shot.'
    changed=prepared(plan,candidate)
    opening_source.review_batch(ctx,changed,shots=candidate)
    transmitted=next(row for row in requests[-1]['clips'] if row['clip_id']=='clip_07')
    assert transmitted['previous_shot_context']['start_state']==candidate[1]['start_state']
    assert transmitted['opening']['start_state']==candidate[2]['start_state']
    assert opening_source.current_review(ctx,'clip_07',changed[1][1]['opening'],shots=candidate)['consistent']
    assert read_json(ctx.episode_dir/'chapter_script.json')['shots']==shots


def test_clarification_cannot_cite_a_previous_shot_across_scene_boundary(tmp_path,monkeypatch):
    ctx,shots,plan=case(tmp_path)
    assert opening_source.previous_shot_context(ctx,plan['clips'][2]) is None
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[]},
        {'clip_id':'clip_08','findings':[clarification()]}]})
    with pytest.raises(ValueError,match='same-scene historical source'):
        opening_source.review_batch(ctx,prepared(plan,shots))
    assert not (ctx.run_dir/'opening-source-checks.json').exists()


def test_no_v2_record_does_not_recheck_historical_frames(tmp_path):
    ctx,_,plan=case(tmp_path)
    atomic_write_json(ctx.run_dir/'opening-source-checks.json',{
        'policy':'first-frame-opening-source-v1','reviews':{'older':{'clip_id':'clip_07'}}})
    original=(ctx.run_dir/'opening-source-checks.json').read_bytes()
    assert opening_source.pending_context(ctx,plan)=={}
    assert (ctx.run_dir/'opening-source-checks.json').read_bytes()==original


def test_v1_source_audit_records_are_retained_when_v2_is_first_checked(tmp_path,monkeypatch):
    ctx,shots,plan=case(tmp_path)
    prior={'clip_id':'clip_07','opening':{'start_state':'old approved opening'},
           'review':{'findings':[],'consistent':True,'problems':[]}}
    atomic_write_json(ctx.run_dir/'opening-source-checks.json',{
        'policy':'first-frame-opening-source-v1','reviews':{'original_key':prior}})
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{'reviews':[
        {'clip_id':'clip_06','findings':[]},{'clip_id':'clip_07','findings':[]},
        {'clip_id':'clip_08','findings':[]}]})
    opening_source.review_batch(ctx,prepared(plan,shots))
    saved=read_json(ctx.run_dir/'opening-source-checks.json')
    assert saved['policy']==opening_source.POLICY and saved['reviews']['original_key']==prior
    assert len(saved['reviews'])==4


def test_preflight_sees_scoped_contract_facts_and_rechecks_only_affected_clip(tmp_path,monkeypatch):
    from novel_manga.media.first_frames import current_contract
    ctx,shots,plan=case(tmp_path);calls=[]
    atomic_write_json(ctx.novel_dir/'story_bible.json',{
        'characters':[{'name':'A','appearance':'brown hair'}],
        'props':[{'name':'lamp','appearance':'brass'}]})
    def source_prepared():
        return [({'clip_id':clip['clip_id']},current_contract(ctx,clip,[]),[]) for clip in plan['clips']]
    def ask(parts,schema,**kwargs):
        payload=json.loads(parts[0]['text'].rsplit('\n',1)[1]);calls.append(payload)
        return {'reviews':[{'clip_id':row['clip_id'],'findings':[]} for row in payload['clips']]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    original=source_prepared();opening_source.review_batch(ctx,original)
    for entry,(_,contract,_) in zip(calls[0]['clips'],original):
        assert entry['opening']==contract['opening']
        assert entry['character_facts']==contract['character_facts']
        assert entry['prop_facts']==contract.get('prop_facts',[])
        assert 'image_references' not in entry and 'style' not in entry
    assert opening_source.pending_context(ctx,plan)=={}
    atomic_write_json(ctx.novel_dir/'story_bible.json',{
        'characters':[{'name':'A','appearance':'brown hair'}],
        'props':[{'name':'lamp','appearance':'copper'}]})
    affected={entry['clip_id'] for entry in calls[0]['clips'] if entry['prop_facts']}
    assert affected and affected!={c['clip_id'] for c in plan['clips']}
    assert set(opening_source.pending_context(ctx,plan))==affected
    opening_source.review_batch(ctx,source_prepared())
    assert {entry['clip_id'] for entry in calls[-1]['clips']}==affected


def test_legacy_source_contradictions_are_preserved_and_generic_uncertainty_not_granted(tmp_path):
    _,_,plan=case(tmp_path);clip=plan['clips'][1]
    old=contradiction()
    review=opening_source._validated_review({'clip_id':'clip_07','findings':[old]},
                                          opening={'start_state':'A stands','camera':'A sits'})
    assert review['findings']==[old] and review['consistent'] is False
    grant=opening_grant(clip,[old],{})
    assert grant is not None and grant.operation=='input' and 'camera' in grant.shot_fields
