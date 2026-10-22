"""Do not let repeated successful image reads consume the entire review window."""
import json

from novel_manga.application.agents.image_read_progress import handle


def event(path, kind='PreToolUse'):
    return {'hook_event_name': kind, 'cwd': str(path.parent),
            'tool_name': 'Read', 'tool_use_id': 'read-1',
            'tool_input': {'file_path': str(path)}}


def read(path, state):
    before = handle(event(path), state)
    if before.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny':
        return before
    return handle(event(path, 'PostToolUse'), state)


def test_successful_same_picture_reads_get_feedback_then_stop_repeating(tmp_path):
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'current picture')
    state = tmp_path / 'attempt/image-read-progress.json'
    assert read(image, state) == {}
    assert 'additionalContext' in read(image, state)['hookSpecificOutput']
    for _ in range(30):
        result = read(image, state)['hookSpecificOutput']
        assert result['permissionDecision'] == 'deny'
        assert 'Do not invent defects or force a pass' in result['permissionDecisionReason']
    assert json.loads(state.read_text())['successful_reads'] == 2
    records = [json.loads(line) for line in state.with_suffix('.jsonl').read_text().splitlines()]
    assert sum(r['action'] == 'read_success' for r in records) == 2
    assert sum(r['action'] == 'blocked_repeat' for r in records) == 30


def test_new_view_or_cross_comparison_allows_a_fresh_look(tmp_path):
    a = tmp_path / 'first.jpg'; a.write_bytes(b'first image')
    b = tmp_path / 'later.jpg'; b.write_bytes(b'new moment')
    state = tmp_path / 'state.json'
    read(a, state); read(a, state)
    assert read(b, state) == {}
    assert read(a, state) == {}


def test_changed_image_is_readable_without_resetting_an_attempt(tmp_path):
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'before')
    state = tmp_path / 'state.json'
    read(image, state); read(image, state)
    image.write_bytes(b'new approved frame with different content')
    assert read(image, state) == {}


def test_new_text_evidence_and_new_attempt_keep_their_own_progress(tmp_path):
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'picture')
    text = tmp_path / 'request.json'; text.write_text('{"state":"removed"}')
    state = tmp_path / 'first/state.json'
    read(image, state); read(image, state)
    assert read(text, state) == {}
    assert read(image, state) == {}
    read(image, state)
    assert read(image, tmp_path / 'second/state.json') == {}


def test_a_failed_read_is_not_recorded_as_successful(tmp_path):
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'picture')
    state = tmp_path / 'state.json'
    for _ in range(3):
        assert handle(event(image), state) == {}
    assert not state.exists()


def test_unrelated_tools_do_not_get_permissions_or_content_verdicts(tmp_path):
    assert handle({'tool_name': 'Write'}, tmp_path / 'state.json') == {}


def test_each_read_binds_its_own_time_even_when_read_backwards_and_separates_references(tmp_path):
    from novel_manga.application.agents.image_read_progress import image_evidence_context
    paths = ['input/clip_18/frame_1.jpg','output/frames/clip_18/zero.jpg','input/clip_18/reference-1.jpeg']
    for name in paths:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'unchanged JPEG fixture')
    primary={'clips':{'clip_18':{'current_frames':[
        {'path':paths[0],'seconds':.7332},{'path':paths[1],'seconds':0}],
        'references':[{'path':paths[2],'role':'character','name':'actor'}]}}}
    (tmp_path/'input/primary.json').write_text(json.dumps(primary))
    (tmp_path/'output/frame_requests.jsonl').write_text(json.dumps({'path':paths[1],'actual_seconds':0})+'\n')
    def feedback(name, identity):
        return image_evidence_context({**event(tmp_path/name,'PostToolUse'),'cwd':str(tmp_path),
                                       'tool_use_id':identity})['hookSpecificOutput']['additionalContext']
    first=feedback(paths[0],'read-later');earlier=feedback(paths[1],'read-opening');ref=feedback(paths[2],'read-reference')
    assert '0.733200s' in first and paths[0] in first
    assert 'source PTS=0.000000s' in earlier and paths[1] in earlier
    assert 'Read order is not time order' in earlier
    assert 'tool_use_id=read-opening' in earlier and 'tool_use_id=read-later' in first
    assert 'REFERENCE IMAGE' in ref and 'not a movie frame' in ref
    assert all((tmp_path/name).read_bytes()==b'unchanged JPEG fixture' for name in paths)


def test_new_decoder_frame_gets_actual_pts_before_host_final_collection(tmp_path):
    from novel_manga.application.agents.image_read_progress import image_evidence_context
    frame=tmp_path/'output/frames/clip_18/take-1/frame_0001210.jpg'
    frame.parent.mkdir(parents=True);frame.write_bytes(b'current decoded JPEG')
    (tmp_path/'input').mkdir()
    (tmp_path/'input/primary.json').write_text(json.dumps({'clips':{'clip_18':{
        'current_take':'clip_18/take-1','current_frames':[]}}}))
    (tmp_path/'output/frame_requests.jsonl').write_text(json.dumps({
        'path':str(frame.relative_to(tmp_path)),'actual_seconds':1.25,
        'source_video':'input/clip_18/take-1/clip.mp4'})+'\n')
    result=image_evidence_context({**event(frame,'PostToolUse'),'cwd':str(tmp_path)})
    assert 'source PTS=1.250000s' in result['hookSpecificOutput']['additionalContext']


def test_episode_deadline_feedback_uses_episode_outputs_and_keeps_no_generation_boundary(tmp_path, monkeypatch):
    from novel_manga.application.agents import image_read_progress as progress
    monkeypatch.setattr(progress.time, 'time', lambda: 900)
    deadline = tmp_path/'deadline.json'
    deadline.write_text(json.dumps({'deadline_at':1000, 'phase':'episode'}))
    message = progress.execution_feedback({'hook_event_name':'PostToolUse'}, deadline)['hookSpecificOutput']['additionalContext']
    assert 'output/repair.json' in message and 'permitted host action' in message
    assert 'review.md' not in message and 'No new time window or generation' in message


def test_review_saves_one_target_before_reading_the_next_without_forcing_a_verdict(tmp_path):
    from novel_manga.application.agents.image_read_progress import review_checkpoint
    work = tmp_path
    images = []
    for cid in ['clip_02','clip_14','context_01']:
        path = work/'input'/cid/'frame.jpg'
        path.parent.mkdir(parents=True);path.write_bytes(b'actual frame fixture')
        images.append(path)
    deadline = work/'deadline.json'
    deadline.write_text(json.dumps({'phase':'review','targets':['clip_02','clip_14'], 'deadline_at':1000}))
    state = work/'attempt/progress.json';state.parent.mkdir()
    def invoke(path, kind):
        return review_checkpoint({**event(path,kind),'cwd':str(work)},state,deadline)
    assert invoke(images[0],'PostToolUse') == {}
    assert invoke(images[2],'PreToolUse') == {}  # source context stays available
    blocked = invoke(images[1],'PreToolUse')['hookSpecificOutput']
    assert blocked['permissionDecision'] == 'deny'
    assert 'uncertain/stop' in blocked['permissionDecisionReason']
    (work/'output').mkdir()
    (work/'output/review.json').write_text(json.dumps({'clips':[
        {'clip_id':'clip_02','verdict':'uncertain','decision_file':'output/clip_02/decision.json'}]}))
    assert invoke(images[1],'PreToolUse')['hookSpecificOutput']['permissionDecision'] == 'deny'
    decision = work/'output/clip_02/decision.json';decision.parent.mkdir()
    decision.write_text('{"action":"stop","reason":"Need a closer view"}')
    assert invoke(images[1],'PreToolUse') == {}
    assert invoke(images[1],'PostToolUse') == {}
    assert json.loads(deadline.read_text())['deadline_at'] == 1000
    # The next full source/request file obeys the same checkpoint as its images.
    target=work/'input/targets/clip_02.json';target.parent.mkdir();target.write_text('{}')
    blocked=invoke(target,'PreToolUse')['hookSpecificOutput']
    assert blocked['permissionDecision']=='deny' and 'clip_14' in blocked['permissionDecisionReason']


def test_real_runner_wires_hooks_without_mutating_installed_skills(tmp_path, monkeypatch):
    from novel_manga.application.agents import sandbox
    work = tmp_path / 'runs/review'
    (work / '.claude/skills').mkdir(parents=True)
    (work / '.claude/skills/test.md').write_text('existing skill')
    (work / 'prompt.txt').write_text('review only')
    digest = sandbox.skills_digest(work / '.claude')
    monkeypatch.setenv('TEST_READ_KEY', 'synthetic-key')
    commands = []
    def fake(command, stdout, stderr, env):
        commands.append(command)
        return 0
    monkeypatch.setattr(sandbox.subprocess, 'call', fake)
    config = {'runs_root': str(work.parent), 'model': 'test', 'image': 'test',
              'base_url': 'http://localhost', 'docker_network': 'host', 'key_var': 'TEST_READ_KEY'}
    result = sandbox.run_agent('review', config=config, timeout=1200, log=lambda *_: None)
    assert result.ok
    command = commands[0]
    assert command[command.index('timeout') + 1] == '1200'
    settings_path = command[command.index('--settings') + 1]
    actual = work / settings_path.removeprefix('/work/')
    settings = json.loads(actual.read_text())
    assert set(settings['hooks']) == {'PreToolUse', 'PostToolUse'}
    assert (work / 'tools/_sandbox_image_read_progress.py').is_file()
    assert sandbox.skills_digest(work / '.claude') == digest
    assert not result.produced


def test_deadline_feedback_on_new_frames_does_not_force_a_verdict(tmp_path, monkeypatch):
    from novel_manga.application.agents import image_read_progress as progress
    monkeypatch.setattr(progress.time, 'time', lambda: 900)
    deadline=tmp_path/'deadline.json';deadline.write_text(json.dumps({'deadline_at':1000,'targets':['a','b']}))
    result=progress.execution_feedback({'hook_event_name':'PostToolUse','cwd':str(tmp_path)},deadline)
    message=result['hookSpecificOutput']['additionalContext']
    assert '100 seconds' in message and 'uncertain/stop' in message
    assert 'original deadline' in message and 'No content verdict' in message
