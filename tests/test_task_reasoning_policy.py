import copy
from novel_manga.llm.policy import agent_policy, json_policy, role_for, session_policy


def test_author_can_reason_and_mechanical_ds_disables_thinking():
    base={'model':'deepseek-v4.1-flash-1017','extra_body':{'thinking':{'type':'disabled'},'max_tokens':16384},'max_thinking_tokens':0}
    old=copy.deepcopy(base)
    result=agent_policy(base,'author')
    assert result['effort']=='medium' and 'thinking' not in result['extra_body']
    assert result.get('max_thinking_tokens') is None
    assert base==old
    checked=agent_policy(base,'translation')
    assert checked['max_thinking_tokens']==0
    assert checked['extra_body']['thinking']=={'type':'disabled'}


def test_luna_api_and_codex_get_same_effective_mode_without_ds_inheritance():
    for backend in [None,'codex']:
        cfg={'model':'gpt-5.6-luna','backend':backend,'max_thinking_tokens':0,'extra_body':{'thinking':{'type':'disabled'},'max_tokens':16384}}
        low=agent_policy(cfg,'translation')
        assert low['effort']=='low'
        assert 'thinking' not in low['extra_body'] and low.get('max_thinking_tokens') is None
        assert low['reasoning_policy']['requested']=='off' and low['reasoning_policy']['effective']=='low'
        assert agent_policy(cfg,'author')['effort']=='medium'
    assert json_policy('gpt-5.6-luna','translation')=={'reasoning_effort':'low'}


def test_task_roles_are_explicit_and_reviews_are_not_all_creative():
    assert role_for('episode_source_translation')=='translation'
    assert role_for('episode_source_draft')=='author'
    assert role_for('clip_review')=='review'
    assert role_for('request_conflict_scope')=='check'


def test_saved_session_policy_is_pinned_and_legacy_resume_is_unchanged(tmp_path):
    cfg={'model':'deepseek-v4.1-flash-1017','effort':'low'}
    legacy=session_policy(cfg,tmp_path,resume=True,role='author')
    assert legacy==cfg
    created=session_policy(cfg,tmp_path,resume=False,role='author')
    assert created['effort']=='medium'
    resumed=session_policy(cfg,tmp_path,resume=True,role='translation')
    assert resumed['reasoning_policy']['role']=='author'
    assert resumed['effort']=='medium'


def test_codex_agent_reserves_helper_capacity_in_the_shared_pool(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import codex_session
    from novel_manga.media import resources
    seen=[]
    monkeypatch.setattr(resources,'acquire_inflight_slot',lambda work,slots,**kw:seen.append(slots))
    monkeypatch.setattr(resources,'release_inflight_slot',lambda h:None)
    monkeypatch.setattr(codex_session,'_run_session',lambda *a,**kw:'ok')
    cfg={'backend':'codex','model':'gpt-5.6-luna','codex_parallel':2,
         'codex_slots_reserved_for_tools':1,'codex_inflight_dir':str(tmp_path/'pool')}
    assert codex_session.run_session(tmp_path/'agent','check',config=cfg)=='ok'
    cfg={**cfg,'codex_slots_reserved_for_tools':0}
    assert codex_session.run_session(tmp_path/'json','check',config=cfg)=='ok'
    assert seen==[1,2]


def test_music_http_clients_use_the_same_task_reasoning_without_luna_ds_flags():
    from novel_manga.llm.policy import chat_options
    assert chat_options('deepseek-v4.1-flash-1017','check')['extra_body']=={'thinking':{'type':'disabled'}}
    assert chat_options('deepseek-v4.1-flash-1017','music')['reasoning_effort']=='low'
    assert chat_options('gpt-5.6-luna','music')=={'reasoning_effort':'low','extra_body':{}}


def test_unregistered_explicit_json_provider_retains_its_existing_controls():
    from novel_manga.llm.policy import chat_options
    assert json_policy('private-vllm-model','author')=={}
    assert chat_options('private-vllm-model','author')=={'extra_body':{}}
