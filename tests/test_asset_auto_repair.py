"""The newly authorized three-picture repair, with no model/provider calls."""
from pathlib import Path
from types import SimpleNamespace
import copy,json
import pytest
from novel_manga.application.assets import candidates
from novel_manga.application.agents.episode import input_repair,input_records,resume_inputs,draft_assets
from novel_manga.application.agents.episode.input_recovery_policy import asset_image_recoverable
from novel_manga.application.agents.episode.input_compilation import CompiledInput
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json,read_json
from support.input_repair import setup

@pytest.fixture
def asset_case(tmp_path,monkeypatch):
    ctx=SimpleNamespace(asset_snapshot_dir=tmp_path,novel_dir=tmp_path/'book',run_dir=tmp_path/'run')
    monkeypatch.setattr(candidates,'style_key',lambda c:{'style':'fixed'})
    return ctx,{'appearance':'source dog body','wardrobe':''}

def builder_log(calls):
    def build(ctx,directory,role,name,design):
        calls.append({'directory':str(directory),'design':copy.deepcopy(design),'feedback':read_json(directory/'image-review-feedback.json',{})})
        path=directory/'card.jpeg';path.write_bytes(directory.name.encode())
        atomic_write_json(directory/'spec.json',{'source_design':design})
        atomic_write_json(path.with_suffix('.jpeg.request.json'),{'source_design':design})
        return path
    return build

def seed_legacy(ctx,design,name='dog'):
    root=ctx.asset_snapshot_dir/'series_assets/draft-assets';folder=root/'draft_character_0001';folder.mkdir(parents=True,exist_ok=True)
    row={'id':folder.name,'role':'character','name':name,'design':design,'style_key':candidates.style_key(ctx)}
    atomic_write_json(root/'index.json',[row]);image=folder/'legacy.jpeg';image.write_bytes(b'original failed pixels')
    atomic_write_json(folder/'spec.json',{'approved_design':design});atomic_write_json(image.with_suffix('.jpeg.request.json'),{'original_job':'old-job'})
    atomic_write_json(folder/'built.json',{'path':str(image.relative_to(ctx.asset_snapshot_dir))})
    atomic_write_json(folder/'rejected.json',{'error':'generated asset failed image review: dog has unapproved armor','image_review_policy':2})
    return folder,{str(f):f.read_bytes()for f in folder.iterdir()if f.is_file()}

def test_first_bad_then_second_good_same_design_feedback_and_accepted_cache(asset_case):
    ctx,design=asset_case;calls=[];checks=[]
    def check(path,*a):
        checks.append(path)
        return {'consistent':len(checks)>1,'problems':[]if len(checks)>1 else['dog has unapproved armor']}
    accepted=candidates._asset(ctx,'character','dog',design,builder=builder_log(calls),checker=check)
    assert len(calls)==len(checks)==2
    assert [Path(c['directory']).name for c in calls]==['attempt_01','attempt_02']
    assert all(c['design']==design for c in calls)
    assert calls[1]['feedback']['problems']==['dog has unapproved armor']
    index=read_json(ctx.asset_snapshot_dir/'series_assets/draft-assets/index.json');assert len(index)==1
    assert accepted['asset_id']==index[0]['id']and accepted['design']==design
    def forbidden(*a):pytest.fail('accepted cache called build/review')
    assert candidates._asset(ctx,'character','dog',design,builder=forbidden,checker=forbidden)==accepted

def test_old_failed_picture_is_first_and_is_immutable(asset_case):
    ctx,design=asset_case;folder,frozen=seed_legacy(ctx,design);calls=[];checks=[]
    result=candidates._asset(ctx,'character','dog',design,builder=builder_log(calls),checker=lambda p,*a:checks.append(p)or{'consistent':True,'problems':[]})
    assert len(calls)==len(checks)==1 and Path(calls[0]['directory']).name=='attempt_02'
    assert 'unapproved armor'in str(calls[0]['feedback'])
    assert all(Path(p).read_bytes()==b for p,b in frozen.items())
    assert result['asset_id']==folder.name and len(read_json(folder.parent/'index.json'))==1

def test_three_rejections_exhaust_and_reentry_does_not_draw(asset_case):
    ctx,design=asset_case;calls=[]
    def reject(*a):raise candidates.AssetImageRejected('generated asset failed image review: armor',image_review={'consistent':False,'problems':['armor']})
    for n in range(2):
        with pytest.raises(candidates.AssetImageRejected)as error:candidates._asset(ctx,'character','dog',design,builder=builder_log(calls),checker=reject)
        assert error.value.exhausted and len(calls)==3
    folder=ctx.asset_snapshot_dir/'series_assets/draft-assets/draft_character_0001'
    assert read_json(folder/'exhausted.json')['attempts']==3
    assert len(list((folder/'attempts').glob('attempt_*/review.json')))==3

def test_api_interruption_resumes_same_attempt_and_job(asset_case):
    ctx,design=asset_case;submissions=[];entries=[]
    def build(c,d,*a):
        entries.append(str(d));task=d/'card.jpeg.task.json'
        if not task.exists():
            submissions.append('job-1');atomic_write_json(task,{'task_id':'job-1'});raise ConnectionError('poll interrupted')
        assert read_json(task)['task_id']=='job-1';p=d/'card.jpeg';p.write_bytes(b'done');return p
    with pytest.raises(ConnectionError):candidates._asset(ctx,'character','dog',design,builder=build,checker=lambda *a:{'consistent':True,'problems':[]})
    result=candidates._asset(ctx,'character','dog',design,builder=build,checker=lambda *a:{'consistent':True,'problems':[]})
    assert len(set(entries))==1 and submissions==['job-1']and' attempt_02'not in result['path']

def test_review_interruption_reuses_existing_picture(asset_case):
    ctx,design=asset_case;calls=[];reviews=[]
    def check(*a):
        reviews.append(1)
        if len(reviews)==1:raise ConnectionError('judge interrupted')
        return {'consistent':True,'problems':[]}
    with pytest.raises(ConnectionError):candidates._asset(ctx,'character','dog',design,builder=builder_log(calls),checker=check)
    candidates._asset(ctx,'character','dog',design,builder=builder_log(calls),checker=check)
    assert len(calls)==1 and len(reviews)==2

def test_feedback_reaches_real_build_style_without_rewriting_source(tmp_path,monkeypatch):
    from novel_manga.config import Settings
    from novel_manga.application import profiles
    from novel_manga.media import asset_style,asset_builder,adapters
    seen=[];atomic_write_json(tmp_path/'story_bible.json',{'novel_title':'test','genre':'generic','visual_style':'live','palette':'gray','style_fingerprint':'test'})
    atomic_write_json(tmp_path/'image-review-feedback.json',{'problems':['remove the unapproved armor']})
    monkeypatch.setattr(Settings,'from_env',lambda **k:Settings())
    monkeypatch.setattr(profiles,'styled_bible',lambda b,*a:b)
    monkeypatch.setattr(asset_style.AssetStyle,'for_genre',lambda *a,**kw:asset_style.AssetStyle(render_direction='original medium',scene_direction='original set'))
    monkeypatch.setattr(adapters,'FramedPhanRouter',lambda *a:object())
    class Factory:
        def __init__(self,*a,**kw):seen.append(kw['style'])
        def build_selected(self,d,b,*a,**kw):seen.append(b)
    monkeypatch.setattr(asset_builder,'FramedAssetFactory',Factory)
    design={'appearance':'ordinary dog','wardrobe':''};old=copy.deepcopy(design)
    candidates.build(SimpleNamespace(novel_dir=tmp_path,run_dir=tmp_path),tmp_path,'character','dog',design)
    assert design==old and seen[1].characters[0].appearance=='ordinary dog'
    assert 'remove the unapproved armor'in seen[0].render_direction
    assert 'remove the unapproved armor'in seen[0].scene_direction

def test_location_people_check_is_kept_and_occupied_cards_exhaust_at_three(asset_case,monkeypatch):
    from novel_manga.media import asset_builder
    ctx,_=asset_case;calls=[];checks=[]
    factory=asset_builder.FramedAssetFactory(SimpleNamespace(),object(),empty_retry_seeds=())
    monkeypatch.setattr(factory,'ensure_card',lambda *a,**kw:pytest.fail('nested location redraw escaped outer cap'))
    monkeypatch.setattr(asset_builder,'people_in_card',lambda path:checks.append(str(path))or{'people_in_scene':1,'where':'person at doorway'})
    monkeypatch.setattr(candidates,'ask_json',lambda *a,**kw:pytest.fail('known occupied card was allowed to skip its people verdict'))
    def build(c,d,*a):
        path=d/'room.jpeg';path.write_bytes(b'provider test pixels');calls.append(str(path))
        factory._keep_empty('empty room',path,SimpleNamespace(path=path))
        return path
    with pytest.raises(candidates.AssetImageRejected)as error:candidates._asset(ctx,'location','room','empty setting',builder=build)
    assert error.value.exhausted and len(calls)==len(checks)==3
    assert all(read_json(Path(path).with_suffix('.jpeg.request.json'))['people_check']['people_in_scene']==1 for path in calls)
    assert 'person at doorway'in Path(calls[1]).parent.joinpath('image-review-feedback.json').read_text()

def test_default_location_empty_retry_seeds_are_unchanged():
    from novel_manga.media.asset_builder import FramedAssetFactory
    assert FramedAssetFactory(SimpleNamespace(),object()).empty_retry_seeds==FramedAssetFactory.EMPTY_RETRY_SEEDS

def public_legacy(setup,monkeypatch):
    ctx,plan,_=setup;(ctx.work_dir/'input').mkdir(exist_ok=True)
    design={'appearance':'成年男子','wardrobe':'灰袍'}
    row={'clip_id':'clip_01','action':'input','clip_ids':['clip_01'],'reason':'same approved source actor','source_quote':'甲坐在宿舍','shots':[{'index':1,'in_frame':['甲','丙']}],'characters':{'丙':design}}
    record={'clip_id':'clip_01','status':'rejected','step':'character_assets','error_kind':'source_rejected','error':'UnsupportedInput: generated asset failed image review: dog has unapproved armor','source':{'consistent':True,'problems':[]},'decision':row,'revision_count':1,'resume_count':1}
    path=ctx.episode_dir/'work/input_repairs/clip_01-4.json';atomic_write_json(path,record)
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'clip_01':{'input_limit':1}})
    monkeypatch.setattr(candidates,'style_key',lambda c:{'style':'fixed'});seed_legacy(ctx,design,'丙')
    monkeypatch.setattr(input_repair,'source_check',lambda *a,**kw:pytest.fail('approved source was rechecked'))
    monkeypatch.setattr(input_repair,'compile_input',lambda *a:CompiledInput()) # no translation/render/publication in this replay
    return ctx,path,record

def test_public_legacy_resume_keeps_text_counters_source_and_auto_resume(setup,monkeypatch):
    ctx,path,old=public_legacy(setup,monkeypatch);calls=[]
    assert input_records.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}
    assert input_repair.repair_status(ctx)['clip_01']['operations']['resume']['allowed']
    assert resume_inputs.pending_input_continuations(ctx)
    monkeypatch.setattr(draft_assets,'build',builder_log(calls));monkeypatch.setattr(draft_assets,'check_image',lambda *a:{'consistent':True,'problems':[]})
    before=history.generation_counts(ctx.episode_dir)
    result=input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})
    new=read_json(path);assert result['input_repaired']==['clip_01']and len(calls)==1
    assert new['source']==old['source']and new['revision_count']==new['resume_count']==1
    assert input_records.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}
    assert history.generation_counts(ctx.episode_dir)==before
    assert len(list(path.parent.glob('*.json')))==1

def test_public_source_false_is_not_image_recovery(setup,monkeypatch):
    ctx,path,old=public_legacy(setup,monkeypatch);old['source']={'consistent':False,'problems':['not in source']};atomic_write_json(path,old)
    assert not asset_image_recoverable(old)
    assert not input_repair.repair_status(ctx)['clip_01']['operations']['resume']['allowed']
    assert not resume_inputs.pending_input_continuations(ctx)
    monkeypatch.setattr(draft_assets,'_asset',lambda *a:pytest.fail('source false drew image'))
    with pytest.raises(ValueError,match='no incomplete'):input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})

def test_public_image_exhaustion_is_not_source_rejected_or_resumable(setup,monkeypatch):
    ctx,path,old=public_legacy(setup,monkeypatch);calls=[]
    monkeypatch.setattr(draft_assets,'build',builder_log(calls))
    monkeypatch.setattr(draft_assets,'check_image',lambda *a:{'consistent':False,'problems':['armor']})
    with pytest.raises(candidates.AssetImageRejected)as error:input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})
    saved=read_json(path);assert error.value.exhausted and len(calls)==2 # old failed one + two new
    assert saved['error_kind']=='asset_image_rejected'and saved['source']==old['source']
    assert saved['revision_count']==saved['resume_count']==1 and saved['asset_image_review']['exhausted']
    assert not input_repair.repair_status(ctx)['clip_01']['operations']['resume']['allowed']
    with pytest.raises(ValueError,match='no incomplete'):input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})
    assert len(calls)==2

def test_public_image_api_outage_keeps_input_count_and_same_job(setup,monkeypatch):
    ctx,path,old=public_legacy(setup,monkeypatch);submissions=[];directories=[]
    def build(c,d,*a):
        directories.append(str(d));task=d/'card.task.json'
        if not task.exists():submissions.append('job2');atomic_write_json(task,{'id':'job2'});raise ConnectionError('API poll interrupted')
        assert read_json(task)['id']=='job2';image=d/'image.jpeg';image.write_bytes(b'finished');return image
    monkeypatch.setattr(draft_assets,'build',build);monkeypatch.setattr(draft_assets,'check_image',lambda *a:{'consistent':True,'problems':[]})
    with pytest.raises(ConnectionError):input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})
    assert read_json(path)['status']=='incomplete'and read_json(path)['error_kind']=='service_interrupted'
    assert input_records.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}
    input_repair.repair(ctx,{'action':'input','clip_id':'clip_01','resume':True})
    assert submissions==['job2']and len(set(directories))==1
    assert read_json(path)['resume_count']==read_json(path)['revision_count']==1
    assert input_records.remaining_input_attempts(ctx,['clip_01'])=={'clip_01':0}
