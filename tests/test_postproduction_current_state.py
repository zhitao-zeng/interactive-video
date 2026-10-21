from types import SimpleNamespace

from novel_manga.util import atomic_write_json, read_json


def test_registered_cutaway_is_published_and_preserved_in_resume(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import postproduction
    from novel_manga.application.agents.episode.current_task import prepare
    from novel_manga.application.agents.episode.prompts import resume_prompt

    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    plan={'schema':'episode-picture-cutaway-v1','status':'prepared',
        'decision':{'clip_id':'clip_21','action':'cutaway','source_clip_id':'clip_20',
            'source_start_seconds':1.2,'source_end_seconds':2.0,'target_clip_id':'clip_21',
            'target_start_seconds':0.4,'target_end_seconds':1.2,'reason':'show the approved insert'},
        'authorization':{'author':'user'},
        'bindings':[{'binding_id':'binding-001','source':{'clip_id':'clip_20',
                'frames':{'start':30,'end':50}},
            'target':{'clip_id':'clip_21','frames':{'start':10,'end':30}}}]}
    atomic_write_json(ctx.episode_dir/'work/postproduction/cutaway.json',plan)
    monkeypatch.setattr(postproduction,'assembly_inputs',lambda *a: (_ for _ in ()).throw(AssertionError('assembly called')))
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(AssertionError('ffmpeg/ffprobe called')))

    status={'adopted':True,'repair_actions':{},'job':None}
    task=prepare(ctx,status,{'issues':[]},[])
    summary=task['postproduction']
    assert summary['status']=='prepared' and summary['authorized'] is True
    assert summary['source']=={'clip_id':'clip_20','range_seconds':[1.2,2.0],'range_frames':[30,50]}
    assert summary['target']=={'clip_id':'clip_21','range_seconds':[0.4,1.2],'range_frames':[10,30]}
    assert summary['binding_id']=='binding-001'
    published=ctx.work_dir/'input/postproduction-plan.json'
    assert read_json(published)==plan
    assert 'input/postproduction-plan.json' in task['evidence_files']
    assert read_json(ctx.work_dir/'input/current-status.json')['postproduction']==summary
    assert any('call finish' in rule and 'normal assembly' in rule for rule in task['execution_rules'])

    prompt=resume_prompt(task)
    assert 'postproduction' in prompt and 'clip_20' in prompt and 'clip_21' in prompt
    assert 'input/postproduction-plan.json' in prompt and 'do not change or re-authorize it' in prompt
    assert 'finish so the host performs normal assembly' in prompt


def test_missing_cutaway_does_not_create_postproduction_state_or_plan_file(tmp_path):
    from novel_manga.application.agents.episode.current_task import prepare

    ctx=SimpleNamespace(work_dir=tmp_path/'sandbox',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path)
    status={'adopted':True,'repair_actions':{},'job':None}
    task=prepare(ctx,status,{'issues':[]},[])
    assert 'postproduction' not in task
    assert 'postproduction' not in read_json(ctx.work_dir/'input/current-status.json')
    assert not (ctx.work_dir/'input/postproduction-plan.json').exists()
