import json

from novel_manga.llm import usage as llm_usage
from novel_manga.llm.transport import _meter


def test_record_writes_scoped_row(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    with llm_usage.scope(novel='book', episode=12, stage='review'):
        assert llm_usage.record(name='judge', model='m1',
                                usage={'prompt_tokens': 100, 'completion_tokens': 5,
                                       'cache_read_input_tokens': 400})
    rows = [json.loads(x) for x in (tmp_path / '.runtime' / llm_usage.FILE).read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row['prompt_tokens'] == 100 and row['completion_tokens'] == 5
    assert row['cache_read_input_tokens'] == 400
    assert row['novel'] == 'book' and row['episode'] == 12 and row['stage'] == 'review'


def test_record_skips_empty_usage_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    assert llm_usage.record(name='x', model='m', usage=None) is False
    assert llm_usage.record(name='x', model='m', usage={}) is False
    assert not (tmp_path / '.runtime' / llm_usage.FILE).exists()


def test_scope_nests_and_restores():
    with llm_usage.scope(novel='a'):
        assert llm_usage.scoped()['novel'] == 'a'
        with llm_usage.scope(novel='b', episode=1):
            assert llm_usage.scoped()['novel'] == 'b' and llm_usage.scoped()['episode'] == 1
        assert llm_usage.scoped()['novel'] == 'a'
    assert llm_usage.scoped() == {}


def test_meter_extracts_schema_name_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    request = {'model': 'Qwen3.8-Flash-Next',
               'response_format': {'type': 'json_schema', 'json_schema': {'name': 'posture_continuity', 'schema': {}}}}
    body = {'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}
    _meter('http://x:1/v1/chat/completions', request, body)
    row = json.loads((tmp_path / '.runtime' / llm_usage.FILE).read_text().splitlines()[0])
    assert row['name'] == 'posture_continuity'
    assert row['model'] == 'Qwen3.8-Flash-Next'
    assert row['endpoint'] == 'http://x:1/v1'
    assert row['total_tokens'] == 12
    assert 'response_model' not in row


def test_meter_distinguishes_requested_alias_from_reported_model_and_keeps_route_billing(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    _meter('http://127.0.0.1:8789/v1/chat/completions',{'model':'phanthy.com/deepseek-v4'},
        {'model':'kimi-k2.6','usage':{'prompt_tokens':10,'completion_tokens':2}})
    row=json.loads((tmp_path/'.runtime'/llm_usage.FILE).read_text().splitlines()[0])
    assert row['model']=='phanthy.com/deepseek-v4'
    assert row['response_model']=='kimi-k2.6'
    assert row['resource_id']=='phanthy-dsv4' and row['billing']=='free'


def test_meter_survives_malformed_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    _meter('http://x/v1/chat/completions', {}, {})
    _meter('http://x/v1/chat/completions', {'model': 1}, {'usage': None})
    assert not (tmp_path / '.runtime' / llm_usage.FILE).exists()


def test_scaled_timeout_grows_with_input_and_respects_floor_and_cap():
    from novel_manga.llm.client import scaled_timeout
    small = [{'type': 'text', 'text': 'x' * 2000}]
    big = [{'type': 'text', 'text': 'x' * 39000}]   # 703 级身份复核
    huge = [{'type': 'text', 'text': 'x' * 500000}]
    assert scaled_timeout(240, small) == 240          # 小调用不变(调用方原值)
    assert scaled_timeout(10, small) == 10            # 调用方的小预算也不动(共享预算契约)
    assert 600 <= scaled_timeout(240, big) <= 900     # 大料放大
    assert scaled_timeout(240, huge) == 1800          # 封顶
    assert scaled_timeout(900, small) == 900          # 调用方更松时不缩


def test_saved_login_json_is_metered_once_with_resource_and_reasoning(tmp_path,monkeypatch):
    from novel_manga.llm import codex,transport
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    monkeypatch.setattr(codex,'completion',lambda *a,**k:{'choices':[{'message':{'content':'{}'}}],
        'usage':{'input_tokens':20,'cached_input_tokens':10,'output_tokens':3}})
    transport.post_any(None,['codex://local'],{}, {'model':'gpt-5.6-luna','reasoning_effort':'medium','messages':[]})
    rows=[json.loads(l) for l in (tmp_path/'.runtime'/llm_usage.FILE).read_text().splitlines()]
    assert len(rows)==1
    assert rows[0]['resource_id']=='codex-luna'
    assert rows[0]['billing']=='subscription' and rows[0]['reasoning_effort']=='medium'
    assert rows[0]['cached_input_tokens']==10
