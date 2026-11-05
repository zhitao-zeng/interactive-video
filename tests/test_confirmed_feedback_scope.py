"""Resolving a confirmation removes only its own contribution to repair feedback."""
from novel_manga.application.review.confirmed import enforce
from novel_manga.util import atomic_write_json


def test_resolved_claim_leaves_only_the_independent_new_repair_note(tmp_path):
    atomic_write_json(tmp_path/'clip_plan.json', {'clips': [{'clip_id': 'a'}]})
    video, take = '/archive/new.mp4', [1, 2, 3]
    claim = {'video': '/archive/old.mp4', 'take': [0, 0, 0],
             'issue': 'old golden stream', 'instruction': 'ordinary corridor light',
             'check': {'video': video, 'take': take, 'result': 'unresolved', 'evidence': 'still golden'}}
    broad = {'video': video, 'take': take, 'severity': 'fail', 'tier': 'must_fix',
             'defect_issue': 'new cheek device', 'visual_defects': True,
             'visual_defect_kind': 'other', 'verify': {'verdict': 'obvious'}}
    report = enforce(tmp_path, {'clips': {'a': broad}, 'feedback': {'a': 'remove the new cheek device'}},
                     {'clips': {'a': {'confirmed': claim}}})
    assert report['feedback']['a'] == 'remove the new cheek device\nordinary corridor light'
    claim['check']['result'] = 'resolved'
    resolved = enforce(tmp_path, report, {'clips': {'a': {'confirmed': claim}}})
    assert resolved['feedback']['a'] == 'remove the new cheek device'
    assert resolved['clips']['a']['defect_issue'] == 'new cheek device'
    assert resolved['clips']['a']['severity'] == 'fail'


def test_repeated_unresolved_enforcement_does_not_accumulate_the_old_instruction(tmp_path):
    atomic_write_json(tmp_path/'clip_plan.json', {'clips': [{'clip_id': 'a'}]})
    video, take = '/archive/new.mp4', [1, 2, 3]
    claim = {'video': '/archive/old.mp4', 'take': [0, 0, 0], 'issue': 'old issue',
             'instruction': 'old acceptance\nwith a second line'}
    broad = {'video': video, 'take': take, 'severity': 'fail', 'tier': 'must_fix',
             'defect_issue': 'independent problem', 'verify': {'verdict': 'obvious'}}
    previous = {'clips': {'a': {'confirmed': claim}}}
    once = enforce(tmp_path, {'clips': {'a': broad}, 'feedback': {'a': 'independent repair'}}, previous)
    twice = enforce(tmp_path, once, previous)
    assert twice['feedback'] == once['feedback']

