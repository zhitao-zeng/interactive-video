from novel_manga.application.agents.episode.media_operations import MediaOperations
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode import local_preparation, pipeline_preparation
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


def local_case(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch, old_english=False)
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'preparation_exception':'input/preparation/exception.json'})
    atomic_write_json(ctx.work_dir/'input/preparation/exception.json', {
        'status':'needs_repair','failure':{'reason':'English prompt incomplete'}})
    return ctx, plan


def test_missing_english_gets_one_structural_correction_without_rewriting_siblings(tmp_path,monkeypatch):
    ctx,plan=local_case(tmp_path,monkeypatch);calls=[]
    def patch(ctx,drafts,repairs,failure):
        calls.append(failure)
        after=read_json(ctx.episode_dir/'clip_plan.json');after['clips'][0]['prompt_h3']='Correct English'
        atomic_write_json(ctx.episode_dir/'clip_plan.json',after)
        return {'ok':True}
    monkeypatch.setattr('novel_manga.application.agents.episode.pipeline_preparation.patch_inputs',patch)
    monkeypatch.setattr(pipeline_preparation,'h3_prompt_outdated',lambda clip,*a,**kw:not clip.get('prompt_h3'))
    monkeypatch.setattr(pipeline_preparation,'request_issues',lambda clip:[])
    blocked={'clip_01':'Automatic translation already attempted'}
    assert pipeline_preparation.correct_requests(ctx,None,None,blocked)=={}
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][1:]==plan['clips'][1:]
    assert pipeline_preparation.correct_requests(ctx,None,None,blocked)=={} and len(calls)==1


def test_failed_structural_correction_does_not_repeat_same_request(tmp_path,monkeypatch):
    ctx,plan=local_case(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr('novel_manga.application.agents.episode.pipeline_preparation.patch_inputs',
        lambda *a:calls.append(1)or{'ok':False})
    blocked={'clip_01':'Missing English'}
    assert pipeline_preparation.correct_requests(ctx,None,None,blocked)==blocked
    assert pipeline_preparation.correct_requests(ctx,None,None,blocked)==blocked
    assert len(calls)==1 and read_json(ctx.episode_dir/'clip_plan.json')==plan


def test_saved_slot_failure_is_rechecked_once_without_another_model_call(tmp_path,monkeypatch):
    from novel_manga.application.profiles import h3_stamp
    ctx,plan=local_case(tmp_path,monkeypatch);calls=[]
    atomic_write_json(ctx.work_dir/'output/pipeline-repair.json',{'decisions':[{'clip_id':'clip_01','action':'edit','h3':{}}]})
    atomic_write_json(ctx.run_dir/'native-request-correction.json',{
        'inputs':{'clip_01':h3_stamp(plan['clips'][0],'')},'status':'completed',
        'result':{'valid':False,'argument_errors':{'clip_01':'Shot 2: dialogue slots must occur once in order'}}})
    monkeypatch.setattr(pipeline_preparation,'patch_inputs',lambda *a:pytest.fail('regenerating saved formatter output'))
    drafts=SimpleNamespace(check=lambda p:calls.append('check')or{'valid':False,'argument_errors':{'clip_01':'dialogue slots must occur once in order'}})
    repairs=SimpleNamespace(apply=lambda p:pytest.fail('applying invalid candidate'))
    blocked={'clip_01':'Missing English'}
    assert pipeline_preparation.correct_requests(ctx,drafts,repairs,blocked)==blocked
    assert pipeline_preparation.correct_requests(ctx,drafts,repairs,blocked)==blocked
    assert calls==['check']
    assert read_json(ctx.episode_dir/'clip_plan.json')==plan


def test_staging_retains_plan_without_english_and_without_video(tmp_path, monkeypatch):
    ctx, before=local_case(tmp_path,monkeypatch)
    before_script=read_json(ctx.episode_dir/'chapter_script.json')
    local_preparation.stage(ctx)
    assert read_json(ctx.run_dir/'prepared.json')['local_preparation_pending']
    assert read_json(ctx.episode_dir/'clip_plan.json')==before
    assert read_json(ctx.episode_dir/'chapter_script.json')==before_script
    assert not (ctx.episode_dir/'thin_media_report.json').exists()


def test_source_conflict_cannot_submit_video_or_trigger_asset_work(tmp_path, monkeypatch):
    ctx,before=local_case(tmp_path,monkeypatch);local_preparation.stage(ctx)
    monkeypatch.setattr(local_preparation,'audit',lambda directory:{'source_readable':True,'issues':[
        {'kind':'location','stage':1,'problem':'bedroom was bound to lobby','source_quote':'the original room'}]})
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'files':{'revision':1}})
    monkeypatch.setattr(local_preparation,'ensure_assets',lambda *a:pytest.fail('assets before source repair'))
    monkeypatch.setattr(MediaOperations, 'runner',lambda *a:pytest.fail('video before source repair'))
    result=EpisodeTools(ctx).dispatch('render',{})
    assert not result['ok'] and 'clip_01' in result['blocked']
    assert read_json(ctx.episode_dir/'thin_media_report.json')['blocked_clips']['clip_01']
    assert read_json(ctx.episode_dir/'clip_plan.json')==before


def test_source_gate_clears_only_resolved_preparation_blocks(tmp_path, monkeypatch):
    ctx,_=local_case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', {
        'status': 'inputs_failed', 'clips': [],
        'blocked_clips': {'clip_01': 'old speaker error', 'clip_99': 'unrelated media error'}})
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json', {
        'error': 'local_preparation_required', 'blocked': {'clip_01': 'old speaker error'}})
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'files':{'revision':1}})
    monkeypatch.setattr(local_preparation,'audit',lambda directory:{'source_readable':True,'issues':[]})
    assert local_preparation.source_gate(ctx) is None
    assert read_json(ctx.episode_dir/'thin_media_report.json')['blocked_clips']=={'clip_99':'unrelated media error'}
    latest=read_json(ctx.work_dir/'input/latest-preparation.json')
    assert latest['ok'] and latest['source_audit']['source_readable']


def test_source_check_reuses_same_candidate_but_rechecks_changed_inputs(tmp_path, monkeypatch):
    ctx,_=local_case(tmp_path,monkeypatch);local_preparation.stage(ctx)
    calls=[];revision=[1]
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'files':{'revision':revision[0]}})
    monkeypatch.setattr(local_preparation,'audit',lambda directory:calls.append(revision[0]) or {'source_readable':True,'issues':[]})
    monkeypatch.setattr(local_preparation,'ensure_assets',lambda *a:None)
    monkeypatch.setattr(local_preparation,'ensure_requests',lambda *a:{})
    assert local_preparation.before_render(ctx) is None
    assert local_preparation.before_render(ctx) is None
    assert calls==[1]
    revision[0]=2
    assert local_preparation.before_render(ctx) is None
    assert calls==[1,2]


def test_source_cache_uses_post_audit_fingerprint(tmp_path, monkeypatch):
    ctx,_=local_case(tmp_path,monkeypatch)
    def mutate(directory):
        entities=directory.parent/'entity/entities.json'
        entities.parent.mkdir(parents=True,exist_ok=True)
        entities.write_text('[]')
        return {'source_readable':True,'issues':[]}
    monkeypatch.setattr(local_preparation,'audit',mutate)
    assert local_preparation.source_gate(ctx) is None
    saved=read_json(ctx.run_dir/'local-source-check.json')
    bible=ctx.novel_dir/'story_bible.json'
    original=ctx.episode_dir/'authored_source.md'
    expected=local_preparation.inputs(ctx.episode_dir)['files'] | {
        str(bible):[bible.stat().st_mtime_ns,bible.stat().st_size] if bible.is_file() else None,
        str(original):[original.stat().st_mtime_ns,original.stat().st_size] if original.is_file() else None}
    assert saved['inputs']==expected


def test_source_policy_change_does_not_reuse_old_verdict(tmp_path, monkeypatch):
    ctx,_=local_case(tmp_path,monkeypatch)
    calls=[]
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'files':{'revision':1}})
    monkeypatch.setattr(local_preparation,'audit',lambda directory:calls.append(1) or {'source_readable':True,'issues':[]})
    bible=ctx.novel_dir/'story_bible.json'
    atomic_write_json(ctx.run_dir/'local-source-check.json', {
        'inputs': {'revision': 1, str(bible): None,
                   str(ctx.episode_dir/'authored_source.md'): None},
        'answer': {'source_readable': False, 'issues': ['old false verdict']}})
    assert local_preparation.source_gate(ctx) is None
    assert calls == [1]


def test_source_audit_receives_original_speaker_structure(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as source_audit
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    atomic_write_json(directory/'chapter_script.json', {'shots':[{'index':1,'turns':[]}]})
    atomic_write_json(directory/'segments.json', [{'segment_id':'seg_1','text':'C01：我能直接拿吗？'}])
    atomic_write_json(directory.parent/'story_bible.json', {'characters':[]})
    original={'scenes':[{'sceneId':'S1001','flow':[{'speaker':'C01','line':'我能直接拿吗？'}]}]}
    (directory/'authored_source.md').write_text(__import__('json').dumps(original,ensure_ascii=False))
    monkeypatch.setattr(source_audit.PlannerContext,'from_env',lambda:None)
    monkeypatch.setattr('novel_manga.application.planning.context.load_entity_index',lambda *a,**kw:None)
    monkeypatch.setattr('novel_manga.planning.cast.mentioned_characters',lambda *a,**kw:set())
    monkeypatch.setattr('novel_manga.application.identity.flow.resolve_chapter',lambda d,settings=None:{})
    monkeypatch.setattr('novel_manga.application.identity.context.prompt_context',lambda *a,**kw:{})
    monkeypatch.setattr('novel_manga.application.identity.context.reading_segments',lambda *a,**kw:[])
    prompts=[]
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda parts,*args,**kwargs:prompts.append((parts[0]['text'],kwargs)) or {'source_readable':True,'issues':[]})
    source_audit.audit(directory, settings='official-k3-settings')
    assert 'flow.speaker' in prompts[0][0] and 'C01' in prompts[0][0]
    assert '失主' in prompts[0][0] and '不能替代' in prompts[0][0]
    assert prompts[0][1]['settings'] == 'official-k3-settings'


def test_only_missing_requests_are_translated_and_valid_requests_survive(tmp_path, monkeypatch):
    ctx,before,prompts=prepared(tmp_path,monkeypatch)
    plan=copy.deepcopy(before);plan['clips'][1].pop('prompt_h3')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    called=[]
    def convert(clip, **kwargs):
        called.append(clip['clip_id']);clip['prompt_h3']=before['clips'][1]['prompt_h3'];return True
    monkeypatch.setattr(local_preparation,'convert',convert)
    monkeypatch.setattr(local_preparation,'h3_prompt_outdated',lambda clip,note,**kwargs:not clip.get('prompt_h3'))
    assert local_preparation.ensure_requests(ctx)=={}
    after=read_json(ctx.episode_dir/'clip_plan.json')
    assert called==['clip_02']
    assert after['clips'][0]==before['clips'][0]


def test_failed_translation_is_not_repeated_on_the_same_input(tmp_path, monkeypatch):
    ctx, _=local_case(tmp_path,monkeypatch)
    called=[]
    monkeypatch.setattr(local_preparation,'convert',lambda clip,**kw:called.append(clip['clip_id']) or False)
    first=local_preparation.ensure_requests(ctx)
    second=local_preparation.ensure_requests(ctx)
    assert first and second
    assert len(called)==len(set(called))
    assert all('already attempted' in reason for reason in second.values())


def test_missing_card_is_generated_from_its_registered_asset_id(tmp_path, monkeypatch):
    from types import SimpleNamespace
    ctx,_=local_case(tmp_path,monkeypatch)
    ctx.asset_snapshot_dir=ctx.run_dir/'assets'
    ref={'role':'location','name':'卧房','asset_id':'location_007',
         'path':'series_assets/locations/location_007/establishing.jpeg'}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips':[{'clip_id':'clip_01','references':[ref]}]})
    calls=[]
    def build(command,**kwargs):
        calls.append(command)
        assert command[command.index('--assets')+1]=='location_007'
        picture=ctx.novel_dir/ref['path'];picture.parent.mkdir(parents=True);picture.write_bytes(b'image')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local_preparation.subprocess,'run',build)
    monkeypatch.setattr('novel_manga.application.agents.episode.input_repair.refresh_inputs',lambda *a:None)
    local_preparation.ensure_assets(ctx)
    assert len(calls)==1
    assert (ctx.novel_dir/ref['path']).is_file()
    assert not (ctx.asset_snapshot_dir/ref['path']).is_file()
