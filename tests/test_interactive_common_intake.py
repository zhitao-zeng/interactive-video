import copy
from pathlib import Path
import pytest

from novel_manga.util import read_json, atomic_write_json
from novel_manga.interactive.authored import prepare_authored, pack_authored
from novel_manga.application.agents.episode.context import create_context
from novel_manga.application.agents.episode.inputs import prepare_inputs

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = ROOT/'projects/adventure/laughing-country'


@pytest.mark.parametrize('project', ['adventure', 'romance', 'suspense'])
def test_three_families_retain_source_state_and_interaction_in_agent_handoff(tmp_path, project):
    # Same approved material exercises the shared contract, not a new story adaptation.
    episode = prepare_authored(MATERIAL, tmp_path/f'{project}-test', family=project)
    pack_authored(episode)
    before = read_json(episode/'chapter_script.json')
    ctx = create_context(episode.parent, episode.name, output_root=tmp_path/'agent')
    from dataclasses import replace
    ctx = replace(ctx, run_dir=tmp_path/'agent/run', work_dir=tmp_path/'agent/run/sandbox')
    # Real old receipts survive adoption and repeated handoff byte for byte.
    old = {'repair_history/index.json': b'{"trials":[]}',
           'work/clips/clip_12.take_1.mp4': b'old-video',
           'work/clips/clip_12.mp4.task.json': b'{"task_id":"existing"}'}
    for name, data in old.items():
        path=episode/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    for _ in range(2):
        manifest=prepare_inputs(ctx)
        assert read_json(episode/'chapter_script.json') == before
        assert all((episode/name).read_bytes()==data for name,data in old.items())
        assert read_json(ctx.work_dir/'input/interaction.json') == read_json(episode/'interaction.json')
        assert read_json(ctx.work_dir/'input/voice_specs.json') == read_json(episode.parent/'voice_specs.json')
        assert (ctx.work_dir/'input/source.txt').read_text() == (MATERIAL/'source.md').read_text()
        assert manifest['source_kind']=='authored_document'
        assert 'input/source_documents/02-material.json' in manifest['authoritative_inputs']
        assert read_json(ctx.work_dir/'input/source_documents/02-material.json') == read_json(MATERIAL/'material.json')
        assert read_json(ctx.work_dir/'input/intake.json')['project']==project
    assert ctx.source_episode_dir == episode.resolve()
    assert ctx.episode_dir != ctx.source_episode_dir


def test_chinese_authored_request_uses_same_bounded_repair(monkeypatch,tmp_path):
    from novel_manga.application.repair import authored_request
    from novel_manga.story.h3 import compose
    clip={'clip_id':'clip_01','kind':'video','source_authored_segment':'原镜头20',
          'prompt':'守卫从远处喊话','request_seconds':5,
          'references':[{'role':'voice','name':'守卫','path':'voice.wav'}]}
    clip['prompt_h3']=compose(clip,['A guard calls from the distance.'],[('', [('守卫','抓住他！',True,'急促')])])
    plan={'clips':[clip]}
    atomic_write_json(tmp_path/'clip_plan.json',plan)
    atomic_write_json(tmp_path/'chapter_script.json',{'shots':[]})
    atomic_write_json(tmp_path/'authored_groups.json',[])
    def convert(draft,**kwargs):
        draft['prompt_h3']=draft['prompt_h3'].replace('from the distance','from behind')
        return True
    monkeypatch.setattr(authored_request,'convert',convert)
    monkeypatch.setattr(authored_request,'request_consistency',lambda *a:{'consistent':True,'problems':[]})
    result,_=authored_request.propose(tmp_path,'clip_01','Repair action direction')
    assert result['changed']==['clip_01']
    assert read_json(tmp_path/'clip_plan.json')==plan
    assert not (tmp_path/'repair_history').exists()


@pytest.mark.parametrize('lane',['h3','sd25'])
def test_common_run_uses_existing_repair_loop_and_restores_scope(monkeypatch,lane):
    import os
    from types import SimpleNamespace
    from novel_manga.interactive import agent
    from novel_manga.application.repair.agent import enabled
    runner=SimpleNamespace(context=SimpleNamespace(profile={'frame':'9:16'}))
    previous=runner.context.profile
    def reviewed(actual):
        assert actual is runner
        assert os.environ['NOVEL_INTERACTIVE_REPAIR_LANE']==lane
        assert runner.context.profile['repair_backend']=='sandbox_agent'
        assert enabled(Path('/unused'))
        return {'status':'candidate'}
    monkeypatch.delenv('NOVEL_INTERACTIVE_REPAIR_LANE',raising=False)
    monkeypatch.setattr('novel_manga.application.rendering.reviewed.run',reviewed)
    assert agent.run(runner,lane)=={'status':'candidate'}
    assert 'NOVEL_INTERACTIVE_REPAIR_LANE' not in os.environ
    assert runner.context.profile is previous


@pytest.mark.parametrize('matches',[True,False])
def test_resume_reuses_only_report_matching_current_requests(monkeypatch,tmp_path,matches):
    from types import SimpleNamespace
    from novel_manga.interactive import agent
    runner=SimpleNamespace(context=SimpleNamespace(profile={},clip_plan={'clips':[]},episode_dir=tmp_path,feedback={}))
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**k:matches)
    seen=[]
    monkeypatch.setattr('novel_manga.application.rendering.reviewed.run',lambda *a,**k:seen.append(k) or {})
    agent.run(runner,'h3',resume=True)
    assert seen==[{'repair_existing':True} if matches else {}]
