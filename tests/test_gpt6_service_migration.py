"""New GPT6 defaults must not change symbolic routes saved by GPT5.6 jobs."""
import copy
import json
from pathlib import Path

from novel_manga.application.resolved_settings import agent_route, apply_agent_route
from novel_manga.llm.catalog import catalogue, choose, resource, select_agent
from novel_manga.llm.config import endpoint_settings
from novel_manga.llm.policy import agent_policy

ROOT = Path(__file__).resolve().parents[1]
BASE = {'model':'deepseek-v4.1-flash-1017','base_url':'http://127.0.0.1:8789',
        'key_var':'PHANTHY_API_KEY'}


def test_new_luna_default_uses_corresponding_six_and_saved_login_adapter():
    selected=select_agent(BASE)
    assert selected['model']=='gpt-6-luna'
    assert selected['resource_id']=='codex-luna-6'
    assert selected['backend']=='codex' and selected['base_url']=='codex://local'
    assert selected['resource_billing']=='subscription'
    assert selected['codex_parallel']==2 and selected['codex_slots_reserved_for_tools']==1
    assert agent_policy(selected,'author')['effort']=='medium'
    assert agent_policy(selected,'check')['effort']=='low'


def test_old_symbolic_review_and_saved_author_route_stay_on_five_six():
    old={'backend':'codex','model':'gpt-5.6-luna','base_url':'codex://local',
         'key_var':'H3_PROMPT_NO_KEY','refusal_fallback':None}
    runtime={'QWEN38_LOCAL_MODEL':old['model'],'QWEN38_LOCAL_BASE_URL':old['base_url'],
             'QWEN38_LOCAL_API_KEY_VAR':old['key_var'],'SECOND_REVIEW_JUDGE':'codex-luna'}
    saved=agent_route(runtime,old)
    restored_runtime={}
    restored=apply_agent_route(restored_runtime,select_agent(BASE),saved)
    assert restored['model']=='gpt-5.6-luna'
    assert restored_runtime['SECOND_REVIEW_JUDGE']=='codex-luna'
    assert endpoint_settings(restored_runtime['SECOND_REVIEW_JUDGE']).model=='gpt-5.6-luna'
    assert select_agent(copy.deepcopy(old),resumed=True)==old
    assert select_agent(copy.deepcopy(old),explicit=True)==old


def test_new_vision_preset_is_six_but_legacy_api_alias_is_not_invented():
    selected=choose(catalogue()['candidates']['vision'],'vision')
    assert selected=='codex-luna-6'
    assert endpoint_settings(selected).model=='gpt-6-luna'
    assert endpoint_settings('codex-luna').model=='gpt-5.6-luna'
    assert resource('phanthy-luna')['available'] is False
    assert resource('phanthy-luna')['model']=='gpt-5.6-luna'


def test_explicit_six_profiles_keep_original_time_and_shared_slot_limits():
    original=json.loads((ROOT/'configs/agent_sandbox.json').read_text())
    for filename,model in [('agent_codex_luna6.json','gpt-6-luna'),('agent_codex_sol6.json','gpt-6-sol')]:
        config=json.loads((ROOT/'configs'/filename).read_text())
        assert config['backend']=='codex' and config['model']==model
        assert config['refusal_fallback'] is None
        assert config['codex_parallel']==2 and config['codex_slots_reserved_for_tools']==1
        for key in ['timeout_seconds','agent_slice_seconds','source_agent_slice_seconds']:
            assert config[key]==original[key]

