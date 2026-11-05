"""Synthetic model failure through the real host flow and batch cold-resume boundary."""
import copy
from dataclasses import replace
from pathlib import Path
import time

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames, flow, readiness, turns
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.rendering import h3
from novel_manga.interactive import batch
from novel_manga.llm.client import ModelOutputMalformed
from novel_manga.media.first_frames import compile_opening
from novel_manga.service_errors import UnsupportedInput
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context, sandbox_config, install_fake_runtime
from support.first_frame_cases import case


def retained_retake(ctx):
    """Two synthetic existing pixels, with the original rejected and its replacement passed."""
    clip=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    clip.update(prompt='【阶段1】水面轻晃。结束时：水面平静。画面呈现',request_seconds=4)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clip]})
    compiled=compile_opening(ctx,clip,{'clip_id':'clip_01','extra_references':[]})
    identity={key:compiled.row[key] for key in ('image_prompt','request_prompt','image_references','visible_characters')}
    identity.update(source_contract=compiled.contract,source_assets=compiled.assets,
                    references=[r.as_dict()for r in compiled.references])
    root=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_01'
    for index in (1,2):
        candidate=root/f'candidate_{index:02d}';candidate.mkdir(parents=True)
        saved=copy.deepcopy(identity)
        if index==2:saved['request_prompt']+='\n\nConfirmed defects to correct in this retake:\nwrong pose'
        atomic_write_json(candidate/'input.json',saved)
        Image.new('RGB',(8,16),'blue').save(candidate/'opening.png')
        review={'findings':[]} if index==2 else {'findings':[{
            'origin':'image','relation':'contradiction','reason':'wrong pose'}]}
        inputs=first_frames.review_input(compiled.row,compiled.contract,compiled.assets,
                                        compiled.references,candidate/'opening.png')
        atomic_write_json(candidate/'reviews/review_001.json',{'input':inputs,'raw':review,'review':review})
    atomic_write_json(root/'candidate_01/retake-decision.json',{'replacement_candidate':'candidate_02',
        'reason':'wrong pose','input_relationship':{'replacement_input':read_json(root/'candidate_02/input.json')}})
    return root


@pytest.mark.parametrize('failure_kind', ['malformed','assertion','source'])
def test_supervisor_automatically_cold_resumes_h3_only_without_images_or_agent(
        tmp_path,monkeypatch,episode_context,sandbox_config,failure_kind):
    ff,_=case(tmp_path/'frames',monkeypatch,host_h3=True)
    ctx=replace(episode_context,novel_dir=ff.novel_dir,episode_dir=ff.episode_dir,
                run_dir=ff.run_dir,work_dir=ff.work_dir,input_mode='script')
    calls=install_fake_runtime(monkeypatch,ctx)
    candidate_root=retained_retake(ctx)
    original_pixels={p.relative_to(candidate_root):p.read_bytes()for p in candidate_root.rglob('opening.png')}
    original_reviews={p.relative_to(candidate_root):p.read_bytes()for p in candidate_root.rglob('review_001.json')}
    session={'session_id':'00000000-0000-4000-8000-000000000001','backend':'codex'}
    atomic_write_json(ctx.work_dir/'episode-session.json',session)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'deferred','remaining_seconds':17,
        'deadline':time.time()+17,'agent_seconds':7,'attempts':[]})
    def forbidden(*args,**kwargs):pytest.fail('No image, image review or production Agent in technical recovery')
    monkeypatch.setattr(first_frames,'build',forbidden)
    monkeypatch.setattr(first_frames,'check',forbidden)
    monkeypatch.setattr(turns,'run_session',forbidden)
    monkeypatch.setattr(flow,'prepare_operations',lambda *a:(None,None))
    translated=[]
    failure={'malformed':ModelOutputMalformed('h3prompt',tmp_path/'preserved-response.json'),
             'assertion':AssertionError('unknown compiler invariant'),
             'source':UnsupportedInput('confirmed source conflict')}[failure_kind]
    def model(*args,**kwargs):
        translated.append(1)
        if len(translated)==1:raise failure
        return {'shots':['Water ripples.'],'soundscape':'None.','conflicts':[]}
    monkeypatch.setattr(h3,'ask_json',model)
    fake_tools=flow.EpisodeTools
    class Tools(fake_tools):
        def __init__(self,context):super().__init__(context);self.ctx=context
        def dispatch(self,action,payload):
            if action=='first_frames':
                calls.append(action)
                return {'action':action,**EpisodeTools.first_frames(self,payload)}
            result=super().dispatch(action,payload)
            if action=='status':
                result.update(adopted=True,first_frames=first_frames.status(ctx,
                    read_json(ctx.episode_dir/'clip_plan.json')))
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    real_route=readiness.next_action
    def route(context,status=None):
        current=real_route(context,status)
        if current.get('service_interrupted') or current['action']=='first_frames':return current
        # Stop at the downstream boundary; this fixture never claims a movie,
        # acceptance or upload, and never dispatches render/finish.
        return {'action':'render','retryable':False,'error':'synthetic_downstream_stop',
                'reason':'synthetic stop after recovered H3, before media generation'}
    monkeypatch.setattr(readiness,'next_action',route)
    task=tmp_path/'task.json';atomic_write_json(task,{'mode':'original','brief':'synthetic fixture'})
    spec={'parallel':1,'max_launches':3,'episodes':[{'edition':'one','task':str(task),'lane':'h3'}]}
    spec_path=tmp_path/'batch.json';atomic_write_json(spec_path,spec)
    worker_results=[];worker_resume=[]
    class Worker:
        def __init__(self,command,**kwargs):
            resume='--resume'in command;worker_resume.append(resume)
            # Popen is the process boundary fixture. Host flow, jobs, first-frame
            # operation, classification, lifecycle retry and supervisor are real.
            outcome=flow.run_episode(ctx,config={**sandbox_config,'production_mode':'agent',
                'resume_session_on_retry':True},retry_issues=resume)
            worker_results.append(outcome)
            work=Path(command[command.index('--output-dir')+1])
            atomic_write_json(work/'state.json',{'status':'accepted'if outcome.get('passed')else'blocked',
                    'retryable':not outcome.get('passed')and outcome.get('retryable')is True,
                    'remaining_seconds':outcome['remaining_seconds']})
            self.returncode=outcome['exit_code']
        def poll(self):return self.returncode
    monkeypatch.setattr(batch.subprocess,'Popen',Worker)
    result=batch.run(ctx.project_root,spec_path,tmp_path/'batch-run',retry_seconds=0,poll_seconds=.001)
    row=result['episodes']['one'];expected=2 if failure_kind=='malformed' else 1
    assert row['launches']==expected and worker_resume==([False,True]if expected==2 else[False])
    assert calls.count('first_frames')==expected and len(translated)==expected
    first=worker_results[0]
    assert first['status']=='blocked' and first['job']['result']['clips']['clip_01']['stage']=='h3_compile'
    assert first['job']['result']['clips']['clip_01']['image_review_passed']is True
    assert 0<first['remaining_seconds']<=17 and first['agent_seconds']==7
    assert read_json(ctx.work_dir/'episode-session.json')==session
    assert all((candidate_root/path).read_bytes()==value for path,value in original_pixels.items())
    assert all((candidate_root/path).read_bytes()==value for path,value in original_reviews.items())
    assert len(list(candidate_root.glob('candidate_*')))==2
    assert not list(ctx.episode_dir.glob('repair_budget_grants*'))
    if expected==2:
        assert result['status']=='blocked' and row['history'][0]['result']['retryable']is True
        assert row['history'][0]['launch']==1
        assert 0<worker_results[1]['remaining_seconds']<=first['remaining_seconds']
        assert worker_results[1]['agent_seconds']==7 and worker_results[1]['retryable']is False
        assert worker_results[1]['error']=='synthetic_downstream_stop'
        assert not {'finish','render'}.intersection(calls)
        assert not (ctx.work_dir/'input/latest-finish.json').exists()
        archived=list((ctx.run_dir/'retries').glob('*/agent-run.json'))
        assert len(archived)==1 and read_json(archived[0])['status']=='blocked'
        frame=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']
        assert frame['candidate']=='candidate_02'
    else:
        assert result['status']=='blocked' and row['result']['retryable']is False
        assert not (ctx.run_dir/'retries').exists()
