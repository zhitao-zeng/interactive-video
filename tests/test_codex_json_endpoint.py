import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.llm import codex,transport
from novel_manga.llm.config import endpoint_settings
from novel_manga.util import atomic_write_json,read_json


def test_explicit_codex_route_never_uses_exhausted_http_key_or_relabels_model(monkeypatch):
    request={'model':'gpt-5.6-luna','messages':[{'role':'user','content':'Check source.'}]};before=copy.deepcopy(request);seen=[]
    def complete(payload,*,timeout):
        seen.append((payload,timeout));return {'choices':[{'message':{'content':'{"consistent":false}'}}]}
    monkeypatch.setattr(codex,'completion',complete)
    answer=transport.post_any(None,list(endpoint_settings('codex-luna').endpoints),{'Authorization':'unused'},request)
    assert json.loads(answer['choices'][0]['message']['content'])=={'consistent':False}
    assert seen[0][0]['model']=='gpt-5.6-luna'and request==before


@pytest.mark.parametrize('schema_mode',[True,False])
def test_codex_json_preserves_actual_image_bytes_order_and_negative_finding(tmp_path,monkeypatch,schema_mode):
    from novel_manga.application import configuration
    from novel_manga.application.agents import sandbox
    from novel_manga.application.agents.episode import codex_session
    monkeypatch.setattr(configuration,'project_root',lambda:tmp_path)
    monkeypatch.setattr(sandbox,'load_config',lambda *a:{'image':'existing-sandbox'})
    schema={'type':'object','properties':{'consistent':{'type':'boolean'}},'required':['consistent'],'additionalProperties':False}
    parts=[{'type':'text','text':'Compare image one and two.'}]+[
        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}}for raw in[b'actual image one',b'actual image two']]
    request={'model':'gpt-5.6-luna','messages':[{'role':'user','content':parts}],
             'response_format':{'type':'json_schema','json_schema':{'schema':schema}}if schema_mode else{'type':'json_object'}}
    before=copy.deepcopy(request)
    def execute(work,prompt,**kwargs):
        config=kwargs['config'];assert config['model']=='gpt-5.6-luna'
        assert[(work/p).read_bytes()for p in config['codex_attached_images']]==[b'actual image one',b'actual image two']
        assert 'base64'not in(work/'input/request.json').read_text()
        assert read_json(work/'input/manifest.json')['phase']=='model_json'
        if schema_mode:assert read_json(work/config['codex_output_schema'])==schema
        else:assert 'codex_output_schema'not in config
        atomic_write_json(work/'output/response.json',{'consistent':False})
        return SimpleNamespace(ok=True,produced=['response.json'],record={'usage':{'input_tokens':18}})
    monkeypatch.setattr(codex_session,'run_session',execute)
    result=codex.completion(request,timeout=90)
    assert json.loads(result['choices'][0]['message']['content'])=={'consistent':False}
    assert result['usage']=={'input_tokens':18}and request==before


def test_codex_unavailable_never_returns_a_successful_empty_review(tmp_path,monkeypatch):
    from novel_manga.application import configuration
    from novel_manga.application.agents import sandbox
    from novel_manga.application.agents.episode import codex_session
    monkeypatch.setattr(configuration,'project_root',lambda:tmp_path)
    monkeypatch.setattr(sandbox,'load_config',lambda *a:{})
    monkeypatch.setattr(codex_session,'run_session',lambda work,*a,**k:SimpleNamespace(ok=False,produced=[],directory=work/'attempt'))
    with pytest.raises(RuntimeError,match='did not complete'):codex.completion({'model':'gpt-5.6-luna','messages':[]},timeout=5)


def test_native_model_capacity_is_a_retryable_service_failure_not_content(tmp_path, monkeypatch):
    from novel_manga.application import configuration
    from novel_manga.application.agents import sandbox
    from novel_manga.application.agents.episode import codex_session
    from novel_manga.service_errors import service_failure

    monkeypatch.setattr(configuration, 'project_root', lambda:tmp_path)
    monkeypatch.setattr(sandbox, 'load_config', lambda *args:{})
    def execute(work, *args, **kwargs):
        attempt = work/'attempt'; attempt.mkdir()
        (attempt/'codex-events.jsonl').write_text(json.dumps({
            'type':'turn.failed', 'error':{'message':'Selected model is at capacity. Please try a different model.'}})+'\n')
        return SimpleNamespace(ok=False, produced=[], directory=attempt)
    monkeypatch.setattr(codex_session, 'run_session', execute)
    with pytest.raises(ConnectionError, match='at capacity') as stopped:
        codex.completion({'model':'gpt-6-sol', 'messages':[]}, timeout=5)
    assert service_failure(stopped.value)['retryable'] is True
    assert list(tmp_path.rglob('codex-events.jsonl'))


def test_codex_clip_repair_uses_saved_login_without_phanthy_credential(tmp_path,monkeypatch):
    from novel_manga.application.agents import sandbox
    from novel_manga.application.agents.episode import codex_session
    work=tmp_path/'repair-case';work.mkdir();(work/'prompt.txt').write_text('Inspect supplied frames; write a decision.')
    monkeypatch.setattr(sandbox,'key_for',lambda *a:pytest.fail('Must not request exhausted Phanthy key'))
    def execute(directory,prompt,**kwargs):
        assert read_json(directory/'input/manifest.json')['phase']=='clip_repair'
        assert kwargs['expected_output']=='decision.json'and not kwargs['resume']
        return SimpleNamespace(ok=True,produced=['decision.json'])
    monkeypatch.setattr(codex_session,'run_session',execute)
    assert sandbox.run_agent('repair-case',config={'backend':'codex','model':'gpt-5.6-luna','runs_root':str(tmp_path)},timeout=300).ok
