"""User-authorized time-window renewal via run_dir/window-grant.json."""
import json

import pytest

from novel_manga.application.agents.episode import lifecycle, recovery
from novel_manga.interactive import first_frame_entry
from novel_manga.util import atomic_write_json, read_json


def ctx_of(tmp_path):
    from types import SimpleNamespace
    run = tmp_path/'run'; run.mkdir(exist_ok=True)
    return SimpleNamespace(run_dir=run, episode_dir=tmp_path/'ep')


def test_grant_renews_once_and_unblocks(tmp_path):
    ctx = ctx_of(tmp_path)
    atomic_write_json(ctx.run_dir/'window-grant.json',
                      {'seconds': 900, 'authorized_by': 'user 2026-10-22', 'granted_at': 'x'})
    saved = {'status': 'blocked', 'reason': 'episode operation timeout exhausted', 'remaining_seconds': 0}
    renewed = lifecycle._consume_window_grant(ctx, saved)
    assert renewed['remaining_seconds'] == 900
    assert renewed['status'] == 'deferred' and renewed['reason'] is None
    assert renewed['window_grant']['authorized_by'] == 'user 2026-10-22'
    consumed = json.loads((ctx.run_dir/'window-grant.json').read_text())
    assert consumed['consumed_at']
    # Second start must not double-consume; the earlier renewal survives as-is.
    again = lifecycle._consume_window_grant(ctx, {**renewed, 'remaining_seconds': 812})
    assert again['remaining_seconds'] == 812 and 'window_grant' in again


def test_grant_stacks_remaining_and_ignores_finished_runs(tmp_path):
    ctx = ctx_of(tmp_path)
    atomic_write_json(ctx.run_dir/'window-grant.json', {'seconds': 300, 'authorized_by': 'user'})
    saved = {'status': 'deferred', 'remaining_seconds': 470}
    renewed = lifecycle._consume_window_grant(ctx, saved)
    assert renewed['remaining_seconds'] == 770 and renewed['status'] == 'deferred'
    # A passed or upstream-recovery run never takes a window grant.
    assert lifecycle._consume_window_grant(ctx, {'status': 'deferred', 'remaining_seconds': 5, 'passed': True})['remaining_seconds'] == 5


def test_plan_resume_reads_existing_window_without_consuming(tmp_path):
    ctx = ctx_of(tmp_path)
    atomic_write_json(ctx.run_dir/'window-grant.json', {'seconds':300,'authorized_by':'user'})
    saved = {'status':'blocked','remaining_seconds':0}
    planned = recovery.plan_resume(ctx, saved, {}, False)
    assert planned['saved']['remaining_seconds'] == 300
    assert planned['saved']['status'] == 'deferred'
    assert 'consumed_at' not in read_json(ctx.run_dir/'window-grant.json', {})
    assert not (ctx.run_dir/'agent-run.json').exists()
    lifecycle.apply_resume_plan(ctx, planned)
    assert read_json(ctx.run_dir/'agent-run.json', {})['remaining_seconds'] == 300
    assert read_json(ctx.run_dir/'window-grant.json', {})['consumed_at']


def test_first_frame_entry_only_passes_resume_intent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = tmp_path
    novel = root/'outputs/book'
    episode = novel/'book_1'
    run_dir = root/'run'
    ctx = SimpleNamespace(run_dir=run_dir,episode_dir=episode,novel_dir=novel,
        work_dir=run_dir/'sandbox',source_episode_dir=episode,input_mode='script')
    atomic_write_json(novel/'profile.json', {})
    atomic_write_json(run_dir/'frozen.json', {})
    atomic_write_json(run_dir/'agent-run.json', {'status':'blocked','remaining_seconds':0})
    monkeypatch.setattr('novel_manga.interactive.runtime.configure',lambda *a,**k:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.context.create_context',lambda *a,**k:ctx)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.bounded_recovery.resume_window',
                        lambda *a:pytest.fail('entry consumed recovery before lifecycle'))
    calls = []
    monkeypatch.setattr('novel_manga.application.agents.episode.flow.run_episode',
                        lambda *a,**k:calls.append(k) or {'status':'blocked','remaining_seconds':0})
    result = first_frame_entry.run(root,novel,project='adventure',destination=root/'delivery',resume=True,config={})
    assert result['status'] == 'blocked'
    assert calls[0]['retry_issues'] is True and 'timeout' not in calls[0]


def test_protocol_fallback_window_preserves_final_state_and_config(tmp_path, monkeypatch):
    ctx = ctx_of(tmp_path)
    ctx.work_dir = ctx.run_dir/'sandbox'
    atomic_write_json(ctx.run_dir/'window-grant.json', {'seconds':300,'authorized_by':'user'})
    saved = {'status':'blocked','remaining_seconds':100,'attempts':['old-attempt']}
    config = {'model':'DS','refusal_fallback':{'model':'Qwen','base_url':'http://qwen','key_var':'QWEN_KEY'}}
    def protocol(_run,_episode,current,*,validate_input):
        return {**current,'status':'deferred','protocol_recovery':{'kind':'idle_turns'}}
    monkeypatch.setattr(recovery,'resume_protocol',protocol)
    planned = recovery.plan_resume(ctx,saved,config,False)
    assert planned['saved']['model_switch']['to_model'] == 'Qwen'
    assert planned['saved']['remaining_seconds'] == 400
    assert not (ctx.run_dir/'agent-run.json').exists()
    assert 'consumed_at' not in read_json(ctx.run_dir/'window-grant.json',{})
    final = lifecycle.apply_resume_plan(ctx,planned)
    assert final['model_switch']['to_model'] == 'Qwen'
    assert final['remaining_seconds'] == 400
    persisted = read_json(ctx.run_dir/'agent-run.json',{})
    assert persisted['model_switch']['to_model'] == 'Qwen'
    assert persisted['remaining_seconds'] == 400
    assert planned['config']['model'] == 'Qwen'
    assert read_json(ctx.run_dir/'model-switch/switch.json',{})['to_model'] == 'Qwen'
    assert read_json(ctx.run_dir/'protocol-recovery.json',{})['recovery'] == {'kind':'idle_turns'}
    assert read_json(ctx.run_dir/'window-grant.json',{})['consumed_at']
