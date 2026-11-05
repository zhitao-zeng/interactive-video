from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from novel_manga.application.preparation import request_check
from novel_manga.application.agents.episode import readiness
from novel_manga.application.agents.episode.media_operations import service_failure_result
from novel_manga.media.retries import failure_result
from novel_manga.service_errors import UnsupportedInput
from novel_manga.util import atomic_write_json,read_json


DENIAL={'consistent':False,'problems':['Hands-only framing cannot show the closed-mouth look.'],
        'scope_check':{'checks':[{'id':0,'scope':'in_scope','relation':'contradiction','reason':'Current constraints conflict.'}]},
        'findings':[{'relation':'contradiction','reason':'Hands-only framing cannot show the closed-mouth look.'}]}


def test_request_content_rejection_keeps_content_identity_through_renderer(tmp_path,monkeypatch):
    monkeypatch.setattr('novel_manga.story.voice_delivery.require_native_speech',lambda *a:None)
    monkeypatch.setattr(request_check,'cached_consistency',lambda *a,**k:DENIAL)
    ctx=NS(novel_dir=tmp_path,work=tmp_path/'work',options=NS(prepared_requests_only=False),state=NS(blocked_clips={}))
    with pytest.raises(UnsupportedInput) as caught:request_check.before_generation(ctx,{'clip_id':'clip_02'},{})
    row=failure_result('clip_02',[],caught.value)
    assert row['request_check']==DENIAL and row['blocked']==ctx.state.blocked_clips['clip_02']
    assert 'retryable' not in row and service_failure_result({'clips':[row]}) is None
    assert row['selected'] is None and not list(tmp_path.rglob('*.task.json'))


@pytest.mark.parametrize('answer',[{'consistent':False,'problems':[]},{'consistent':None,'problems':['judge did not finish'],'findings':[]}])
def test_unknown_check_conclusion_remains_execution_failure(tmp_path,monkeypatch,answer):
    monkeypatch.setattr('novel_manga.story.voice_delivery.require_native_speech',lambda *a:None)
    monkeypatch.setattr(request_check,'cached_consistency',lambda *a,**k:answer)
    ctx=NS(novel_dir=tmp_path,work=tmp_path/'work',options=NS(prepared_requests_only=False),state=NS(blocked_clips={}))
    with pytest.raises(ValueError) as caught:request_check.before_generation(ctx,{'clip_id':'clip_02'},{})
    assert not isinstance(caught.value,UnsupportedInput)
    assert service_failure_result({'clips':[failure_result('clip_02',[],caught.value)]})['error']=='media_execution_failed'


def legacy_case(tmp_path,monkeypatch):
    episode=tmp_path/'book/book_1';episode.mkdir(parents=True)
    work=tmp_path/'sandbox';(work/'input').mkdir(parents=True)
    ref=episode.parent/'ref.png';ref.write_bytes(b'original reference')
    clip={'clip_id':'clip_02','kind':'video','prompt':'中文当前稿','prompt_h3':'hands-only close-up, closed-mouth look','references':[]}
    atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    monkeypatch.setattr('novel_manga.media.generation.image_paths',lambda *a:[ref])
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:DENIAL)
    request_check.current_request(episode,clip)
    row={'clip_id':'clip_02','attempts':[],'selected':None,'error':'ValueError: H3 request/reference mismatch: saved message','retryable':False}
    report={'clips':[row],'failed_clips':['clip_02'],'blocked_clips':{'clip_02':['request: '+p for p in DENIAL['problems']]}}
    atomic_write_json(episode/'thin_media_report.json',report)
    job={'action':'render','status':'completed','result':service_failure_result(report)}
    ctx=NS(novel_dir=episode.parent,episode_dir=episode,work_dir=work,run_dir=tmp_path/'run')
    monkeypatch.setattr(readiness,'first_frame_blocks',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.episode.opening_source.source_blockers',lambda *a:{})
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:pytest.fail('readiness cannot call a model'))
    return ctx,clip,ref,job


def test_old_content_receipt_reconciles_once_through_normal_render_without_new_submission(tmp_path,monkeypatch):
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    paths=[ctx.episode_dir/'thin_media_report.json',ctx.episode_dir/'work/request_checks/clip_02.json']
    before={p:p.read_bytes() for p in paths}
    route=readiness.next_action(ctx,{'job':job})
    assert route['action']=='render' and route['reconcile_request_rejections']==['clip_02']
    assert 'retryable' not in route
    automatic=readiness.automatic_request(ctx,{'adopted':True,'job':job})
    assert automatic['action']=='render'
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{'automatic_fingerprint':automatic['fingerprint']}})
    assert readiness.automatic_request(ctx,{'adopted':True,'job':job}) is None
    assert all(p.read_bytes()==value for p,value in before.items())


def test_all_gate_rejections_distinguish_prior_normal_render_with_unchanged_targets(tmp_path,monkeypatch):
    import copy
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    plan={'clips':[{**clip,'clip_id':cid} for cid in ('clip_01','clip_02','clip_03')]}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    report=read_json(ctx.episode_dir/'thin_media_report.json')
    failed=report['clips'][0]
    report['clips']=[{**failed,'clip_id':row['clip_id']} for row in plan['clips']]
    report['failed_clips']=[row['clip_id'] for row in plan['clips']]
    report['blocked_clips']={row['clip_id']:['request: '+p for p in DENIAL['problems']] for row in plan['clips']}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    cache=read_json(ctx.episode_dir/'work/request_checks/clip_02.json')
    for row in plan['clips']:
        atomic_write_json(ctx.episode_dir/'work/request_checks'/(row['clip_id']+'.json'),cache)
    job['result']=service_failure_result(report)
    state={'adopted':True,'job':job}
    route=readiness.next_action(ctx,state)
    assert route['reconcile_request_rejections']==['clip_01','clip_02','clip_03']
    # Keep targets, takes, payload and even reason identical. The existing route
    # fact alone must distinguish reconciliation from the prior ordinary render.
    previous_route=copy.deepcopy(route);previous_route.pop('reconcile_request_rejections')
    previous=readiness._automatic_fingerprint(ctx,state,
        {'action':'render','route':previous_route,'payload':{}},plan)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{'automatic_fingerprint':previous}})
    automatic=readiness.automatic_request(ctx,state)
    assert automatic['action']=='render' and automatic['fingerprint']!=previous
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{'automatic_fingerprint':automatic['fingerprint']}})
    assert readiness.automatic_request(ctx,state) is None


def test_reconciled_content_receipt_routes_to_agent_and_fixed_request_can_render(tmp_path,monkeypatch):
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    error=UnsupportedInput('known request contradiction');error.request_check=DENIAL
    media=read_json(ctx.episode_dir/'thin_media_report.json')
    media['clips']=[failure_result('clip_02',[],error)]
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',media)
    typed_job={'action':'render','status':'completed','result':{'ok':True}}
    route=readiness.next_action(ctx,{'job':typed_job})
    assert route['action']=='agent' and list(route['request_input_conflicts'])==['clip_02']
    assert service_failure_result(media) is None
    clip['prompt_h3']='hands-only close-up, hold the clicked objects'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    monkeypatch.setattr(readiness,'media_started',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**k:False)
    assert readiness.next_action(ctx,{'job':typed_job})['action']=='render'


def test_cached_rejection_does_not_reopen_other_unsubmitted_clips(tmp_path,monkeypatch):
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip,{'clip_id':'clip_03','kind':'video'}]})
    assert readiness.next_action(ctx,{'job':job})['error']=='media_execution_failed'


@pytest.mark.parametrize('scope_check',[
    {'error':'TimeoutError'},
    {'error':'incomplete scope recheck'},
    {'checks':[{'id':0,'scope':'uncertain','relation':'contradiction','reason':'scope not established'}]},
    {'checks':[{'id':0,'scope':'in_scope','relation':'uncertain','reason':'facts not established'}]},
])
def test_incomplete_scope_recheck_cannot_become_content_rejection_or_cached_recovery(tmp_path,monkeypatch,scope_check):
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    answer={**DENIAL,'scope_check':scope_check}
    path=ctx.episode_dir/'work/request_checks/clip_02.json'
    saved=read_json(path);saved['answer']=answer;atomic_write_json(path,saved)
    assert request_check.cached_current_rejection(ctx.episode_dir,clip) is None
    assert readiness.next_action(ctx,{'job':job})['error']=='media_execution_failed'
    monkeypatch.setattr('novel_manga.story.voice_delivery.require_native_speech',lambda *a:None)
    monkeypatch.setattr(request_check,'cached_consistency',lambda *a,**k:answer)
    render_ctx=NS(novel_dir=ctx.novel_dir,work=ctx.episode_dir/'work',
        options=NS(prepared_requests_only=False),state=NS(blocked_clips={}))
    with pytest.raises(ValueError) as caught:request_check.before_generation(render_ctx,clip,{})
    assert not isinstance(caught.value,UnsupportedInput)
    assert service_failure_result({'clips':[failure_result('clip_02',[],caught.value)]})['error']=='media_execution_failed'


@pytest.mark.parametrize('change',['english','chinese','reference','role','uncertain','video','service','ungrounded'])
def test_old_failure_does_not_reconcile_without_exact_current_unsubmitted_content_evidence(tmp_path,monkeypatch,change):
    ctx,clip,ref,job=legacy_case(tmp_path,monkeypatch)
    if change in {'english','chinese','role'}:
        if change=='english':clip['prompt_h3']+=' changed'
        elif change=='chinese':clip['prompt']+=' changed'
        else:clip['references']=[{'role':'character','name':'new person','path':'ref.png'}]
        atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    elif change=='reference':ref.write_bytes(b'new reference')
    elif change in {'uncertain','video'}:
        p=ctx.episode_dir/'work/clips/clip_02/attempt_01';p.mkdir(parents=True)
        if change=='uncertain':atomic_write_json(p/'clip.mp4.task.json',{'submit_uncertain_at':'retained'})
        else:(p/'clip.mp4').write_bytes(b'retained video')
    elif change=='service':job['result']['service_interrupted']={'http_status':503,'detail':'outage'}
    else:
        p=ctx.episode_dir/'work/request_checks/clip_02.json';cache=read_json(p);cache['answer']['findings']=[];atomic_write_json(p,cache)
    route=readiness.next_action(ctx,{'job':job})
    assert not route.get('reconcile_request_rejections')
    assert route.get('error')=='media_execution_failed' or route.get('service_interrupted')
