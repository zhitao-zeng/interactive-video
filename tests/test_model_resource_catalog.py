from novel_manga.llm.catalog import choose, resource, quota_group, inventory


def test_declared_priority_wins_over_billing_and_explicit_choice_is_preserved():
    rows={'free':{'billing':'free','qualified':['json'],'available':True},
          'paid':{'billing':'metered','qualified':['json'],'available':True},
          'unknown':{'billing':'unknown','qualified':['json'],'available':True},
          'gpt':{'billing':'subscription','qualified':['json'],'available':True}}
    assert choose(['free','unknown','gpt'], 'json', rows=rows)=='free'
    assert choose(['unknown','gpt'], 'json', rows=rows)=='unknown'
    assert choose(['paid','free'], 'json', explicit='paid', rows=rows)=='paid'


def test_free_requires_capability_and_availability():
    rows={'free':{'billing':'free','qualified':['json'],'available':False},
          'paid':{'billing':'metered','qualified':['agent'],'available':True}}
    assert choose(['free','paid'],'agent',rows=rows)=='paid'
    assert choose(['free'],'json',rows=rows) is None


def test_confirmed_groups_are_shared_and_account_unknown_is_not_free():
    assert quota_group('sd25')==quota_group('general-video')
    assert quota_group('image')==quota_group('h3cloud')==quota_group('official-ds41')
    assert quota_group('sd25')!=quota_group('image')
    assert resource('official-ds41')['billing']=='unknown'
    assert resource('phanthy-dsv4')['billing']=='free'
    report=inventory({'PHANROUTER_IMAGE_API_KEY':'test-secret','PHANROUTER_H3CLOUD_API_KEY':'test-secret'})
    assert 'test-secret' not in str(report)
    assert ['PHANROUTER_IMAGE_API_KEY','PHANROUTER_H3CLOUD_API_KEY'] in report['same_credentials']


def test_current_default_agent_uses_verified_free_native_tools_without_moving_saved_routes():
    from novel_manga.llm.catalog import select_agent
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY'}
    selected=select_agent(cfg)
    assert selected['model']=='phanthy.com/deepseek-v4'
    assert selected['resource_billing']=='free'
    assert selected.get('backend')!='codex'
    assert selected['base_url']==cfg['base_url'] and selected['key_var']==cfg['key_var']
    assert select_agent(cfg,explicit=True)==cfg
    assert select_agent(cfg,resumed=True)==cfg


def test_new_native_free_agent_does_not_inherit_unavailable_legacy_api_fallback(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    rows['resources']['phanthy-dsv4'].update(available=True,qualified=['json','http-chat','agent'])
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY',
         'refusal_fallback':{'endpoint':'phanthy-luna','model':'gpt-5.6-luna'}}
    selected=catalog.select_agent(cfg)
    assert selected['resource_id']=='phanthy-dsv4'
    assert selected['refusal_fallback'] is None
    assert catalog.select_agent(cfg,explicit=True)==cfg
    assert catalog.select_agent(cfg,resumed=True)==cfg


def test_unlimited_key_k3_is_next_when_free_gateway_is_unavailable(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    rows['resources']['phanthy-dsv4']['available']=False
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY'}
    selected=catalog.select_agent(cfg)
    assert selected['model']=='kimi-k3'
    assert selected.get('backend')!='codex'
    assert selected['base_url']=='https://cloud.phanthy.com/phanrouter'
    assert selected['key_var']=='PHANROUTER_IMAGE_API_KEY'
    assert selected['resource_billing']=='unknown'


def test_agent_and_helpers_follow_full_order_without_changing_budgets(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789',
         'key_var':'PHANTHY_API_KEY','timeout_seconds':5400,'agent_slice_seconds':600,
         'source_agent_slice_seconds':1200,'refusal_fallback':None}
    order=['phanthy-dsv4','official-k3','official-ds41','official-glm','codex-luna-6']
    for name in order:
        assert catalog.choose(rows['candidates']['agent'],'agent')==name
        assert catalog.choose(rows['candidates']['json'],'json')==name
        assert catalog.choose(rows['candidates']['json'],'http-chat')==(name if name!='codex-luna-6' else None)
        selected=catalog.select_agent(cfg)
        expected=rows['resources'][name]
        assert selected['resource_id']==name and selected['model']==expected['model']
        assert selected['base_url']==expected['base_url']
        assert selected['key_var']==expected.get('key_var','H3_PROMPT_NO_KEY')
        for field in ['timeout_seconds','agent_slice_seconds','source_agent_slice_seconds']:
            assert selected[field]==cfg[field]
        assert selected['refusal_fallback'] is None
        rows['resources'][name]['available']=False
    assert catalog.choose(rows['candidates']['agent'],'agent') is None
    import pytest
    with pytest.raises(ValueError,match='No qualified Agent'):
        catalog.select_agent(cfg)


def test_visual_fallbacks_skip_failed_glm_probe(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    for name in ['phanthy-dsv4','official-k3','official-ds41','codex-luna-6']:
        assert catalog.choose(rows['candidates']['vision'],'vision')==name
        rows['resources'][name]['available']=False
    assert catalog.choose(rows['candidates']['vision'],'vision') is None
    assert rows['resources']['official-glm']['available'] is True
    assert 'agent' in rows['resources']['official-glm']['qualified']


def test_inventory_can_report_no_qualified_resources_without_legacy_fallback(tmp_path,monkeypatch,capsys):
    import copy,json,runpy,sys
    from pathlib import Path
    from novel_manga.application import configuration
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    for names in rows['candidates'].values():
        for name in names:rows['resources'][name]['available']=False
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    monkeypatch.setattr(configuration,'environment',lambda root:{})
    monkeypatch.setattr(sys,'argv',['model_resources.py','--project-root',str(tmp_path)])
    script=Path(__file__).resolve().parents[1]/'scripts/model_resources.py'
    assert runpy.run_path(str(script))['main']()==0
    result=json.loads(capsys.readouterr().out)
    assert all(value is None for value in result['preferred'].values())
    assert all(row['resource'] is None and row['effective'] is None for row in result['preferred_reasoning'].values())


def test_text_qualification_does_not_make_official_models_image_judges(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    rows['resources']['phanthy-dsv4']['available']=False
    for name in ['official-k3','official-ds41','official-glm']:
        rows['resources'][name]['qualified']=['json','agent','http-chat']
    assert catalog.choose(rows['candidates']['agent'],'agent')=='official-k3'
    assert catalog.choose(rows['candidates']['vision'],'vision')=='codex-luna-6'
    assert catalog.review_defaults('official-k3')==('codex-luna-6','')


def test_new_free_creator_saves_disabled_fallback_across_actual_resume(tmp_path,monkeypatch):
    import copy
    import json
    import pytest
    from novel_manga.interactive import creation
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    rows['resources']['phanthy-dsv4']['qualified'].append('agent')
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789',
         'key_var':'PHANTHY_API_KEY','refusal_fallback':{'endpoint':'phanthy-luna','model':'gpt-5.6-luna'}}
    monkeypatch.setattr(creation,'load_config',lambda:copy.deepcopy(cfg))
    calls=[]
    class CapturedModelCall(Exception):pass
    def capture(*args,**kwargs):
        calls.append(copy.deepcopy(kwargs['config']))
        raise CapturedModelCall
    monkeypatch.setattr(creation,'run_session',capture)
    source=tmp_path/'task.json'
    source.write_text(json.dumps({'mode':'original','project':'adventure','title':'route fixture',
        'brief':'A traveller reads a sign; no media generation.','target_seconds':[180,300],
        'voice_reference_policy':'none','world_card_seconds':3,'end_card_seconds':3}))
    run=tmp_path/'run'
    with pytest.raises(CapturedModelCall):creation.run(source,run)
    saved=json.loads((run/'creator-agent-route.json').read_text())
    with pytest.raises(CapturedModelCall):creation.run(source,run,resume=True)
    assert calls[0]['model']==calls[1]['model']=='phanthy.com/deepseek-v4'
    assert 'refusal_fallback' in saved and saved['refusal_fallback'] is None
    assert calls[1]['refusal_fallback'] is None


def test_new_free_production_route_pins_disabled_fallback_on_restore(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    rows['resources']['phanthy-dsv4']['qualified'].append('agent')
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    from novel_manga.llm.catalog import select_agent
    from novel_manga.application.resolved_settings import agent_route,apply_agent_route
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789',
         'key_var':'PHANTHY_API_KEY','refusal_fallback':{'endpoint':'phanthy-luna','model':'gpt-5.6-luna'}}
    fresh=select_agent(cfg)
    saved=agent_route({},fresh)
    resumed=apply_agent_route({},copy.deepcopy(cfg),saved)
    assert 'refusal_fallback' in saved['agent'] and saved['agent']['refusal_fallback'] is None
    assert resumed['refusal_fallback'] is None
