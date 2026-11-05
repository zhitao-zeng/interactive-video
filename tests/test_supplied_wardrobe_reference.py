from pathlib import Path
from types import SimpleNamespace
from PIL import Image
import pytest

from novel_manga.util import atomic_write_json,read_json
from novel_manga.media import reference_views
from novel_manga.media.first_frames import compose_opening_image_prompt,current_contract
from novel_manga.application.agents.episode import inputs,first_frames


def supplied(root,name='A',asset='character_001'):
    folder=root/'series_assets/characters'/asset;folder.mkdir(parents=True,exist_ok=True)
    for file,scope in [('turnaround.jpeg','identity-baseline'),('wardrobe-card.jpeg','wardrobe-visual-card')]:
        Image.new('RGB',(16,24),'blue').save(folder/file)
        atomic_write_json(folder/(file+'.origin.json'),{'kind':'supplied-reference','reference_scope':scope})
    return {'role':'character','name':name,'asset_id':asset,'path':str((folder/'turnaround.jpeg').relative_to(root))}


@pytest.mark.parametrize('localized',[False,True])
def test_original_wardrobe_view_is_restored_to_existing_snapshot_catalog(tmp_path,localized):
    source=tmp_path/'source';local=tmp_path/'local';assets=tmp_path/'assets';run=tmp_path/'run'
    ref=supplied(source);assets.mkdir();local.mkdir()
    if localized:
        import shutil
        supplied(assets)  # earlier recovery populated only the shared copy
        target=local/ref['path'];target.parent.mkdir(parents=True)
        shutil.copy2(source/ref['path'],target)
        shutil.copy2(source/(ref['path']+'.origin.json'),Path(str(target)+'.origin.json'))
    else:(local/'series_assets').symlink_to(assets/'series_assets',target_is_directory=True)
    ctx=SimpleNamespace(source_novel_dir=source,novel_dir=local,asset_snapshot_dir=assets,run_dir=run,
        work_dir=run/'sandbox',input_mode='script',source_episode_dir=source/'book_1')
    plan={'clips':[{'references':[ref]}]}
    references=inputs._candidate_references(ctx,'A',plan,{'shots':[{'characters':['A']}]})
    catalog,missing,_=inputs._catalog(ctx,references)
    assert not missing and len(catalog)==2
    wardrobe=next(row for row in catalog if row.get('reference_scope')=='wardrobe_design')
    assert (local/wardrobe['path']).read_bytes()==(source/wardrobe['path']).read_bytes()
    assert (local/(wardrobe['path']+'.origin.json')).is_file()


def test_generation_and_review_bind_face_and_wardrobe_separately(tmp_path,monkeypatch):
    ref=supplied(tmp_path);other=supplied(tmp_path,'B','character_002')
    ctx=SimpleNamespace(novel_dir=tmp_path,episode_dir=tmp_path/'book_1',run_dir=tmp_path/'run')
    opening={'image_prompt':'A at the table.','visible_characters':['A'],'reference_subjects':['A']}
    clip={'clip_id':'clip_01','references':[ref,other],
          'shots':[{'index':1,'opening_frame':opening}],'shot_indexes':[1]}
    row=first_frames.authored_row(ctx,clip,{'clip_id':'clip_01'})
    assert len(row['image_references'])==2
    assert all('character_001' in path for path in row['image_references'])
    references=reference_views.opening_references(ctx,clip,row['image_references'])
    prompt=compose_opening_image_prompt('A at the table.',{},row['image_references'],references)
    assert 'Image 1: character A' in prompt and 'face and hair only' in prompt
    assert 'Image 2: character A' in prompt and 'clothing and body proportions only' in prompt
    contract=current_contract(ctx,clip,[ref['path']])
    assert contract['image_references']==row['image_references']
    from novel_manga.config import Settings
    monkeypatch.setattr(Settings,'from_env',lambda **k:Settings())
    calls=[]
    def generate(prompt,path,**kwargs):calls.append(kwargs)
    monkeypatch.setattr('novel_manga.providers.phanrouter.PhanRouterMediaProvider',
                        lambda settings:SimpleNamespace(create_image=generate))
    row.update(image_prompt='A at the table.',request_prompt=prompt)
    first_frames.build(ctx,row,tmp_path/'opening.png')
    assert calls[0]['reference']==tmp_path/ref['path']
    assert calls[0]['additional_references']==(tmp_path/row['image_references'][1],)
    from novel_manga.llm import client
    import json
    seen=[];pictures=[]
    monkeypatch.setattr(client,'ask_json',lambda parts,*a,**k:seen.append(parts) or {'observations':[],'findings':[]})
    monkeypatch.setattr(client,'image_part',lambda path,*a:pictures.append(path) or {'type':'text','text':str(path)})
    row['visible_characters']=[]
    first_frames.check(ctx,tmp_path/'opening.png',row,contract)
    evidence=json.loads(seen[0][0]['text'].split('\n')[-1])
    assert [r['image_number'] for r in evidence['reference_images']]==[2,3]
    assert evidence['reference_images'][1]['name']=='A'
    assert evidence['reference_images'][1]['reference_scope']=='wardrobe_design'
    assert pictures==[tmp_path/'opening.png',*(tmp_path/p for p in row['image_references'])]


def test_wardrobe_discovery_does_not_scan_unowned_or_generated_views(tmp_path):
    ref=supplied(tmp_path);folder=tmp_path/ref['path'];folder=folder.parent
    Image.new('RGB',(16,24),'red').save(folder/'rejected.jpeg')
    atomic_write_json(folder/'rejected.jpeg.origin.json',{'kind':'generated','reference_scope':'wardrobe-visual-card'})
    found=reference_views.supplied_wardrobe_views(tmp_path,ref)
    assert len(found)==1 and found[0]['path'].endswith('wardrobe-card.jpeg')


def test_registered_sandbox_picture_addresses_bind_the_same_source_asset(tmp_path):
    ref=supplied(tmp_path);ctx=SimpleNamespace(novel_dir=tmp_path,run_dir=tmp_path/'run')
    atomic_write_json(ctx.run_dir/'catalog.json',[{**ref,'image':'input/refs/character_001.jpeg'}])
    clip={'references':[ref]}
    for path in ['input/refs/character_001.jpeg','/work/input/refs/character_001.jpeg']:
        rows=reference_views.opening_references(ctx,clip,[path])
        assert rows[0]['path']==ref['path'] and len(rows)==2
    assert reference_views.canonical_paths(ctx,['unregistered.png'])==['unregistered.png']


def test_original_view_restoration_can_recheck_existing_pixels_without_new_image_budget(tmp_path,monkeypatch):
    from support.first_frame_cases import case
    ctx,clips=case(tmp_path,monkeypatch)
    folder=ctx.novel_dir/'series_assets/characters/character_001';folder.mkdir(parents=True)
    face=folder/'turnaround.jpeg';Image.new('RGB',(16,24),'blue').save(face)
    atomic_write_json(face.with_name(face.name+'.origin.json'),{'kind':'supplied-reference','reference_scope':'identity-baseline'})
    ref={'role':'character','asset_id':'character_001','name':'A','path':str(face.relative_to(ctx.novel_dir))}
    clip=clips[0];clip['references'].append(ref)
    clip['shots'][0]['opening_frame']['reference_subjects']=['pool','A']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',clip['references'])
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    row['image_references'].append(ref['path'])
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    built=[]
    def build(c,r,path):built.append(path);Image.new('RGB',(16,24),'blue').save(path)
    checker=lambda *a:{'consistent':True,'problems':[]}
    first_frames.FirstFrameOperations(ctx,builder=build,checker=checker).prepare({})
    candidate=built[0].parent;generation=(candidate/'image-request.json').read_bytes()
    wardrobe=folder/'wardrobe-card.jpeg';Image.new('RGB',(16,24),'green').save(wardrobe)
    atomic_write_json(wardrobe.with_name(wardrobe.name+'.origin.json'),{'kind':'supplied-reference','reference_scope':'wardrobe-visual-card'})
    refs=reference_views.opening_references(ctx,clip,read_json(candidate/'input.json')['image_references'])
    atomic_write_json(ctx.run_dir/'catalog.json',refs)
    status=first_frames.status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))['clips']['clip_01']
    assert 'reuse_candidate' in status['next_action']
    assert status['restored_original_reference_views']==[str(wardrobe.relative_to(ctx.novel_dir))]
    row['reuse_candidate']=candidate.name
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    seen=[]
    def recheck(c,path,r,contract):
        seen.append(r['image_references'])
        return {'consistent':True,'problems':[]}
    result=first_frames.FirstFrameOperations(ctx,builder=build,checker=recheck).prepare({})
    assert result['ok'] and len(built)==1
    assert str(wardrobe.relative_to(ctx.novel_dir)) in seen[0]
    assert (candidate/'image-request.json').read_bytes()==generation
    assert len(list(candidate.parent.glob('candidate_*')))==1


def test_compiled_references_keep_ordered_immutable_scope_and_content_hash(tmp_path):
    from dataclasses import FrozenInstanceError
    from novel_manga.media.first_frames import compile_opening
    ref=supplied(tmp_path)
    ctx=SimpleNamespace(novel_dir=tmp_path,run_dir=tmp_path/'run')
    opening={'image_prompt':'A at the table.','visible_characters':['A'],'reference_subjects':['A']}
    clip={'clip_id':'clip_01','references':[ref],
          'shots':[{'index':1,'in_frame':['A','B'],'opening_frame':opening}]}
    compiled=compile_opening(ctx,clip,{'clip_id':'clip_01'})
    assert [r.path for r in compiled.references]==[ref['path'],
        'series_assets/characters/character_001/wardrobe-card.jpeg']
    assert [r.reference_scope for r in compiled.references]==['identity_portrait','wardrobe_design']
    assert [r.order for r in compiled.references]==[0,1]
    assert all(r.role=='character' and len(r.hash)==64 for r in compiled.references)
    assert compiled.references[1].scope=='wardrobe_design'
    with pytest.raises(FrozenInstanceError):compiled.references[0].order=9
    with pytest.raises(AttributeError):compiled.references=()


def owned_bundle_case(tmp_path,monkeypatch,catalog_body=True):
    from support.first_frame_cases import case
    ctx,clips=case(tmp_path,monkeypatch);clip=clips[0]
    actor=supplied(ctx.novel_dir);other=supplied(ctx.novel_dir,'B','character_002')
    body=reference_views.supplied_wardrobe_views(ctx.novel_dir,actor)[0]
    other_body=reference_views.supplied_wardrobe_views(ctx.novel_dir,other)[0]
    extra={**actor,'path':actor['path'].replace('turnaround.jpeg','unused.jpeg')}
    Image.new('RGB',(16,24),'red').save(ctx.novel_dir/extra['path'])
    clip['references'].append(actor)
    clip['shots'][0]['opening_frame'].update(image_prompt='A at the pool.',visible_characters=['A'],reference_subjects=['pool','A'])
    catalog=[*clip['references'],other,other_body,extra,*([body] if catalog_body else [])]
    atomic_write_json(ctx.run_dir/'catalog.json',catalog)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01','extra_references':[]}]})
    return ctx,clip,body,other_body,extra


@pytest.mark.parametrize('catalog_body',[False,True])
def test_owned_bundle_compiler_round_trip_keeps_same_sources_and_video_bindings(tmp_path,monkeypatch,catalog_body):
    import copy
    from novel_manga.media.first_frames import compile_opening
    ctx,clip,body,_,_=owned_bundle_case(tmp_path,monkeypatch,catalog_body)
    before=copy.deepcopy(clip)
    first=compile_opening(ctx,clip,{'clip_id':'clip_01','extra_references':[]},require_assets=False)
    errors={};second=compile_opening(ctx,clip,first.row,field_errors=errors,require_assets=True)
    assert errors=={}
    assert first.references==second.references
    assert body['path'] in second.row['image_references']
    assert [r.reference_scope for r in second.references if r.name=='A']==['identity_portrait','wardrobe_design']
    assert clip==before


@pytest.mark.parametrize('catalog_body',[False,True])
def test_owned_bundle_passes_full_prepare_and_generation_review_share_images(tmp_path,monkeypatch,catalog_body):
    import copy
    ctx,clip,body,_,_=owned_bundle_case(tmp_path,monkeypatch,catalog_body)
    original_refs=copy.deepcopy(clip['references']);sent=[];reviewed=[]
    def build(context,row,path):
        sent.append(row['image_references']);Image.new('RGB',(16,24),'blue').save(path)
    def check(context,path,row,contract):
        reviewed.append(contract['image_references']);return {'findings':[],'observations':[]}
    result=first_frames.FirstFrameOperations(ctx,builder=build,checker=check).prepare({})
    assert result['ok'] and len(sent)==1
    assert sent==reviewed and body['path'] in sent[0]
    updated=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    assert updated['references']==original_refs
    assert len(list((ctx.novel_dir/'first_frames/book_1/clip_01').glob('candidate_*')))==1


@pytest.mark.parametrize('extra_kind',['other_actor','same_actor_unselected_view','unknown'])
def test_owned_bundle_does_not_admit_unselected_catalog_or_unknown_paths(tmp_path,monkeypatch,extra_kind):
    from novel_manga.media.first_frames import compile_opening
    ctx,clip,body,other_body,extra=owned_bundle_case(tmp_path,monkeypatch)
    compiled=compile_opening(ctx,clip,{'clip_id':'clip_01'})
    path={'other_actor':other_body['path'],'same_actor_unselected_view':extra['path'],'unknown':'unknown.png'}[extra_kind]
    row={**compiled.row,'image_references':[*compiled.row['image_references'],path]}
    errors={};compile_opening(ctx,clip,row,field_errors=errors)
    assert 'image_references' in errors
    if extra_kind!='unknown':assert 'catalog-only' in errors['image_references']
    assert not (ctx.novel_dir/'first_frames').exists()
