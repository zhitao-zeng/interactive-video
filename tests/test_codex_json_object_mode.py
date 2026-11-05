"""Native structured JSON handoff and the exact retained B9 malformed response."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.llm import client, codex
from novel_manga.llm.config import JsonEndpoint


B9_MALFORMED = '{"shots":["A wide, chest-height handheld view follows from behind <Subject 1> along the wooden pier toward the shallow water, keeping her outstretched hand, the drifting dark gray backpack, and <Subject 2> beside the mooring post on the right in frame. <Subject 1> reaches the edge and stops, still watching the backpack as it slides across the shallows. “<DIALOGUE_1>” <Subject 2> approaches from the right and warns her to steady herself. “<DIALOGUE_2>” They remain at the pier edge while the backpack floats near shore, unrecovered."],"soundscape":"Rapid footsteps begin as <Subject 1> runs along the pier and stop when she reaches the edge; the backpack scrapes through shallow water as it slides, then stops; sea wind continues throughout."],"conflicts":[]}'
SCHEMA = {'type':'object', 'additionalProperties':False,
          'required':['shots','soundscape','conflicts'], 'properties':{
              'shots':{'type':'array','minItems':1,'maxItems':1,'items':False,
                       'prefixItems':[{'type':'string','minLength':1,'pattern':'^.*<DIALOGUE_1>.*$'}]},
              'soundscape':{'type':'string'},
              'conflicts':{'type':'array','items':{'type':'string'}}}}
VALID = {'shots':['She stops. <DIALOGUE_1>'], 'soundscape':'Sea wind.', 'conflicts':[]}


def native(monkeypatch, tmp_path, responses):
    from novel_manga.application import configuration
    from novel_manga.application.agents import sandbox
    from novel_manga.application.agents.episode import codex_session
    monkeypatch.setattr(configuration, 'project_root', lambda:tmp_path)
    monkeypatch.setattr(sandbox, 'load_config', lambda *a:{})
    monkeypatch.delenv('NOVEL_REFUSAL_FALLBACK', raising=False)
    calls, deadlines = [], []
    real_post = client.post_any
    def execute(work, prompt, **kwargs):
        calls.append({'work':work, 'prompt':prompt, **kwargs})
        text = responses[min(len(calls)-1, len(responses)-1)]
        (work/'output/response.json').write_text(text)
        return SimpleNamespace(ok=True, produced=['response.json'], directory=work/'attempt',
                               record={'seconds':1, 'usage':{'input_tokens':10,'output_tokens':5}})
    def post(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        return real_post(*args, **kwargs)
    monkeypatch.setattr(codex_session, 'run_session', execute)
    monkeypatch.setattr(client, 'post_any', post)
    return calls, deadlines


def ask(**kwargs):
    return client.ask_json([{'type':'text','text':'Translate the supplied shot.'}], SCHEMA,
        name='h3prompt', json_object=True, max_tokens=1150, timeout=60,
        settings=JsonEndpoint('gpt-6-sol', ('codex://local',)), **kwargs)


@pytest.mark.parametrize('from_env', [False, True])
def test_native_json_object_keeps_full_caller_schema_and_first_success(monkeypatch, tmp_path, from_env):
    calls, deadlines = native(monkeypatch, tmp_path, [json.dumps(VALID)])
    before = copy.deepcopy(SCHEMA)
    if from_env:
        monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'codex://local')
        monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'gpt-6-sol')
        result=client.ask_json([], SCHEMA, name='h3prompt', json_object=True)
    else:
        result=ask()
    assert result == VALID and len(calls) == 1
    call=calls[0]
    assert call['config']['codex_output_schema'] == 'input/schema.json'
    assert json.loads((call['work']/'input/schema.json').read_text()) == before
    request=json.loads((call['work']/'input/request.json').read_text())
    assert request['response_format']['type'] == 'json_schema'
    assert request['response_format']['json_schema']['schema'] == before
    assert SCHEMA == before


def test_native_transport_preserves_exact_malformed_text_without_parsing(monkeypatch, tmp_path):
    calls, _ = native(monkeypatch, tmp_path, [B9_MALFORMED])
    result=codex.completion({'model':'gpt-6-sol','messages':[]},timeout=60)
    assert result['choices'][0]['message']['content'] == B9_MALFORMED
    assert result['codex_execution']['response_path'] == str(calls[0]['work']/'output/response.json')


@pytest.mark.parametrize('malformed_attempts', [1, 2])
def test_b9_malformed_retries_inside_existing_shared_deadline(monkeypatch, tmp_path, malformed_attempts):
    calls, deadlines = native(monkeypatch, tmp_path,
        [B9_MALFORMED]*malformed_attempts+[json.dumps(VALID)])
    assert ask() == VALID
    assert len(calls) == malformed_attempts+1
    assert all(deadline is deadlines[0] for deadline in deadlines)
    assert all(0 < call['timeout'] <= 60 for call in calls)
    for call in calls[:malformed_attempts]:
        assert (call['work']/'output/response.json').read_text() == B9_MALFORMED
    assert [json.loads((c['work']/'input/request.json').read_text())['max_tokens']
            for c in calls] == [1150]*len(calls)


@pytest.mark.parametrize('text', [B9_MALFORMED, '[]', ''])
@pytest.mark.parametrize('retry', [True, False])
def test_malformed_exhaustion_is_typed_and_preserves_receipt_not_content(monkeypatch, tmp_path, text, retry):
    calls, deadlines = native(monkeypatch, tmp_path, [text])
    with pytest.raises(ValueError) as stopped:
        ask(retry_truncated=retry)
    assert isinstance(stopped.value, client.ModelOutputMalformed)
    assert stopped.value.name == 'h3prompt'
    assert len(calls) == (3 if retry else 1)
    response=calls[-1]['work']/'output/response.json'
    assert stopped.value.response_path == str(response)
    assert str(response) in str(stopped.value) and 'h3prompt' in str(stopped.value)
    assert 'A wide, chest-height' not in str(stopped.value)
    assert response.read_text() == text
    assert all(deadline is deadlines[0] for deadline in deadlines)


def test_valid_fenced_json_is_still_extracted_without_retry(monkeypatch, tmp_path):
    calls, _=native(monkeypatch,tmp_path,['```json\n'+json.dumps(VALID)+'\n```'])
    assert ask() == VALID and len(calls) == 1


def test_unknown_assertion_is_not_model_output_retry(monkeypatch, tmp_path):
    calls, _=native(monkeypatch,tmp_path,[])
    def failed(*args, **kwargs):
        raise AssertionError('unrelated implementation bug')
    monkeypatch.setattr(client,'post_any',failed)
    with pytest.raises(AssertionError, match='unrelated implementation bug'):
        ask()
    assert calls == []


def test_malformed_retry_does_not_renew_an_exhausted_deadline(monkeypatch, tmp_path):
    calls, deadlines=native(monkeypatch,tmp_path,[B9_MALFORMED])
    post=client.post_any
    def expired(*args, **kwargs):
        result=post(*args, **kwargs)
        kwargs['deadline'].at-=1000
        return result
    monkeypatch.setattr(client,'post_any',expired)
    with pytest.raises(TimeoutError, match='budget exhausted'):
        ask()
    assert len(calls) == 1
    assert all(deadline is deadlines[0] for deadline in deadlines)
