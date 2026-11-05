import json

from novel_manga.application.agents.episode import readiness
from novel_manga.application.agents.episode.prompts import resume_prompt
from support.first_frame_cases import case


def frame_status():
    return {'clips': {
        'clip_01': {'current': False, 'repair_origin': 'source',
                    'last_rejection': {'problems': ['two-shot conflicts with actor alone'],
                                       'evidence': 'input/first_frames/clip_01/review.json'},
                    'next_action': 'Use input repair: check then apply.'},
        'clip_02': {'current': False, 'repair_origin': 'image',
                    'next_action': 'Inspect the rejected image.'},
    }}


def test_source_conflict_routes_to_author_input_repair_before_more_images(tmp_path, monkeypatch):
    ctx, _ = case(tmp_path, monkeypatch)
    result = readiness.next_action(ctx, {'first_frames': frame_status()})
    assert result['action'] == 'agent'
    assert set(result['source_input_conflicts']) == {'clip_01'}
    assert 'check' in result['reason'] and 'apply' in result['reason']
    assert result['blocked'] == {'clip_01': ['two-shot conflicts with actor alone']}


def test_resolved_source_route_returns_to_first_frames(tmp_path, monkeypatch):
    ctx, _ = case(tmp_path, monkeypatch)
    status = frame_status()
    status['clips']['clip_01'].pop('repair_origin')
    result = readiness.next_action(ctx, {'first_frames': status})
    assert result['action'] == 'first_frames'
    assert 'source_input_conflicts' not in result


def test_local_preparation_failure_routes_to_agent_not_repeated_first_frames(tmp_path, monkeypatch):
    from novel_manga.util import atomic_write_json
    ctx, _ = case(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'blocked_clips': {'clip_02': 'speaker ownership conflicts with source'}})
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json', {
        'error': 'local_preparation_required', 'next': 'Repair these existing inputs with apply.'})
    from novel_manga.application.agents.episode.local_preparation import SOURCE_AUDIT_POLICY
    atomic_write_json(ctx.run_dir/'local-source-check.json', {'policy': SOURCE_AUDIT_POLICY})
    monkeypatch.setattr(readiness, 'first_frame_blocks',
                        lambda *a: (_ for _ in ()).throw(AssertionError('source block must precede first_frames')))
    result = readiness.next_action(ctx, {})
    assert result['action'] == 'agent'
    assert result['blocked'] == {'clip_02': 'speaker ownership conflicts with source'}
    assert 'check' in result['reason'] and 'apply' in result['reason']
    assert 'recheck_input' in result['reason']


def test_changed_source_policy_revalidates_before_agent_repair(tmp_path, monkeypatch):
    from novel_manga.util import atomic_write_json
    ctx, _ = case(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'blocked_clips': {'clip_02': 'speaker ownership conflicts with source'}})
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json', {
        'error': 'local_preparation_required', 'next': 'Repair these existing inputs with apply.'})
    atomic_write_json(ctx.run_dir/'local-source-check.json', {'policy': 'old-source-policy'})
    result = readiness.next_action(ctx, {})
    assert result['action'] == 'first_frames'
    assert 'changed' in result['reason']


def test_nested_input_wrapper_error_explains_actual_shots_protocol():
    import pytest
    from novel_manga.application.agents.episode.arguments import RepairArgumentError
    from novel_manga.application.agents.episode.input_contracts import normalize_arguments
    with pytest.raises(RepairArgumentError, match='shots.*index') as error:
        normalize_arguments({'action': 'input', 'input': {'speaker_map': {'知遥': '潮叔'}}})
    assert 'speaker_map' in str(error.value)


def test_resumed_source_repair_keeps_evidence_and_focuses_current_source_targets():
    conflicts = {'clip_01': frame_status()['clips']['clip_01']['last_rejection']}
    task = {'next_action': {'action': 'agent', 'reason': 'Use check then apply.',
                            'source_input_conflicts': conflicts, 'blocked': {'clip_01': ['source conflict']}},
            'targets': [{'clip_id': 'clip_01', 'actions': ['input']}, {'clip_id': 'clip_02', 'actions': ['input']}],
            'first_frames': frame_status()}
    prompt = resume_prompt(task)
    state = json.loads(prompt.split('Current actionable state:\n', 1)[1])
    assert state['next_action']['source_input_conflicts'] == conflicts
    assert [row['clip_id'] for row in state['targets']] == ['clip_01']
    assert set(state['first_frames']['clips']) == {'clip_01'}
