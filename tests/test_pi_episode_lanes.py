import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))


SPEC = importlib.util.spec_from_file_location(
    'episode_lanes', Path(__file__).parents[1] / 'scripts/pi_episode_lanes.py')
lanes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lanes)


def state(root, jobs):
    (root / 'state.json').write_text(json.dumps({'pid': 1, 'jobs': jobs,
                                               'lanes': {k: {} for k in lanes.CAPACITY}}))
    return lanes.Controller(root)


def test_review_concurrency_controls_the_pool_workers_and_reported_capacity(tmp_path, monkeypatch):
    leases = tmp_path / 'leases'
    leases.mkdir()
    controller = state(tmp_path, [])
    controller.review_workers = 4
    original_path = lanes.Path
    monkeypatch.setattr(lanes, 'Path', lambda p: leases if str(p).endswith('172.28.4.52_30022') else original_path(p))
    class Reviews:
        def __init__(self, c): pass
        def seed(self): pass
        def recheck_protocol_reports(self): pass
        def worker(self): pass
    monkeypatch.setattr(lanes, 'Reviews', Reviews)
    observed = []
    class Future:
        def result(self): pass
    class Pool:
        def __init__(self, max_workers):
            assert max_workers == sum(lanes.CAPACITY.values()) + 4
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, function, *args):
            observed.append((function, args))
            return Future()
    monkeypatch.setattr(lanes.concurrent.futures, 'ThreadPoolExecutor', Pool)
    controller.run()
    assert sum(isinstance(fn.__self__, Reviews) for fn, _ in observed) == 4
    assert len(observed) == sum(lanes.CAPACITY.values()) + 4
    assert controller.state['review_queue']['capacity'] == 4


def test_review_concurrency_can_be_selected_without_changing_production_slots(tmp_path):
    (tmp_path / 'state.json').write_text('{"pid":1,"jobs":[],"lanes":{}}')
    controller = lanes.Controller(tmp_path, review_workers=4)
    assert controller.review_workers == 4
    assert lanes.CAPACITY == {'cloud-h3': 2, 'gpu52-fast': 3, 'gpu16-h3': 1}
    with pytest.raises(ValueError, match='positive integer'):
        lanes.Controller(tmp_path, review_workers=0)


def test_mainline_dispatch_uses_merged_starter_without_batch_owned_snapshots(tmp_path):
    import pi_episode_review as reviewing
    controller = state(tmp_path, [])
    sdk = Path(__file__).resolve().parents[1]
    assert controller.sdk_root == sdk
    assert controller.entry == sdk / 'scripts/pi_video_agent.py'
    assert controller.grant_entry == controller.entry
    assert reviewing.Reviews(controller).entry == controller.entry
    assert controller.runtime == sdk / 'examples/pi-video-agent/template/tools'


def test_historical_dispatch_keeps_its_frozen_starter_and_tools(tmp_path):
    import pi_episode_review as reviewing
    for folder in ['frozen-main', 'frozen-tools-v2', 'reviewer-code-snapshot-v2', 'reviewer-code-snapshot-v3']:
        target = tmp_path / folder / 'scripts'
        target.mkdir(parents=True)
        (target / 'pi_video_agent.py').write_text('historical pinned entry')
    controller = state(tmp_path, [])
    assert controller.sdk_root == tmp_path / 'frozen-main'
    assert controller.entry == tmp_path / 'frozen-main/scripts/pi_video_agent.py'
    assert controller.grant_entry == tmp_path / 'reviewer-code-snapshot-v2/scripts/pi_video_agent.py'
    assert reviewing.Reviews(controller).entry == tmp_path / 'reviewer-code-snapshot-v3/scripts/pi_video_agent.py'
    assert controller.runtime == tmp_path / 'frozen-tools-v2'


def test_original_runner_survives_parent_and_is_adopted_without_another_run(tmp_path):
    work = tmp_path / 'episode'
    (work / 'output').mkdir(parents=True)
    budget = work / 'run-budget.json'
    budget.write_text('{"provider_requests":17,"deadline_at":987654321}')
    receipt = work / 'original-task-receipt.json'
    receipt.write_text('{"task_id":"already-submitted"}')
    child_code = '''
import json,time,sys
from pathlib import Path
w=Path(sys.argv[1]);time.sleep(0.7)
(w/'output/film.mp4').write_bytes(b'fixture')
(w/'output/delivery.json').write_text(json.dumps({'movie':str(w/'output/film.mp4')}))
(w/'agent-run.json').write_text(json.dumps({'ended_at':1,'status':'session_ended','error':None}))
'''
    parent_code = '''
import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])
Path(sys.argv[3]).write_text(str(p.pid));time.sleep(20)
'''
    marker = tmp_path / 'child.pid'
    parent = subprocess.Popen([sys.executable, '-c', parent_code, child_code, str(work), str(marker)])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child_pid = int(marker.read_text())
        parent.terminate()
        parent.wait(timeout=5)
        controller = state(tmp_path, [{'id': 'original', 'lane': 'cloud-h3', 'state': 'running',
                                       'pid': child_pid, 'workspace': str(work)}])
        controller.dispatch = lambda job: pytest.fail('Original runner must not be launched again')
        controller.worker('cloud-h3')
        job = controller.state['jobs'][0]
        assert job['state'] == 'complete_movie_present'
        assert job['returncode'] is None
        assert job['completion_evidence'] == 'original_runner_record'
        assert budget.read_text() == '{"provider_requests":17,"deadline_at":987654321}'
        assert receipt.read_text() == '{"task_id":"already-submitted"}'
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_exhausted_original_runner_pauses_only_its_lane_without_reset(tmp_path):
    work = tmp_path / 'failed'
    work.mkdir()
    (work / 'agent-run.json').write_text(json.dumps({
        'error': 'RUN_BUDGET_EXHAUSTED', 'status': 'stopped', 'ended_at': 1}))
    jobs = [
        {'id': 'failed', 'lane': 'gpu16-h3', 'state': 'running', 'pid': 99999999,
         'workspace': str(work)},
        {'id': 'waiting', 'lane': 'gpu16-h3', 'state': 'prepared'},
        {'id': 'unaffected', 'lane': 'cloud-h3', 'state': 'prepared'},
    ]
    controller = state(tmp_path, jobs)
    controller.worker('gpu16-h3')
    assert jobs[0]['state'] == 'running'  # The input object is not the controller's state.
    assert controller.state['jobs'][0]['state'] == 'needs_attention'
    assert controller.state['jobs'][1]['state'] == 'prepared'
    assert controller.claim('gpu16-h3') is None
    assert controller.claim('cloud-h3')[0]['id'] == 'unaffected'
    assert json.loads((work / 'agent-run.json').read_text())['error'] == 'RUN_BUDGET_EXHAUSTED'


def test_started_workspace_is_never_patched(tmp_path):
    work = tmp_path / 'work'
    (work / 'tools').mkdir(parents=True)
    (work / 'tools/runtime.mjs').write_text('original runtime')
    (work / 'run-budget.json').write_text('{"provider_requests":3}')
    with pytest.raises(RuntimeError, match='Already started'):
        lanes.prepare_pending(work, tmp_path / 'new-runtime')
    assert (work / 'tools/runtime.mjs').read_text() == 'original runtime'
    assert (work / 'run-budget.json').read_text() == '{"provider_requests":3}'


def test_pending_patch_preserves_authorized_task_route_and_quotas(tmp_path):
    work, source = tmp_path / 'work', tmp_path / 'runtime'
    (work / 'tools').mkdir(parents=True)
    source.mkdir()
    for name in ['runtime.mjs', 'bash_limits.mjs']:
        (source / name).write_text('selected frozen source ' + name)
    (work / 'TASK.md').write_text('The existing authorized content and limits.')
    (work / 'SYSTEM.md').write_text('Original system')
    (work / 'CAPABILITIES.md').write_text('Original exact paths')
    config = {'wall_seconds': 14400, 'max_model_requests': 256,
              'sdk_root': '/original/frozen-sdk', 'media_lane': 'MiniMax-H3',
              'first_frame_policy': 'none', 'viewpoint': 'mixed'}
    (work / 'local.json').write_text(json.dumps(config))
    lanes.prepare_pending(work, source)
    result = json.loads((work / 'local.json').read_text())
    assert {key: result[key] for key in config} == config
    assert result['bash_timeout_seconds'] == 900
    assert (work / 'TASK.md').read_text() == 'The existing authorized content and limits.'
    assert not (work / 'run-budget.json').exists()
    assert 'SoulTuner' in (work / 'CAPABILITIES.md').read_text()


def review_fixture(tmp_path, verdict='accepted', findings=None):
    import pi_episode_review as reviewing
    work = tmp_path / 'work'
    (work / 'output').mkdir(parents=True)
    (tmp_path / 'selected-package/episode').mkdir(parents=True)
    (tmp_path / 'selected-package/episode/02-script.json').write_text('{"original":true}')
    film = work / 'output/film.mp4'
    film.write_bytes(b'original reviewed movie')
    directory = tmp_path / 'review'
    (directory / 'output').mkdir(parents=True)
    digest = hashlib.sha256(film.read_bytes()).hexdigest()
    result = {'film': str(film), 'sha256': digest, 'verdict': verdict,
              'findings': findings or [], 'coverage': {'video': 'fixture frames', 'audio': 'fixture audio'}, 'limits': []}
    (directory / 'output/review.json').write_text(json.dumps(result))
    record = {'directory': str(directory), 'movie': str(film), 'sha256': digest,
              'status': 'running'}
    job = {'id': 'film', 'workspace': str(work), 'movie': str(film),
           'lane': 'gpu52-fast', 'state': 'complete_movie_present',
           'independent_review': 'running', 'review': record}
    return reviewing, work, job, record


def test_review_acceptance_is_bound_to_actual_movie_and_no_blocking(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    assert reviewing.load_review(record)['verdict'] == 'accepted'
    Path(job['movie']).write_bytes(b'different export after review')
    with pytest.raises(reviewing.ReviewProtocolError, match='version'):
        reviewing.load_review(record)


def test_accepted_report_with_blocking_is_protocol_error_not_a_pass(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, findings=[{
        'severity': 'blocking', 'owner': 'episode', 'seconds': [1, 2], 'what': 'wrong person'}])
    with pytest.raises(reviewing.ReviewProtocolError, match='contradict'):
        reviewing.load_review(record)


def test_advisory_only_never_queues_director_repair(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, findings=[{
        'severity': 'advisory', 'owner': 'episode', 'seconds': [1, 2], 'what': 'minor clothing detail'}])
    c = state(tmp_path, [job])
    manager = reviewing.Reviews(c)
    manager.settle(c.state['jobs'][0], record)
    assert c.state['jobs'][0]['independent_review'] == 'accepted'
    assert c.state['jobs'][0]['state'] == 'complete_movie_present'
    assert not c.state['jobs'][0].get('repair_feedback')


def test_blocking_review_returns_to_original_workspace_with_original_limits(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='rejected', findings=[{
        'severity': 'blocking', 'owner': 'episode', 'seconds': [1, 2], 'what': 'cause is unreadable'}])
    original = {'deadline_at': (time.time() + 1000) * 1000,
                'provider_requests': 17, 'request_limit': 256, 'started_at': 1}
    budget = work / 'run-budget.json'
    budget.write_text(json.dumps(original))
    (work / 'session-path.txt').write_text('original-session.jsonl')
    c = state(tmp_path, [job])
    reviewing.Reviews(c).settle(c.state['jobs'][0], record)
    assert c.state['jobs'][0]['state'] == 'repair_queued'
    assert json.loads(budget.read_text()) == original
    assert Path(c.state['jobs'][0]['repair_feedback']).is_file()


def test_expired_original_limits_do_not_get_renewed_for_repair(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='rejected', findings=[{
        'severity': 'blocking', 'owner': 'episode', 'what': 'unreadable cause', 'seconds': [1, 2]}])
    original = '{"deadline_at":1,"provider_requests":17,"request_limit":256}'
    (work / 'run-budget.json').write_text(original)
    (work / 'session-path.txt').write_text('original-session.jsonl')
    c = state(tmp_path, [job])
    reviewing.Reviews(c).settle(c.state['jobs'][0], record)
    assert c.state['jobs'][0]['repair_status'] == 'blocked_original_limits'
    assert c.state['jobs'][0]['independent_review'] == 'rejected'
    assert (work / 'run-budget.json').read_text() == original
    assert not (work / 'window-grant.json').exists()


def test_incomplete_review_never_becomes_content_failure_or_repair(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='incomplete')
    c = state(tmp_path, [job])
    reviewing.Reviews(c).settle(c.state['jobs'][0], record)
    assert c.state['jobs'][0]['independent_review'] == 'incomplete'
    assert c.state['jobs'][0]['state'] == 'complete_movie_present'
    assert not c.state['jobs'][0].get('repair_feedback')


def test_original_director_resume_preserves_history_and_exports_new_version(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='rejected', findings=[{
        'severity': 'blocking', 'owner': 'episode', 'what': 'unreadable event', 'seconds': [1, 2]}])
    original = {'deadline_at': (time.time() + 1000) * 1000, 'provider_requests': 17,
                'request_limit': 256, 'started_at': 123}
    (work / 'run-budget.json').write_text(json.dumps(original))
    (work / 'session-path.txt').write_text('original-session.jsonl')
    (work / 'agent-run.json').write_text('{"status":"session_ended","ended_at":1}')
    c = state(tmp_path, [job])
    reviewing.Reviews(c).settle(c.state['jobs'][0], record)
    entry = tmp_path / 'fake-entry.py'
    entry.write_text('''import sys,json
from pathlib import Path
w=Path(sys.argv[2]);assert '--resume' in sys.argv and '--prompt' in sys.argv
(w/'argv.json').write_text(json.dumps(sys.argv))
b=json.loads((w/'run-budget.json').read_text());b['provider_requests']+=1
(w/'run-budget.json').write_text(json.dumps(b))
f=w/'output/repaired.mp4';f.write_bytes(b'new fixed export')
(w/'output/delivery.json').write_text(json.dumps({'movie':str(f)}))
(w/'agent-run.json').write_text(json.dumps({'status':'session_ended','ended_at':2,'error':None}))
''')
    c.entry, c.python = entry, Path(sys.executable)
    actual = c.state['jobs'][0]
    assert c.dispatch(actual) == 0
    c.finish(actual, 0, False)
    changed = json.loads((work / 'run-budget.json').read_text())
    assert changed['provider_requests'] == 18
    assert {k: changed[k] for k in ['deadline_at', 'started_at', 'request_limit']} == {
        k: original[k] for k in ['deadline_at', 'started_at', 'request_limit']}
    assert (work / 'session-path.txt').read_text() == 'original-session.jsonl'
    assert actual['independent_review'] == 'pending'
    assert Path(actual['movie']).read_bytes() == b'new fixed export'
    assert (Path(record['directory']) / 'producer-export-before-repair.mp4').read_bytes() == b'original reviewed movie'
    assert (work / 'output/film.mp4').read_bytes() == b'original reviewed movie'


def test_existing_reviewer_is_adopted_and_idle_producer_does_not_hang(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    reviewing, work, job, record = review_fixture(tmp_path)
    record.update(id='film', pid=99999999)
    (tmp_path / 'review-seeds.json').write_text(json.dumps([record]))
    c = state(tmp_path, [job])
    c.reviews = reviewing.Reviews(c)
    c.reviews.seed()
    c.reviews.start = lambda job: pytest.fail('Never launch a duplicate existing reviewer')
    c.dispatch = lambda job: pytest.fail('Advisory acceptance must not launch a producer')
    with ThreadPoolExecutor(max_workers=2) as pool:
        producer = pool.submit(c.worker, 'gpu52-fast')
        reviewer = pool.submit(c.reviews.worker)
        reviewer.result(timeout=5)
        producer.result(timeout=5)
    assert c.state['jobs'][0]['independent_review'] == 'accepted'


def test_same_rejected_bytes_are_not_sent_to_another_paid_review(tmp_path, monkeypatch):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='rejected', findings=[{
        'severity': 'blocking', 'owner': 'episode', 'what': 'unreadable event', 'seconds': [1, 2]}])
    record['status'] = 'rejected'
    job['review_history'] = [dict(record)]
    (work / 'audio-runtime.json').write_text('{"device":"cpu"}')
    c = state(tmp_path, [job])
    manager = reviewing.Reviews(c)
    def initialized(command, **kwargs):
        directory = Path(command[command.index('init') + 1])
        directory.mkdir()
        (directory / 'TASK.md').write_text('SHA256：`' + record['sha256'] + '`')
        (directory / 'local.json').write_text('{}')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(reviewing.subprocess, 'run', initialized)
    monkeypatch.setattr(reviewing.subprocess, 'Popen', lambda *a, **k: pytest.fail('Same bytes must not pay for review again'))
    assert manager.start(c.state['jobs'][0]) is None
    assert c.state['jobs'][0]['repair_status'] == 'unchanged_export'


def test_missing_review_pauses_reviews_without_rejecting_content(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    (Path(record['directory']) / 'output/review.json').unlink()
    record['pid'] = 99999999
    c = state(tmp_path, [job])
    c.reviews = reviewing.Reviews(c)
    c.reviews.worker()
    assert c.reviews.paused
    assert c.state['jobs'][0]['independent_review'] == 'protocol_error'
    assert not c.paused  # A reviewer failure must not stop healthy production lanes.
    assert c.state['jobs'][0]['state'] == 'complete_movie_present'


def test_delivered_movie_is_reviewed_even_when_director_hits_its_original_limit(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    (work / 'output/delivery.json').write_text(json.dumps({'movie': job['movie']}))
    (work / 'agent-run.json').write_text(json.dumps({
        'error': 'RUN_BUDGET_EXHAUSTED: original wall deadline reached.',
        'status': 'stopped', 'ended_at': 1}))
    job['state'] = 'running'
    c = state(tmp_path, [job])
    actual = c.state['jobs'][0]
    c.finish(actual, 1, False)
    assert actual['state'] == 'complete_movie_present'
    assert actual['independent_review'] == 'pending'
    assert 'RUN_BUDGET_EXHAUSTED' in actual['producer_error']
    assert actual['producer_successful_exit'] is False
    assert not c.paused


@pytest.mark.parametrize('coverage,limits', [({}, []), (None, []), ('all checked', []),
                                             ({'video': ''}, []), ({'video': 'frames'}, None)])
def test_empty_or_malformed_evidence_is_not_accepted(tmp_path, coverage, limits):
    reviewing, work, job, record = review_fixture(tmp_path)
    path = Path(record['directory']) / 'output/review.json'
    result = json.loads(path.read_text())
    result.update(coverage=coverage, limits=limits)
    path.write_text(json.dumps(result))
    with pytest.raises(reviewing.ReviewProtocolError, match='coverage|limits'):
        reviewing.load_review(record)


def test_reviewer_uses_original_package_instead_of_mutable_director_input(tmp_path, monkeypatch):
    reviewing, work, job, record = review_fixture(tmp_path)
    original = work.parent / 'selected-package'
    (original / 'episode').mkdir(parents=True, exist_ok=True)
    (original / 'episode/02-script.json').write_text('{"original":true}')
    (work / 'input').mkdir()
    c = state(tmp_path, [job])
    def inspect_init(command, **kwargs):
        assert Path(command[command.index('--input') + 1]) == original
        return subprocess.CompletedProcess(command, 1, '', 'fixture stops before any model call')
    monkeypatch.setattr(reviewing.subprocess, 'run', inspect_init)
    with pytest.raises(reviewing.ReviewProtocolError, match='Reviewer init failed'):
        reviewing.Reviews(c).start(c.state['jobs'][0])


def test_filename_label_with_exact_bound_bytes_is_a_valid_review(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    path = Path(record['directory']) / 'output/review.json'
    result = json.loads(path.read_text())
    result['film'] = Path(record['movie']).name
    path.write_text(json.dumps(result))
    assert reviewing.load_review(record)['verdict'] == 'accepted'
    result['film'] = 'another-movie.mp4'
    path.write_text(json.dumps(result))
    with pytest.raises(reviewing.ReviewProtocolError, match='version'):
        reviewing.load_review(record)


def test_original_connection_failure_queues_one_same_session_resume(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    original = {'started_at': 123, 'deadline_at': (time.time() + 1000) * 1000,
                'provider_requests': 107, 'request_limit': 256}
    (work / 'run-budget.json').write_text(json.dumps(original))
    (work / 'session-path.txt').write_text('original-session.jsonl')
    (work / 'agent-run.json').write_text(json.dumps({'status':'stopped','error':'Connection error.','ended_at':1}))
    job.update(state='running', active_operation='production')
    c = state(tmp_path, [job])
    actual = c.state['jobs'][0]
    c.finish(actual, 1, False)
    assert actual['state'] == 'resume_queued'
    assert actual['automatic_session_resumes'] == 1
    assert not c.paused
    assert json.loads((work / 'run-budget.json').read_text()) == original
    # The same transport failure after that bounded recovery must stop, not loop forever.
    c.finish(actual, 1, False)
    assert actual['state'] == 'needs_attention'
    assert actual['automatic_session_resumes'] == 1


def test_repair_that_expires_while_waiting_is_reported_without_taking_a_slot(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path, verdict='rejected', findings=[{
        'severity':'blocking','owner':'episode','seconds':[1,2],'what':'duplicate person'}])
    (work / 'session-path.txt').write_text('original-session.jsonl')
    (work / 'run-budget.json').write_text('{"deadline_at":1,"provider_requests":107,"request_limit":256}')
    job.update(state='repair_queued', repair_status='queued', independent_review='rejected')
    c = state(tmp_path, [job])
    assert c.claim('gpu52-fast') is None
    assert c.state['jobs'][0]['repair_status'] == 'blocked_original_limits'
    assert c.state['jobs'][0]['state'] == 'complete_movie_present'


def test_connection_resume_executes_original_session_without_reinitializing(tmp_path):
    reviewing, work, job, record = review_fixture(tmp_path)
    original = {'started_at':123, 'deadline_at':(time.time()+1000)*1000,
                'provider_requests':107, 'request_limit':256}
    (work/'run-budget.json').write_text(json.dumps(original))
    (work/'agent-run.json').write_text('{"error":"Connection error."}')
    (work/'session-path.txt').write_text('original-session.jsonl')
    job.update(state='resume_queued', automatic_session_resumes=1, movie=None)
    c=state(tmp_path,[job]);c.python=Path(sys.executable);c.entry=tmp_path/'resume.py'
    c.entry.write_text('''import sys,json
from pathlib import Path
w=Path(sys.argv[2]);assert '--resume' in sys.argv
b=json.loads((w/'run-budget.json').read_text());b['provider_requests']+=1
(w/'run-budget.json').write_text(json.dumps(b))
''')
    assert c.dispatch(c.state['jobs'][0])==0
    after=json.loads((work/'run-budget.json').read_text())
    assert after==dict(original,provider_requests=108)
    assert (work/'session-path.txt').read_text()=='original-session.jsonl'
    assert json.loads((work/'recoveries/connection-001/run-budget.json').read_text())==original


def test_gateway_fix_rechecks_saved_report_without_new_reviewer_calls(tmp_path,monkeypatch):
    reviewing,work,job,record=review_fixture(tmp_path)
    report=Path(record['directory'])/'output/review.json'
    result=json.loads(report.read_text());result['film']=Path(record['movie']).name
    report.write_text(json.dumps(result));original_report=report.read_bytes()
    budget=Path(record['directory'])/'run-budget.json';budget.write_text('{"provider_requests":84,"request_limit":96}')
    job.update(independent_review='protocol_error',review_error='Review targets a different movie version')
    c=state(tmp_path,[job]);manager=reviewing.Reviews(c)
    monkeypatch.setattr(manager,'start',lambda job:pytest.fail('Saved review must not call the model again'))
    manager.recheck_protocol_reports()
    assert c.state['jobs'][0]['independent_review']=='accepted'
    assert 'review_error' not in c.state['jobs'][0]
    assert report.read_bytes()==original_report
    assert budget.read_text()=='{"provider_requests":84,"request_limit":96}'


def test_authorized_pending_window_grant_waits_for_actual_dispatch(tmp_path):
    reviewing,work,job,record=review_fixture(tmp_path,verdict='rejected',findings=[{
        'severity':'blocking','owner':'episode','what':'duplicate child','seconds':[126,130]}])
    original={'started_at':123,'deadline_at':1,'provider_requests':160,'request_limit':256}
    (work/'run-budget.json').write_text(json.dumps(original))
    (work/'session-path.txt').write_text('original-session.jsonl')
    grant={'seconds':900,'authorized_by':'human user explicit15min reply','reason':'blocking duplicate child'}
    (work/'window-grant.json').write_text(json.dumps(grant))
    assert reviewing.repair_budget(work) is None
    assert json.loads((work/'run-budget.json').read_text())==original
    job.update(state='complete_movie_present',independent_review='rejected',repair_status='blocked_original_limits')
    c=state(tmp_path,[job]);selected=c.claim('gpu52-fast')
    assert selected[0]['state']=='repair_queued'
    assert json.loads((work/'run-budget.json').read_text())==original
    assert json.loads((work/'window-grant.json').read_text())==grant


def test_consumed_grant_cannot_renew_expired_or_exhausted_budget(tmp_path):
    reviewing,work,job,record=review_fixture(tmp_path)
    (work/'session-path.txt').write_text('original-session.jsonl')
    (work/'run-budget.json').write_text('{"deadline_at":1,"provider_requests":160,"request_limit":256}')
    (work/'window-grant.json').write_text('{"seconds":900,"authorized_by":"human","consumed_at":"earlier"}')
    assert reviewing.repair_budget(work)=='Original director wall deadline reached'
    (work/'window-grant.json').write_text('{"seconds":900,"authorized_by":"human"}')
    (work/'run-budget.json').write_text('{"deadline_at":1,"provider_requests":256,"request_limit":256}')
    assert reviewing.repair_budget(work)=='Original director request limit reached'


def test_granted_repair_uses_grant_launcher_and_preserves_stopped_runtime(tmp_path,monkeypatch):
    reviewing,work,job,record=review_fixture(tmp_path,verdict='rejected',findings=[{
        'severity':'blocking','owner':'episode','what':'duplicate child','seconds':[126,130]}])
    original={'started_at':123,'deadline_at':1,'provider_requests':160,'request_limit':256}
    (work/'run-budget.json').write_text(json.dumps(original))
    (work/'session-path.txt').write_text('original-session.jsonl')
    (work/'window-grant.json').write_text('{"seconds":900,"authorized_by":"human user"}')
    (work/'tools').mkdir();(work/'tools/runtime.mjs').write_text('original stopped harness')
    c=state(tmp_path,[job]);c.runtime=tmp_path/'fixed-runtime';c.runtime.mkdir()
    for name in ['runtime.mjs','bash_limits.mjs']:(c.runtime/name).write_text('fixed '+name)
    c.grant_entry=tmp_path/'time-aware-launcher.py'
    reviewing.Reviews(c).settle(c.state['jobs'][0],record)
    class Started:
        pid=12345
        def wait(self):return 0
    def launch(command,**kwargs):
        assert command[1]==str(c.grant_entry)
        assert '--resume' in command
        assert '900秒' in command[-1]
        return Started()
    monkeypatch.setattr(lanes.subprocess,'Popen',launch)
    assert c.dispatch(c.state['jobs'][0])==0
    before=Path(record['directory'])/'original-director-before-resume'
    assert (before/'runtime.mjs').read_text()=='original stopped harness'
    assert (work/'tools/runtime.mjs').read_text()=='fixed runtime.mjs'
    assert json.loads((work/'run-budget.json').read_text())==original
    assert (work/'session-path.txt').read_text()=='original-session.jsonl'


def test_authorized_unfinished_resume_consumes_time_via_normal_launcher_not_connection_recovery(tmp_path, monkeypatch):
    work = tmp_path / 'original'
    (work / 'tools').mkdir(parents=True)
    budget = {'started_at': 123, 'deadline_at': 1, 'provider_requests': 65, 'request_limit': 256}
    (work / 'run-budget.json').write_text(json.dumps(budget))
    (work / 'agent-run.json').write_text('{"error":"RUN_BUDGET_EXHAUSTED"}')
    (work / 'session-path.txt').write_text('original-session.jsonl')
    (work / 'tools/runtime.mjs').write_text('original stopped runtime')
    (work / 'window-grant.json').write_text('{"seconds":1800,"authorized_by":"human explicit approval"}')
    feedback = tmp_path / 'continuation.md'
    feedback.write_text('Finish the original existing edit and checks; preserve all limits.')
    job = {'id':'unfinished', 'lane':'gpu16-h3', 'workspace':str(work), 'state':'resume_queued',
           'resume_authorization':{'id':'explicit-approval', 'feedback':str(feedback)}}
    c = state(tmp_path, [job])
    c.runtime = tmp_path / 'fixed-tools'; c.runtime.mkdir()
    for name in ['runtime.mjs','bash_limits.mjs']:(c.runtime / name).write_text('bounded '+name)
    c.grant_entry = tmp_path / 'grant-aware-entry.py'
    class Started:
        pid=12345
        def wait(self): return 0
    def launch(command, **kwargs):
        assert command[1] == str(c.grant_entry)
        assert '--resume' in command and str(feedback) in command[-1]
        assert '1800' in command[-1] and '连接中断' not in command[-1]
        return Started()
    monkeypatch.setattr(lanes.subprocess,'Popen',launch)
    actual=c.state['jobs'][0]
    assert c.dispatch(actual)==0
    assert json.loads((work/'run-budget.json').read_text())==budget
    assert (work/'session-path.txt').read_text()=='original-session.jsonl'
    before=work/'recoveries/authorized-explicit-approval'
    assert (before/'runtime.mjs').read_text()=='original stopped runtime'
    assert (work/'tools/runtime.mjs').read_text()=='bounded runtime.mjs'
    assert 'resume_authorization' not in actual
    assert 'automatic_session_resumes' not in actual


def test_batch_authorization_grants_only_once_and_only_for_an_existing_rejected_movie(tmp_path):
    reviewing,work,job,record=review_fixture(tmp_path,verdict='rejected',findings=[{
        'severity':'blocking','owner':'episode','what':'face obscured','seconds':[132,145]}])
    (work/'session-path.txt').write_text('original-session.jsonl')
    original={'started_at':123,'deadline_at':1,'provider_requests':199,'request_limit':256}
    (work/'run-budget.json').write_text(json.dumps(original))
    (tmp_path/'repair-time-policy.json').write_text(json.dumps({
        'seconds':900,'authorized_by':'human batch approval','eligible_episode_ids':['film']}))
    job.update(independent_review='rejected',repair_status='blocked_original_limits')
    c=state(tmp_path,[job]);assert c.claim('gpu52-fast')[0]['state']=='repair_queued'
    grant=json.loads((work/'window-grant.json').read_text());assert grant['seconds']==900
    assert json.loads((work/'run-budget.json').read_text())==original
    grant['consumed_at']='already used';(work/'window-grant.json').write_text(json.dumps(grant))
    c.state['jobs'][0].update(state='complete_movie_present',repair_status='blocked_original_limits')
    c.claimed.clear();assert c.claim('gpu52-fast') is None
    assert json.loads((work/'window-grant.json').read_text())['consumed_at']=='already used'
    (work/'window-grant.json').unlink()
    c.state['jobs'][0]['movie']=None
    assert not reviewing.authorize_repair_window(tmp_path,c.state['jobs'][0])
    assert not (work/'window-grant.json').exists()


def test_repaired_new_export_gets_review_before_a_fresh_first_review(tmp_path):
    reviewing,work,job,record=review_fixture(tmp_path)
    job.update(independent_review='pending',repair_status='new_export_ready')
    (work/'run-budget.json').write_text('{"deadline_at":1,"provider_requests":171,"request_limit":256}')
    fresh=tmp_path/'fresh';fresh.mkdir();(fresh/'run-budget.json').write_text(json.dumps({
        'deadline_at':(time.time()+1000)*1000,'provider_requests':100,'request_limit':256}))
    other={'id':'new-film','workspace':str(fresh),'movie':job['movie'],'lane':'gpu52-fast',
           'state':'complete_movie_present','independent_review':'pending'}
    c=state(tmp_path,[other,job]);selected=reviewing.Reviews(c).claim()
    assert selected[0]['id']=='film'


def test_approved_repair_expiring_in_queue_gets_its_window_in_the_same_claim(tmp_path):
    reviewing,work,job,record=review_fixture(tmp_path,verdict='rejected',findings=[{
        'severity':'blocking','owner':'episode','what':'face render fault','seconds':[130,137]}])
    (work/'session-path.txt').write_text('original-session.jsonl')
    budget={'deadline_at':1,'provider_requests':199,'request_limit':256}
    (work/'run-budget.json').write_text(json.dumps(budget))
    (tmp_path/'repair-time-policy.json').write_text(json.dumps({
        'seconds':900,'authorized_by':'human batch approval','eligible_episode_ids':['film']}))
    job.update(state='repair_queued',repair_status='queued',independent_review='rejected')
    c=state(tmp_path,[job]);selected=c.claim('gpu52-fast')
    assert selected is not None and selected[0]['id']=='film'
    assert json.loads((work/'window-grant.json').read_text())['seconds']==900
    assert json.loads((work/'run-budget.json').read_text())==budget
