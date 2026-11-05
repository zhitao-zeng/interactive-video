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


def test_automatic_request_derives_current_first_frame_file_in_plan_order(tmp_path, monkeypatch):
    ctx = SimpleNamespace(episode_dir=tmp_path/'episode')
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [
        {'clip_id': 'clip_02', 'kind': 'video'},
        {'clip_id': 'title', 'kind': 'card'},
        {'clip_id': 'clip_01', 'kind': 'video'},
    ]})
    route = {'action': 'first_frames', 'reason': 'two openings are missing',
             'blocked': {'clip_01': 'stale', 'clip_02': 'missing'}}
    monkeypatch.setattr(readiness, 'next_action', lambda *_: route)
    status = {'adopted': True, 'job': {'action': 'apply', 'status': 'completed',
                                      'result': {'changed': ['clip_01']}}}
    request = readiness.automatic_request(ctx, status)
    assert request['action'] == 'first_frames' and request['route'] == route
    assert request['payload'] == {'file': 'output/first-frames.json'}
    assert request['document'] == {'render_ready': False, 'frames': [
        {'clip_id': 'clip_02', 'extra_references': []},
        {'clip_id': 'clip_01', 'extra_references': []},
    ]}
    assert len(request['fingerprint']) == 64
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        **route, 'blocked': {'not_in_the_current_plan': 'missing'}})
    assert readiness.automatic_request(ctx, status) is None


@pytest.mark.parametrize('action', ['agent', 'adopt', 'select_music'])
def test_automatic_request_leaves_authored_actions_to_their_existing_owners(action, monkeypatch):
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {'action': action})
    assert readiness.automatic_request(object(), {'adopted': True}) is None


@pytest.mark.parametrize('job_status', ['completed', 'failed'])
def test_automatic_request_does_not_replay_an_unchanged_terminal_action(job_status, monkeypatch):
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        'action': 'first_frames', 'blocked': {'clip_01': 'missing'}})
    status = {'adopted': True, 'job': {'action': 'first_frames', 'status': job_status}}
    assert readiness.automatic_request(object(), status) is None


def test_actual_controller_first_frames_render_review_finish_without_agent_session(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [
        {'clip_id': 'clip_01', 'kind': 'video'},
        {'clip_id': 'clip_02', 'kind': 'video'},
    ]})
    first_frame_payloads = []

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'first_frames':
                first_frame_payloads.append(payload)
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(turns, 'run_session',
                        lambda *args, **kwargs: pytest.fail('normal stages must not create an Agent session'))

    def route(_, status=None):
        action = (status.get('job') or {}).get('action')
        if action is None:
            return {'action': 'first_frames', 'reason': 'missing openings',
                    'blocked': {'clip_01': 'missing', 'clip_02': 'missing'}}
        return {
            'first_frames': {'action': 'render'},
            'render': {'action': 'review'},
            'review': {'action': 'finish'},
            'finish': {'action': 'agent'},
        }[action]

    monkeypatch.setattr(readiness, 'next_action', route)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True
    assert [action for action in calls if action != 'status'] == [
        'first_frames', 'render', 'review', 'finish']
    assert first_frame_payloads == [{'file': 'output/first-frames.json'}]
    assert read_json(ctx.work_dir/'output/first-frames.json') == {
        'render_ready': False, 'frames': [
            {'clip_id': 'clip_01', 'extra_references': []},
            {'clip_id': 'clip_02', 'extra_references': []},
        ],
    }
    assert not (ctx.work_dir/'episode-session.json').exists()


def test_input_apply_handoff_uses_the_same_host_first_frame_request(ctx, config, monkeypatch):
    import time

    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'running', 'deadline': time.time() + 30,
        'job': {'id': 'job-4', 'action': 'apply', 'status': 'completed',
                'result': {'ok': True, 'changed': ['clip_01'], 'input_repaired': ['clip_01']}},
    })

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(turns, 'run_session',
                        lambda *args, **kwargs: pytest.fail('an applied input change must continue on the host'))

    def route(_, status=None):
        action = (status.get('job') or {}).get('action')
        return {
            'apply': {'action': 'first_frames', 'reason': 'input changed the opening',
                      'blocked': {'clip_01': 'stale'}},
            'first_frames': {'action': 'finish'},
            'finish': {'action': 'agent'},
        }[action]

    monkeypatch.setattr(readiness, 'next_action', route)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True
    assert [action for action in calls if action != 'status'] == ['first_frames', 'finish']
    assert read_json(ctx.work_dir/'output/first-frames.json') == {
        'render_ready': False,
        'frames': [{'clip_id': 'clip_01', 'extra_references': []}],
    }


def test_premature_failed_finish_returns_to_current_host_work(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'finish' and 'first_frames' not in calls:
                atomic_write_json(ctx.work_dir/'input/latest-finish.json', {
                    'passed': False, 'technical_passed': False,
                    'author_session': read_json(ctx.work_dir/'episode-session.json'),
                })
                return {'ok': False, 'action': 'finish', 'passed': False,
                        'error': 'missing_video', 'required_action': 'first_frames'}
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    bridges = []
    original_bridge = flow.EpisodeBridge

    def make_bridge(*args):
        bridge = original_bridge(*args)
        bridges.append(bridge)
        return bridge

    monkeypatch.setattr(flow, 'EpisodeBridge', make_bridge)
    sessions = []

    def session(work, prompt, **options):
        if sessions:
            pytest.fail('a failed early finish must not repeatedly ask the Agent to finish')
        sessions.append(options)
        assert 'first_frames' not in calls
        bridges[0].handle('finish', {})
        directory = ctx.work_dir/'attempt-early-finish'
        directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(ok=True, exit_code=0, directory=directory,
                               record={'session_id': 'hostless'}, produced=[])

    monkeypatch.setattr(turns, 'run_session', session)

    def route(_, status=None):
        job = status.get('job') or {}
        if job.get('action') == 'first_frames':
            return {'action': 'finish'}
        if job.get('action') == 'finish' and (job.get('result') or {}).get('passed') is True:
            return {'action': 'finish'}
        if job.get('action') == 'finish':
            return {'action': 'first_frames', 'reason': 'the movie is not complete',
                    'blocked': {'clip_01': 'missing'}}
        return {'action': 'agent', 'reason': 'authored exception remains'}

    monkeypatch.setattr(readiness, 'next_action', route)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True
    assert len(sessions) == 1
    assert [action for action in calls if action != 'status'] == ['first_frames', 'finish']


@pytest.mark.parametrize('with_session', [False, True])
def test_render_finish_shortcut_rejects_stale_manifest(ctx, config, monkeypatch, with_session):
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
    author = {'session_id': 'old-session'} if with_session else {}
    manifest_path = ctx.work_dir/'input/latest-finish.json'
    atomic_write_json(manifest_path, {
        'passed': True, 'technical_passed': True, 'author_session': author})
    before = manifest_path.read_bytes()
    if with_session:
        atomic_write_json(ctx.work_dir/'episode-session.json', author)

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'finish':
                calls.append(action)
                return {'ok': True, 'action': action, 'passed': True}
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            elif action == 'render':
                result['quality_passed'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(readiness, 'next_action', lambda _, status=None: (
        {'action': 'agent', 'reason': 'the finish receipt is stale'}
        if (status.get('job') or {}).get('action') == 'finish' else {'action': 'render'}))
    sessions = []

    def session(work, prompt, **options):
        sessions.append(options)
        directory = ctx.work_dir/f'attempt-stale-{len(sessions)}'
        directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(ok=True, exit_code=0, directory=directory,
                               record={'session_id': 'unused'}, produced=[])

    monkeypatch.setattr(turns, 'run_session', session)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['status'] == 'blocked' and result['passed'] is False
    assert len(sessions) == 2
    assert calls.count('render') == 1 and calls.count('finish') == 1
    assert manifest_path.read_bytes() == before


def test_automatic_request_fingerprint_tracks_remaining_targets_and_inputs(tmp_path, monkeypatch):
    ctx = SimpleNamespace(episode_dir=tmp_path/'episode', run_dir=tmp_path/'run')
    clips = [
        {'clip_id': 'clip_01', 'kind': 'video', 'shots': [{'start_state': 'one'}]},
        {'clip_id': 'clip_02', 'kind': 'video', 'shots': [{'start_state': 'two'}]},
    ]
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    route = {'action': 'first_frames', 'reason': 'missing openings',
             'blocked': {'clip_01': 'missing', 'clip_02': 'missing'}}
    monkeypatch.setattr(readiness, 'next_action', lambda *_: route)
    first = readiness.automatic_request(ctx, {'adopted': True, 'job': None})
    assert first is not None
    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action': {
        **first['route'], 'automatic_fingerprint': first.get('fingerprint')}})

    clips[0]['first_frame'] = {'path': 'accepted-clip-01.png'}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    route = {**route, 'blocked': {'clip_02': 'missing'}}
    status = {'adopted': True, 'job': {'action': 'first_frames', 'status': 'completed',
        'result': {'ok': False, 'paused': True, 'changed': ['clip_01'], 'pending_clips': ['clip_02']}}}
    second = readiness.automatic_request(ctx, status)
    assert second is not None
    assert second['document']['frames'] == [{'clip_id': 'clip_02', 'extra_references': []}]
    assert len(first['fingerprint']) == 64 and second['fingerprint'] != first['fingerprint']

    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action': {
        **second['route'], 'automatic_fingerprint': second['fingerprint']}})
    failed = {'adopted': True, 'job': {
        'action': 'first_frames', 'status': 'failed', 'error': 'unchanged failure'}}
    assert readiness.automatic_request(ctx, failed) is None

    clips[1]['shots'][0]['start_state'] = 'corrected source input'
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    assert readiness.automatic_request(ctx, failed) is not None


def test_paused_partial_first_frames_resume_remaining_without_agent(ctx, config, monkeypatch):
    import time

    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    clips = [
        {'clip_id': 'clip_01', 'kind': 'video', 'shots': [{'start_state': 'one'}]},
        {'clip_id': 'clip_02', 'kind': 'video', 'shots': [{'start_state': 'two'}]},
    ]
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        'action': 'first_frames', 'blocked': {'clip_01': 'missing', 'clip_02': 'missing'}})
    initial = readiness.automatic_request(ctx, {'adopted': True, 'job': None})
    clips[0]['first_frame'] = {'path': 'accepted-clip-01.png'}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'paused', 'deadline': time.time() + 30,
        'updated_at': time.strftime('%F %T'),
        'next_action': {**initial['route'], 'automatic_fingerprint': initial.get('fingerprint')},
        'job': {'id': 'job-5', 'action': 'first_frames', 'status': 'completed',
                'result': {'ok': False, 'paused': True, 'changed': ['clip_01'],
                           'pending_clips': ['clip_02']}},
    })

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            elif action == 'first_frames':
                result.update(changed=['clip_02'], pending_clips=[], paused=False)
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(turns, 'run_session',
                        lambda *args, **kwargs: pytest.fail('paused partial work must resume on the host'))

    def route(_, status=None):
        job = status.get('job') or {}
        outcome = job.get('result') or {}
        if job.get('action') == 'first_frames' and outcome.get('changed') != ['clip_02']:
            return {'action': 'first_frames', 'blocked': {'clip_02': 'missing'}}
        return {'action': 'finish'}

    monkeypatch.setattr(readiness, 'next_action', route)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True
    assert [action for action in calls if action != 'status'] == ['first_frames', 'finish']
    assert read_json(ctx.work_dir/'output/first-frames.json') == {
        'render_ready': False,
        'frames': [{'clip_id': 'clip_02', 'extra_references': []}],
    }


@pytest.mark.parametrize('with_session', [False, True])
def test_bounded_recovery_finish_rejects_stale_manifest(ctx, config, monkeypatch, with_session):
    import time

    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
    author = {'session_id': 'old-session'} if with_session else {}
    manifest_path = ctx.work_dir/'input/latest-finish.json'
    atomic_write_json(manifest_path, {
        'passed': True, 'technical_passed': True, 'author_session': author})
    before = manifest_path.read_bytes()
    if with_session:
        atomic_write_json(ctx.work_dir/'episode-session.json', author)
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'running', 'deadline': time.time() + 30,
        'upstream_recovery': {'proposals': 2},
        'job': {'id': 'job-4', 'action': 'apply', 'status': 'completed', 'result': {'ok': True}},
    })

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'finish':
                calls.append(action)
                return {'ok': True, 'action': action, 'passed': True}
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    monkeypatch.setattr(turns, 'run_session',
                        lambda *args, **kwargs: pytest.fail('bounded recovery must settle without an Agent turn'))
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['status'] == 'blocked' and result['passed'] is False
    assert result['reason'] == 'bounded recovery completed two proposal turns'
    assert result['bounded_finish']['passed'] is True
    assert result['job']['action'] == 'finish' and result['job']['status'] == 'completed'
    assert calls.count('finish') == 1 and manifest_path.read_bytes() == before


def test_bounded_recovery_finish_accepts_fresh_host_manifest(ctx, config, monkeypatch):
    import time

    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'running', 'deadline': time.time() + 30,
        'upstream_recovery': {'proposals': 2},
        'job': {'id': 'job-4', 'action': 'apply', 'status': 'completed', 'result': {'ok': True}},
    })

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args: (None, None))
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'agent'})
    assert result['passed'] is True and result['status'] == 'finished'
    assert result['job']['id'] == 'job-5' and result['job']['action'] == 'finish'
    assert calls.count('finish') == 1


@pytest.mark.parametrize('action', ['render', 'review', 'finish'])
def test_automatic_fingerprint_ignores_output_timing_but_tracks_execution_inputs(
        tmp_path, monkeypatch, action):
    ctx = SimpleNamespace(episode_dir=tmp_path/'episode', run_dir=tmp_path/'run')
    video = tmp_path/'take-01.mp4'
    video.write_bytes(b'current take')
    clips = [{'clip_id': 'clip_01', 'kind': 'video', 'prompt': 'current request',
              'prompt_h3': 'current English request', 'shots': [{'start_state': 'current source'}]}]
    plan = {'clips': clips}
    media = {'clip_plan_fingerprint': 'rendered-plan-v1', 'prompt_h3_fingerprint': 'rendered-h3-v1',
             'clips': [{'clip_id': 'clip_01', 'selected': {'video': str(video)}, 'review_s': 1}],
             'timing': {'render': 1}, 'elapsed': 1,
             'assembly': {'final_video': str(tmp_path/'movie.mp4'), 'needs_subtitle_refresh': False},
             'quality_review': {'passed': True}}
    review = {'policy': 'current-review-policy', 'review_mode': 'classic',
              'clips': {'clip_01': {'video': str(video), 'take': [1, 2, 3], 'severity': 'pass'}},
              'feedback': {}, 'elapsed': 1, 'timing': {'review': 1}}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', media)
    atomic_write_json(ctx.episode_dir/'episode_review.json', review)
    route = {'action': action, 'reason': 'current deterministic work'}
    monkeypatch.setattr(readiness, 'next_action', lambda *_: route)
    initial_status = {'adopted': True, 'unreviewed_clips': [],
                      'job': {'action': 'apply', 'status': 'completed', 'result': {'ok': True}}}
    first = readiness.automatic_request(ctx, initial_status)
    assert first is not None
    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action': {
        **first['route'], 'automatic_fingerprint': first['fingerprint']}})
    terminal = {**initial_status, 'job': {'action': action, 'status': 'completed',
                                         'result': {'ok': True, 'passed': action == 'finish'}}}

    media['timing']['render'] = 99
    media['review_s'] = 88
    media['elapsed'] = 77
    media['bookkeeping'] = {'job_receipt': 'new output only'}
    media['clips'][0]['review_s'] = 66
    review['elapsed'] = 55
    review['timing']['review'] = 44
    review['history'] = ['new output receipt']
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', media)
    atomic_write_json(ctx.episode_dir/'episode_review.json', review)
    assert readiness.automatic_request(ctx, terminal) is None

    if action == 'render':
        clips[0]['prompt'] = 'corrected executable request'
        atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    elif action == 'review':
        replacement = tmp_path/'take-02.mp4'
        replacement.write_bytes(b'new current take')
        media['clips'][0]['selected']['video'] = str(replacement)
        atomic_write_json(ctx.episode_dir/'thin_media_report.json', media)
    else:
        media['assembly']['needs_subtitle_refresh'] = True
        atomic_write_json(ctx.episode_dir/'thin_media_report.json', media)
    assert readiness.automatic_request(ctx, terminal) is not None


def _finish_fingerprint_case(tmp_path, monkeypatch, *, with_audio=False, with_independent=False):
    from novel_manga.application.review import final_audio, final_cut, independent

    ctx = SimpleNamespace(episode_dir=tmp_path/'episode', run_dir=tmp_path/'run')
    native = tmp_path/'native.mp4'
    movie = tmp_path/'movie.mp4'
    native.write_bytes(b'native take')
    movie.write_bytes(b'final movie')
    plan = {'clips': [{'clip_id': 'clip_01', 'kind': 'video', 'shot_indexes': [1],
                       'prompt': 'current request', 'prompt_h3': 'current English request'}]}
    script = {'music_plan': {'cues': []},
              'shots': [{'index': 1, 'start_state': 'current source', 'turns': []}]}
    media = {
        'clips': [{'clip_id': 'clip_01', 'selected': {'video': str(native), 'chunks': []}}],
        'clip_plan_fingerprint': 'selected-plan', 'prompt_h3_fingerprint': 'selected-h3',
        'assembly': {'final_video': str(movie), 'needs_subtitle_refresh': False,
                     'timeline': [{'unit_id': 'clip_01', 'role': 'dialogue', 'start': 0, 'end': 9}]},
        'quality_review': {'passed': True},
        'final_audio_review_required': with_audio,
    }
    review = {'policy': 'native-policy', 'review_mode': 'classic', 'clips': {}, 'feedback': {}}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    atomic_write_json(ctx.episode_dir/'chapter_script.json', script)
    atomic_write_json(ctx.episode_dir/'timeline.json', {
        'timeline': [{'unit_id': 'clip_01', 'role': 'dialogue', 'start': 0, 'end': 9}]})
    atomic_write_json(ctx.episode_dir/'music_policy.json', {'intent': 'quiet'})
    atomic_write_json(ctx.episode_dir/'review_feedback.json', {})
    atomic_write_json(tmp_path/'story_bible.json', {})
    atomic_write_json(tmp_path/'layout.json', {})
    final = {'movie': str(movie), 'policy': final_cut.POLICY, 'complete': True,
             'passed': True, 'issues': [], 'errors': [],
             'context': final_cut.context(ctx.episode_dir, media, movie)}
    if with_independent:
        row = {'movie': str(movie), 'policy': independent.POLICY, 'context': final['context'],
               'complete': True, 'passed': True, 'issues': [], 'errors': [], 'frames': []}
        final['independent_review'] = row
        atomic_write_json(ctx.episode_dir/'independent_review/registration.json', {'policy': independent.POLICY})
        atomic_write_json(ctx.episode_dir/'independent_review.json', row)
    media['final_cut_review'] = final
    model_dir = None
    if with_audio:
        model_dir = tmp_path/'audio-model'
        model_dir.mkdir()
        (model_dir/'weights.bin').write_bytes(b'weights-v1')
        source_identity, _ = final_audio._source_facts(ctx.episode_dir)
        identity = {'movie': final_audio._file_state(movie),
                    'model': final_audio._model_state(model_dir),
                    'audio_source_root': str(tmp_path/'audio-source')}
        media['final_audio_review'] = {
            'movie': str(movie), 'complete': True, 'passed': True, 'issues': [], 'errors': [],
            'policy': final_audio.POLICY, 'identity': identity, 'source_identity': source_identity,
        }
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', media)
    atomic_write_json(ctx.episode_dir/'episode_review.json', review)
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        'action': 'finish', 'reason': 'current acceptance work'})
    initial = {'adopted': True, 'unreviewed_clips': [], 'publication_blockers': [],
               'job': {'action': 'apply', 'status': 'completed', 'result': {'ok': True}}}
    first = readiness.automatic_request(ctx, initial)
    assert first is not None
    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action': {
        **first['route'], 'automatic_fingerprint': first['fingerprint']}})
    terminal = {**initial, 'job': {'action': 'finish', 'status': 'completed',
                                  'result': {'ok': True, 'passed': True}}}
    return SimpleNamespace(ctx=ctx, media=media, review=review, final=final,
                           terminal=terminal, movie=movie, model_dir=model_dir)


def test_finish_fingerprint_tracks_current_final_cut_context(tmp_path, monkeypatch):
    from novel_manga.application.review import final_cut

    case = _finish_fingerprint_case(tmp_path, monkeypatch)
    assert final_cut.current(case.ctx.episode_dir, case.media, case.final) is True
    case.media['timing'] = {'finish': 99}
    case.media['review_s'] = 88
    atomic_write_json(case.ctx.episode_dir/'thin_media_report.json', case.media)
    assert readiness.automatic_request(case.ctx, case.terminal) is None

    script = read_json(case.ctx.episode_dir/'chapter_script.json')
    script['shots'][0]['start_state'] = 'changed formal source evidence'
    atomic_write_json(case.ctx.episode_dir/'chapter_script.json', script)
    assert final_cut.current(case.ctx.episode_dir, case.media, case.final) is False
    assert readiness.automatic_request(case.ctx, case.terminal) is not None


@pytest.mark.parametrize('change', ['verdict', 'context', 'saved_policy', 'embedded_policy'])
def test_finish_fingerprint_tracks_independent_verdict_and_context(tmp_path, monkeypatch, change):
    from novel_manga.application.review import final_cut, independent

    case = _finish_fingerprint_case(tmp_path, monkeypatch, with_independent=True)
    assert final_cut.current(case.ctx.episode_dir, case.media, case.final) is True
    saved = read_json(case.ctx.episode_dir/'independent_review.json')
    saved['elapsed'] = 99
    atomic_write_json(case.ctx.episode_dir/'independent_review.json', saved)
    case.media['timing'] = {'finish': 99}
    atomic_write_json(case.ctx.episode_dir/'thin_media_report.json', case.media)
    assert readiness.automatic_request(case.ctx, case.terminal) is None

    if change == 'verdict':
        saved['passed'] = False
    elif change == 'context':
        saved['context'] = {**saved['context'], 'script': {'changed': 'independent evidence'}}
    elif change == 'saved_policy':
        saved['policy'] = 'stale-independent-policy'
    else:
        case.final['independent_review']['policy'] = 'stale-independent-policy'
        atomic_write_json(case.ctx.episode_dir/'thin_media_report.json', case.media)
    atomic_write_json(case.ctx.episode_dir/'independent_review.json', saved)
    assert independent.current(case.ctx.episode_dir, case.final) is False
    assert final_cut.current(case.ctx.episode_dir, case.media, case.final) is False
    assert readiness.automatic_request(case.ctx, case.terminal) is not None


@pytest.mark.parametrize('change', ['source', 'model', 'report_identity'])
def test_finish_fingerprint_tracks_audio_freshness_identity(tmp_path, monkeypatch, change):
    from novel_manga.application.review import final_audio

    case = _finish_fingerprint_case(tmp_path, monkeypatch, with_audio=True)
    audio = case.media['final_audio_review']
    audio['elapsed'] = 99
    case.media['timing'] = {'finish': 99}
    atomic_write_json(case.ctx.episode_dir/'thin_media_report.json', case.media)
    assert readiness.automatic_request(case.ctx, case.terminal) is None

    if change == 'source':
        script = read_json(case.ctx.episode_dir/'chapter_script.json')
        script['music_plan']['native_bgm'] = 'changed audio intent'
        atomic_write_json(case.ctx.episode_dir/'chapter_script.json', script)
        current, _ = final_audio._source_facts(case.ctx.episode_dir)
        assert current != audio['source_identity']
    elif change == 'model':
        (case.model_dir/'weights.bin').write_bytes(b'weights-v2-longer')
        assert final_audio._model_state(case.model_dir) != audio['identity']['model']
    else:
        audio['identity']['model']['files'] = []
        atomic_write_json(case.ctx.episode_dir/'thin_media_report.json', case.media)
    assert readiness.automatic_request(case.ctx, case.terminal) is not None


def test_registration_only_first_frame_failure_never_creates_agent_session(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips':[{'clip_id':'clip_01', 'kind':'video'}]})
    original = flow.EpisodeTools
    first_calls = []

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'first_frames':
                first_calls.append(action)
                return {'ok':False, 'error':'first_frame_registration', 'retryable':False,
                        'invalid_fields':{'clip_01':['image_references']},
                        'argument_errors':{'clip_01':['restore the registered reference']},
                        'clips':{'clip_01':{'repair_origin':'registration'}}}
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    route = {'action':'first_frames', 'reason':'registered reference evidence is unavailable',
             'error':'first_frame_registration', 'retryable':False,
             'blocked':{'clip_01':'first frame missing'},
             'invalid_fields':{'clip_01':['image_references']},
             'argument_errors':{'clip_01':['restore the registered reference']},
             'repair_origin':{'clip_01':'registration'}}
    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args:(None, None))
    monkeypatch.setattr(readiness, 'next_action', lambda *_:route)
    sessions = []
    def session(work, prompt, **options):
        sessions.append(options)
        directory = ctx.work_dir/'attempt-registration'
        directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(ok=True, exit_code=0, directory=directory,
                               record={'session_id':'unnecessary'}, produced=[])
    monkeypatch.setattr(turns, 'run_session', session)

    result = flow.run_episode(ctx, config={**config, 'production_mode':'agent',
                                           'agent_invocations_per_slice':1})
    assert result['status'] == 'blocked'
    assert result['retryable'] is False
    assert result['error'] == 'first_frame_registration'
    assert result['invalid_fields'] == {'clip_01':['image_references']}
    assert result['repair_origin'] == {'clip_01':'registration'}
    assert first_calls == []  # The route already identifies the registration failure before dispatch.
    assert sessions == []
    assert not (ctx.work_dir/'episode-session.json').exists()


def test_transient_finish_failure_recovers_only_on_a_cold_retry(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips':[{'clip_id':'clip_01', 'kind':'video'}]})
    original = flow.EpisodeTools
    failed = [True]
    finish_calls = []

    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'finish':
                finish_calls.append('finish')
            if action == 'finish' and failed[0]:
                failed[0] = False
                return {'ok':False, 'action':'finish', 'passed':False,
                        'error':'final review service unavailable', 'retryable':True,
                        'service_interrupted':{'http_status':503, 'detail':'service unavailable'}}
            result = super().dispatch(action, payload)
            if action == 'status':
                result['adopted'] = True
            return result

    def route(_, status=None):
        outcome = (status.get('job') or {}).get('result') or {}
        return {'action':'finish',
                **{key:outcome[key] for key in ('service_interrupted', 'retryable') if key in outcome}}

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(flow, 'prepare_operations', lambda *args:(None, None))
    monkeypatch.setattr(readiness, 'next_action', route)
    monkeypatch.setattr(turns, 'run_session',
                        lambda *args, **kwargs:pytest.fail('service recovery must not create an Agent session'))
    options = {**config, 'production_mode':'agent'}

    first = flow.run_episode(ctx, config=options)
    assert first['status'] == 'blocked' and first['retryable'] is True
    assert len(finish_calls) == 1
    second = flow.run_episode(ctx, config=options, retry_issues=True)
    assert second['status'] == 'finished' and second['passed'] is True
    assert len(finish_calls) == 2


@pytest.mark.parametrize(('field','value'), [
    ('invalid_fields', ['image_references']),
    ('argument_errors', ['restore the registered reference']),
    ('repair_origin', 'registration'),
    ('repair_grant', {'operation':'input', 'shot_fields':['opening_frame']}),
    ('repair_grant', {'operation':'resume', 'evidence':{'origin':'service'}}),
])
def test_first_frame_fingerprint_tracks_current_preflight(tmp_path, monkeypatch, field, value):
    ctx = SimpleNamespace(episode_dir=tmp_path/'episode', run_dir=tmp_path/'run')
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {
        'clips':[{'clip_id':'clip_01', 'kind':'video'}]})
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        'action':'first_frames', 'blocked':{'clip_01':'missing'}})
    frames = {'clips':{'clip_01':{}}}
    initial = {'adopted':True, 'first_frames':frames,
               'job':{'action':'apply', 'status':'completed', 'result':{'ok':True}}}
    first = readiness.automatic_request(ctx, initial)
    assert first is not None
    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action':{
        **first['route'], 'automatic_fingerprint':first['fingerprint']}})
    terminal = {**initial, 'job':{'action':'first_frames', 'status':'completed',
                                  'result':{'ok':False, 'error':'preflight failed'}}}
    assert readiness.automatic_request(ctx, terminal) is None
    frames['clips']['clip_01'][field] = value
    plan = read_json(ctx.episode_dir/'clip_plan.json')
    assert readiness._automatic_fingerprint(ctx, terminal, first, plan) != first['fingerprint']
    if field in {'invalid_fields','repair_origin'} or (field=='repair_grant' and value['operation']=='input'):
        # A changed fingerprint is not permission to submit with an invalid
        # reference or to bypass an author input grant.
        assert readiness.automatic_request(ctx, terminal) is None
    else:
        assert readiness.automatic_request(ctx, terminal) is not None


def test_first_frame_fingerprint_tracks_reference_file_restoration(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import first_frames
    from support.first_frame_cases import case

    ctx, clips = case(tmp_path, monkeypatch)
    clip = clips[0]
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips':[clip]})
    source = ctx.novel_dir/'scene.png'
    original = source.read_bytes()
    source.unlink()
    frames = first_frames.status(ctx, {'clips':[clip]})
    monkeypatch.setattr(readiness, 'next_action', lambda *_: {
        'action':'first_frames', 'blocked':{'clip_01':'missing'}})
    initial = {'adopted':True, 'first_frames':frames,
               'job':{'action':'apply', 'status':'completed', 'result':{'ok':True}}}
    assert readiness.automatic_request(ctx, initial) is None

    source.write_bytes(original)
    restored = {**initial, 'first_frames':first_frames.status(ctx, {'clips':[clip]})}
    first = readiness.automatic_request(ctx, restored)
    assert first is not None
    atomic_write_json(ctx.run_dir/'agent-run.json', {'next_action':{
        **first['route'], 'automatic_fingerprint':first['fingerprint']}})
    terminal = {**restored, 'job':{'action':'first_frames', 'status':'completed',
                                   'result':{'ok':False, 'error':'previous request failed'}}}
    assert readiness.automatic_request(ctx, terminal) is None
    from PIL import Image
    Image.new('RGB',(8,16),'red').save(source)
    changed = {**terminal, 'first_frames':first_frames.status(ctx, {'clips':[clip]})}
    second = readiness.automatic_request(ctx, changed)
    assert second is not None and second['fingerprint'] != first['fingerprint']
