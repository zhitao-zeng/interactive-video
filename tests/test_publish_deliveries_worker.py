import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from novel_manga.util import atomic_write_json,read_json


@pytest.mark.parametrize('kind,attempts,sleeps',[
    ('ModuleNotFoundError',1,0),('RuntimeError',3,2),
    ('AuthenticationError',1,0),('AuthorizationError',1,0),
])
def test_worker_records_local_missing_module_once_and_preserves_other_retry_rules(tmp_path,monkeypatch,kind,attempts,sleeps):
    script=Path(__file__).resolve().parents[1]/'scripts/publish_deliveries.py'
    namespace=runpy.run_path(str(script));main=namespace['main'];scope=main.__globals__
    spec=tmp_path/'spec.json';state=tmp_path/'publication/state.json'
    atomic_write_json(spec,{'repository':'owner/dataset'})
    monkeypatch.setattr(sys,'argv',[str(script),'--spec',str(spec),'--state',str(state)])
    # The worker intentionally clears command-scoped proxy settings. Restore the
    # test process environment afterwards without ever printing their values.
    for key,value in list(os.environ.items()):
        if key.lower() in {'http_proxy','https_proxy','all_proxy'}:monkeypatch.setenv(key,value)
    monkeypatch.setitem(sys.modules,'modelscope_hub',SimpleNamespace(HubApi=lambda:SimpleNamespace(
        whoami=lambda:SimpleNamespace(username='owner'))))
    calls=[];waits=[]
    def publish(*args):
        calls.append(args)
        if kind=='ModuleNotFoundError':raise ModuleNotFoundError('private details must not be stored',name='httpx')
        raise type(kind,(Exception,),{})('private details must not be stored')
    monkeypatch.setitem(scope,'publish_deliveries',publish)
    monkeypatch.setattr(scope['time'],'sleep',lambda seconds:waits.append(seconds))
    monkeypatch.setattr(scope['logging'],'disable',lambda *a:None)
    assert main()==1
    assert len(calls)==attempts and waits==[30]*sleeps
    records=sorted(state.parent.glob('error-*.json'))
    assert len(records)==attempts
    expected={'type':kind,**({'module':'httpx'} if kind=='ModuleNotFoundError' else {})}
    assert all(read_json(path)==expected for path in records)
