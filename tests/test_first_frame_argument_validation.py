import pytest

from novel_manga.application.agents.episode.first_frames import FirstFrameOperations
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


def test_authored_conflicts_do_not_hide_other_preflight_errors_in_same_batch(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    for clip in clips:
        clip['shots'][0]['opening_frame']={'image_prompt':'Approved opening at an empty pool',
            'visible_characters':['A'],'reference_subjects':['pool']}
    plan_path=ctx.episode_dir/'clip_plan.json';atomic_write_json(plan_path,{'clips':clips})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    for row in draft['frames']:
        row['image_references']=['unregistered.png']
        row['extra_references']=['not-an-original-reference.png']
    atomic_write_json(draft_path,draft)
    protected={p:p.read_bytes() for p in (draft_path,plan_path)}
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('preflight must spend no image capacity'))
    with pytest.raises(ValueError):operation.prepare({})
    result=operation.partial_outcome
    assert result['validation_targets']==['clip_01','clip_02']
    assert result['validated_clips']==[]
    for cid in result['validation_targets']:
        assert set(result['invalid_fields'][cid])=={
            'image_prompt','visible_characters','image_references','extra_references'}
        errors=' '.join(result['argument_errors'][cid])
        assert 'input repair' in errors
        assert 'approved source opening_frame' in errors and 'registered source cards' in errors
        assert 'current source image paths' in errors
    assert all(p.read_bytes()==value for p,value in protected.items())
    assert not (ctx.novel_dir/'first_frames').exists()


def test_bad_row_field_types_still_report_independent_errors_and_other_rows(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    draft['frames'][0].update(image_references=None,extra_references='not-a-list')
    draft['frames'][1]['extra_references']='also-not-a-list'
    atomic_write_json(draft_path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('invalid batch'))
    with pytest.raises(ValueError):operation.prepare({})
    assert set(operation.partial_outcome['invalid_fields']['clip_01'])=={'image_references','extra_references'}
    assert operation.partial_outcome['invalid_fields']['clip_02']==['extra_references']
    assert operation.partial_outcome['validation_targets']==['clip_01','clip_02']


def test_correcting_authored_fields_leaves_only_reference_failure_without_rewriting_source(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    for clip in clips:
        clip['shots'][0]['opening_frame']={'image_prompt':'Approved opening at an empty pool',
            'visible_characters':[],'reference_subjects':[]}
    plan_path=ctx.episode_dir/'clip_plan.json';atomic_write_json(plan_path,{'clips':clips})
    before=plan_path.read_bytes()
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    for row in draft['frames']:
        row.pop('image_prompt');row.pop('visible_characters')
        row['extra_references']=['not-an-original-reference.png']
    atomic_write_json(draft_path,draft)
    operation=FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('reference failure still blocks'))
    with pytest.raises(ValueError):operation.prepare({})
    assert operation.partial_outcome['invalid_fields']=={
        'clip_01':['extra_references'],'clip_02':['extra_references']}
    assert operation.partial_outcome['validated_clips']==[]
    assert plan_path.read_bytes()==before


def test_duplicate_references_are_not_a_second_reference_authority(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0]
    row['image_references']=['scene.png','scene.png']
    atomic_write_json(draft_path,{'frames':[row]})
    with pytest.raises(ValueError,match='host-compiled opening references'):
        FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('duplicate references must not generate')).prepare({})


def test_missing_h3_reuses_current_valid_request(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    from novel_manga.application.profiles import h3_stamp
    clips[0]['prompt_h3']='existing complete H3 request'
    clips[0]['first_frame']={'path':'first_frames/book_1/clip_01/candidate_01/opening.png'}
    clips[0]['prompt_h3_of']=h3_stamp(clips[0],'')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0]
    row.pop('h3',None);row.pop('prompt_h3',None)
    atomic_write_json(draft_path,{'frames':[row]})
    captured=[]
    def build(c,r,p):captured.append(r);Image.new('RGB',(8,16)).save(p)
    result=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok']
    assert captured[0]['image_references']==['scene.png']
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['prompt_h3']=='existing complete H3 request'


def test_first_frame_row_does_not_create_source_quote(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    atomic_write_json(draft_path,{'frames':[draft['frames'][0]]})
    captured=[]
    def build(c,r,p):captured.append(r);Image.new('RGB',(8,16)).save(p)
    result=FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok'] and 'source_quote' not in captured[0]


def test_missing_source_text_does_not_block_host_compiled_first_frame(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    clips[0]['segment_ids']=['missing']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    atomic_write_json(draft_path,{'frames':[draft['frames'][0]]})
    result=FirstFrameOperations(ctx,
        builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
        checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok']


def test_legacy_h3_mismatch_is_rejected_after_host_compilation(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0];row['prompt_h3']='Agent request differing from the host'
    atomic_write_json(draft_path,{'frames':[row]})
    with pytest.raises(ValueError,match='supplied H3 differs'):
        FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
            checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})


def test_each_redundant_h3_field_must_match_host_compilation(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft_path=ctx.work_dir/'output/first-frames.json';draft=read_json(draft_path)
    row=draft['frames'][0]
    row['h3']={'shots':['A waits.'],'soundscape':'Water.'}
    row['prompt_h3']='A different complete request'
    atomic_write_json(draft_path,{'frames':[row]})
    monkeypatch.setattr('novel_manga.application.agents.episode.requests.compose_request',
        lambda *a:'English request')
    with pytest.raises(ValueError,match='supplied H3 differs'):
        FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
            checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})


def minimal_case(tmp_path,monkeypatch):
    from PIL import Image
    ctx,clips=case(tmp_path,monkeypatch,host_h3=True)
    clip=clips[0]
    clip['shots'][0]['opening_frame']={'image_prompt':'empty pool','visible_characters':[],
                                      'reference_subjects':['pool']}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{
        'frames':[{'clip_id':clip['clip_id'],'extra_references':[]}]})
    return ctx,clip,Image


def install_host_h3(monkeypatch,ctx,events):
    from novel_manga.application.profiles import h3_stamp
    calls=[]
    def convert(candidate,**kwargs):
        events.append('convert')
        calls.append(candidate['first_frame']['path'])
        assert candidate['first_frame']['path']=='first_frames/book_1/clip_01/candidate_01/opening.png'
        candidate['prompt_h3']='host compiled six-section request'
        candidate['prompt_h3_of']=h3_stamp(candidate,kwargs.get('note',''))
        return True
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',convert)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',
        lambda candidate,prompt:events.append('validate'))
    return calls


def test_missing_h3_is_compiled_after_opening_review_with_actual_frame(tmp_path,monkeypatch):
    ctx,clip,Image=minimal_case(tmp_path,monkeypatch)
    events=[]
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',
        lambda context,prepared:events.append('opening_review') or {})
    calls=install_host_h3(monkeypatch,ctx,events)
    def build(context,row,path):
        events.append('image')
        assert 'source_quote' not in row
        Image.new('RGB',(8,16)).save(path)
    result=FirstFrameOperations(ctx,builder=build,
        checker=lambda *a:events.append('image_review') or {'consistent':True,'problems':[]}).prepare({})
    assert result['ok']
    assert events==['opening_review','image','image_review','convert','validate']
    saved=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    assert saved['prompt_h3']=='host compiled six-section request'
    assert calls==[saved['first_frame']['path']]
    assert 'source_quote' not in result['first_frames']['schema']['frames'][0]


def test_existing_h3_reuse_requires_fresh_stamp_and_successful_validation(tmp_path,monkeypatch):
    ctx,clip,Image=minimal_case(tmp_path,monkeypatch)
    events=[]
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',
        lambda *a:{})
    calls=install_host_h3(monkeypatch,ctx,events)
    operation=lambda:FirstFrameOperations(ctx,
        builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
        checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert operation()['ok'] and len(calls)==1
    assert operation()['ok'] and len(calls)==1
    plan_path=ctx.episode_dir/'clip_plan.json'
    plan=read_json(plan_path);plan['clips'][0]['prompt_h3_of']='stale';atomic_write_json(plan_path,plan)
    assert operation()['ok'] and len(calls)==2
    original=__import__('novel_manga.application.agents.episode.first_frames',fromlist=['validate_existing_request']).validate_existing_request
    state={'fail':True}
    def reject_once(candidate,prompt):
        if state['fail']:
            state['fail']=False
            raise ValueError('invalid Picture mapping')
        return original(candidate,prompt)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request',reject_once)
    assert operation()['ok'] and len(calls)==3


def test_opening_source_conflict_blocks_host_h3_translation(tmp_path,monkeypatch):
    ctx,clip,Image=minimal_case(tmp_path,monkeypatch)
    finding={'origin':'source','relation':'contradiction','observed':'wide','expected':'close',
             'basis':['opening.shot_scale','opening.camera'],'reason':'incompatible opening'}
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',
        lambda *a:{clip['clip_id']:{'findings':[finding]}})
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',
        lambda *a,**k:pytest.fail('opening conflict must block translation'))
    result=FirstFrameOperations(ctx,
        builder=lambda *a:pytest.fail('opening conflict must block image generation')).prepare({})
    assert not result['ok'] and result['error']=='opening_source_conflict'


def test_agent_first_frame_protocol_is_minimal_everywhere(tmp_path,monkeypatch):
    from pathlib import Path
    from novel_manga.application.agents.episode.first_frames import status
    from novel_manga.application.agents.episode.prompts import first_frame_instructions
    ctx,clip,Image=minimal_case(tmp_path,monkeypatch)
    schema=status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))['schema']['frames'][0]
    assert set(schema)=={'clip_id','extra_references','reuse_candidate','retake_candidate'}
    instructions=first_frame_instructions(ctx)
    assert 'PREFER h3' not in instructions and 'source_quote' not in instructions
    assert 'frames:[{clip_id,extra_references' in instructions
    root=Path(__file__).parents[1]
    for name in ('output-contract.md','production-workflow.md'):
        text=(root/'skills/episode-production/references'/name).read_text()
        section=text.split('## First-frame host compilation',1)[1]
        assert '"clip_id"' in section and '"extra_references"' in section
        assert 'source_quote' not in section


def test_unknown_subject_names_aggregate_all_clips_before_any_candidate(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,clips=case(tmp_path,monkeypatch)
    for clip,name in zip(clips,('scene','pool_alias')):
        clip['shots'][0]['opening_frame']['reference_subjects']=[name]
    atomic_write_json(ctx.novel_dir/'bible_aliases.json',{'pool_alias':'pool'})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    monkeypatch.setattr(first_frames,'build',
        lambda *a:pytest.fail('unknown source names must not allocate or build an image'))
    result=EpisodeTools(ctx).first_frames({})
    assert result['invalid_fields']=={
        'clip_01':['opening_frame.reference_subjects'],
        'clip_02':['opening_frame.reference_subjects']}
    assert result['required_action']=='apply'
    assert result['image_candidates_allocated']==0
    for cid,name in [('clip_01','scene'),('clip_02','pool_alias')]:
        row=result['clips'][cid]
        assert row['repair_origin']=='source' and name in row['error']
        assert row['repair_grant']['operation']=='input'
        assert row['repair_grant']['shot_fields']==['opening_frame']
    assert not (ctx.novel_dir/'first_frames').exists()


@pytest.mark.parametrize('failure',['catalog_unbound','missing_file','hash_mismatch'])
def test_reference_registration_failures_do_not_grant_story_repairs(tmp_path,monkeypatch,failure):
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,clips=case(tmp_path,monkeypatch)
    clip=clips[0]
    if failure=='catalog_unbound':
        clip['references']=[]
    elif failure=='missing_file':
        (ctx.novel_dir/'scene.png').unlink()
    else:
        clip['references'][0]['sha256']='0'*64
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    monkeypatch.setattr(first_frames,'build',
        lambda *a:pytest.fail('registration failures must stop before image generation'))
    result=EpisodeTools(ctx).first_frames({})
    row=result['clips']['clip_01']
    assert row['repair_origin']=='registration' and 'repair_grant' not in row
    assert result.get('required_action')!='apply'
    assert result['image_candidates_allocated']==0
    expected=(['image_references','opening_frame.reference_subjects']
              if failure=='catalog_unbound' else ['image_references'])
    assert result['invalid_fields']['clip_01']==expected
    assert not (ctx.novel_dir/'first_frames').exists()


def test_catalog_unbound_precedes_unknown_then_bound_names_compile_normally(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,clips=case(tmp_path,monkeypatch)
    clip=clips[0];other={'role':'location','name':'other','path':'other.png'}
    Image.new('RGB',(8,16),'green').save(ctx.novel_dir/'other.png')
    clip['shots'][0]['opening_frame']['reference_subjects']=['pool','other','actually-unknown']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',[*clip['references'],other])
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    monkeypatch.setattr(first_frames,'build',
        lambda *a:pytest.fail('invalid registration must not build'))
    result=EpisodeTools(ctx).first_frames({})
    assert result['clips']['clip_01']['repair_origin']=='registration'
    assert 'repair_grant' not in result['clips']['clip_01'] and result.get('required_action')!='apply'

    clip['references'].append(other)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    result=EpisodeTools(ctx).first_frames({})
    assert result['clips']['clip_01']['repair_origin']=='source'
    assert result['clips']['clip_01']['repair_grant']['shot_fields']==['opening_frame']
    assert result['required_action']=='apply'

    clip['shots'][0]['opening_frame']['reference_subjects']=['pool','other']
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01','extra_references':[]}]})
    built=[]
    def build(context,row,path):
        built.append(row['image_references']);Image.new('RGB',(8,16)).save(path)
    result=first_frames.FirstFrameOperations(ctx,builder=build,
        checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok'] and built==[['scene.png','other.png']]


def test_explicit_catalog_only_path_is_registration_not_an_author_grant(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,clips=case(tmp_path,monkeypatch)
    clip=clips[0];other={'role':'location','name':'other','path':'other.png'}
    Image.new('RGB',(8,16),'green').save(ctx.novel_dir/'other.png')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    atomic_write_json(ctx.run_dir/'catalog.json',[*clip['references'],other])
    row=read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    row['image_references']=['scene.png','other.png']
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    monkeypatch.setattr(first_frames,'build',lambda *a:pytest.fail('catalog-only path must not build'))
    result=EpisodeTools(ctx).first_frames({})
    assert result['invalid_fields']['clip_01']==['image_references']
    assert result['clips']['clip_01']['repair_origin']=='registration'
    assert 'repair_grant' not in result['clips']['clip_01'] and result.get('required_action')!='apply'
