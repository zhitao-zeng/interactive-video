from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode import prompts, readiness, current_task, flow, turns
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context as ctx, sandbox_config as config, install_fake_runtime


def test_resume_prompt_uses_current_authority_after_first_pass():
    prompt = prompts.resume_prompt({
        'phase': 'review', 'mode': 'adopted_repair', 'workflow_scope': 'full_production',
        'targets': [], 'next_action': {'action': 'review', 'reason': 'first videos complete'},
        'execution_rules': ['Review and finish are allowed in the current phase.'],
    })
    assert 'Review and finish are allowed in the current phase.' in prompt
    assert 'full_production' in prompt
    assert 'keep them in force' not in prompt


def test_scope_change_preserves_saved_budget_and_original_session(tmp_path, monkeypatch):
    ctx = SimpleNamespace(work_dir=tmp_path/'work', episode_dir=tmp_path/'episode', run_dir=tmp_path/'run')
    saved = {'session_id': 'original-author', 'backend': 'codex'}
    budget = {'status': 'deferred', 'remaining_seconds': 123, 'agent_seconds': 40}
    atomic_write_json(ctx.work_dir/'episode-session.json', saved)
    atomic_write_json(ctx.run_dir/'agent-run.json', budget)
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY', '1')
    first = current_task.prepare(ctx, {'adopted': True, 'next_action': {'action': 'render'}}, {'issues': []}, [])
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY', '0')
    full = current_task.prepare(ctx, {'adopted': True, 'next_action': {'action': 'review'}}, {'issues': []}, [])
    assert first['workflow_scope'] == 'first_pass'
    assert full['workflow_scope'] == 'full_production'
    assert read_json(ctx.work_dir/'episode-session.json') == saved
    assert read_json(ctx.run_dir/'agent-run.json') == budget


def test_host_dispatches_review_but_never_repeats_unchanged_failed_finish(monkeypatch):
    ctx = object()
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {'action': 'review'})
    assert readiness.automatic_action(ctx, {'adopted': True, 'job': {
        'action': 'render', 'status': 'completed', 'result': {'ok': True}}}) == 'review'
    assert readiness.automatic_action(ctx, {'adopted': True}, first_pass_only=True) is None
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {'action': 'finish'})
    assert readiness.automatic_action(ctx, {'adopted': True, 'job': {
        'action': 'finish', 'status': 'completed', 'result': {'ok': False, 'error': 'music_not_applied'}}}) is None
    assert readiness.automatic_action(ctx, {'adopted': False}) is None
    assert readiness.automatic_action(ctx, {'adopted': True, 'job': {
        'action': 'finish', 'status': 'failed', 'error': 'connection interrupted'}}) is None


def test_actual_controller_review_finish_handoff_needs_no_agent_turn(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(turns, 'run_session', lambda *args, **kwargs: pytest.fail('Host stage handoff must not require a manual or Agent turn'))
    monkeypatch.setattr(readiness, 'next_action', lambda _, status=None: {
        'action': 'finish' if (status.get('job') or {}).get('action') == 'review' else 'review'})
    atomic_write_json(ctx.work_dir/'episode-session.json', {'session_id': 'old-first-pass'})
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True
    assert calls.count('review') == 1
    assert calls.count('finish') == 1
    assert 'render' not in calls


def test_current_failed_review_returns_to_author_before_automatic_finish(tmp_path,monkeypatch):
    ctx=SimpleNamespace(episode_dir=tmp_path/'episode',work_dir=tmp_path/'work')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'a','kind':'video'}]})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'status':'clips_ready','clips':[{'clip_id':'a'}]})
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'clips':{'a':{'severity':'fail'}},'feedback':{'a':'wrong identity'}})
    monkeypatch.setattr(readiness,'first_frame_blocks',lambda *a:[])
    monkeypatch.setattr(readiness,'media_started',lambda *a:True)
    monkeypatch.setattr(readiness,'review_is_current',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**k:True)
    assert readiness.next_action(ctx)['action']=='agent'
    assert readiness.automatic_action(ctx,{'adopted':True,'job':{'action':'review','status':'completed'}}) is None
