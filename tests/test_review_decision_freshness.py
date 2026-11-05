"""A saved Agent action remains valid only for the source/assets it reviewed."""
from pathlib import Path

import pytest

from novel_manga.application.repair import agent, history
from novel_manga.util import atomic_write_json
from support.managed_episode import fixture_episode


def reviewed(tmp_path, *, actual_reference=False):
    directory, clips, previous = fixture_episode(tmp_path)
    clip = clips[0]
    reference = directory.parent/'character.jpg'
    reference.write_bytes(b'original character reference')
    clip.update(shot_indexes=[1], segment_ids=['one'],
                references=[{'role': 'character', 'name': 'person', 'path': 'character.jpg'}])
    if actual_reference:
        reference = directory.parent/'submitted-character.jpg'
        reference.write_bytes(b'actual submitted character reference')
        atomic_write_json(Path(previous['a']['video']).parent/'request.json',
                          {'references': [str(reference)]})
    shots = [{'index': 1, 'event': 'badge is removed'}]
    passages = [{'segment_id': 'one', 'text': 'The badge was already removed.'}]
    atomic_write_json(directory/'chapter_script.json', {'shots': shots})
    atomic_write_json(directory/'segments.json', passages)
    stat = reference.stat()
    row = {**previous['a'], 'review_backend': 'sandbox_agent', 'agent_protocol_valid': True,
           'agent_decision': {'action': 'retake', 'reason': 'badge remains visible', 'evidence': ['current.jpg']},
           'agent_clip_snapshot': history.accepted_clip_material(clip), 'agent_source_snapshot': shots,
           'agent_input_snapshot': {'segment_ids': ['one'], 'source_passages': passages,
                                    'reference_stats': [{'path': str(reference), 'stat': [stat.st_mtime_ns, stat.st_size]}]},
           'agent_budget_snapshot': {'generation_count': history.generation_counts(directory).get('a', 0),
                                     'generation_limit': history.generation_limit(directory, 'a')},
           'agent_notes_snapshot': ''}
    return directory, clip, row, reference


@pytest.mark.parametrize('change', ['passage', 'source_address', 'reference', 'added_neighbour'])
def test_changed_review_inputs_require_fresh_diagnosis(tmp_path, change):
    directory, clip, row, reference = reviewed(tmp_path)
    assert agent.reviewed_decision(directory, clip, row) == row['agent_decision']
    if change == 'passage':
        atomic_write_json(directory/'segments.json', [{'segment_id': 'one', 'text': 'The badge is worn deliberately.'}])
    elif change == 'source_address':
        clip['segment_ids'] = ['two']
    elif change == 'reference':
        reference.write_bytes(b'a changed character card with a different badge')
    else:
        atomic_write_json(directory/'chapter_script.json', {'shots': [*row['agent_source_snapshot'],
            {'index': 2, 'event': 'the badge is deliberately replaced'}]})
    assert agent.reviewed_decision(directory, clip, row) is None


def test_submitted_reference_change_invalidates_review_even_when_plan_reference_is_unchanged(tmp_path):
    directory, clip, row, reference = reviewed(tmp_path, actual_reference=True)
    assert agent.reviewed_decision(directory, clip, row) == row['agent_decision']
    reference.write_bytes(b'the actual submitted card has been replaced')
    assert agent.reviewed_decision(directory, clip, row) is None


@pytest.mark.parametrize('changed', [{'video': '/different.mp4'}, {'take': [7, 8, 9]}, {'video': None, 'take': None}])
def test_resolved_confirmation_must_belong_to_reviewed_take(tmp_path, changed):
    directory, clip, row, reference = reviewed(tmp_path)
    row['confirmed'] = {'check': {'result': 'resolved', 'video': row['video'], 'take': row['take']}}
    assert agent.reviewed_decision(directory, clip, row) == row['agent_decision']
    row['confirmed']['check'].update(changed)
    assert agent.reviewed_decision(directory, clip, row) is None


def test_old_report_without_source_asset_binding_is_not_reused(tmp_path):
    directory, clip, row, reference = reviewed(tmp_path)
    row.pop('agent_input_snapshot')
    assert agent.reviewed_decision(directory, clip, row) is None


def test_binding_snapshot_matches_actual_submitted_inputs(tmp_path):
    directory, clip, row, reference = reviewed(tmp_path, actual_reference=True)
    assert agent.review_input_snapshot(directory, clip, Path(row['video'])) == row['agent_input_snapshot']


def test_unrelated_passage_does_not_invalidate_local_review(tmp_path):
    directory, clip, row, reference = reviewed(tmp_path)
    atomic_write_json(directory/'segments.json', [*row['agent_input_snapshot']['source_passages'],
                      {'segment_id': 'elsewhere', 'text': 'An unrelated later scene.'}])
    assert agent.reviewed_decision(directory, clip, row) == row['agent_decision']

