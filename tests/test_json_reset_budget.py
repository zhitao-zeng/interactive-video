"""The existing JSON retry budgets must reach the real streaming boundary."""
import json

import httpx
import pytest

from novel_manga.llm import client, usage
from novel_manga.llm.config import JsonEndpoint


def stream_boundary(monkeypatch, tmp_path, *, succeed_at=None):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    monkeypatch.delenv('NOVEL_REFUSAL_FALLBACK', raising=False)
    monkeypatch.delenv('NOVEL_MODEL_RESOURCES', raising=False)
    wire, calls = [], []
    real_client, real_post = httpx.Client, client.post_any

    def respond(request):
        payload = json.loads(request.content)
        wire.append((str(request.url), payload))
        success = len(wire) == succeed_at
        event = {'choices': [{'delta': {'content': '{"consistent":true}'} if success else
                              {'reasoning_content': 'still considering'},
                              'finish_reason': 'stop' if success else 'length'}],
                 'usage': {'completion_tokens': 5 if success else payload['max_tokens']}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\ndata: [DONE]\n')

    def post(*args, **kwargs):
        calls.append(kwargs['deadline'])
        return real_post(*args, **kwargs)

    monkeypatch.setattr(client, 'post_any', post)
    monkeypatch.setattr(client.httpx, 'Client', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(respond), **kwargs))
    return wire, calls


def settings():
    return JsonEndpoint('phanthy.com/deepseek-v4', ('http://review.invalid/v1',),
                        stream=True, min_max_tokens=16000)


def usage_budgets(tmp_path):
    return [json.loads(line)['max_tokens'] for line in
            (tmp_path / '.runtime' / usage.FILE).read_text().splitlines()]


@pytest.mark.parametrize('from_environment', [False, True])
def test_reasoning_reset_reaches_wire_and_remains_typed_on_exhaustion(monkeypatch, tmp_path, from_environment):
    endpoint = settings()
    if from_environment:
        for key, value in {'QWEN38_LOCAL_MODEL': endpoint.model,
                           'QWEN38_LOCAL_BASE_URL': endpoint.endpoints[0],
                           'QWEN38_LOCAL_STREAM': '1',
                           'QWEN38_LOCAL_MIN_MAX_TOKENS': '16000',
                           'QWEN38_LOCAL_API_KEY_VAR': 'TEST_NO_KEY'}.items():
            monkeypatch.setenv(key, value)
    wire, calls = stream_boundary(monkeypatch, tmp_path)
    with pytest.raises(client.ModelOutputTruncated) as failed:
        client.ask_json([], {}, name='episode_first_frame', max_tokens=8000,
                        settings=None if from_environment else endpoint)
    assert [payload['max_tokens'] for _, payload in wire] == [16000, 16000, 8000]
    assert usage_budgets(tmp_path) == [16000, 16000, 8000]
    assert failed.value.max_tokens == 8000
    assert 'JSON truncated at 8000' in str(failed.value)
    assert len(calls) == 3 and all(call is calls[0] for call in calls)
    assert {url for url, _ in wire} == {'http://review.invalid/v1/chat/completions'}
    assert {payload['model'] for _, payload in wire} == {endpoint.model}
    assert endpoint.min_max_tokens == 16000
    assert usage.scoped() == {}


def test_reset_can_return_normal_json_without_an_extra_attempt(monkeypatch, tmp_path):
    wire, calls = stream_boundary(monkeypatch, tmp_path, succeed_at=3)
    assert client.ask_json([], {}, name='episode_first_frame', max_tokens=8000,
                           settings=settings()) == {'consistent': True}
    assert [payload['max_tokens'] for _, payload in wire] == [16000, 16000, 8000]
    assert len(calls) == 3


def test_first_success_keeps_reasoning_floor(monkeypatch, tmp_path):
    wire, calls = stream_boundary(monkeypatch, tmp_path, succeed_at=1)
    assert client.ask_json([], {}, name='episode_first_frame', max_tokens=8000,
                           settings=settings()) == {'consistent': True}
    assert [payload['max_tokens'] for _, payload in wire] == [16000]
    assert usage_budgets(tmp_path) == [16000]
    assert len(calls) == 1


def test_no_retry_reports_the_actual_floor_budget(monkeypatch, tmp_path):
    wire, calls = stream_boundary(monkeypatch, tmp_path)
    with pytest.raises(client.ModelOutputTruncated) as failed:
        client.ask_json([], {}, name='episode_first_frame', max_tokens=8000,
                        settings=settings(), retry_truncated=False)
    assert [payload['max_tokens'] for _, payload in wire] == [16000]
    assert failed.value.max_tokens == 16000
    assert 'JSON truncated at 16000' in str(failed.value)
    assert usage_budgets(tmp_path) == [16000]
    assert len(calls) == 1
