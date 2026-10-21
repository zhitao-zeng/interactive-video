"""Visual judgment, evidence extent and generation permission are distinct."""
import copy

from novel_manga.review.agent_contract import validate_review, episode_report


def sample(verdict='needs_repair', action='stop'):
    frames = [{'path': 'input/a/take-1/frames/first.jpg', 'seconds': 2.0},
              {'path': 'input/a/take-1/frames/later.jpg', 'seconds': 10.5}]
    primary = {'target_clips': ['a'], 'clips': {'a': {'current_frames': frames,
               'current_video': '/episode/current.mp4', 'take': [1, 2, 3],
               'remaining_video_takes': 0}}}
    review = {'clips': [{'clip_id': 'a', 'verdict': verdict, 'finding': 'actual picture',
              'evidence': [{'image': frames[0]['path'], 'seconds': 2.0, 'observation': 'visible device'}],
              'findings': [] if verdict == 'pass' else [{'kind': 'state_continuity', 'must_fix': True,
                    'start_seconds': 2.0, 'end_seconds': 2.0,
                    'evidence': [frames[0]['path']], 'summary': 'device present here'}],
              'decision_file': 'output/a/decision.json'}]}
    decisions = {'a': {'action': action, 'reason': 'grounded', 'evidence': [frames[0]['path']]}}
    return primary, review, decisions, {f['path'] for f in frames}


def test_pass_requires_keep_even_with_no_remaining_generations():
    primary, review, decisions, seen = sample('pass', 'stop')
    assert any('pass requires keep' in e for e in validate_review(primary, review, decisions, seen))
    decisions['a']['action'] = 'keep'
    assert validate_review(primary, review, decisions, seen) == []
    report = episode_report(primary, review, decisions)
    assert report['clips']['a']['severity'] == 'pass' and not report['feedback']


def test_one_positive_frame_does_not_support_a_whole_clip_defect_range():
    primary, review, decisions, seen = sample()
    review['clips'][0]['findings'][0].update(start_seconds=0.0, end_seconds=14.0)
    assert any('range exceeds' in e for e in validate_review(primary, review, decisions, seen))


def test_reference_or_other_take_cannot_be_current_picture_evidence():
    primary, review, decisions, seen = sample()
    other = 'input/other/take-1/frame.jpg'
    seen.add(other)
    review['clips'][0]['evidence'][0]['image'] = other
    assert any('current take' in e for e in validate_review(primary, review, decisions, seen))


def test_unread_frame_cannot_validate_a_claim():
    primary, review, decisions, seen = sample()
    seen.clear()
    assert any('not read' in e for e in validate_review(primary, review, decisions, seen))


def test_failed_content_stays_failed_when_generation_is_blocked():
    primary, review, decisions, seen = sample()
    assert validate_review(primary, review, decisions, seen) == []
    result = episode_report(primary, review, decisions)
    assert result['clips']['a']['severity'] == 'fail'
    assert result['clips']['a']['verify']['verdict'] == 'obvious'
    assert 'a' in result['feedback']
    decisions['a']['action'] = 'retake'
    assert any('generation unavailable' in e for e in validate_review(primary, review, decisions, seen))


def test_missing_review_is_incomplete_not_a_pass_or_a_retake():
    primary, review, decisions, seen = sample()
    review['clips'] = []
    assert any('missing review' in e for e in validate_review(primary, review, decisions, seen))
    result = episode_report(primary, review, decisions)
    assert result['clips']['a']['severity'] == 'review_error' and not result['feedback']


def test_harmless_notes_are_not_promoted_into_automatic_repairs():
    primary, review, decisions, seen = sample('pass', 'keep')
    review['clips'][0]['notes'] = ['small sign letters are indistinct but location and action remain clear']
    assert validate_review(primary, review, decisions, seen) == []
    assert episode_report(primary, review, decisions)['feedback'] == {}


def test_uncertain_cannot_trigger_retake_even_with_budget_available():
    primary, review, decisions, seen = sample('uncertain', 'retake')
    primary['clips']['a']['remaining_video_takes'] = 1
    review['clips'][0]['evidence'] = []
    assert any('uncertain requires stop' in e for e in validate_review(primary, review, decisions, seen))


def test_malformed_native_collections_return_contract_errors():
    primary, review, decisions, seen = sample()
    review['clips'][0]['findings'] = [None]
    decisions['a'] = []
    errors = validate_review(primary, review, decisions, seen)
    assert any('decision must be an object' in e for e in errors)
    assert any('findings must be an array' in e for e in errors)
