from novel_manga.application.resolved_settings import agent_route, apply_agent_route


def test_recorded_agent_route_keeps_task_model_separate_from_role_models():
    runtime = {
        'QWEN38_LOCAL_BASE_URL': 'https://qwen.local/v1',
        'QWEN38_LOCAL_MODEL': 'qwen-vision-2026',
        'QWEN38_LOCAL_API_KEY_VAR': 'QWEN_LOCAL_KEY',
        'NOVEL_PLANNER_ENDPOINT': 'phanthy-ds41',
        'NOVEL_H3_TRANSLATION_ENDPOINT': 'phanthy-ds41',
        'NOVEL_H3_REQUEST_CHECK_ENDPOINT': 'phanthy-ds41',
    }
    selected = {'provider': 'phanrouter-sandbox', 'model': 'deepseek-v4.1-flash-1017',
                'base_url': 'https://cloud.phanthy.com/phanrouter',
                'key_var': 'PHANROUTER_IMAGE_API_KEY'}
    recorded = agent_route(runtime, selected)
    assert recorded['agent'] == {**selected,'transport':'http'}
    assert recorded['model'] == selected['model']
    assert recorded['api']['model'] == 'qwen-vision-2026'
    assert recorded['roles']['planner']['endpoint'] == 'phanthy-ds41'
    assert recorded['roles']['h3_translation']['endpoint'] == 'phanthy-ds41'

    resumed_runtime = {'QWEN38_LOCAL_MODEL': 'wrong-current-default',
                       'NOVEL_PLANNER_ENDPOINT': 'different-current-default'}
    resumed = apply_agent_route(resumed_runtime,
        {'provider': 'old', 'model': 'old-model'}, recorded)
    assert resumed['provider'] == selected['provider']
    assert resumed['model'] == selected['model']
    assert resumed['base_url'] == selected['base_url']
    assert resumed['key_var'] == selected['key_var']
    assert resumed_runtime['QWEN38_LOCAL_MODEL'] == 'qwen-vision-2026'
    assert resumed_runtime['NOVEL_PLANNER_ENDPOINT'] == 'phanthy-ds41'
    assert resumed_runtime['NOVEL_H3_TRANSLATION_ENDPOINT'] == 'phanthy-ds41'


def test_legacy_ambiguous_route_does_not_overwrite_an_explicit_task_config():
    runtime = {'QWEN38_LOCAL_MODEL': 'qwen-vision-2026'}
    selected = {'provider': 'phanrouter-sandbox', 'model': 'deepseek-v4.1-flash-1017',
                'base_url': 'https://cloud.phanthy.com/phanrouter',
                'key_var': 'PHANROUTER_IMAGE_API_KEY'}
    current = apply_agent_route(runtime, selected, {'model': 'qwen-vision-2026'})
    assert current is selected
    assert current['model'] == 'deepseek-v4.1-flash-1017'
    assert runtime['QWEN38_LOCAL_MODEL'] == 'qwen-vision-2026'


def test_restoring_legacy_native_route_does_not_inherit_new_codex_backend():
    from novel_manga.application.resolved_settings import apply_agent_route
    current={'backend':'codex','model':'gpt-5.6-luna','base_url':'codex://local'}
    restored=apply_agent_route({},current,{'agent':{'model':'deepseek-v4.1-flash-1017',
        'base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY','provider':'phanrouter-sandbox'}})
    assert 'backend' not in restored
    assert restored['model']=='deepseek-v4.1-flash-1017'


def test_role_resume_uses_recorded_model_base_and_transport(monkeypatch):
    runtime = {'QWEN38_LOCAL_BASE_URL':'https://qwen.local/v1',
        'QWEN38_LOCAL_MODEL':'qwen-vision-role','QWEN38_LOCAL_API_KEY_VAR':'QWEN_ROLE_KEY',
        'NOVEL_PLANNER_ENDPOINT':'phanthy-ds41'}
    recorded = agent_route(runtime, {'provider':'phanrouter-sandbox','model':'agent-model',
        'base_url':'https://agent.local','key_var':'AGENT_KEY'})
    resumed_runtime = {'NOVEL_PLANNER_ENDPOINT':'phanthy-ds41'}
    apply_agent_route(resumed_runtime, {}, recorded)
    from novel_manga.llm.config import ENDPOINTS, endpoint_settings
    monkeypatch.setitem(ENDPOINTS['phanthy-ds41'], 'QWEN38_LOCAL_MODEL', 'new-default-model')
    monkeypatch.setitem(ENDPOINTS['phanthy-ds41'], 'QWEN38_LOCAL_BASE_URL', 'https://new.default/v1')
    monkeypatch.setenv('NOVEL_RESOLVED_ROUTE', resumed_runtime['NOVEL_RESOLVED_ROUTE'])
    restored = endpoint_settings('phanthy-ds41')
    assert restored.model == 'deepseek-v4.1-flash-1017'
    assert restored.endpoints == ('http://127.0.0.1:8789/v1',)
    assert restored.stream is True
    assert restored.min_max_tokens == 16000
