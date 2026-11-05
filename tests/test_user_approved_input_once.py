"""One exact human-approved input, through the existing recovery grant/receipt route."""
import copy
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.agents.episode import bounded_recovery as recovery,input_records
from novel_manga.application.repair import history
from support.upstream_recovery import ctx_pair

@pytest.fixture
def ctx(ctx_pair):return ctx_pair[0]

def candidate():
    return {'clip_id':'clip_01','action':'input','clip_ids':['clip_01'],'source_quote':'甲坐在宿舍',
            'reason':'user approved exact candidate','shots':[{'index':1,'light':'清晨'}]}

def approve(ctx):
    path=ctx.episode_dir/'work/input_authorizations/clip_01.json'
    record={'status':'approved','authorization':'user-explicit-exact-input-once-20261023','reason':'User approved this specific draft once, no image/video/time increase',
            'source_text':(ctx.run_dir/'source.txt').read_text(),'candidate':candidate(),'approved_at':'2026-10-23'}
    atomic_write_json(path,record);return path,record

def test_no_approval_cannot_create_grant(ctx):
    assert not recovery.evidence(ctx,'clip_01')
    with pytest.raises(ValueError,match='evidence'):recovery.grant(ctx,'clip_01',{'kind':'user_approved_input'},kind='input')
    assert not(ctx.episode_dir/'repair_budget_grants.json').exists()

@pytest.mark.parametrize('change',['pending','missing_authorization','source_changed','wrong_cid','wrong_action','wrong_quote'])
def test_inexact_authorization_is_not_evidence(ctx,change):
    path,row=approve(ctx)
    if change=='pending':row['status']='pending'
    elif change=='missing_authorization':row['authorization']=''
    elif change=='source_changed':row['source_text']+='different source'
    elif change=='wrong_cid':row['candidate']['clip_id']='clip_02'
    elif change=='wrong_action':row['candidate']['action']='retake'
    elif change=='wrong_quote':row['candidate']['source_quote']='not in original'
    atomic_write_json(path,row);assert not recovery.evidence(ctx,'clip_01')

@pytest.mark.parametrize('kind',['time','image','video'])
def test_approval_only_grants_input(ctx,kind):
    _,row=approve(ctx);proof=recovery.evidence(ctx,'clip_01')
    assert proof['allowed_kinds']==['input']
    before=history.generation_limit(ctx.episode_dir,'clip_01')
    with pytest.raises(ValueError):recovery.grant(ctx,'clip_01',proof,kind=kind)
    assert not(ctx.episode_dir/'repair_budget_grants.json').exists()
    assert history.generation_limit(ctx.episode_dir,'clip_01')==before

def test_exact_candidate_consumed_once_without_legacy_authorization(ctx):
    _,row=approve(ctx);proof=recovery.evidence(ctx,'clip_01');grant=recovery.grant(ctx,'clip_01',proof,kind='input')
    assert grant['authorization']==row['authorization']and'20261009'not in grant['authorization']
    wrong=candidate();wrong['shots'][0]['light']='夜间'
    assert not recovery.input_operation(ctx,'clip_01',wrong)['allowed']
    assert recovery.input_operation(ctx,'clip_01',candidate(),consume=True)['allowed']
    assert not recovery.input_operation(ctx,'clip_01',candidate(),consume=True)['allowed']
    recovery.grant(ctx,'clip_01',proof,kind='input')
    assert not recovery.input_operation(ctx,'clip_01',candidate())['allowed']

def test_source_change_invalidates_existing_grant(ctx):
    approve(ctx);recovery.grant(ctx,'clip_01',recovery.evidence(ctx,'clip_01'),kind='input')
    (ctx.run_dir/'source.txt').write_text('source was changed')
    assert not recovery.input_operation(ctx,'clip_01',candidate())['allowed']

def test_public_route_check_and_receipt_keep_old_counts_and_consume_once(ctx):
    old=[]
    for number in [1,2]:
        decision=candidate();decision['shots'][0]['light']='旧方案'+str(number)
        p=ctx.episode_dir/f'work/input_repairs/clip_01-{number}.json'
        atomic_write_json(p,{'clip_id':'clip_01','decision':decision,'status':'incomplete','step':'compile','revision_count':1,'resume_count':1})
        old.append((p,p.read_bytes()))
    assert input_records.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}
    with pytest.raises(ValueError):input_records.input_route(ctx,'clip_01',candidate())
    approve(ctx);recovery.grant(ctx,'clip_01',recovery.evidence(ctx,'clip_01'),kind='input')
    route,path,_=input_records.input_route(ctx,'clip_01',candidate());assert route=='upstream_recovery'and path.name=='clip_01-3.json'
    checked,record=input_records.input_receipt(ctx,'clip_01',candidate(),check_only=True)
    assert checked==path and not path.exists()and recovery.input_operation(ctx,'clip_01',candidate())['allowed']
    assert record['resume_count']==record['revision_count']==1
    before_video=history.generation_counts(ctx.episode_dir);before_limit=history.generation_limit(ctx.episode_dir,'clip_01')
    published,record=input_records.input_receipt(ctx,'clip_01',candidate())
    assert published==path and path.exists()and record['upstream_recovery']['consumed']
    assert record['resume_count']==record['revision_count']==1
    assert all(p.read_bytes()==data for p,data in old)
    assert len(list(path.parent.glob('*.json')))==3
    assert not recovery.input_operation(ctx,'clip_01',candidate())['allowed']
    assert history.generation_counts(ctx.episode_dir)==before_video
    assert history.generation_limit(ctx.episode_dir,'clip_01')==before_limit
    assert not history.conditional_extra(ctx.episode_dir,'clip_01')
    assert not read_json(ctx.episode_dir/'repair_budget_grants.json')['clip_01']['upstream_recovery'].get('time')
