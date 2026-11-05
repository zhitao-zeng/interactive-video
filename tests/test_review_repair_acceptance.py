"""Offline repair acceptance: resolving an old issue cannot certify a new defect."""
import copy

from novel_manga.application.repair.delivery import precise_review_blockers
from novel_manga.application.review.confirmed import apply_claim
from novel_manga.review.acceptance import evaluate_acceptance


def test_old_issue_resolved_but_new_defect_still_blocks_acceptance_and_publication():
    video, take = '/archive/returned-take.mp4', [1, 2, 3]
    current = {'video': video, 'take': take, 'severity': 'fail', 'tier': 'must_fix',
        'visual_defects': True, 'defect_issue': 'new cheek device in the rear-door shot',
        'verify': {'verdict': 'obvious'}, 'feedback': 'keep the rear-door face bare'}
    claim = {'video': '/archive/prior.mp4', 'take': [0, 0, 0],
        'issue': 'old golden stream', 'instruction': 'ordinary corridor light',
        'check': {'video': video, 'take': take, 'result': 'resolved'}}
    result = apply_claim(current, claim, video, take)
    assert result['severity'] == 'fail' and result['defect_issue'] == current['defect_issue']
    review = {'clips': {'a': result}, 'feedback': {'a': result['feedback']}}
    assert precise_review_blockers(review, {'a': {'video': video, 'take': take}}, ['a']) == ['a']
    assert not evaluate_acceptance({'a'}, {'assembly': {'thin_passed': True}}, review).passed


def test_current_specific_evidence_replaces_old_color_and_timestamp():
    video, take = '/archive/new.mp4', [1, 2, 3]
    claim = {'video': '/archive/old.mp4', 'take': [0, 0, 0],
        'issue': 'dark blue device at1.78s', 'instruction': 'bare cheeks',
        'check': {'video': video, 'take': take, 'result': 'unresolved',
                  'evidence': 'flesh-colored round patches at6.8s'}}
    result = apply_claim({'severity': 'pass'}, claim, video, take)
    assert result['defect_issue'] == 'flesh-colored round patches at6.8s'
    assert 'dark blue' not in result['defect_issue'] and '1.78' not in result['defect_issue']
    assert result['confirmed']['issue'] == 'dark blue device at1.78s'


def test_new_take_without_specific_check_is_pending_not_old_pixels_replayed_as_fact():
    claim = {'video': '/archive/old.mp4', 'take': [0, 0, 0],
             'issue': 'dark blue device at1.78s', 'instruction': 'bare cheeks'}
    result = apply_claim({'severity': 'pass'}, claim, '/archive/new.mp4', [1, 2, 3])
    assert 'pending' in result['defect_issue'] and '1.78' not in result['defect_issue']
    assert result['severity'] == 'fail'
