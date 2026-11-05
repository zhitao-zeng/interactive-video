from types import SimpleNamespace

from novel_manga.application.agents.episode import progress


def state(job_id, invalid, *, targets=('clip_01', 'clip_02'), wording='invalid'):
    return {'job': {'id': job_id, 'action': 'first_frames', 'status': 'completed', 'result': {
        'ok': False,
        'validation_targets': list(targets),
        'validated_clips': [cid for cid in targets if cid not in invalid],
        'invalid_fields': invalid,
        'argument_errors': {cid: [wording] for cid in invalid},
    }}}


def check(tmp_path, before, after):
    ctx = SimpleNamespace(work_dir=tmp_path/'work', run_dir=tmp_path/'run')
    return progress.made_progress(ctx, progress.progress(ctx, before), before, after)


def test_correcting_source_owned_fields_counts_even_if_every_row_still_has_a_bad_reference(tmp_path):
    before = state('one', {cid: ['image_prompt', 'extra_references'] for cid in ('clip_01', 'clip_02')})
    after = state('two', {cid: ['extra_references'] for cid in ('clip_01', 'clip_02')})
    assert before['job']['result']['validated_clips'] == after['job']['result']['validated_clips'] == []
    assert check(tmp_path, before, after)


def test_rewording_or_replacing_one_bad_field_is_not_progress(tmp_path):
    before = state('one', {'clip_01': ['extra_references'], 'clip_02': ['extra_references']})
    assert not check(tmp_path, before, state('two', before['job']['result']['invalid_fields'], wording='different wording'))
    assert not check(tmp_path, before, state('two', {'clip_01': ['image_prompt'], 'clip_02': ['extra_references']}))


def test_dropping_target_cannot_hide_its_invalid_fields(tmp_path):
    before = state('one', {'clip_01': ['image_prompt', 'extra_references'], 'clip_02': ['extra_references']})
    after = state('two', {'clip_01': ['extra_references']}, targets=('clip_01',))
    assert not check(tmp_path, before, after)


def test_old_results_without_field_evidence_do_not_invent_progress(tmp_path):
    before = state('one', {'clip_01': ['image_prompt', 'extra_references'], 'clip_02': ['extra_references']})
    before['job']['result'].pop('invalid_fields')
    after = state('two', {'clip_01': ['extra_references'], 'clip_02': ['extra_references']})
    assert not check(tmp_path, before, after)


def test_source_preflight_after_argument_correction_allows_one_repair_turn(tmp_path):
    before = state('one', {'clip_01': ['extra_references']})
    after = {'job': {'id': 'two', 'action': 'first_frames', 'status': 'completed', 'result': {
        'ok': False, 'error': 'opening_source_conflict',
        'source_input_conflicts': {'clip_01': [{'basis': ['opening.start_state', 'opening.opening_frame.image_prompt']}]}}}}
    assert check(tmp_path, before, after)
    repeated = {'job': {**after['job'], 'id': 'three'}}
    assert not check(tmp_path, after, repeated)
    assert not check(tmp_path, after, state('four', {'clip_01': ['extra_references']}))


def test_local_preparation_requires_one_agent_repair_turn(tmp_path):
    before = state('one', {'clip_01': ['extra_references']})
    after = {'job': {'id': 'two', 'action': 'first_frames', 'status': 'completed', 'result': {
        'ok': False, 'error': 'local_preparation_required', 'blocked': {'clip_01': 'wrong location'}}}}
    assert check(tmp_path, before, after)
    repeated = {'job': {**after['job'], 'id': 'three'}}
    assert not check(tmp_path, after, repeated)
    assert not check(tmp_path, after, state('four', {'clip_01': ['extra_references']}))
