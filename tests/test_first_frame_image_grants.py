"""An explicit image grant adds one clip's capacity without touching take authority."""
import copy

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames as first_frame_module
from novel_manga.application.agents.episode.first_frames import FirstFrameOperations, status
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


def pixel_hash(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            Image.new('RGB', (8, 16)).save(candidate/'opening.png')
            atomic_write_json(candidate/'reviews/review_001.json',{
                'input':{**review_inputs[cid],'candidate_sha256':pixel_hash(candidate/'opening.png')},
                'review':{'consistent':False,'problems':['visible pixel defect']}})
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
        'image_prompt':row['image_prompt'],'visible_characters':row['visible_characters'],
        'candidate_sha256':pixel_hash(candidate/'opening.png')}
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

    changed_prompt = 'A different source-supported opening request.'
    plan = read_json(ctx.episode_dir/'clip_plan.json')
    plan['clips'][0]['shots'][0]['opening_frame']['image_prompt'] = changed_prompt
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    rows[0]['image_prompt'] = changed_prompt
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
    request = read_json(replacement/'image-request.json')
    assert request['request_prompt'].count(rows[0]['retake_candidate']['reason']) == 1
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


def test_service_interruption_only_resumes_original_candidate(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    def interrupted(c,r,path):
        atomic_write_json(path.with_suffix('.png.task.json'),{'task_id':'retained-service-task'})
        raise RuntimeError('task submission returned HTTP 503: service unavailable')
    operation=FirstFrameOperations(ctx,builder=interrupted)
    with pytest.raises(RuntimeError,match='503'):operation.prepare({})
    grant=operation.partial_outcome['clips']['clip_01']['repair_grant']
    assert grant['operation']=='resume'
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',operation.partial_outcome)
    changed={**row,'image_prompt':'a different request cannot ride service resume'}
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    plan['clips'][0]['shots'][0]['opening_frame']['image_prompt']=changed['image_prompt']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[changed]})
    with pytest.raises(ValueError,match='resume.*original candidate'):
        FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('changed request must not allocate')).prepare({})
    plan['clips'][0]['shots'][0]['opening_frame']['image_prompt']=row['image_prompt']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    def resume(c,r,path):Image.new('RGB',(8,16)).save(path)
    result=FirstFrameOperations(ctx,builder=resume,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok'] and len(list((ctx.novel_dir/'first_frames/book_1/clip_01').glob('candidate_*')))==1


def test_first_frame_entry_propagates_retryable_service_without_reopening_content(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.interactive import first_frame_entry,runtime
    root=tmp_path;novel=root/'outputs/book';episode=novel/'book_1';run_dir=root/'run'
    ctx=SimpleNamespace(run_dir=run_dir,episode_dir=episode,novel_dir=novel,
        work_dir=run_dir/'sandbox',source_episode_dir=episode,input_mode='script')
    atomic_write_json(novel/'profile.json',{})
    atomic_write_json(run_dir/'frozen.json',{})
    monkeypatch.setattr(runtime,'configure',lambda *a,**k:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.context.create_context',lambda *a,**k:ctx)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway',lambda *a:None)
    failure={'http_status':503,'detail':'service unavailable'}
    monkeypatch.setattr('novel_manga.application.agents.episode.flow.run_episode',
        lambda *a,**k:{'status':'blocked','retryable':True,'service_interrupted':failure,'remaining_seconds':10})
    result=first_frame_entry.run(root,novel,project='adventure',destination=root/'delivery',resume=True,config={})
    assert result['status']=='blocked' and result['retryable'] is True
    assert result['service_interrupted']==failure


@pytest.mark.parametrize(('receipt','expected'), [
    ({'status':'blocked','retryable':True,'service_interrupted':{'http_status':503,'detail':'outage'}},True),
    ({'status':'candidate','retryable':True,'service_interrupted':{'http_status':503,'detail':'outage'}},False),
])
def test_film_propagates_only_retryable_blocked_production(tmp_path,monkeypatch,receipt,expected):
    from types import SimpleNamespace
    from novel_manga.interactive import film,creation,production,creative_material,authored,runtime,first_frame_entry
    from novel_manga.application.preparation import audit
    from novel_manga.application.preparation import actions
    source=tmp_path/'request.json'
    atomic_write_json(source,{'mode':'original','project':'suspense','brief':'Original test','target_seconds':[45,60]})
    def create(path,directory,**kwargs):
        atomic_write_json(directory/'sandbox/input/manifest.json',{
            'profile':{},'task':{'brief':'Original test'},'source_documents':[]})
        (directory/'sandbox/input/brief.md').write_text('Original test')
        atomic_write_json(directory/'sandbox/output/episode.json',{'title':'candidate','chapter_script':{'shots':[]}})
        atomic_write_json(directory/'creator-state.json',{'status':'creative_candidate'})
        return {'status':'creative_candidate','seconds_used':1,'allowance_seconds':10}
    monkeypatch.setattr(creation,'run',create)
    monkeypatch.setattr(audit,'creative_audit',lambda *a,**k:{
        'fingerprint':'current-content','answer':{'source_readable':True,'issues':[]}})
    monkeypatch.setattr(creative_material,'write_material',lambda *a,**k:{})
    def prepare(material,book,family):
        (book/(book.name+'_1')).mkdir(parents=True)
    monkeypatch.setattr(authored,'prepare_authored',prepare)
    monkeypatch.setattr(authored,'pack_authored',lambda episode:atomic_write_json(episode/'clip_plan.json',{'clips':[]}))
    monkeypatch.setattr(first_frame_entry,'select_mode',lambda *a:None)
    monkeypatch.setattr(runtime,'configure',lambda *a,**k:None)
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(build_assets=lambda:None))
    monkeypatch.setattr(actions,'creative_review_fingerprint',lambda *_:'current-content')
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:receipt)
    result=film.run(tmp_path,source,tmp_path/'run','new-film')
    assert result['status']==receipt['status'] and result['retryable'] is expected
    if expected:
        assert result['service_interrupted']==receipt['service_interrupted']


def test_film_bare_connection_error_keeps_service_structure(tmp_path,monkeypatch):
    from novel_manga.interactive import film,creation,runtime
    source=tmp_path/'request.json'
    atomic_write_json(source,{'mode':'original','project':'suspense','brief':'Original test','target_seconds':[45,60]})
    monkeypatch.setattr(runtime,'configure',lambda *args,**kwargs:None)
    monkeypatch.setattr(creation,'run',lambda *a,**k: (_ for _ in ()).throw(ConnectionError('connection lost')))
    result=film.run(tmp_path,source,tmp_path/'run','new-film')
    assert result['status']=='blocked' and result['retryable'] is True
    assert result['service_interrupted']['http_status'] is None
    assert 'connection lost' in result['service_interrupted']['detail']


def test_existing_assembly_service_failure_flows_through_film_and_bounded_batch(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.interactive import (first_frame_entry,runtime,film,creation,production,
        creative_material,authored,batch)
    from novel_manga.application.preparation import audit,actions
    entry_root=tmp_path/'entry';novel=entry_root/'outputs/book';episode=novel/'book_1';run_dir=entry_root/'run'
    ctx=SimpleNamespace(run_dir=run_dir,episode_dir=episode,novel_dir=novel,
        work_dir=run_dir/'sandbox',source_episode_dir=episode,input_mode='script')
    atomic_write_json(novel/'profile.json',{})
    atomic_write_json(run_dir/'frozen.json',{})
    atomic_write_json(episode/'thin_media_report.json',{'assembly':{'final_video':'existing.mp4'}})
    monkeypatch.setattr(runtime,'configure',lambda *a,**k:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.context.create_context',lambda *a,**k:ctx)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway',lambda *a:None)
    failure={'http_status':503,'detail':'service unavailable'}
    monkeypatch.setattr('novel_manga.application.agents.episode.flow.run_episode',
        lambda *a,**k:{'status':'blocked','retryable':True,'service_interrupted':failure,
                       'required_action':'resume_render','remaining_seconds':10})
    monkeypatch.setattr('novel_manga.interactive.delivery.finish_reviewed_delivery',
        lambda *a,**k:pytest.fail('service interruption must not reach delivery'))
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(build_assets=lambda:None))
    receipt=first_frame_entry.run(entry_root,novel,project='adventure',
        destination=entry_root/'delivery',resume=True,config={})
    assert receipt['status']=='blocked' and receipt['retryable'] is True
    assert receipt['service_interrupted']==failure and receipt['required_action']=='resume_render'

    film_root=tmp_path/'film'
    source=film_root/'request.json'
    atomic_write_json(source,{'mode':'original','project':'suspense','brief':'Original test','target_seconds':[45,60]})
    def create(path,directory,**kwargs):
        atomic_write_json(directory/'sandbox/input/manifest.json',{
            'profile':{},'task':{'brief':'Original test'},'source_documents':[]})
        (directory/'sandbox/input/brief.md').write_text('Original test')
        atomic_write_json(directory/'sandbox/output/episode.json',{'title':'candidate','chapter_script':{'shots':[]}})
        atomic_write_json(directory/'creator-state.json',{'status':'creative_candidate'})
        return {'status':'creative_candidate','seconds_used':1,'allowance_seconds':10}
    monkeypatch.setattr(creation,'run',create)
    monkeypatch.setattr(audit,'creative_audit',lambda *a,**k:{
        'fingerprint':'current-content','answer':{'source_readable':True,'issues':[]}})
    monkeypatch.setattr(creative_material,'write_material',lambda *a,**k:{})
    monkeypatch.setattr(authored,'prepare_authored',
        lambda material,book,family:(book/(book.name+'_1')).mkdir(parents=True))
    monkeypatch.setattr(authored,'pack_authored',lambda episode:atomic_write_json(episode/'clip_plan.json',{'clips':[]}))
    monkeypatch.setattr(first_frame_entry,'select_mode',lambda *a:None)
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(build_assets=lambda:None))
    monkeypatch.setattr(actions,'creative_review_fingerprint',lambda *_:'current-content')
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:receipt)
    film_state=film.run(film_root,source,film_root/'run','new-film')
    assert film_state['status']=='blocked' and film_state['retryable'] is True
    assert film_state['service_interrupted']==failure

    receipt_path=film_root/'receipt.json';atomic_write_json(receipt_path,film_state)
    worker=film_root/'worker.py'
    worker.write_text('''import json,sys,pathlib
receipt=json.loads(pathlib.Path(sys.argv[1]).read_text())
out=pathlib.Path(sys.argv[2]);calls=out.parent/'calls'
n=int(calls.read_text())+1 if calls.exists() else 1;calls.write_text(str(n))
out.write_text(json.dumps(receipt if n==1 else {'status':'accepted'}))
''')
    task=film_root/'batch-task.json';atomic_write_json(task,{'brief':'original'})
    spec={'max_launches':2,'episodes':[{'edition':'one','task':str(task)}]}
    batch_path=film_root/'batch.json';atomic_write_json(batch_path,spec)
    result=batch.run(film_root,batch_path,film_root/'batch-run',poll_seconds=.01,retry_seconds=0,
        command=lambda root,item,directory,resume:[
            __import__('sys').executable,str(worker),str(receipt_path),str(directory/'state.json')])
    row=result['episodes']['one']
    assert row['launches']==2 and row['result']['status']=='accepted'
    assert row['history'][0]['result']['service_interrupted']==failure
    assert row['history'][0]['result']['retryable'] is True
