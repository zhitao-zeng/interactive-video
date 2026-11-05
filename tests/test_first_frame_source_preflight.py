import copy
import json

import pytest
from PIL import Image

from novel_manga.application.agents.episode import first_frames
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


def finding(relation='contradiction'):
    return {'origin': 'source', 'relation': relation,
            'observed': 'Source shot_scale requests a wide establishing view',
            'expected': 'Source camera and opening request a medium selfie',
            'basis': ['opening.shot_scale', 'opening.camera', 'opening.opening_frame.image_prompt'],
            'reason': 'The opening requirements cannot hold simultaneously.'}


def conflicting_case(tmp_path, monkeypatch):
    ctx, clips = case(tmp_path, monkeypatch)
    shot = clips[0]['shots'][0]
    shot.update(camera='Front-facing phone-height selfie medium shot',
                shot_scale='wide establishing', in_frame=['知遥'],
                opening_frame={'image_prompt': 'Zhiyáo alone in a medium selfie; no second person.',
                               'visible_characters': ['知遥'], 'reference_subjects': ['pool']})
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    draft = read_json(ctx.work_dir/'output/first-frames.json')
    draft['frames'][0].update(image_prompt=shot['opening_frame']['image_prompt'],
                              visible_characters=['知遥'])
    atomic_write_json(ctx.work_dir/'output/first-frames.json', draft)
    return ctx, clips


@pytest.mark.parametrize('render_ready', [False, True])
def test_source_conflict_blocks_entire_batch_before_candidate_allocation(tmp_path, monkeypatch, render_ready):
    ctx, clips = conflicting_case(tmp_path, monkeypatch)
    seen = []
    def source_review(context, prepared):
        from novel_manga.application.agents.episode import opening_source
        seen.extend(row['clip_id'] for row, _, _ in prepared)
        reviews = {'clip_01': {'findings': [finding()]}, 'clip_02': {'findings': []}}
        opening_source.remember(context, prepared, reviews)
        return reviews
    monkeypatch.setattr(first_frames, 'review_opening_sources', source_review)
    result = first_frames.FirstFrameOperations(
        ctx, builder=lambda *a: pytest.fail('source conflict must not allocate a paid image'),
        checker=lambda *a: pytest.fail('source conflict must not reach image review')).prepare(
            {'render_ready': render_ready})
    assert not result['ok'] and result['error'] == 'opening_source_conflict'
    assert seen == ['clip_01', 'clip_02']
    assert set(result['source_input_conflicts']) == {'clip_01'}
    assert not (ctx.novel_dir/'first_frames').exists()
    row = result['first_frames']['clips']['clip_01']
    assert row['repair_origin'] == 'source'
    assert row['last_rejection']['source_conflicts'] == [finding()]
    assert 'input repair' in row['next_action']


@pytest.mark.parametrize('relation', ['compatible', 'underspecified'])
def test_only_explicit_source_contradiction_blocks_generation(tmp_path, monkeypatch, relation):
    ctx, clips = case(tmp_path, monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [clips[0]]})
    draft = read_json(ctx.work_dir/'output/first-frames.json')
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': [draft['frames'][0]]})
    monkeypatch.setattr(first_frames, 'review_opening_sources',
                        lambda *a: {'clip_01': {'findings': [finding(relation)]}})
    result = first_frames.FirstFrameOperations(
        ctx, builder=lambda c, r, p: Image.new('RGB', (8, 16)).save(p),
        checker=lambda *a: {'consistent': True, 'problems': []}).prepare({})
    assert result['ok']


def test_opening_fallback_has_one_authority(tmp_path, monkeypatch):
    from novel_manga.media.first_frames import current_contract, matches_current_contract, unchanged_opening_scale
    ctx, clips = case(tmp_path, monkeypatch)
    clip = clips[0]
    fallback = copy.deepcopy(clip['shots'][0])
    fallback.update(start_state='accepted snapshot', shot_scale='wide',
                    opening_frame={'image_prompt': 'snapshot prompt', 'visible_characters': []})
    clip['first_frame_opening'] = [fallback]
    clip['shots'] = [{**clip['shots'][0], 'start_state': 'stale packed shot', 'shot_scale': 'close',
                      'opening_frame': {'image_prompt': 'stale prompt', 'visible_characters': []}}]
    assert first_frames.source_opening(ctx, clip) == fallback['opening_frame']
    contract = current_contract(ctx, clip, [])
    assert contract['opening']['start_state'] == fallback['start_state']
    assert contract['opening']['shot_scale'] == fallback['shot_scale']
    assert matches_current_contract(ctx, clip, contract)
    assert unchanged_opening_scale(clip, contract)
    from novel_manga.media.first_frames import opening_shot
    assert opening_shot(clip) == fallback


def test_source_review_cache_is_bound_to_exact_opening(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import opening_source
    from novel_manga.media.first_frames import current_contract
    ctx, clips = case(tmp_path, monkeypatch)
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(parts)
        payload = json.loads(parts[0]['text'].rsplit('\n', 1)[1])
        return {'reviews': [{'clip_id': row['clip_id'],
                             'findings': [{**finding(),'observed':'','expected':''}]
                                 if row['opening'].get('shot_scale') == 'wide' else []}
                            for row in payload['clips']]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    shot = copy.deepcopy(clips[0]['shots'][0])
    shot.update(shot_scale='wide',camera='medium selfie',
                storyboard={'frame': '零点只有A、B两卡', 'action_beats': ['镜内加入C']})
    prepared = [({'clip_id': 'clip_01'}, current_contract(ctx, clips[0], [], shots=[shot]), [])]
    first = opening_source.review_batch(ctx, prepared)
    second = opening_source.review_batch(ctx, prepared)
    assert first == second and not first['clip_01']['consistent'] and len(calls) == 1
    changed_shot = copy.deepcopy(shot)
    changed_shot['shot_scale'] = 'medium'
    changed = [({'clip_id': 'clip_01'}, current_contract(ctx, clips[0], [], shots=[changed_shot]), [])]
    changed_review = opening_source.review_batch(ctx, changed)
    assert len(calls) == 2 and changed_review['clip_01']['consistent']
    assert opening_source.current_review(ctx, 'clip_01', changed[0][1]['opening'])['consistent']
    assert not opening_source.current_review(ctx, 'clip_01', prepared[0][1]['opening'])['consistent']
    frame_changed_shot = copy.deepcopy(changed_shot)
    frame_changed_shot['storyboard']['frame'] = '零点已有A、B、C三卡'
    frame_changed = [({'clip_id': 'clip_01'},
                      current_contract(ctx, clips[0], [], shots=[frame_changed_shot]), [])]
    opening_source.review_batch(ctx, frame_changed)
    assert len(calls) == 3
    assert 'action_beats' not in frame_changed[0][1]['opening']['storyboard']


def test_source_review_is_text_only_and_requires_time_zero_field_evidence(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import opening_source
    ctx, clips = case(tmp_path, monkeypatch)
    captured = {}
    def ask(parts, schema, **kwargs):
        captured.update(parts=parts, schema=schema)
        return {'reviews': [{'clip_id': 'clip_01', 'findings': []}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    opening_source.review_batch(ctx, [({'clip_id': 'clip_01'}, {'opening': {'camera': 'wide'}}, [])])
    text = captured['parts'][0]['text']
    assert 't=0' in text and 'trigger' in text and '至少两个' in text
    assert len(captured['parts']) == 1 and captured['parts'][0]['type'] == 'text'
    properties = captured['schema']['properties']['reviews']['items']['properties']
    assert properties['findings']['items']['properties']['origin']['enum'] == ['source']


def test_skill_documents_existing_input_fields_and_storyboard_is_executable():
    from pathlib import Path
    from novel_manga.application.agents.episode.input_contracts import SHOT_FIELDS
    text = (Path(__file__).parents[1]/'skills/episode-production/references/output-contract.md').read_text()
    section = text.split('For a confirmed source/input defect', 1)[1].split('After adoption, `characters`', 1)[0]
    documented = set(__import__('re').findall(r'`([a-z_]+)`', section))
    assert SHOT_FIELDS - {'storyboard'} <= documented
    assert 'storyboard' in SHOT_FIELDS


def remember_conflict(ctx, clip):
    from novel_manga.application.agents.episode import opening_source
    from novel_manga.media.first_frames import current_contract
    opening = current_contract(ctx, clip, [])['opening']
    prepared = [({'clip_id': clip['clip_id']}, {'opening': opening}, [])]
    opening_source.remember(ctx, prepared, {clip['clip_id']: {'findings': [finding()]}})


def test_cached_conflict_routes_to_input_even_when_frame_is_current(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import readiness
    ctx, clips = conflicting_case(tmp_path, monkeypatch)
    remember_conflict(ctx, clips[0])
    monkeypatch.setattr(readiness, 'first_frame_blocks', lambda *a: {})
    result = readiness.next_action(ctx, {})
    assert result['action'] == 'agent'
    assert set(result['source_input_conflicts']) == {'clip_01'}


def test_direct_render_cannot_bypass_cached_source_conflict(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.media_operations import MediaOperations
    ctx, clips = conflicting_case(tmp_path, monkeypatch)
    remember_conflict(ctx, clips[0])
    operation = object.__new__(MediaOperations)
    operation.ctx = ctx
    result = operation.render({}, repairs=None)
    assert not result['ok'] and result['error'] == 'opening_source_conflict'
    assert set(result['source_input_conflicts']) == {'clip_01'}


@pytest.mark.parametrize('malformed', [
    {'observed': 'missing required verdict fields'},
    {**finding(), 'origin': 'image'},
    {**finding(), 'basis': ['opening.camera']},
])
def test_malformed_source_finding_fails_closed_and_is_not_cached(tmp_path, monkeypatch, malformed):
    from novel_manga.application.agents.episode import opening_source
    ctx, clips = case(tmp_path, monkeypatch)
    monkeypatch.setattr('novel_manga.llm.client.ask_json',
                        lambda *a, **kw: {'reviews': [{'clip_id': 'clip_01', 'findings': [malformed]}]})
    with pytest.raises(ValueError, match='invalid opening source finding'):
        opening_source.review_batch(ctx, [({'clip_id': 'clip_01'}, {'opening': {'camera': 'wide'}}, [])])
    assert not (ctx.run_dir/'opening-source-checks.json').exists()


def test_unbuilt_reference_does_not_hide_opening_conflict_or_spend_image(tmp_path, monkeypatch):
    from novel_manga.media.first_frames import compile_opening
    ctx, clips = conflicting_case(tmp_path, monkeypatch)
    (ctx.novel_dir/'scene.png').unlink()
    deferred = compile_opening(ctx, clips[0], {'clip_id': 'clip_01'}, require_assets=False)
    assert deferred.references[0].hash == ''
    assert deferred.assets[0]['missing'] is True
    with pytest.raises(FileNotFoundError):
        compile_opening(ctx, clips[0], {'clip_id': 'clip_01'})
    seen = []
    def source_review(context, prepared):
        from novel_manga.application.agents.episode import opening_source
        seen.extend(row['clip_id'] for row, _, _ in prepared)
        reviews = {'clip_01': {'findings': [finding()]}, 'clip_02': {'findings': []}}
        opening_source.remember(context, prepared, reviews)
        return reviews
    monkeypatch.setattr(first_frames, 'review_opening_sources', source_review)
    result = first_frames.FirstFrameOperations(
        ctx, builder=lambda *a: pytest.fail('source conflict must not allocate an image'),
        checker=lambda *a: pytest.fail('source conflict must not reach image review')).prepare({})
    assert result['error'] == 'opening_source_conflict'
    assert seen == ['clip_01', 'clip_02']


def test_storyboard_review_evidence_does_not_expand_actual_first_frame_prompt():
    from novel_manga.media.first_frames import OPENING_FIELDS, opening_contract, request_prompt
    shot = {'start_state': '实际起点', 'event': 'DURING_EVENT_MUST_NOT_LEAK',
            'opening_frame': {'image_prompt': 'ACTUAL_OPENING_IMAGE_PROMPT'},
            'storyboard': {'frame': 'STORYBOARD_FRAME_MUST_NOT_LEAK',
                           'action_beats': ['ACTION_BEAT_MUST_NOT_LEAK']}}
    contract = opening_contract({'shots': [shot], 'references': []}, [])
    assert 'storyboard' not in OPENING_FIELDS
    assert contract['opening']['storyboard'] == {'frame': 'STORYBOARD_FRAME_MUST_NOT_LEAK'}
    prompt = request_prompt(None, {'image_prompt': 'ACTUAL_OPENING_IMAGE_PROMPT'}, contract)
    assert 'ACTUAL_OPENING_IMAGE_PROMPT' in prompt and '实际起点' in prompt
    assert 'STORYBOARD_FRAME_MUST_NOT_LEAK' not in prompt
    assert 'ACTION_BEAT_MUST_NOT_LEAK' not in prompt
    assert 'DURING_EVENT_MUST_NOT_LEAK' not in prompt


def test_skill_post_adoption_field_list_matches_real_input_contract():
    from pathlib import Path
    from novel_manga.application.agents.episode.input_contracts import SHOT_FIELDS
    text = (Path(__file__).parents[1]/'skills/episode-production/references/output-contract.md').read_text()
    section = text.split('For a confirmed source/input defect', 1)[1].split('After adoption, `characters`', 1)[0]
    documented = set(__import__('re').findall(r'`([a-z_]+)`', section))
    assert SHOT_FIELDS <= documented

