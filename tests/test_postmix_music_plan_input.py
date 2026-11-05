import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from novel_manga.application.agents.episode.arguments import RepairArgumentError
from novel_manga.application.agents.episode.input_contracts import normalize_arguments
from novel_manga.application.agents.episode.input_effects import effects
from novel_manga.application.agents.episode.input_compilation import InputCompilation,compile_input


def music_plan(shot=1):
    return {'cues':[{'id':'cue','query':'quiet piano','reason':'wait','source_quote':'wait',
        'start':{'shot':shot,'edge':'start'},'end':{'shot':shot,'edge':'end'}}],'silences':[]}


def test_music_plan_is_supported_and_changes_receipt_identity():
    row={'clip_id':'clip_01','music_plan':music_plan()}
    assert normalize_arguments(row)==row
    other={**row,'music_plan':music_plan(2)}
    assert effects(row)!=effects(other)


def test_music_plan_must_be_object():
    with pytest.raises(RepairArgumentError,match='music_plan'):
        normalize_arguments({'clip_id':'clip_01','music_plan':None})


def test_candidate_validates_current_shot_anchors():
    from novel_manga.application.agents.episode.input_candidate import prepare_candidate
    ctx=SimpleNamespace(episode_dir=Path('/unused'))
    with pytest.raises(RepairArgumentError,match='current shot'):
        prepare_candidate(ctx,{'clip_id':'clip_01','music_plan':music_plan(99)},
            {'clips':[]},{'shots':[{'index':1,'duration_seconds':4}]},{},set(),None,None)


def case(tmp_path):
    shot={'index':1,'duration_seconds':4,'audio_plan':{'music_delivery':'postmix','music_cue':'old'}}
    script={'shots':[shot],'music_plan':music_plan()}
    candidate=copy.deepcopy(script);candidate['shots'][0]['audio_plan']['music_cue']='new'
    clip={'clip_id':'clip_01','shot_indexes':[1],'kind':'video','music_delivery':'postmix',
        'prompt':'native text','prompt_h3':'English native','prompt_h3_of':'stamp','repair_take':2,
        'references':[{'path':'original.png'}],'external_music_cues':[{'stage':1,'cue':'old'}]}
    return InputCompilation(SimpleNamespace(episode_dir=tmp_path),
        {'clip_id':'clip_01','music_plan':music_plan()}, {'clips':[clip]},script,candidate,
        {1:candidate['shots'][0]},[{'index':1,'audio_plan':{'music_cue':'new'}}],clip,
        {'clip_01'},{'clip_01':3},None,{},{},{},'clip_01')


def test_postmix_does_not_recompile_or_mutate_native_take(tmp_path,monkeypatch):
    from novel_manga.application.repair import flow
    monkeypatch.setattr(flow,'rebuild_clips',lambda *a,**k:pytest.fail('native compiler drift must not reach pure postmix'))
    c=case(tmp_path);original=copy.deepcopy(c.plan)
    result=compile_input(c,tmp_path/'receipt.json',{'source':{'consistent':True}},lambda _:None)
    expected=copy.deepcopy(original);expected['clips'][0]['external_music_cues']=[{'stage':1,'cue':'new'}]
    assert result.changed==[] and result.plan==expected and c.plan==original
    assert result.proposal.script['music_plan']==music_plan()


def test_postmix_requires_normal_source_approval(tmp_path):
    with pytest.raises(ValueError,match='normal source review'):
        compile_input(case(tmp_path),tmp_path/'receipt.json',{},lambda _:None)


def test_postmix_rejects_native_music_delivery(tmp_path):
    c=case(tmp_path);c.plan['clips'][0]['music_delivery']='native'
    with pytest.raises(ValueError,match='native music delivery'):
        compile_input(c,tmp_path/'receipt.json',{'source':{'consistent':True}},lambda _:None)


from support.input_repair import setup


def test_root_plan_reaches_source_gate_and_publication_without_native_drift(setup,monkeypatch):
    from novel_manga.application.agents.episode import input_repair
    from novel_manga.util import read_json,atomic_write_json
    ctx,plan,_=setup
    for clip in plan['clips']:
        clip['music_delivery']='postmix'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    before=read_json(ctx.episode_dir/'chapter_script.json')
    seen={}
    def check(*args,**kwargs):
        seen.update(kwargs)
        return {'consistent':True,'problems':[]}
    monkeypatch.setattr(input_repair,'source_check',check)
    row={'clip_id':'clip_01','action':'input','reason':'postmix only',
         'source_quote':'甲坐在宿舍','music_plan':music_plan()}
    result=input_repair.repair(ctx,row)
    assert not result['changed']
    assert seen['music_plans']=={'before':before.get('music_plan'),'after':music_plan()}
    assert read_json(ctx.episode_dir/'chapter_script.json')['music_plan']==music_plan()
    assert read_json(ctx.episode_dir/'clip_plan.json')==plan
