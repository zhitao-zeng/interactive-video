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
    assert recorded['agent'] == selected
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
