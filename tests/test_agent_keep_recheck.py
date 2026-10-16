import copy
import json
from types import SimpleNamespace

import pytest

from novel_manga.application.review import execution, judges, cast_video, confirmed
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def episode(tmp_path, monkeypatch):
    novel = tmp_path / 'book'
    directory = novel / 'book_1'
    raw = directory / 'work/clips/a/attempt_01/clip.mp4'
    mixed = raw.parent / 'postmix/clip.mp4'
    for video in (raw, mixed):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b'existing take')
    card = novel / 'series_assets/card.jpeg'
    card.parent.mkdir(parents=True)
    card.write_bytes(b'existing reference')
    clip = {'clip_id': 'a', 'kind': 'video', 'prompt_h3': 'original English',
            'references': [{'role': 'character', 'name': '甲', 'path': 'series_assets/card.jpeg'}],
            'request_seconds': 7, 'generation_seed': 123, 'repair_take': 0,
            'segment_ids': ['s1'],
            'lines': [{'speaker_name': '甲', 'text': '原台词', 'delivery_mode': 'visible_dialogue'}]}
    script = {'shots': [{'turns': copy.deepcopy(clip['lines']), 'actions': []}]}
    bible = dict(novel_title='书', genre='都市', visual_style='漫画', palette='自然',
                 style_fingerprint='style', characters=[])
    values = {
        'clip_plan.json': {'clips': [clip, {'clip_id': 'b', 'kind': 'video'}]},
        'chapter_script.json': script,
        'segments.json': [{'segment_id': 's1', 'text': '甲将花留在桌上。'}],
        'review_feedback.json': {'a': '原纠正', 'b': '其他纠正'},
        'thin_media_report.json': {'clips': [{'clip_id': 'a', 'selected': {
            'video': str(raw), 'postmix_video': str(mixed), 'passed': True, 'issues': [],
            'hypothesis': '原台词'}}]},
        'episode_review.json': {'policy': 'existing-policy', 'clips': {
            'a': {'severity': 'fail', 'tier': 'must_fix', 'verify': {'verdict': 'obvious'}},
            'b': {'severity': 'fail', 'tier': 'must_fix', 'custom': 'preserved'}},
            'feedback': {'a': '旧误报', 'b': '其他失败'},
            'flags': ['a: 旧误报', 'b: 其他失败'], 'custom': 'preserved'},
        'repair_history/history.json': {'trials': [], 'observations': {}},
    }
    atomic_write_json(novel / 'story_bible.json', bible)
    atomic_write_json(novel / 'visual_grammar.json', {'location_time': {'诊室': '白天'}})
    atomic_write_json(novel / 'profile.json', {'speech_gate': 'enforce'})
    for name, value in values.items():
        atomic_write_json(directory / name, value)
    calls = []
    monkeypatch.setattr(cast_video, 'recorded_model', lambda path: calls.append(('model', path)) or 'minimax-h3-ref2va-turbo')
    def judge(checked, video, bible, times, hypothesis, work, **kwargs):
        calls.append(('judge', copy.deepcopy(checked), video, times, hypothesis, kwargs))
        return {'severity': 'pass', 'verify': {'verdict': 'fine'}, 'feedback': ''}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    observed = []
    monkeypatch.setattr(execution.history, 'observe', lambda *args: observed.append(args))
    protected = {path: path.read_bytes() for path in directory.rglob('*.json')
                 if path.name not in {'episode_review.json', 'history.json'}}
    protected[card] = card.read_bytes()
    return directory, raw, mixed, values, protected, calls, observed


def assert_frozen(protected):
    assert all(path.read_bytes() == value for path, value in protected.items())


def test_keep_pass_rechecks_selected_postmix_and_preserves_inputs_and_other_clips(episode):
    directory, raw, mixed, values, protected, calls, observed = episode
    result = execution.recheck_current_clip(directory, 'a')
    assert result['changed'] == [] and result['accepted'] == ['a']
    assert calls[0] == ('model', raw)
    _, checked, video, times, words, kwargs = calls[1]
    assert checked['_review_video_model'] == 'minimax-h3-ref2va-turbo'
    assert checked['references'] == values['clip_plan.json']['clips'][0]['references']
    assert video == mixed and times == {'诊室': '白天'} and words == '原台词'
    assert kwargs['source_script'] == values['chapter_script.json']
    review = read_json(directory / 'episode_review.json')
    assert review['clips']['a']['severity'] == 'pass'
    assert review['clips']['b'] == values['episode_review.json']['clips']['b']
    assert review['feedback'] == {'b': '其他失败'} and review['flags'] == ['b: 其他失败']
    assert review['custom'] == 'preserved'
    assert set(observed[0][2]) == {'a'}
    assert_frozen(protected)


def test_keep_reads_only_host_authored_criteria_for_the_current_clip(episode):
    directory, _, _, _, _, calls, _ = episode
    criteria = ['镜头切换可接受；核对人物、座位、行李和花的空间关系。']
    atomic_write_json(directory / 'review_criteria.json', {'clips': {'a': criteria}})
    result = execution.recheck_current_clip(directory, 'a')
    assert result['accepted'] == ['a']
    assert calls[1][-1]['review_criteria'] == criteria
    row = read_json(directory / 'episode_review.json')['clips']['a']
    assert row['review_criteria'] == criteria
    assert row.get('confirmed') is None
    assert 'review_criteria' not in read_json(directory / 'episode_review.json')['clips']['b']


def _scoped_review_artifact(directory, raw, mixed, *, session='session-1', criteria=None):
    from novel_manga.review.storage import take_identity
    work = directory.parent.parent / 'run'
    evidence = work / 'input/frame-1.jpg'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b'frame')
    (raw.parent / 'clip.mp4.task.json').write_text('{"task_id":"task-a"}')
    atomic_write_json(work / 'episode-session.json', {'session_id': session})
    selected_criteria = criteria or ['换机位本身可接受；核对人物、座位和道具的实际空间关系。']
    atomic_write_json(directory / 'review_criteria.json', {'clips': {'a': selected_criteria}})
    atomic_write_json(directory / 'episode_review.json', {
        **read_json(directory / 'episode_review.json'), 'policy': 'existing-policy', 'review_mode': 'classic',
        'clips': {**read_json(directory / 'episode_review.json')['clips'],
                  'a': {**read_json(directory / 'episode_review.json')['clips']['a'],
                        'defect_issue': '画面里人物与道具空间关系混乱'}}})
    return work, {
        'schema': 'scoped-agent-review-v1', 'author_session': session, 'clip_id': 'a',
        'task_id': 'task-a', 'video': str(mixed), 'take': take_identity(mixed),
        'source_segment_ids': ['s1'],
        'source_evidence': [{'segment_id': 's1', 'text': '甲将花留在桌上。'}],
        'review_criteria': selected_criteria, 'assessment': 'pass',
        'judgment': '当前抽帧未证明空间关系混乱。',
        'observations': [{'kind': 'video_frame', 'file': 'input/frame-1.jpg',
                          'times': '0.0–1.0s', 'observation': '人物、座位和道具位置连续。'}],
        'reviewed_findings': [{'finding': '画面里人物与道具空间关系混乱',
                               'disposition': 'not_demonstrated'}],
    }


def test_scoped_agent_keep_publishes_confirm_record_and_retains_contested_review(episode, monkeypatch):
    directory, raw, mixed, _, _, calls, observed = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    independent = {'session_id': 'review-session', 'assessment': 'acceptable_with_minor_contact'}
    atomic_write_json(work / 'input/independent-review.json', independent)
    artifact['independent_review_file'] = 'input/independent-review.json'
    monkeypatch.setattr(judges, 'judge_clip', lambda *a, **k: pytest.fail('use the supplied reviewed frames'))
    result = execution.recheck_current_clip(directory, 'a', review_artifact=artifact,
                                            work_dir=work, author_session='session-1')
    assert result['accepted'] == ['a']
    saved = read_json(directory / 'episode_review.json')
    row = saved['clips']['a']
    assert row['severity'] == 'pass' and row['scoped_agent_review']['assessment'] == 'pass'
    assert row['contested_evidence']['prior_automated_review']['defect_issue'] == '画面里人物与道具空间关系混乱'
    assert row['contested_evidence']['reviewed_findings'][0]['disposition'] == 'not_demonstrated'
    assert row['scoped_agent_review']['independent_review'] == independent
    records = [json.loads(line) for line in (directory.parent / 'repair_manager/verified.jsonl').read_text().splitlines()]
    assert records[-1]['mode'] == 'confirm' and records[-1]['picture_review']['severity'] == 'pass'
    assert records[-1]['independent_review'] == independent
    assert calls == [('model', raw)] and len(observed) == 1


def test_scoped_agent_new_failure_survives_when_old_findings_are_disproven(episode, monkeypatch):
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    old = read_json(directory / 'episode_review.json')
    old['clips']['a']['confirmed'] = {
        'video': str(mixed), 'take': artifact['take'], 'issue': '旧判词称玫瑰被重新拿起',
        'instruction': '玫瑰应留在桌面', 'check': {'result': 'resolved', 'video': str(mixed),
                                                    'take': artifact['take']}}
    atomic_write_json(directory / 'episode_review.json', old)
    artifact['reviewed_findings'].extend([
        {'finding': '旧判词称玫瑰被重新拿起', 'disposition': 'not_demonstrated'},
        {'finding': '玫瑰应留在桌面', 'disposition': 'not_demonstrated'},
    ])
    artifact['assessment'] = 'fail'
    artifact['judgment'] = '新增观察到第二支白花同时出现在同一桌面。'
    artifact['reviewed_findings'][0]['disposition'] = 'not_demonstrated'
    result = execution.recheck_current_clip(directory, 'a', review_artifact=artifact,
                                            work_dir=work, author_session='session-1')
    assert result['accepted'] == []
    row = read_json(directory / 'episode_review.json')['clips']['a']
    assert row['severity'] == 'fail'
    assert row['defect_issue'] == '新增观察到第二支白花同时出现在同一桌面。'
    assert row['confirmed']['issue'] == '新增观察到第二支白花同时出现在同一桌面。'
    assert row['contested_evidence']['prior_automated_review']['defect_issue'] == '画面里人物与道具空间关系混乱'
    assert row['contested_evidence']['prior_automated_review']['confirmed']['issue'] == '旧判词称玫瑰被重新拿起'


def test_partial_old_confirmation_is_replaced_by_agent_current_failure(episode):
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    old = read_json(directory / 'episode_review.json')
    old_claim = {'video': str(mixed), 'take': artifact['take'], 'issue': '许棠首帧手持玫瑰',
                 'instruction': '许棠全段空手',
                 'check': {'result': 'unresolved', 'video': str(mixed), 'take': artifact['take'],
                           'evidence': '首帧玫瑰已被拿起'}}
    old['clips']['a']['confirmed'] = old_claim
    atomic_write_json(directory / 'episode_review.json', old)
    artifact['assessment'] = 'fail'
    artifact['judgment'] = '阮夏约7秒拿起白色栀子，并在约9秒放回桌面。'
    artifact['repair_instruction'] = '阮夏的手保持空闲，白色栀子全程留在西北桌面。'
    artifact['reviewed_findings'] = [
        {'finding': '画面里人物与道具空间关系混乱', 'disposition': 'upheld'},
        {'finding': old_claim['issue'], 'disposition': 'not_demonstrated'},
        {'finding': old_claim['instruction'], 'disposition': 'not_demonstrated'},
        {'finding': old_claim['check']['evidence'], 'disposition': 'not_demonstrated'},
    ]
    result = execution.recheck_current_clip(directory, 'a', review_artifact=artifact,
                                            work_dir=work, author_session='session-1')
    assert result['accepted'] == []
    saved = read_json(directory / 'episode_review.json')
    row = saved['clips']['a']
    assert row['severity'] == 'fail'
    assert row['confirmed']['issue'] == artifact['judgment']
    assert row['confirmed']['instruction'] == artifact['repair_instruction']
    assert row['contested_evidence']['prior_automated_review']['confirmed']['issue'] == old_claim['issue']

    future = copy.deepcopy(saved)
    future['clips']['a'].update(severity='pass', tier='optional')
    future['feedback'] = {}
    enforced = confirmed.enforce(directory, future, saved, verify=True)
    assert enforced['clips']['a']['severity'] == 'fail'
    assert enforced['clips']['a']['confirmed']['issue'] == artifact['judgment']
    assert artifact['repair_instruction'] in enforced['feedback']['a']
    assert old_claim['issue'] not in enforced['feedback']['a']


def test_keep_review_file_flows_through_existing_check_and_apply_operations(episode, monkeypatch):
    from novel_manga.application.agents.episode.draft_operations import DraftOperations
    from novel_manga.application.agents.episode.repair_operations import RepairOperations
    from novel_manga.application.agents.episode import repair_targets
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    atomic_write_json(work / 'output/scoped-review.json', artifact)
    atomic_write_json(work / 'output/repair.json', {'decisions': [{
        'clip_id': 'a', 'action': 'keep', 'reason': 'Agent completed the scoped review.',
        'review_file': 'output/scoped-review.json'}]})
    ctx = SimpleNamespace(work_dir=work, run_dir=work, episode_dir=directory, input_mode='script')
    monkeypatch.setattr(repair_targets, 'validate_target', lambda *a, **kw: None)
    checked = DraftOperations(ctx).check({'file': 'output/repair.json'})
    assert checked['valid'] is True

    class Evidence:
        @staticmethod
        def current_review(review): return True
        @staticmethod
        def voice_recording_blocks(plan): return {}

    result = RepairOperations(ctx, None, Evidence()).apply_decisions(
        {'decisions': read_json(work / 'output/repair.json')['decisions']}, {'a'})
    assert result['accepted'] == ['a'] and result['keep_checks'] == ['a']
    assert read_json(directory / 'episode_review.json')['clips']['a']['scoped_agent_review']['clip_id'] == 'a'


def test_passed_take_scoped_keep_can_record_a_new_failure_without_spending_video_budget(episode, monkeypatch):
    from novel_manga.application.agents.episode.draft_operations import DraftOperations
    from novel_manga.application.agents.episode.repair_operations import RepairOperations
    from novel_manga.review.contracts import POLICY
    from novel_manga.review.storage import take_identity
    from novel_manga.application.repair.history import generation_counts
    directory, raw, mixed, _, _, _, _ = episode
    media = read_json(directory / 'thin_media_report.json')
    media['clips'][0]['selected'].pop('postmix_video')
    atomic_write_json(directory / 'thin_media_report.json', media)
    review = read_json(directory / 'episode_review.json')
    review.update(policy=POLICY, review_mode='classic')
    review['clips']['a'].update(video=str(raw), take=take_identity(raw), severity='pass', tier='optional',
                                 verify={'verdict': 'fine'})
    review['clips']['a'].pop('defect_issue', None)
    review['feedback'].pop('a', None)
    review['flags'] = [flag for flag in review.get('flags', []) if not flag.startswith('a:')]
    atomic_write_json(directory / 'episode_review.json', review)
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    review = read_json(directory / 'episode_review.json')
    review.update(policy=POLICY, review_mode='classic')
    review['clips']['a'].update(video=str(raw), take=take_identity(raw), severity='pass', tier='optional',
                                verify={'verdict': 'fine'})
    review['clips']['a'].pop('defect_issue', None)
    atomic_write_json(directory / 'episode_review.json', review)
    artifact['video'] = str(raw)
    artifact['take'] = take_identity(raw)
    artifact['assessment'] = 'fail'
    artifact['judgment'] = '画面中新出现一朵与既定花朵独立的白花。'
    artifact['reviewed_findings'] = []
    atomic_write_json(work / 'output/scoped-review.json', artifact)
    decision = {'clip_id': 'a', 'action': 'keep', 'reason': 'Review the current take under host criteria.',
                'review_file': 'output/scoped-review.json'}
    atomic_write_json(work / 'output/repair.json', {'decisions': [decision]})
    ctx = SimpleNamespace(work_dir=work, run_dir=work, episode_dir=directory, input_mode='script')
    checked = DraftOperations(ctx).check({'file': 'output/repair.json'})
    assert checked['valid'] is True

    counts_before = generation_counts(directory)

    class Evidence:
        @staticmethod
        def current_review(value): return True
        @staticmethod
        def voice_recording_blocks(plan): return {}

    result = RepairOperations(ctx, None, Evidence()).apply_decisions(
        {'decisions': [decision]}, set())
    assert result['accepted'] == [] and result['keep_checks'] == ['a']
    assert result['blocked']['a'] == 'current video independent recheck still failed'
    row = read_json(directory / 'episode_review.json')['clips']['a']
    assert row['severity'] == 'fail' and row['defect_issue'] == artifact['judgment']
    assert row['contested_evidence']['prior_automated_review']['severity'] == 'pass'
    assert generation_counts(directory) == counts_before


@pytest.mark.parametrize('change', ['session', 'take', 'criteria', 'clip', 'source', 'finding'])
def test_scoped_agent_keep_rejects_stale_or_out_of_scope_artifacts(episode, change, monkeypatch):
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    expected_session = 'session-1'
    if change == 'session':
        artifact['author_session'] = 'different-session'
    elif change == 'take':
        artifact['take'] = [0, 0, 0]
    elif change == 'criteria':
        artifact['review_criteria'] = ['another tolerance']
    elif change == 'clip':
        artifact['clip_id'] = 'b'
    elif change == 'source':
        artifact['source_evidence'][0]['text'] = 'stale source'
    elif change == 'finding':
        artifact['reviewed_findings'] = []
    with pytest.raises(ValueError):
        execution.validate_scoped_agent_review(directory, 'a', artifact,
                                               work_dir=work, author_session=expected_session)


def test_scoped_agent_review_is_unavailable_without_host_criteria(episode):
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    (directory / 'review_criteria.json').unlink()
    with pytest.raises(ValueError, match='host criteria'):
        execution.validate_scoped_agent_review(directory, 'a', artifact,
                                               work_dir=work, author_session='session-1')


@pytest.mark.parametrize('subtitle_issue', ['画面底部出现未计划对白字幕', ''])
def test_scoped_keep_must_adjudicate_native_subtitle_blocker(episode, subtitle_issue):
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    review = read_json(directory / 'episode_review.json')
    review['clips']['a'].update(unplanned_subtitles=True, subtitle_issue=subtitle_issue)
    atomic_write_json(directory / 'episode_review.json', review)
    before = (directory / 'episode_review.json').read_bytes()
    with pytest.raises(ValueError, match='each current automated blocker'):
        execution.recheck_current_clip(directory, 'a', review_artifact=artifact,
                                       work_dir=work, author_session='session-1')
    assert (directory / 'episode_review.json').read_bytes() == before
    artifact['reviewed_findings'].append({
        'finding': subtitle_issue or 'unplanned_subtitles', 'disposition': 'not_demonstrated'})
    assert execution.recheck_current_clip(
        directory, 'a', review_artifact=artifact, work_dir=work,
        author_session='session-1')['accepted'] == ['a']


@pytest.mark.parametrize('criteria', [['新的验收标准'], []])
@pytest.mark.parametrize('state', ['new_failure', 'fallback_confirm', 'saved_acceptance'])
def test_reconciliation_cannot_replay_scoped_pass_after_host_criteria_change(episode, criteria, state):
    from novel_manga.application.review import store
    directory, raw, mixed, _, _, _, _ = episode
    work, artifact = _scoped_review_artifact(directory, raw, mixed)
    review = read_json(directory / 'episode_review.json')
    review['clips']['a']['confirmed'] = {
        'video': str(mixed), 'take': artifact['take'], 'issue': '旧空间问题',
        'instruction': '核对旧空间问题'}
    atomic_write_json(directory / 'episode_review.json', review)
    artifact['reviewed_findings'].extend([
        {'finding': value, 'disposition': 'not_demonstrated'}
        for value in ('旧空间问题', '核对旧空间问题')])
    execution.recheck_current_clip(directory, 'a', review_artifact=artifact,
                                   work_dir=work, author_session='session-1')
    saved = read_json(directory / 'episode_review.json')
    if state != 'saved_acceptance':
        saved['clips']['a'] = {
            'video': str(mixed), 'take': artifact['take'],
            'severity': 'fail', 'tier': 'must_fix', 'review_criteria': criteria,
            'verify': {'verdict': 'obvious'}, 'defect_issue': '新标准下的实际缺陷'}
        if state == 'new_failure':
            saved['clips']['a']['flash_checked'] = True
        atomic_write_json(directory / 'episode_review.json', saved)
    atomic_write_json(directory / 'review_criteria.json', {'clips': {'a': criteria}})
    local = store.load_evidence([directory.parent / 'repair_manager/verified.jsonl'])
    result, _ = store.reconcile(directory, local, {}, write=False)
    row = result['clips']['a']
    assert row['severity'] in {'fail', 'review_error'}
    assert row.get('verify', {}).get('verdict') != 'fine'
    if state != 'saved_acceptance':
        assert row['defect_issue'] == '新标准下的实际缺陷'
        assert row['review_criteria'] == criteria


def test_changed_mode_keep_cannot_be_reused_as_the_previous_mode(episode,monkeypatch):
    from novel_manga.application.review import episode as review_episode, evidence
    from novel_manga.review.contracts import POLICY, current_review
    directory,_,_,_,protected,_,_=episode
    previous=read_json(directory/'episode_review.json')
    previous.update(policy=POLICY,review_mode='verify')
    atomic_write_json(directory/'episode_review.json',previous)
    modes=[]
    def judge(clip,video,bible,times,hypothesis,work,**kwargs):
        modes.append(evidence.review_mode(work))
        return {'severity':'pass','verify':{'verdict':'fine'},'feedback':''}
    monkeypatch.setattr(judges,'judge_clip',judge)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    assert execution.recheck_current_clip(directory,'a')['accepted']==['a']
    saved=read_json(directory/'episode_review.json')
    assert not current_review(saved,'verify')
    assert saved['clips']['b']==previous['clips']['b']
    current=review_episode.review_episode(directory,fresh=False)
    assert modes==['classic'] and current_review(current,'classic')
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    result=review_episode.review_episode(directory,fresh=False)
    assert modes==['classic','verify'] and current_review(result,'verify')
    assert_frozen(protected)


@pytest.mark.parametrize('case', ['picture', 'speech', 'extra_speech', 'confirmed'])
def test_keep_cannot_clear_picture_speech_or_human_confirmed_failure(episode, monkeypatch, case):
    directory, _, mixed, values, protected, _, _ = episode
    if case == 'picture':
        monkeypatch.setattr(judges, 'judge_clip', lambda *a, **kw: {
            'severity': 'fail', 'verify': {'verdict': 'obvious'}, 'feedback': '保留正确服装'})
    elif case in {'speech', 'extra_speech'}:
        media = read_json(directory / 'thin_media_report.json')
        issue = 'excess_unplanned_speech' if case == 'extra_speech' else 'missing_speech'
        media['clips'][0]['selected'].update(passed=False, issues=[issue])
        atomic_write_json(directory / 'thin_media_report.json', media)
        protected[directory / 'thin_media_report.json'] = (directory / 'thin_media_report.json').read_bytes()
    else:
        previous = read_json(directory / 'episode_review.json')
        take = execution.current_takes(directory, values['clip_plan.json'], previous)['a']
        previous['clips']['a']['confirmed'] = {**take, 'issue': '人工确认多出身体', 'instruction': '同框一个身体'}
        atomic_write_json(directory / 'episode_review.json', previous)
        monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **kw: pytest.fail('same human-confirmed take must not be cleared'))
    result = execution.recheck_current_clip(directory, 'a')
    assert result['accepted'] == [] and result['changed'] == [] and result['skip_render']
    assert result['review']['clips']['a']['severity'] == 'fail'
    assert 'a' in result['review']['feedback']
    assert result['review']['clips']['b'] == values['episode_review.json']['clips']['b']
    assert_frozen(protected)


@pytest.mark.parametrize('case', ['exception', 'review_error', 'take_changed'])
def test_incomplete_or_changed_take_keeps_previous_failure(episode, monkeypatch, case):
    directory, _, mixed, _, protected, _, observed = episode
    before = (directory / 'episode_review.json').read_bytes()
    def judge(*args, **kwargs):
        if case == 'exception':
            raise RuntimeError('review failed')
        if case == 'take_changed':
            mixed.write_bytes(b'a replacement video')
        return {'severity': 'review_error' if case == 'review_error' else 'pass'}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    with pytest.raises(ValueError):
        execution.recheck_current_clip(directory, 'a')
    assert (directory / 'episode_review.json').read_bytes() == before
    assert not observed
    assert_frozen(protected)
