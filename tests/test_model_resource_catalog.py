from novel_manga.llm.catalog import choose, resource, quota_group, inventory


def test_verified_free_wins_but_explicit_choice_is_preserved():
    rows={'free':{'billing':'free','qualified':['json'],'available':True},
          'paid':{'billing':'metered','qualified':['json'],'available':True},
          'unknown':{'billing':'unknown','qualified':['json'],'available':True}}
    assert choose(['paid','unknown','free'], 'json', rows=rows)=='free'
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


def test_current_default_agent_uses_subscription_while_free_agent_is_unqualified():
    from novel_manga.llm.catalog import select_agent
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY'}
    selected=select_agent(cfg)
    assert selected['model']=='gpt-6-luna'
    assert selected['resource_billing']=='subscription'
    assert selected['backend']=='codex'
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


def test_subscription_remains_qualified_choice_when_free_gateway_is_unavailable(monkeypatch):
    import copy
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    rows['resources']['phanthy-dsv4']['available']=False
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    cfg={'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789','key_var':'PHANTHY_API_KEY'}
    selected=catalog.select_agent(cfg)
    assert selected['backend']=='codex'
    assert selected['resource_billing']=='subscription'


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
