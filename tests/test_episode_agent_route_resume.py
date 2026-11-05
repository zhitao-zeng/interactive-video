from pathlib import Path

from novel_manga.application.agents.episode import lifecycle
from novel_manga.application.agents.episode.lifecycle import BootstrapResult
from novel_manga.util import atomic_write_json
from support.episode_existing import prepared


def _start(ctx, monkeypatch, selected, *, explicit):
    route_env = {'QWEN38_LOCAL_BASE_URL':'https://qwen.local/v1',
        'QWEN38_LOCAL_MODEL':'qwen-vision-role', 'QWEN38_LOCAL_API_KEY_VAR':'QWEN_ROLE_KEY',
        'NOVEL_PLANNER_ENDPOINT':'phanthy-ds41', 'NOVEL_H3_TRANSLATION_ENDPOINT':'phanthy-ds41'}
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
