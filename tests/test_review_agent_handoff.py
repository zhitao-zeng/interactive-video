"""A validated review's repair decision reaches the existing repair flow unchanged."""
import json
import copy
from pathlib import Path

from support.managed_episode import fixture_episode
from novel_manga.application.repair import agent, history, managed
from novel_manga.application.profiles import h3_stamp
from novel_manga.review.agent_contract import episode_report
from novel_manga.story.h3 import compose
from novel_manga.util import atomic_write_json


def test_validated_review_reuses_same_agent_decision_without_a_second_diagnosis(tmp_path, monkeypatch):
    d, clips, previous = fixture_episode(tmp_path)
    for c in clips:
        c.update(shot_indexes=[1], prompt_h3=compose(c, ['A person stands by the door.'], [('', [])]))
        c['prompt_h3_of'] = h3_stamp(c)
    atomic_write_json(d/'clip_plan.json', {'clips': clips})
    atomic_write_json(d.parent/'profile.json', {'repair_backend': 'sandbox_agent'})
    shots = [{'index': 1, 'event': 'stand by the door'}]
    atomic_write_json(d/'chapter_script.json', {'shots': shots})
    history.adopt_reviewed_run(d)
    counts = history.generation_counts(d)
    c = clips[0]
    primary = {'target_clips': ['a'], 'clips': {'a': {
        'current_video': previous['a']['video'], 'take': previous['a']['take'],
        'clip_snapshot': history.accepted_clip_material(c), 'shots_and_neighbours': shots,
        'review_input_snapshot': agent.review_input_snapshot(d, c, Path(previous['a']['video'])),
        'generation_count': counts.get('a', 0), 'generation_limit': history.generation_limit(d, 'a')}}}
    decision = {'action': 'edit', 'reason': 'clarify visible state', 'evidence': ['current frame'],
                'prompt': c['prompt_h3'].replace('stands by', 'stands calmly by'),
                'run': 'one-review-session', 'attempt': 'one'}
    report = episode_report(primary, {'clips': [{'clip_id': 'a', 'verdict': 'needs_repair',
        'finding': 'actual visible defect', 'evidence': [], 'findings': [{'kind': 'other', 'must_fix': True}]}]}, {'a': decision})
    report['clips']['a'].update(agent_protocol_valid=True, agent_notes_snapshot='')
    report['agent_execution'] = {'status': 'complete'}
    atomic_write_json(d/'episode_review.json', report)
    row = report['clips']['a']
    assert agent.reviewed_decision(d, c, row)['action'] == 'edit'
    stale = copy.deepcopy(row); stale['agent_budget_snapshot']['generation_limit'] += 1
    assert agent.reviewed_decision(d, c, stale) is None
    changed = copy.deepcopy(c); changed['prompt_h3'] += '\nA changed input.'
    assert agent.reviewed_decision(d, changed, row) is None
    atomic_write_json(d/'chapter_script.json', {'shots': [{'index': 1, 'event': 'a different action'}]})
    assert agent.reviewed_decision(d, c, row) is None
    atomic_write_json(d/'chapter_script.json', {'shots': shots})
    monkeypatch.setattr(agent, 'propose', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must reuse reviewed decision')))
    monkeypatch.setattr(agent, 'check_request', lambda *a: {'consistent': True, 'problems': []})
    result = managed.prepare(d, ['a'])
    assert result['changed'] == ['a'] and history.generation_counts(d) == counts
    assert 'stands calmly by' in json.loads((d/'clip_plan.json').read_text())['clips'][0]['prompt_h3']
    assert history.load(d)['trials'][-1]['changes']['a']['run'] == 'one-review-session'


def test_incomplete_agent_review_cannot_become_a_blind_retake(tmp_path):
    d, clips, previous = fixture_episode(tmp_path)
    atomic_write_json(d/'episode_review.json', {'review_backend': 'sandbox_agent',
        'agent_execution': {'status': 'incomplete'}, 'clips': previous, 'feedback': {'a': 'old issue'}})
    wanted, blocked = managed.candidates(d)
    assert wanted == [] and 'Agent review incomplete' in blocked['a']
