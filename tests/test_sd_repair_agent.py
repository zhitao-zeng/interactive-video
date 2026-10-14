import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from novel_manga.application.repair import agent, managed, history
from novel_manga.interactive.agent import repair_lane
from novel_manga.util import atomic_write_json, read_json
from support.managed_episode import fixture_episode


def sd_prompt():
    return ('【生成目标】约5秒。\n【人物】甲对应@图片1。\n'
            '【阶段一·中景】开始时：甲站在门前。主要事件：甲站在门前。入镜：甲。'
            '声音：甲开口说：{你好。}；<脚步声>。结束时：甲站在门前。\n【保持一致】保持人物身份。')


@pytest.mark.parametrize('old,new',[('甲站在门前','甲停在门前'),('你好。','再见。'),
                                   ('甲开口说','乙开口说'),('@图片1','@图片2'),
                                   ('阶段一','阶段二'),('约5秒','约8秒'),('入镜：甲','入镜：甲')])
def test_sd_validator_preserves_compiler_owned_contract(old,new):
    clip={'prompt':sd_prompt(),'prompt_h3':'Stale H3 from a shared master'}
    candidate=clip['prompt'].replace(old,new)
    with repair_lane('sd25'):
        if old in ('甲站在门前','入镜：甲'):
            agent.validate_request(clip,candidate)
        else:
            with pytest.raises(ValueError):agent.validate_request(clip,candidate)


def test_sd_repair_changes_sent_chinese_request_and_keeps_budget(monkeypatch,tmp_path):
    directory,clips,review=fixture_episode(tmp_path)
    plan=read_json(directory/'clip_plan.json')
    plan['clips'][0]['prompt']=sd_prompt()
    plan['clips'][0]['prompt_h3']='unused old H3'
    atomic_write_json(directory/'clip_plan.json',plan)
    seen=[]
    def propose(directory,clip,verdict):
        return {'action':'edit','reason':'source-backed stop','evidence':['input/source.txt'],
                'prompt':clip['prompt'].replace('主要事件：甲站在门前','主要事件：甲停在门前')}
    def check(clip,novel):
        seen.append(copy.deepcopy(clip))
        assert clip['prompt_h3']==''
        return {'consistent':True,'problems':[]}
    monkeypatch.setattr(agent,'propose',propose)
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',check)
    monkeypatch.setattr(managed,'input_state',lambda *a,**k:{})
    with repair_lane('sd25'):
        result=managed.prepare_agent_batch(directory,['a'])
    assert result['changed']==['a']
    after=read_json(directory/'clip_plan.json')
    assert after['clips'][0]['prompt_h3']=='unused old H3'
    assert '主要事件：甲停在门前' in after['clips'][0]['prompt']
    assert after['clips'][1]==plan['clips'][1]
    assert history.load(directory)['trials'][-1]['managed']
    assert not any(history.generation_counts(directory).values())
    assert seen


@pytest.mark.parametrize('lane',['h3','sd25'])
def test_same_agent_gets_original_documents_and_provider(monkeypatch,tmp_path,lane):
    from novel_manga.story.h3 import compose
    directory=tmp_path/'book/book_1';directory.mkdir(parents=True)
    clip={'clip_id':'a','prompt':sd_prompt(),'request_seconds':5,'references':[]}
    clip['prompt_h3']=compose(clip,['A person pauses at the door.'],[('',[])])
    atomic_write_json(directory/'intake.json',{'documents':[{'path':'source_documents/original.md'}]})
    (directory/'source_documents').mkdir()
    (directory/'source_documents/original.md').write_text('原文声源：守卫远处喊声。')
    config={'runs_root':str(tmp_path/'runs')}
    def run(name,**kwargs):
        work=Path(config['runs_root'])/name
        evidence=read_json(work/'input/evidence.json')
        assert evidence['provider']==lane
        assert (work/'input/source_documents/original.md').read_text()=='原文声源：守卫远处喊声。'
        assert (work/'input/prompt.txt').read_text()==clip['prompt_h3' if lane=='h3' else 'prompt']
        atomic_write_json(work/'output/decision.json',{'action':'stop','reason':'needs source correction','evidence':['input/source_documents/original.md']})
        return SimpleNamespace(ok=True,produced=['decision.json'],attempt='1')
    monkeypatch.setattr(agent,'run_agent',run)
    monkeypatch.setattr(agent,'project_root',lambda:Path(__file__).resolve().parents[1])
    with repair_lane(lane):
        answer=agent.propose(directory,clip,{},config=config)
    assert answer['action']=='stop'


def test_sd_published_candidate_is_the_renderer_request(monkeypatch,tmp_path):
    from novel_manga.media.generation import clip_base
    directory=tmp_path/'book/book_1';directory.mkdir(parents=True)
    clip={'clip_id':'a','prompt':sd_prompt(),'prompt_h3':'unused H3','references':[]}
    prompt=clip['prompt'].replace('主要事件：甲站在门前','主要事件：甲停在门前')
    with repair_lane('sd25'):
        proposal=agent.final_proposal(directory,{'clips':[clip]},clip,{'prompt':prompt,'reason':'clear action'})
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url=''),feedback={})
    assert clip_base(ctx,proposal.plan['clips'][0])==prompt


@pytest.mark.parametrize('lane',['h3','sd25'])
def test_rejected_same_input_does_not_restart_agent(monkeypatch,tmp_path,lane):
    directory,clips,_=fixture_episode(tmp_path)
    monkeypatch.setattr(managed,'input_state',lambda *a,**k:{'same':True})
    called=[]
    def propose(*args):
        called.append(1)
        return {'action':'stop','reason':'source binding needs repair','evidence':['input/source.txt']}
    monkeypatch.setattr(agent,'propose',propose)
    with repair_lane(lane):
        first=managed.prepare_agent_batch(directory,['a'])
        again=managed.prepare_agent_batch(directory,['a'])
    assert first['blocked']==again['blocked']
    assert len(called)==1
