"""The additional provider preserves request limits and existing runtime routes."""
from types import SimpleNamespace
import pytest

from novel_manga.llm import client,config
from novel_manga.application.production.conductor_workers import server_env
from novel_manga.application.agents import sandbox


def test_reasoning_only_truncation_gets_the_existing_bounded_retry(monkeypatch):
    calls=[]
    def respond(http,bases,headers,payload,**kwargs):
        calls.append(payload['max_tokens'])
        return {'choices':[{'message':{'content':'' if len(calls)==1 else '{"ok":true}'},
                            'finish_reason':'length' if len(calls)==1 else 'stop'}]}
    monkeypatch.setattr(client,'post_any',respond)
    result=client.ask_json([{'type':'text','text':'test'}],client.obj({'ok':{'type':'boolean'}}),
        name='synthetic',max_tokens=128,settings=config.JsonEndpoint('glm-5.3-flash',('http://unused.invalid',)))
    assert result=={'ok':True} and calls==[128,256]


def test_repeated_empty_truncation_fails_instead_of_claiming_valid_json(monkeypatch):
    calls=[]
    def respond(http,bases,headers,payload,**kwargs):
        calls.append(payload['max_tokens'])
        return {'choices':[{'message':{'content':''},'finish_reason':'length'}]}
    monkeypatch.setattr(client,'post_any',respond)
    with pytest.raises(ValueError,match='JSON truncated'):
        client.ask_json([{'type':'text','text':'test'}],client.obj({'ok':{'type':'boolean'}}),
            name='synthetic',max_tokens=128,settings=config.JsonEndpoint('glm-5.3-flash',('http://unused.invalid',)))
    assert calls==[128,256,700]  # 翻倍一次,仍空则按原量级重发一次赌随机性(2026-10-10 网关实测)


def test_provider_flags_reach_conductor_workers_without_affecting_ds():
    conductor=SimpleNamespace(workspaces=None)
    value=server_env(conductor,{'base':'http://127.0.0.1:8789/v1','model':'glm-5.3-flash',
        'key_var':'PHANTHY_API_KEY','endpoint':'phanthy'})
    assert value['QWEN38_LOCAL_STREAM']=='1'
    assert value['QWEN38_LOCAL_MIN_MAX_TOKENS']=='16000'
    ds=server_env(conductor,{'base':'http://172.28.4.52:8831/v1','model':'DeepSeek-V4.1-Flash',
        'key_var':'GPU52_DEEPSEEK_API_KEY','endpoint':'deepseek'})
    assert ds['QWEN38_LOCAL_STREAM']=='0'


def test_shared_sandbox_arguments_are_provider_specific():
    assert sandbox.provider_container_args({})==[]
    args=sandbox.provider_container_args({'docker_network':'host','extra_body':{'reasoning_effort':'low'}})
    assert args[:2]==['--network','host']
    assert any(v.startswith('CLAUDE_CODE_EXTRA_BODY=') for v in args)


def test_sandbox_declares_the_configured_custom_model_context_window():
    args = sandbox.provider_container_args({'context_window': 1000000})
    assert args == ['-e', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000']


def test_oneoff_sandbox_honors_native_thinking_control():
    args=sandbox.provider_container_args({'max_thinking_tokens':0})
    assert args==['-e','MAX_THINKING_TOKENS=0','-e','CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1']
def test_disabled_thinking_also_disables_cli_adaptive_thinking():
    for config in [{'max_thinking_tokens': 0}, {'extra_body': {'thinking': {'type': 'disabled'}}}]:
        args = sandbox.provider_container_args(config)
        assert args.count('MAX_THINKING_TOKENS=0') == 1
        assert args.count('CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1') == 1
    assert sandbox.thinking_budget({}) is None
    assert sandbox.thinking_budget({'max_thinking_tokens': 2048}) == 2048
