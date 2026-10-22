"""Final-cut severity grading: only must_fix blocks; prompt-tier notes never re-open repair.

Regression basis: backrooms55 EP02-A, 2026-10-22 — the single-boolean gate let a
headlamp state note (bible carried EP01's "not worn until S20" shape into an
episode whose script lights it from shot 1) block acceptance and cycle the
repair agent.  With severity grading that finding is minor and non-blocking.
"""
from novel_manga.application.review import final_cut


def _selected():
    return [{'clip_id': 'clip_01'}, {'clip_id': 'clip_02'}]


def _issue(severity, clip='clip_01'):
    return {'clip_ids': [clip], 'frame_numbers': [1], 'severity': severity,
            'problem': 'S11 头灯亮着', 'evidence': 'frame 1 灯珠发亮',
            'source_basis': 'script S11 start_state 头灯已关'}


def test_minor_issue_does_not_block_passage():
    answer = {'passed': True, 'observations': ['画面与该镜请求一致'],
              'issues': [_issue('minor')]}
    final_cut.validate_group(answer, _selected())


def test_must_fix_issue_forces_failure():
    answer = {'passed': True, 'observations': ['ok'],
              'issues': [_issue('must_fix')]}
    try:
        final_cut.validate_group(answer, _selected())
    except ValueError as error:
        assert 'conflicting passage' in str(error)
    else:
        raise AssertionError('a must_fix issue must force passed=false')


def test_minor_only_with_passed_false_is_conflicting():
    answer = {'passed': False, 'observations': ['ok'],
              'issues': [_issue('minor')]}
    try:
        final_cut.validate_group(answer, _selected())
    except ValueError as error:
        assert 'conflicting passage' in str(error)
    else:
        raise AssertionError('minor-only issues must not claim passed=false')


def test_missing_severity_is_rejected():
    # A severity-less issue is protocol-invalid for a reviewer answer; with
    # passed=true it also trips the passage identity first (severity-less
    # issues stay blocking by the code-added compatibility rule).
    issue = _issue('minor'); issue.pop('severity')
    answer = {'passed': True, 'observations': ['ok'], 'issues': [issue]}
    try:
        final_cut.validate_group(answer, _selected())
    except ValueError as error:
        assert 'conflicting passage' in str(error) or 'severity' in str(error)
    else:
        raise AssertionError('reviewer issues must carry a severity grade')


def test_code_added_issues_without_severity_stay_blocking():
    # ambience / audio / independent issues carry no severity field; they must
    # keep blocking exactly as before the grading change.
    assert final_cut.blocking_issues([{'problem': 'x'}]) == [{'problem': 'x'}]
    assert final_cut.blocking_issues([_issue('must_fix')])
    assert final_cut.blocking_issues([_issue('minor')]) == []
    assert final_cut.blocking_issues([]) == []


def test_prompt_tier_note_never_feeds_repair_feedback():
    review = {'clips': {'clip_01': {}, 'clip_02': {}}, 'feedback': {}}
    final = {'complete': True, 'passed': True, 'movie': 'x.mp4', 'context': {},
             'issues': [_issue('minor')], 'audio_review': None}
    merged = final_cut.apply_feedback(None, review, final)
    assert merged['final_cut_feedback'] == {}
    assert 'final_cut' not in merged['clips']['clip_01']


def test_must_fix_note_still_feeds_repair_feedback():
    review = {'clips': {'clip_01': {}, 'clip_02': {}}, 'feedback': {}}
    final = {'complete': True, 'passed': False, 'movie': 'x.mp4', 'context': {},
             'issues': [_issue('must_fix')], 'audio_review': None}
    merged = final_cut.apply_feedback(None, review, final)
    assert 'clip_01' in merged['final_cut_feedback']
    assert merged['clips']['clip_01']['final_cut']['issues'][0]['severity'] == 'must_fix'


def test_ambience_issue_is_must_fix():
    issues = [{'clip_ids': ['a'], 'problem': 'p', 'evidence': '{}',
               'source_basis': 's', 'frame_paths': []}]
    # shape check only: the code-added ambience issue is born must_fix
    source = {'kind': 'not_independent', 'clip_id': 'a', 'start': 0, 'end': 3}
    media = {'clips': [{'clip_id': 'a', 'selected': {'chunks': [
        {'hypothesis': '你好', 'start': 0, 'end': 1}]}}],
        'assembly': {'timeline': [{'unit_id': 'a', 'start': 0, 'end': 15}]}}
    receipt = {'ambience_source': source}
    rows = final_cut.ambience_speech_issues(media, receipt, [])
    assert rows and rows[0]['severity'] == 'must_fix'
