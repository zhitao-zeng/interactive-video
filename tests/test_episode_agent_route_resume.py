from pathlib import Path

from novel_manga.application.agents.episode import lifecycle
from novel_manga.application.agents.episode.lifecycle import BootstrapResult
from novel_manga.util import atomic_write_json
from support.episode_existing import prepared


def _start(ctx, monkeypatch, selected, *, explicit, review=False):
    route_env = {'QWEN38_LOCAL_BASE_URL':'https://qwen.local/v1',
        'QWEN38_LOCAL_MODEL':'qwen-vision-role', 'QWEN38_LOCAL_API_KEY_VAR':'QWEN_ROLE_KEY',
        'NOVEL_PLANNER_ENDPOINT':'phanthy-ds41', 'NOVEL_H3_TRANSLATION_ENDPOINT':'phanthy-ds41'}
    if review:route_env['SECOND_REVIEW_JUDGE']='phanthy-dsv4'
    monkeypatch.setattr(lifecycle, 'resolve_agent_environment', lambda *_: dict(route_env))
    monkeypatch.setattr(lifecycle, 'prepare_inputs', lambda *_: None)
    monkeypatch.setattr(lifecycle, 'install_client', lambda *_: None)
    monkeypatch.setattr(lifecycle, 'paused', lambda: False)
    from novel_manga.application.agents.episode import recovery
    monkeypatch.setattr(recovery, 'resume_source_review', lambda _r,_e,s:s)
    monkeypatch.setattr(recovery, 'resume_protocol', lambda _r,_e,s,**_k:s)
    monkeypatch.setattr(recovery, 'protocol_fallback', lambda _c,config,s:(config,s))
    from novel_manga.application.agents.episode import bounded_recovery
    monkeypatch.setattr(bounded_recovery, 'resume_window', lambda *_:None)
    config = {'provider':'phanrouter-sandbox','model':selected['model'],'base_url':selected['base_url'],
              'key_var':selected['key_var'],'timeout_seconds':90,'production_mode':'agent',
              '_agent_config_explicit':explicit}
    initial = lifecycle.start_run(ctx,config=config,tools_factory=lambda *_:None)
    assert isinstance(initial, BootstrapResult) and initial.state is not None
    return initial, route_env


def test_explicit_same_route_can_resume_recorded_task(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status':'retrying','agent_route':{
        'agent':{'provider':'phanrouter-sandbox','model':'deepseek-v4.1-flash-1017',
                 'base_url':'https://cloud.phanthy.com/phanrouter','key_var':'PHANROUTER_IMAGE_API_KEY'},
        'roles':{'planner':{'endpoint':'phanthy-ds41'}}}})
    selected = {'model':'deepseek-v4.1-flash-1017','base_url':'https://cloud.phanthy.com/phanrouter',
                'key_var':'PHANROUTER_IMAGE_API_KEY'}
    initial, runtime = _start(ctx,monkeypatch,selected,explicit=True)
    assert initial.state.config['model'] == selected['model']
    assert initial.state.result['agent_route']['model'] == selected['model']
    assert runtime['NOVEL_PLANNER_ENDPOINT'] == 'phanthy-ds41'


def test_default_resume_restores_recorded_agent_and_role_route(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    recorded = {'provider':'phanrouter-sandbox','model':'deepseek-v4.1-flash-1017',
        'base_url':'https://cloud.phanthy.com/phanrouter','key_var':'PHANROUTER_IMAGE_API_KEY',
        'agent':{'provider':'phanrouter-sandbox','model':'deepseek-v4.1-flash-1017',
                 'base_url':'https://cloud.phanthy.com/phanrouter','key_var':'PHANROUTER_IMAGE_API_KEY'},
        'api':{'base_url':'https://qwen.local/v1','model':'qwen-vision-role','key_var':'QWEN_ROLE_KEY'},
        'roles':{'planner':{'endpoint':'phanthy-ds41','model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789/v1'},
                 'h3_translation':{'endpoint':'phanthy-ds41','model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789/v1'}}}
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status':'retrying','agent_route':recorded})
    selected = {'model':'old-default','base_url':'https://old-default.invalid','key_var':'OLD_KEY'}
    initial, runtime = _start(ctx,monkeypatch,selected,explicit=False)
    assert initial.state.config['model'] == recorded['model']
    assert initial.state.config['base_url'] == recorded['base_url']
    role = {'endpoint':'phanthy-ds41','model':'deepseek-v4.1-flash-1017',
            'base_url':'http://127.0.0.1:8789/v1'}
    assert initial.state.result['agent_route']['roles'] == {
        'planner':role,'h3_translation':role}
    assert runtime['QWEN38_LOCAL_MODEL'] == 'qwen-vision-role'
    assert runtime['NOVEL_PLANNER_ENDPOINT'] == 'phanthy-ds41'
    assert runtime['NOVEL_H3_TRANSLATION_ENDPOINT'] == 'phanthy-ds41'


def test_explicit_different_route_is_rejected(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status':'retrying','agent_route':{
        'agent':{'provider':'old','model':'old-model','base_url':'https://old.invalid','key_var':'OLD_KEY'}}})
    selected = {'model':'deepseek-v4.1-flash-1017','base_url':'https://cloud.phanthy.com/phanrouter',
                'key_var':'PHANROUTER_IMAGE_API_KEY'}
    import pytest
    with pytest.raises(ValueError, match='route migration'):
        _start(ctx,monkeypatch,selected,explicit=True)


def test_legacy_route_history_marks_unknown_without_changing_initial(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    recorded = {'agent':{'provider':'phanrouter-sandbox','model':'old-model',
        'base_url':'https://old.invalid','key_var':'OLD_KEY'}}
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status':'retrying','agent_route':recorded})
    selected = {'model':'default-model','base_url':'https://default.invalid','key_var':'DEFAULT_KEY'}
    initial, _ = _start(ctx,monkeypatch,selected,explicit=False)
    result = initial.state.result
    assert result['initial_route'] == recorded
    assert result['effective_route'] == recorded
    assert result['route_history'] == [{'kind':'legacy','route':'unknown'}]


def test_fallback_changes_only_effective_route_and_appends_history(tmp_path, monkeypatch):
    ctx, _, _ = prepared(tmp_path, monkeypatch)
    fallback = {'model':'Qwen','base_url':'http://qwen','key_var':'QWEN_KEY'}
    recorded = {'agent':{'provider':'phanrouter-sandbox','model':'old-model',
        'base_url':'https://old.invalid','key_var':'OLD_KEY','refusal_fallback':fallback}}
    switch = {'from_model':'old-model','to_model':'Qwen','reason':'observed refusal'}
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status':'retrying','agent_route':recorded,'model_switch':switch})
    selected = {'model':'default-model','base_url':'https://default.invalid','key_var':'DEFAULT_KEY'}
    initial, _ = _start(ctx,monkeypatch,selected,explicit=False)
    result = initial.state.result
    assert result['initial_route'] == recorded
    assert result['effective_route']['agent']['model'] == 'Qwen'
    assert result['route_history'][0] == {'kind':'legacy','route':'unknown'}
    assert result['route_history'][1] == {'kind':'fallback','record':switch}


def test_explicit_same_model_budget_increase_resumes_with_original_route_and_budget_history(tmp_path,monkeypatch):
    from novel_manga.application.resolved_settings import agent_route
    from novel_manga.llm.config import ENDPOINTS
    ctx,_,_=prepared(tmp_path,monkeypatch)
    selected={'model':'writer-unchanged','base_url':'https://writer.local/v1','key_var':'WRITER_KEY'}
    config={'provider':'phanrouter-sandbox',**selected}
    recorded=agent_route({'SECOND_REVIEW_JUDGE':'phanthy-dsv4'},config)
    recorded['roles']['review']['transport']['QWEN38_LOCAL_MIN_MAX_TOKENS']='0'
    saved={'status':'retrying','agent_route':recorded,'initial_route':recorded,
           'effective_route':recorded,'route_history':[]}
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    initial,_=_start(ctx,monkeypatch,selected,explicit=True,review=True)
    result=initial.state.result
    assert result['initial_route']==recorded
    assert result['effective_route']['agent']==recorded['agent']
    assert result['effective_route']['roles']['review']['transport']['QWEN38_LOCAL_MIN_MAX_TOKENS']=='16000'
    assert result['route_history']==[{'kind':'output_budget','changes':[{
        'role':'roles.review','field':'QWEN38_LOCAL_MIN_MAX_TOKENS','before':'0','after':'16000'}]}]
    # These tests do not manually rewrite the persisted route to make resume pass.
    assert ENDPOINTS['phanthy-dsv4']['QWEN38_LOCAL_MIN_MAX_TOKENS']=='16000'


def test_transport_floor_exception_does_not_allow_other_route_changes():
    import copy
    import pytest
    from novel_manga.application.resolved_settings import assert_route_compatible
    role={'provider':'service','model':'same','base_url':'http://host/v1','endpoint':'judge',
        'transport':{'QWEN38_LOCAL_MIN_MAX_TOKENS':'16000','QWEN38_LOCAL_STREAM':'1','QWEN38_LOCAL_REASONING':'low'}}
    recorded={'roles':{'review':role}}
    changes=[('transport','QWEN38_LOCAL_MIN_MAX_TOKENS','8000'),
        ('transport','QWEN38_LOCAL_STREAM','0'),('transport','QWEN38_LOCAL_REASONING','high'),
        (None,'model','different'),(None,'provider','different'),(None,'endpoint','different'),
        (None,'base_url','http://other/v1')]
    for group,key,value in changes:
        current=copy.deepcopy(recorded)
        target=current['roles']['review']
        target['transport']['QWEN38_LOCAL_MIN_MAX_TOKENS']='32000'
        (target[group] if group else target)[key]=value
        with pytest.raises(ValueError,match='explicit route migration'):
            assert_route_compatible(current,recorded)
