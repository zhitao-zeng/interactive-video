from types import SimpleNamespace
import pytest
from novel_manga.application.agents.episode import readiness
from novel_manga.util import atomic_write_json


def case(tmp_path, monkeypatch, error='publication_gate', clips=None):
    ctx=SimpleNamespace(episode_dir=tmp_path/'book/book_1',novel_dir=tmp_path/'book',
        run_dir=tmp_path/'run',work_dir=tmp_path/'sandbox')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[]})
    monkeypatch.setattr(readiness,'next_action',lambda *a:{'action':'finish','reason':'current assembly and review are ready for acceptance'})
    state={'adopted':True,'job':{'action':'finish','status':'completed','result':{
        'ok':False,'error':error,'clips':[] if clips is None else clips}}}
    return ctx,state


def test_ready_publication_can_resume_once_after_direct_finish_without_auto_fingerprint(tmp_path,monkeypatch):
    ctx,state=case(tmp_path,monkeypatch)
    request=readiness.automatic_request(ctx,state)
    assert request is not None and request['action']=='finish'
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{'automatic_fingerprint':request['fingerprint']}})
    assert readiness.automatic_request(ctx,state) is None


@pytest.mark.parametrize('error,clips', [('unknown_failure',[]),('publication_gate',['clip_01'])])
def test_no_unbounded_retry_for_other_finish_errors_or_unresolved_content(tmp_path,monkeypatch,error,clips):
    ctx,state=case(tmp_path,monkeypatch,error,clips)
    assert readiness.automatic_request(ctx,state) is None
