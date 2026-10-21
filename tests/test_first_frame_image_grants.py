"""An explicit image grant adds one clip's capacity without touching take authority."""
import copy

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames as first_frame_module
from novel_manga.application.agents.episode.first_frames import FirstFrameOperations, status
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


@pytest.fixture
def spent_frames(tmp_path, monkeypatch):
    ctx, clips = case(tmp_path, monkeypatch)
    rows = read_json(ctx.work_dir/'output/first-frames.json')['frames']
    from novel_manga.media.first_frames import REVIEW_POLICY, current_contract, source_asset_state
    review_inputs={}
    for clip in clips:
        row=first_frame_module.authored_row(ctx,clip,next(r for r in rows if r['clip_id']==clip['clip_id']))
        contract=current_contract(ctx,clip,row['image_references'],include_style=True)
        review_inputs[clip['clip_id']]={'policy':REVIEW_POLICY,'source_contract':contract,
            'source_assets':source_asset_state(ctx,row['image_references']),
            'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters']}
    roots = {}
    for clip in clips:
        cid = clip['clip_id']
        root = ctx.novel_dir/'first_frames'/ctx.episode_dir.name/cid
        roots[cid] = root
        for index in range(1, 4):
            candidate = root/f'candidate_{index:02d}'
            atomic_write_json(candidate/'input.json', {'retained_opening': index})
            atomic_write_json(candidate/'opening.png.task.json', {'task_id': f'retained-{cid}-{index}'})
            atomic_write_json(candidate/'rejected.json', {'error': 'earlier opening was rejected',
                'review': {'consistent': False, 'problems': ['visible pixel defect']}})
            atomic_write_json(candidate/'reviews/review_001.json',{'input':review_inputs[cid],
                'review':{'consistent':False,'problems':['visible pixel defect']}})
            Image.new('RGB', (8, 16)).save(candidate/'opening.png')
    retained = {p: p.read_bytes() for root in roots.values() for p in root.rglob('*') if p.is_file()}
    calls = []

    def build(ctx, row, path):
        calls.append((row['clip_id'], path.parent.name))
        atomic_write_json(path.with_suffix('.png.task.json'), {'task_id': 'simulated-paid-image'})
        Image.new('RGB', (8, 16)).save(path)

    checker = lambda *args: {'consistent': True, 'problems': []}
    return ctx, rows, roots, retained, calls, build, checker


def grant_image(ctx, value):
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {'clip_01': {'first_frame': value}})


def save_current_review(ctx, rows, candidate, review):
    from novel_manga.media.first_frames import REVIEW_POLICY, current_contract, source_asset_state
    clip=next(c for c in read_json(ctx.episode_dir/'clip_plan.json')['clips'] if c['clip_id']==rows[0]['clip_id'])
    row=first_frame_module.authored_row(ctx,clip,rows[0])
    contract=current_contract(ctx,clip,row['image_references'],include_style=True)
    review_input={'policy':REVIEW_POLICY,'source_contract':contract,
        'source_assets':source_asset_state(ctx,row['image_references']),
        'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters']}
    atomic_write_json(candidate/'reviews/review_002.json',{'input':review_input,'review':review})


def test_default_fourth_image_is_blocked_with_zero_remaining(spent_frames):
    ctx, _, roots, retained, calls, build, checker = spent_frames
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert not result['ok'] and calls == []
    assert all(row['error'] == 'first-frame image candidate budget used' for row in result['clips'].values())
    assert all(row['remaining_image_candidates'] == 0 for row in result['first_frames']['clips'].values())
    assert all(not (root/'candidate_04').exists() for root in roots.values())
    assert all(path.read_bytes() == data for path, data in retained.items())


def test_explicit_clip_grant_allows_only_fourth_and_resume_never_refills(spent_frames):
    ctx, rows, roots, retained, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'corrected source opening', 'limit': 4})
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert result['clips']['clip_01']['ok'] is True
    assert result['clips']['clip_02']['error'] == 'first-frame image candidate budget used'
    assert calls == [('clip_01', 'candidate_04')]
    assert result['first_frames']['clips']['clip_01']['image_candidate_limit'] == 4
    assert result['first_frames']['clips']['clip_02']['image_candidate_limit'] == 3
    assert result['first_frames']['clips']['clip_01']['remaining_image_candidates'] == 0
    assert all(path.read_bytes() == data for path, data in retained.items())

    # A new operation simulates a resumed job: its immutable request and receipt are reused.
    receipt = roots['clip_01']/'candidate_04/opening.png.task.json'
    before = receipt.read_bytes()
    resumed = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert resumed['clips']['clip_01']['ok'] is True
    assert calls == [('clip_01', 'candidate_04')] and receipt.read_bytes() == before

    rows[0]['image_prompt'] = 'A different source-supported opening request.'
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    changed = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert changed['clips']['clip_01']['error'] == 'first-frame image candidate budget used'
    assert calls == [('clip_01', 'candidate_04')]
    assert not (roots['clip_01']/'candidate_05').exists()


@pytest.mark.parametrize('missing', ['authorization', 'reason'])
def test_incomplete_image_authorization_does_not_raise_capacity(spent_frames, missing):
    ctx, _, _, _, calls, build, checker = spent_frames
    grant = {'authorization': 'user-approved', 'reason': 'corrected source opening', 'limit': 4}
    grant.pop(missing)
    grant_image(ctx, grant)
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert not result['clips']['clip_01']['ok'] and calls == []
    assert result['first_frames']['clips']['clip_01']['image_candidate_limit'] == 3


def test_only_clip_image_authority_changes_status_and_no_video_permissions(spent_frames):
    ctx, _, _, _, calls, build, checker = spent_frames
    protected = ['clip_19', 'clip_32', 'clip_40', 'clip_44']
    grants = {cid: {'limit': 4, 'reason': 'earlier approved video round',
                   'input_limit': 2, 'recheck_limit': 1,
                   'conditional_extra': {'authorization': 'earlier-video-approval',
                                         'reason': 'earlier approved video round', 'limit': 4,
                                         'prepared': True, 'activated': True}}
              for cid in protected}
    path = ctx.episode_dir/'repair_budget_grants.json'
    atomic_write_json(path, grants)
    history.save(ctx.episode_dir, {'prior_generation_counts': {'clips': {cid: 4 for cid in protected}}, 'trials': []})
    history_path = ctx.episode_dir/'repair_history/history.json'
    history_before = history_path.read_bytes()

    def permissions():
        return {cid: {'video': history.generation_limit(ctx.episode_dir, cid),
                      'preparation': history.preparation_limit(ctx.episode_dir, cid),
                      'input': history.input_limit(ctx.episode_dir, cid, default=2),
                      'recheck': history.recheck_limit(ctx.episode_dir, cid),
                      'extra': copy.deepcopy(history.conditional_extra(ctx.episode_dir, cid))}
                for cid in ['clip_01', 'clip_02', *protected]}

    before = permissions()
    grants['clip_01'] = {'first_frame': {'authorization': 'user-approved-one-image',
                                      'reason': 'corrected source opening', 'limit': 4}}
    atomic_write_json(path, grants)
    grant_bytes = path.read_bytes()
    snapshot = status(ctx, read_json(ctx.episode_dir/'clip_plan.json'))['clips']
    assert snapshot['clip_01']['remaining_image_candidates'] == 1
    assert snapshot['clip_02']['remaining_image_candidates'] == 0
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert result['clips']['clip_01']['ok'] and calls == [('clip_01', 'candidate_04')]
    assert permissions() == before
    assert history_path.read_bytes() == history_before and path.read_bytes() == grant_bytes
    assert {cid: read_json(path)[cid] for cid in protected} == {cid: grants[cid] for cid in protected}


def test_video_and_wildcard_grants_do_not_authorize_images(spent_frames):
    ctx, _, _, _, calls, build, checker = spent_frames
    grant = {'authorization': 'user-approved', 'reason': 'video only', 'limit': 4}
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json', {
        '*': {'first_frame': grant},
        'clip_01': {**grant, 'conditional_extra': grant},
    })
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert not result['clips']['clip_01']['ok'] and calls == []
    assert result['first_frames']['clips']['clip_01']['remaining_image_candidates'] == 0


def test_explicit_retake_spends_one_image_and_both_resume_paths_reuse_it(spent_frames):
    ctx, rows, roots, _, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one corrected opening', 'limit': 4})
    rows[0]['retake_candidate'] = {'candidate': 'candidate_03', 'reason': 'ankle pixels are on dry ground'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert result['clips']['clip_01']['ok']
    assert calls == [('clip_01', 'candidate_04')]
    parent = roots['clip_01']/'candidate_03'
    decision = read_json(parent/'retake-decision.json')
    assert decision['replacement_candidate'] == 'candidate_04'
    assert decision['reason'] == rows[0]['retake_candidate']['reason']
    replacement = roots['clip_01']/'candidate_04'
    assert read_json(replacement/'input.json')['image_prompt'] == rows[0]['image_prompt']
    assert result['first_frames']['clips']['clip_01']['image_candidates'] == 4

    # Same explicit request, then an ordinary resume with the retake field omitted.
    again = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert again['clips']['clip_01']['ok']
    rows[0].pop('retake_candidate')
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    ordinary = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert ordinary['clips']['clip_01']['ok']
    assert calls == [('clip_01', 'candidate_04')]
    assert not (roots['clip_01']/'candidate_05').exists()


@pytest.mark.parametrize('retake,accepted', [
    ({'candidate': 'candidate_99', 'reason': 'defect'}, False),
    ({'candidate': 'candidate_01', 'reason': '   '}, False),
    ({'candidate': 'candidate_01'}, False),
    ({'candidate': 'candidate_01', 'reason': 'pixel defect'}, True),
])
def test_retake_requires_existing_review_rejection_and_reason(spent_frames, retake, accepted):
    ctx, rows, roots, _, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    if accepted:
        save_current_review(ctx,rows,roots['clip_01']/'candidate_01',
                            {'consistent':True,'problems':[]})
    rows[0]['retake_candidate'] = retake
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    with pytest.raises(ValueError, match='retake_candidate'):
        FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert calls == [] and not (roots['clip_01']/'candidate_04').exists()


def test_retake_rejects_reuse_and_cap_exhaustion_without_allocation(spent_frames):
    ctx, rows, roots, _, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    rows[0].update(reuse_candidate='candidate_01',
                   retake_candidate={'candidate': 'candidate_03', 'reason': 'pixel defect'})
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    with pytest.raises(ValueError, match='mutually exclusive'):
        FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert calls == []
    rows[0].pop('reuse_candidate')
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 3})
    result = FirstFrameOperations(ctx, builder=build, checker=checker).prepare({})
    assert result['clips']['clip_01']['error'] == 'first-frame image candidate budget used'
    assert calls == [] and not (roots['clip_01']/'candidate_04').exists()


def test_failed_replacement_remains_rejected(spent_frames):
    ctx, rows, roots, _, calls, _, _ = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    rows[0]['retake_candidate'] = {'candidate': 'candidate_03', 'reason': 'pixel defect'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    def build_failed(ctx, row, path):
        calls.append((row['clip_id'], path.parent.name))
        atomic_write_json(path.with_suffix('.png.task.json'), {'task_id': 'simulated-paid-image'})
        Image.new('RGB', (8, 16)).save(path)
    failed = FirstFrameOperations(ctx, builder=build_failed,
        checker=lambda *args: {'consistent': False, 'problems': ['ankle pixels are on dry ground']}).prepare({})
    assert not failed['clips']['clip_01']['ok']
    assert read_json(roots['clip_01']/'candidate_04/rejected.json')['problems'] == ['ankle pixels are on dry ground']


def test_old_accepted_marker_does_not_block_current_review_rejection(spent_frames):
    ctx, rows, roots, _, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    parent=roots['clip_01']/'candidate_02'
    atomic_write_json(parent/'accepted.json',{'historical_acceptance':True})
    rows[0]['retake_candidate']={'candidate':'candidate_02','reason':'current review found dry pixels'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':rows})
    result=FirstFrameOperations(ctx,builder=build,checker=checker).prepare({})
    assert result['clips']['clip_01']['ok'] and calls==[('clip_01','candidate_04')]


def test_latest_matching_pass_blocks_retake_despite_old_rejection(spent_frames):
    ctx, rows, roots, _, calls, build, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    parent=roots['clip_01']/'candidate_01'
    save_current_review(ctx,rows,parent,{'consistent':True,'problems':[]})
    rows[0]['retake_candidate']={'candidate':'candidate_01','reason':'old defect'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':rows})
    with pytest.raises(ValueError,match='rejected by image review'):
        FirstFrameOperations(ctx,builder=build,checker=checker).prepare({})
    assert calls==[] and not (roots['clip_01']/'candidate_04').exists()


def test_supplier_exception_resume_stays_on_reserved_replacement(spent_frames):
    ctx, rows, roots, _, calls, _, checker = spent_frames
    grant_image(ctx, {'authorization': 'user-approved-one-image', 'reason': 'one image', 'limit': 4})
    rows[0]['retake_candidate'] = {'candidate': 'candidate_03', 'reason': 'pixel defect'}
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    posts = []
    def flaky_builder(ctx, row, path):
        calls.append((row['clip_id'], path.parent.name))
        receipt = path.with_suffix('.png.task.json')
        if not receipt.exists():
            posts.append(path.parent.name)
            atomic_write_json(receipt, {'task_id': 'same-task'})
            raise RuntimeError('temporary supplier timeout')
        Image.new('RGB', (8, 16)).save(path)
    with pytest.raises(RuntimeError, match='temporary supplier timeout'):
        FirstFrameOperations(ctx, builder=flaky_builder, checker=checker).prepare({})
    resumed = FirstFrameOperations(ctx, builder=flaky_builder, checker=checker).prepare({})
    assert resumed['clips']['clip_01']['ok']
    assert posts == ['candidate_04']
    assert calls == [('clip_01', 'candidate_04'), ('clip_01', 'candidate_04')]
    assert read_json(roots['clip_01']/'candidate_03/retake-decision.json')['replacement_candidate'] == 'candidate_04'
    assert len(list(roots['clip_01'].glob('candidate_*'))) == 4
