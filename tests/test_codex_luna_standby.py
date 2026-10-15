import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/standby/codex_luna'))

@pytest.fixture(autouse=True)
def isolated_usage_root(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))

def test_bounded_sandbox_luna_uses_login_and_preserves_prior_outputs(tmp_path,monkeypatch):
    import luna_sandbox as adapter
    work=tmp_path/'run'; (work/'input').mkdir(parents=True); (work/'output').mkdir()
    (work/'prompt.txt').write_text('Read input/primary.json; write output/decision.json')
    (work/'output/old.json').write_text('{"old":true}')
    def execute(command,attempt,**kwargs):
        assert command[command.index('-m')+1]=='gpt-6-luna' and '--ignore-user-config' in command
        assert command[command.index('--sandbox')+1]=='workspace-write'
        assert kwargs['cwd']==work and kwargs['timeout']==45
        assert 'actual JPEG' in kwargs['prompt']
        (work/'output/decision.json').write_text('{"action":"stop","reason":"insufficient picture evidence"}')
        (attempt/'events.jsonl').write_text('')
        return {'exit_code':0,'completed':True,'seconds':1,'usage':{'input_tokens':9},'thread_id':'native'}
    monkeypatch.setattr(adapter,'execute',execute)
    result=adapter.run_agent('run',config={'runs_root':str(tmp_path),'model':'old-ds'},timeout=45)
    assert result.ok and result.produced==['decision.json']
    assert result.record['model']=='gpt-6-luna' and result.record['timeout_seconds']==45
    assert (work/'output/old.json').read_text()=='{"old":true}'
    assert (result.directory/'output-before/old.json').read_text()=='{"old":true}'


def test_bounded_sandbox_incomplete_native_turn_is_not_current_success(tmp_path,monkeypatch):
    import luna_sandbox as adapter
    work=tmp_path/'run';work.mkdir();(work/'prompt.txt').write_text('Read pictures and write review')
    def execute(command,attempt,**kwargs):
        (attempt/'events.jsonl').write_text('')
        return {'exit_code':0,'completed':False,'seconds':1,'usage':{},'thread_id':None}
    monkeypatch.setattr(adapter,'execute',execute)
    result=adapter.run_agent('run',config={'runs_root':str(tmp_path)},timeout=12)
    assert not result.ok and result.produced==[]


def test_explicit_sandbox_install_covers_preimported_repair_and_independent_callers(tmp_path):
    import os,subprocess
    env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT/'scripts/standby/codex_luna'),str(ROOT/'src')]),'CODEX_LUNA_ADAPTER':'0'}
    config=tmp_path/'config.json';config.write_text('{"model":"original-ds"}')
    code="""from novel_manga.application.agents import sandbox
from novel_manga.application.repair import agent
from novel_manga.application.review import independent
assert sandbox.run_agent.__module__ != 'luna_sandbox'
import luna_sandbox
luna_sandbox.install()
assert sandbox.run_agent is agent.run_agent is independent.run_agent
assert sandbox.load_config is agent.load_config is independent.load_config
"""+f"assert sandbox.load_config({str(config)!r})['model']=='gpt-6-luna'\n"
    subprocess.run([sys.executable,'-c',code],env=env,check=True,capture_output=True,text=True)
