"""The full entry preserves work and never spends media after a failed author."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.interactive import film,creation,production,runtime

REAL_CONFIGURE = runtime.configure


@pytest.fixture(autouse=True)
def isolated_service_configuration(monkeypatch):
    monkeypatch.setattr(runtime, 'configure', lambda *args, **kwargs:None)


def task(tmp_path):
    path=tmp_path/'request.json'
    atomic_write_json(path,{'mode':'original','project':'suspense','brief':'Original test','target_seconds':[45,60]})
    return path


def test_failed_creation_stops_before_media_and_freezes_input(tmp_path,monkeypatch):
    source=task(tmp_path);calls=[]
    def create(path,directory,**kwargs):
        calls.append(read_json(path))
        return {'status':'time_budget_used'}
    monkeypatch.setattr(creation,'run',create)
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:pytest.fail('media must not start'))
    result=film.run(tmp_path,source,tmp_path/'run','new-film')
    assert result['status']=='blocked' and not(tmp_path/'outputs/new-film').exists()
    assert calls[0]['production_handoff'] is True
    assert read_json(source).get('production_handoff') is None
    assert film.run(tmp_path,source,tmp_path/'run','new-film')==result
    assert len(calls)==1


def test_existing_edition_is_refused_before_calling_author(tmp_path,monkeypatch):
    source=task(tmp_path);(tmp_path/'outputs/old').mkdir(parents=True)
    monkeypatch.setattr(creation,'run',lambda *a,**k:pytest.fail('existing edition must be preserved'))
    with pytest.raises(ValueError,match='Existing edition'):
        film.run(tmp_path,source,tmp_path/'run','old')


def test_unsupported_world_card_is_rejected_before_spending_author_budget(tmp_path,monkeypatch):
    source=task(tmp_path)
    data=read_json(source);data.update(world_card_seconds=3,world_card_text='Required world fact')
    atomic_write_json(source,data)
    monkeypatch.setattr(creation,'run',lambda *a,**k:pytest.fail('do not accept a card that cannot be delivered'))
    with pytest.raises(ValueError,match='world.card'):
        film.run(tmp_path,source,tmp_path/'run','new')


@pytest.mark.parametrize('error,retryable',[(TimeoutError('poll timed out'),True),
    (RuntimeError('task submission returned HTTP 502: gateway unavailable'),True),
    (RuntimeError('task submission returned HTTP 402: quota_not_enough'),False),
    (RuntimeError('task submission returned HTTP 429: {"error":{"code":"quota_not_enough"}}'),False),
    (RuntimeError('task submission returned HTTP 429: {"error":{"code":"insufficient_quota"}}'),False),
    (RuntimeError('task submission returned HTTP 429: {"error":{"code":"insufficient_credits","available_points":0}}'),False),
    (RuntimeError('task submission returned HTTP 429: {"error":{"code":"insufficient_credits","available_points":12}}'),True),
    (RuntimeError('task submission returned HTTP 429: rate limit reached'),True),
    (ValueError('source actor mismatch'),False)])
def test_formal_produce_distinguishes_transient_service_from_content_or_quota(tmp_path,monkeypatch,error,retryable):
    source=task(tmp_path)
    def failed(*a,**k):raise error
    monkeypatch.setattr(creation,'run',failed)
    result=film.run(tmp_path,source,tmp_path/'run','new')
    assert result['status']=='blocked' and result['retryable'] is retryable


@pytest.mark.parametrize('status,changed,used,attempts',[
    ('draft_invalid',True,12,2),('agent_failed',True,12,2),
    ('draft_invalid',False,12,1),('revision_stalled',True,12,1),
    ('draft_invalid',True,90,1)])
def test_progressing_creator_continues_inside_the_same_film_call(tmp_path,monkeypatch,status,changed,used,attempts):
    calls=[];run_dir=tmp_path/'run'
    atomic_write_json(run_dir/'creator/sandbox/output/episode.json',{'revision':0})
    def create(path,directory,**kwargs):
        calls.append((directory,kwargs))
        if len(calls)>1:return {'status':'time_budget_used','seconds_used':90,'allowance_seconds':90}
        atomic_write_json(directory/'sandbox/output/episode.json',{'revision':int(changed)})
        return {'status':status,'draft_changed':changed,'agent_exit':124,
            'seconds_used':used,'allowance_seconds':90,'issues':['top-level clips required']}
    monkeypatch.setattr(creation,'run',create)
    result=film.run(tmp_path,task(tmp_path),run_dir,'new')
    assert result['status']=='blocked' and result['retryable'] is False
    assert len(calls)==attempts
    if attempts==2:
        assert calls[0][0]==calls[1][0] and calls[1][1]['resume'] is True


def test_changed_flag_does_not_repeat_an_unchanged_invalid_draft(tmp_path,monkeypatch):
    calls=[]
    def create(*args,**kwargs):
        calls.append(1)
        return {'status':'draft_invalid','draft_changed':True,'seconds_used':12,'allowance_seconds':5400}
    monkeypatch.setattr(creation,'run',create)
    result=film.run(tmp_path,task(tmp_path),tmp_path/'run','new')
    assert result['status']=='blocked' and result['retryable'] is False and calls==[1]


def test_creator_can_continue_correcting_a_retained_invalid_top_level_array(tmp_path,monkeypatch):
    run_dir=tmp_path/'run';calls=[]
    atomic_write_json(run_dir/'creator/sandbox/output/episode.json',[])
    def create(path,directory,**kwargs):
        calls.append(1)
        atomic_write_json(directory/'sandbox/output/episode.json',{'revision':len(calls)})
        return {'status':'draft_invalid' if len(calls)==1 else 'time_budget_used',
            'draft_changed':True,'seconds_used':10*len(calls),'allowance_seconds':20}
    monkeypatch.setattr(creation,'run',create)
    result=film.run(tmp_path,task(tmp_path),run_dir,'new-film')
    assert result['status']=='blocked' and result['retryable'] is False and calls==[1,1]
    assert 'error' not in result


def test_partially_fixed_json_syntax_is_real_author_progress(tmp_path, monkeypatch):
    run_dir = tmp_path/'run'; draft = run_dir/'creator/sandbox/output/episode.json'
    draft.parent.mkdir(parents=True); draft.write_text('{')
    calls = []
    def create(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            draft.write_text('{"title":')
            return {'status':'draft_invalid', 'draft_changed':True,
                    'seconds_used':10, 'allowance_seconds':20}
        return {'status':'time_budget_used', 'seconds_used':20, 'allowance_seconds':20}
    monkeypatch.setattr(creation, 'run', create)
    result = film.run(tmp_path, task(tmp_path), run_dir, 'new-film')
    assert result['status'] == 'blocked' and result['retryable'] is False
    assert calls == [1, 1]


def review_draft(event='待修订'):
    return {'title':'审核交接','chapter_script':{'shots':[{
        'index':1,'start_state':'C卡尚未加入','event':event,'end_state':'三张卡并排',
        'storyboard':{'frame':'时间零点：A、B、C三张卡并排'}}]}}


def creative_receipt(episode, sources, *, rejected=True):
    from novel_manga.application.preparation.audit import creative_fingerprint
    issues=[{'kind':'action','stage':1,'relation':'contradiction','evidence_origin':'authored_field',
        'basis':['chapter_script.shots[0].start_state','chapter_script.shots[0].storyboard.frame'],
        'observed':'C卡尚未加入','expected':'A、B、C三张卡并排','observed_time':'t=0','expected_time':'t=0',
        'source_segment':'','source_quote':'','problem':'零点描述冲突','correction':'统一零点描述'}] if rejected else []
    answer={'source_readable':True,'source_problem':'','issues':issues}
    return {'fingerprint':creative_fingerprint(episode,sources),'answer':answer,
            'raw':answer,'confirmation':None}


def candidate_creator(calls, drafts):
    def create(path,directory,**kwargs):
        calls.append((directory,kwargs))
        atomic_write_json(directory/'sandbox/input/manifest.json', {
            'profile':{},'task':{'brief':'Original test'},'source_documents':[]})
        (directory/'sandbox/input/brief.md').write_text('Original test')
        draft=drafts[min(len(calls)-1,len(drafts)-1)]
        atomic_write_json(directory/'sandbox/output/episode.json',draft)
        atomic_write_json(directory/'creator-state.json', {
            'status':'creative_candidate','session_id':'same-author','seconds_used':10*len(calls),'allowance_seconds':90})
        return {'status':'creative_candidate','session_id':'same-author',
                'seconds_used':10*len(calls),'allowance_seconds':90}
    return create


def test_formal_entry_configures_the_actual_audit_endpoint_before_source_review(tmp_path, monkeypatch):
    import os
    from novel_manga.application.preparation import audit as source_audit
    from novel_manga.llm.config import ENDPOINTS

    (tmp_path/'configs').mkdir()
    atomic_write_json(tmp_path/'configs/interactive.json', {
        'services':{'text_endpoint':'codex-luna'}, 'production':{}})
    atomic_write_json(tmp_path/'configs/pipeline.json', {})
    for key in list(os.environ):
        if key.startswith('QWEN38_LOCAL_') or key == 'NOVEL_INTERACTIVE_TEXT_ENDPOINT':
            monkeypatch.delenv(key)
    monkeypatch.setattr(runtime, 'configure', REAL_CONFIGURE)
    calls = []
    monkeypatch.setattr(creation, 'run', candidate_creator(calls, [review_draft()]))
    observed = []
    def review(episode, sources, **kwargs):
        observed.append((os.environ.get('QWEN38_LOCAL_BASE_URL'), os.environ.get('QWEN38_LOCAL_MODEL')))
        return creative_receipt(episode, sources)
    monkeypatch.setattr(source_audit, 'creative_audit', review)
    monkeypatch.setattr(production, 'run_episode', lambda *args, **kwargs:pytest.fail('rejected source entered production'))
    result = film.run(tmp_path, task(tmp_path), tmp_path/'run', 'new-film')
    assert result['pending_feedback']
    endpoint = ENDPOINTS['codex-luna']
    assert observed == [(endpoint['QWEN38_LOCAL_BASE_URL'], endpoint['QWEN38_LOCAL_MODEL'])]
    assert not (tmp_path/'run/material').exists()


@pytest.mark.parametrize('draft_count',[2,5])
def test_creative_rejection_returns_to_same_author_until_approved_in_one_film_call(tmp_path,monkeypatch,draft_count):
    from novel_manga.application.preparation import audit as source_audit
    from novel_manga.interactive import creative_material,authored,batch
    calls=[];reviews=[];events=[]
    source=task(tmp_path);run_dir=tmp_path/'batch/new-film'
    create=candidate_creator(calls,[review_draft(f'第{number}稿') for number in range(draft_count)])
    def author(*args,**kwargs):
        events.append('author');return create(*args,**kwargs)
    monkeypatch.setattr(creation,'run',author)
    def review(episode,sources,**kwargs):
        events.append('review')
        assert not (run_dir/'material').exists() and not (tmp_path/'outputs/new-film').exists()
        receipt=creative_receipt(episode,sources,rejected=len(reviews)<draft_count-1)
        reviews.append(receipt)
        assert sources and all(isinstance(text,str) for text in sources.values())
        return receipt
    monkeypatch.setattr(source_audit,'creative_audit',review)
    def material(episode,manifest,destination,**kwargs):
        events.append('material');atomic_write_json(destination/'material.json',episode);return destination
    def prepare(material,book,**kwargs):
        atomic_write_json(book/(book.name+'_1')/'clip_plan.json',{'clips':[]})
    def assets():
        events.append('assets')
        receipt=read_json(tmp_path/'outputs/new-film/new-film_1/pre_render_story_audit.json')
        assert receipt['fingerprint']==reviews[-1]['fingerprint'] and receipt['answer']['issues']==[]
    monkeypatch.setattr(creative_material,'write_material',material)
    monkeypatch.setattr(authored,'prepare_authored',prepare)
    monkeypatch.setattr('novel_manga.interactive.first_frame_entry.select_mode',lambda *a:None)
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(build_assets=assets))
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:events.append('production') or {'status':'accepted'})
    if draft_count>3:
        spec=tmp_path/'batch.json'
        atomic_write_json(spec,{'max_launches':3,'episodes':[{'edition':'new-film','task':str(source)}]})
        # Keep the supervisor real; replace only the subprocess boundary with the
        # ordinary film entry so an early blocked result would spend a launch.
        def launch(command,**kwargs):
            film.run(tmp_path,source,Path(command[0]),'new-film',resume=command[1])
            return SimpleNamespace(poll=lambda:0)
        monkeypatch.setattr(batch.subprocess,'Popen',launch)
        batch_result=batch.run(tmp_path,spec,run_dir.parent,
            command=lambda root,item,directory,resume:[str(directory),resume],retry_seconds=0,poll_seconds=.01)
        row=batch_result['episodes']['new-film']
        assert batch_result['status']=='accepted' and row['launches']==1
        result=row['result']
    else:
        result=film.run(tmp_path,source,run_dir,'new-film')
    assert result['status']=='accepted' and len(calls)==len(reviews)==draft_count
    assert events==['author','review']*draft_count+['material','assets','production']
    assert all(directory==calls[0][0] for directory,kwargs in calls)
    for number,(directory,options) in enumerate(calls[1:]):
        feedback=run_dir/'creative-feedback'/f"{reviews[number]['fingerprint']}.json"
        assert options['resume'] is True and options['feedback']==feedback
        assert read_json(feedback)['issues']==reviews[number]['answer']['issues']
    assert result['creator']['session_id']=='same-author' and result['creator']['seconds_used']==10*draft_count
    assert 'pending_feedback' not in result and 'pending_review_fingerprint' not in result


def test_same_fingerprint_after_feedback_stops_without_another_author_or_review_call(tmp_path,monkeypatch):
    from novel_manga.application.preparation import audit as source_audit
    from novel_manga.interactive import creative_material
    calls=[];reviews=[]
    monkeypatch.setattr(creation,'run',candidate_creator(calls,[review_draft()]))
    monkeypatch.setattr(source_audit,'creative_audit',
        lambda episode,sources,**kwargs:reviews.append(1) or creative_receipt(episode,sources))
    monkeypatch.setattr(creative_material,'write_material',lambda *a,**k:pytest.fail('unchanged rejected draft must not materialize'))
    source=task(tmp_path);run_dir=tmp_path/'run'
    stopped=film.run(tmp_path,source,run_dir,'new-film')
    assert stopped['status']=='blocked' and stopped['retryable'] is False
    assert len(calls)==2 and calls[1][1]['feedback'] is not None and len(reviews)==1
    assert film.run(tmp_path,source,run_dir,'new-film',resume=True)==stopped
    assert len(calls)==2 and len(reviews)==1


def test_author_review_loop_keeps_the_original_5400_seconds_and_stops_at_exhaustion(tmp_path,monkeypatch):
    from novel_manga.application.preparation import audit as source_audit
    from novel_manga.interactive import creative_material
    run_dir=tmp_path/'run';creator=run_dir/'creator';calls=[];reviews=[]
    atomic_write_json(creator/'creator-state.json',{
        'status':'draft_invalid','session_id':'original-author','seconds_used':5380,'allowance_seconds':5400})
    atomic_write_json(creator/'sandbox/output/episode.json',review_draft('保留的原稿'))
    def invoke(work,prompt,**kwargs):
        calls.append((work,kwargs))
        assert kwargs['resume'] is True
        assert kwargs['timeout']==(20 if len(calls)==1 else 10)
        if len(calls)==2:
            feedback=read_json(work/'input/feedback/revision-01.md')
            assert feedback['fingerprint']==reviews[0]['fingerprint']
        atomic_write_json(work/'output/episode.json',review_draft(f'修订{len(calls)}'))
        (work/'output/creative-notes.md').write_text('Test author boundary')
        attempt=work/'attempts'/str(len(calls));attempt.mkdir(parents=True)
        atomic_write_json(attempt/'attempt.json',{'seconds':10})
        return SimpleNamespace(ok=True,exit_code=0,seconds=10,directory=attempt,
            record={'session_id':'original-author','model':'configured'})
    monkeypatch.setattr(creation,'run_session',invoke)
    monkeypatch.setattr(creation,'validate',lambda *a:[])
    monkeypatch.setattr(creation,'read_evidence',lambda work:[
        {'path':path} for path in read_json(work/'input/manifest.json')['required_reads']])
    monkeypatch.setattr(creation,'export',lambda *a,**k:None)
    def review(episode,sources,**kwargs):
        receipt=creative_receipt(episode,sources);reviews.append(receipt);return receipt
    monkeypatch.setattr(source_audit,'creative_audit',review)
    monkeypatch.setattr(creative_material,'write_material',lambda *a,**k:pytest.fail('rejected draft must not materialize'))
    result=film.run(tmp_path,task(tmp_path),run_dir,'new-film',config={'timeout_seconds':9999})
    assert result['status']=='blocked' and result['retryable'] is False
    assert len(calls)==len(reviews)==2 and calls[0][0]==calls[1][0]==creator/'sandbox'
    assert result['creator']['session_id']=='original-author'
    assert result['creator']['seconds_used']==5400 and result['creator']['allowance_seconds']==5400
    assert read_json(creator/'creator-state.json')['seconds_used']==5400
    assert result['pending_review_fingerprint']==reviews[-1]['fingerprint']
    assert not (run_dir/'material').exists()
    film.run(tmp_path,task(tmp_path),run_dir,'new-film',resume=True,config={'timeout_seconds':9999})
    assert len(calls)==2


@pytest.mark.parametrize('retryable,outage',[
    (True,{'http_status':503,'detail':'unavailable'}),
    (False,{'http_status':401,'detail':'credentials required'}),(False,None)])
def test_creator_terminal_or_service_failure_returns_without_writing_again(tmp_path,monkeypatch,retryable,outage):
    calls=[]
    def create(path,directory,**kwargs):
        calls.append(1)
        atomic_write_json(directory/'sandbox/output/episode.json',{'revision':1})
        return {'status':'agent_failed','agent_exit':124,'draft_changed':True,
            'seconds_used':12,'allowance_seconds':5400,'retryable':retryable,
            **({'service_interrupted':outage} if outage else {})}
    monkeypatch.setattr(creation,'run',create)
    result=film.run(tmp_path,task(tmp_path),tmp_path/'run','new-film')
    assert result['status']=='blocked' and result['retryable'] is retryable and calls==[1]
    if outage:
        assert result['service_interrupted']==outage
    assert 'pending_feedback' not in result


@pytest.mark.parametrize('failure', ['unreadable','service'])
def test_unreadable_source_and_review_service_failure_do_not_create_author_feedback(tmp_path,monkeypatch,failure):
    from novel_manga.application.preparation import audit as source_audit
    from novel_manga.interactive import creative_material
    calls=[]
    monkeypatch.setattr(creation,'run',candidate_creator(calls,[review_draft()]))
    def review(episode,sources,**kwargs):
        if failure=='service':raise RuntimeError('task submission returned HTTP 503: unavailable')
        receipt=creative_receipt(episode,sources,rejected=False)
        receipt['answer'].update(source_readable=False,source_problem='原文不可读')
        return receipt
    monkeypatch.setattr(source_audit,'creative_audit',review)
    monkeypatch.setattr(creative_material,'write_material',lambda *a,**k:pytest.fail('unverified draft must not materialize'))
    run_dir=tmp_path/'run'
    result=film.run(tmp_path,task(tmp_path),run_dir,'new-film')
    assert result['status']=='blocked'
    assert result['retryable'] is (failure=='service')
    assert 'pending_feedback' not in result and 'pending_review_fingerprint' not in result
    assert not (run_dir/'creative-feedback').exists() and len(calls)==1
