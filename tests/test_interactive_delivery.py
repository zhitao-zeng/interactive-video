from novel_manga.interactive.delivery import delivery_status, interaction_window, evidence_events, video_seconds
from novel_manga.interactive.production import can_resume_unsubmitted
import pytest


def test_failed_content_cannot_be_accepted():
    report = {'quality_review': {'passed': False}, 'assembly': {'thin_passed': True}}
    assert delivery_status(report, True) == 'candidate'
    report['quality_review']['passed'] = True
    assert delivery_status(report, False) == 'candidate'
    assert delivery_status(report, True) == 'accepted'


def test_media_runner_binds_the_current_agent_run_dir(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.application.agents.episode.media_operations import MediaOperations
    from novel_manga.application.rendering import flow
    from novel_manga.config import Settings
    from novel_manga.models.bible import StoryBible
    from novel_manga.util import atomic_write_json
    novel=tmp_path/'book';episode=novel/'book_1';episode.mkdir(parents=True)
    run=tmp_path/'agent-run'
    ctx=SimpleNamespace(novel_dir=novel,episode_dir=episode,run_dir=run,project_root=tmp_path)
    atomic_write_json(novel/'profile.json',{})
    atomic_write_json(novel/'story_bible.json',{})
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls,**kwargs:object()))
    monkeypatch.setattr(StoryBible,'model_validate',classmethod(lambda cls,value:value))
    runner=SimpleNamespace(context=SimpleNamespace(options=SimpleNamespace()))
    monkeypatch.setattr(flow,'ThinMediaRunner',lambda **kwargs:runner)
    for name in ('NOVEL_RENDER_WORKERS','NOVEL_RENDER_INFLIGHT','NOVEL_AGENT_MANAGED'):
        monkeypatch.delenv(name,raising=False)
    assert MediaOperations(ctx,None,None).runner().context.run_dir == run


def test_first_frame_delivery_runner_binds_the_current_agent_run_dir(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.application.agents.episode import context,flow
    from novel_manga.interactive import delivery,first_frame_entry,runtime
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
    from novel_manga.util import atomic_write_json
    novel=tmp_path/'book';episode=novel/'book_1';episode.mkdir(parents=True)
    run=tmp_path/'agent-run'
    ctx=SimpleNamespace(novel_dir=novel,episode_dir=episode,run_dir=run,
                        work_dir=run/'sandbox',source_novel_dir=novel)
    atomic_write_json(novel/'profile.json',{'h3_input_mode':'first-frame',
                                            'h3_speech_policy':NATIVE_SPEECH_POLICY})
    atomic_write_json(episode/'thin_media_report.json',{'assembly':{'final_video':'existing'}})
    monkeypatch.setattr(runtime,'configure',lambda *args,**kwargs:None)
    monkeypatch.setattr(context,'create_context',lambda *args,**kwargs:ctx)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway',lambda *args:None)
    monkeypatch.setattr(flow,'run_episode',lambda *args,**kwargs:{'status':'finished','passed':True})
    runner=SimpleNamespace(context=SimpleNamespace(episode_dir=episode))
    monkeypatch.setattr(runtime,'runner_for',lambda *args,**kwargs:runner)
    monkeypatch.setattr(delivery,'finish_reviewed_delivery',lambda actual,*args:{
        'status':'accepted','run_dir':str(actual.context.run_dir)})
    result=first_frame_entry.run(tmp_path,novel,project='adventure',
                                 destination=tmp_path/'delivery/one',config={})
    assert result['run_dir'] == str(run)
    assert result['agent_run_dir'] == str(run)


def test_tail_is_exactly_three_seconds_of_actual_timeline():
    assert interaction_window(178.32) == {'start':178.32, 'end':181.32, 'seconds':3.0}


def test_video_frames_not_audio_padding_set_interaction_start():
    assert video_seconds({'nb_frames':'4425','r_frame_rate':'25/1','duration':'177.024'}) == 177


def test_overlay_uses_actual_clip_span_and_authored_shot_duration():
    script = {'shots': [{'index':1,'duration_seconds':6},{'index':2,'duration_seconds':6}]}
    plan = {'clips':[{'clip_id':'clip_01','shot_indexes':[1,2]}]}
    timeline = [{'unit_id':'clip_01','start':0,'end':12.2}]
    events = evidence_events([{'index':2,'text':'证据'}],script,plan,timeline)
    assert events == [{'text':'证据','start':6.1,'end':12.2}]


def test_resume_only_when_no_generation_has_been_submitted(tmp_path):
    report={'status':'clips_failed','clips':[{'clip_id':'clip_01','error':'reference 404','attempts':[],'selected':None}]}
    assert can_resume_unsubmitted(tmp_path,report)
    task=tmp_path/'work/clips/clip_01/attempt_01/clip.mp4.task.json'
    task.parent.mkdir(parents=True);task.write_text('{"task_id":"pending"}')
    assert not can_resume_unsubmitted(tmp_path,report)


def test_branding_is_encoded_before_final_movie_review(labelled_delivery):
    import json
    from novel_manga.interactive import delivery
    from types import SimpleNamespace
    case=labelled_delivery
    runner=SimpleNamespace(context=case.ctx)
    report,destination,commands=case.report,case.destination,case.commands
    wm={'text':'series EP01','fontfile':'/font.ttf','fontsize':28,'fontcolor':'white',
        'borderw':2,'bordercolor':'black','x':'w-text_w-48','y':48}
    (runner.context.episode_dir/'delivery_policy.json').write_text(json.dumps({'packaging':{'watermark':wm}}))
    receipt=delivery.deliver(runner,report,destination)
    graph=next(c[c.index('-filter_complex')+1] for c in commands if '-filter_complex' in c)
    assert 'concat=n=2:v=1:a=1[plain][a];[plain]drawtext=' in graph
    assert (destination/'watermark.txt').read_text()=='series EP01'
    assert receipt['packaging']['watermark']==wm


def test_controlled_native_publication_keeps_reviewed_export_receipt_current(labelled_delivery,monkeypatch):
    import copy
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    from novel_manga.application.repair.delivery import publish_if_ready
    from novel_manga.application.review import final_cut
    from novel_manga.util import atomic_write_json,read_json
    case=labelled_delivery;ctx=case.ctx;runner=SimpleNamespace(context=ctx)
    candidate=ctx.episode_dir/'repair_candidate/native.mp4';candidate.parent.mkdir();candidate.write_bytes(b'native')
    case.destination.mkdir();exported=case.destination/'episode01.mp4';exported.write_bytes(b'reviewed native with final captions and card')
    (case.destination/'cover.jpeg').write_bytes(b'cover')
    report=copy.deepcopy(case.report);report['assembly'].update(final_video=str(candidate),pending_publish=True)
    for clip in ctx.clip_plan['clips']:clip['kind']='video'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',ctx.clip_plan)
    report['final_cut_review']={'policy':final_cut.POLICY,'movie':str(exported),'complete':True,'passed':True,
        'issues':[],'errors':[],'context':final_cut.context(ctx.episode_dir,report,exported)}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    previous=delivery.export_context(runner,report)
    atomic_write_json(case.destination/'delivery.json',{'final_video':str(exported),'export_context':previous,'technical_passed':True})
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'done')
    monkeypatch.setattr('novel_manga.review.contracts.current_review',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.repair.delivery.precise_review_blockers',lambda *a:[])
    assert publish_if_ready(ctx.episode_dir,{}, {})
    published=read_json(ctx.episode_dir/'thin_media_report.json')
    assert delivery.finish_reviewed_delivery(runner,published,case.destination)['status']=='candidate'
    delivery.record_native_publication(runner,published,case.destination,previous)
    assert delivery.finish_reviewed_delivery(runner,published,case.destination)['status']=='accepted'
    receipt=read_json(case.destination/'delivery.json');bad_previous={**previous,'captions':['different source']}
    receipt['export_context']=bad_previous;atomic_write_json(case.destination/'delivery.json',receipt)
    delivery.record_native_publication(runner,published,case.destination,previous)
    assert read_json(case.destination/'delivery.json')['export_context']==bad_previous


@pytest.mark.parametrize('content_ok,media_ok', [(True,True),(False,True),(True,False)])
@pytest.mark.parametrize('audio_edit',[None,'audio_repair','episode_ambience_mix','episode_music_mix'])
def test_user_can_cancel_scene_overlays_without_removing_dialogue_or_choices(tmp_path,monkeypatch,content_ok,media_ok,audio_edit):
    import json
    from types import SimpleNamespace
    import pytest
    from novel_manga.interactive import delivery
    episode=tmp_path/'episode';episode.mkdir()
    work=episode/'work';work.mkdir()
    assembled=episode/'assembled.mp4';assembled.write_bytes(b'assembled')
    (work/'joined.mp4').write_bytes(b'native join')
    ass=episode/'dialogue.ass';ass.write_text('approved dialogue')
    (episode/'delivery_policy.json').write_text(json.dumps({'extra_visual_overlays':False}))
    (episode/'interaction.json').write_text(json.dumps({'seconds':3,'question':'choose','options':[]}))
    (episode/'overlays.json').write_text(json.dumps([{'index':1,'text':'remove this'}]))
    (episode/'evidence_surfaces.json').write_text('{}')
    ctx=SimpleNamespace(episode_dir=episode,work=work,script={'shots':[]},clip_plan={'clips':[]},
                        settings=SimpleNamespace(width=1080,height=1920,font_path=tmp_path/'font'))
    monkeypatch.setattr('novel_manga.interactive.evidence.prepare_evidence',lambda *a:pytest.fail('cancelled compositor must not run'))
    monkeypatch.setattr(delivery,'_overlay_ass',lambda *a:pytest.fail('cancelled labels must not be written'))
    monkeypatch.setattr(delivery,'_card',lambda *a:None)
    commands=[]
    monkeypatch.setattr(delivery,'run',lambda command:commands.append(command))
    monkeypatch.setattr(delivery.subprocess,'run',lambda command,**kw:commands.append(command) or SimpleNamespace(returncode=0,stderr=''))
    streams={'streams':[{'codec_type':'video','codec_name':'h264','width':1080,'height':1920,'r_frame_rate':'25/1','nb_frames':'7500'}, {'codec_type':'audio','codec_name':'aac'}]}
    def probe(command,**kwargs):
        data=json.loads(json.dumps(streams))
        data['streams'][0]['nb_frames']='7500' if str(command[-1]).endswith('/episode01.mp4') else '7425'
        return json.dumps(data)
    monkeypatch.setattr(delivery.subprocess,'check_output',probe)
    measured=[]
    def inspect(video,cover,ending,subtitles,settings,report,**kwargs):
        measured.append((video,kwargs))
        return {'passed':media_ok,'checks':{'silence_ratio':{'passed':media_ok}}}
    monkeypatch.setattr(delivery,'inspect_media',inspect)
    report={'assembly':{'final_video':str(assembled),'ass':str(ass),'thin_passed':False,
                        'media_qc':{'checks':{'silence_ratio':{'passed':False}}}},
            'quality_review':{'passed':False,'content_passed':content_ok}}
    if audio_edit:report['assembly'][audio_edit]={'source':'pre-mix.mp4'}
    receipt=delivery.deliver(SimpleNamespace(context=ctx),report,tmp_path/'delivery')
    first=next(c for c in commands if '-filter_complex' in c)
    assert first[first.index('-i')+1]==str(work/'joined.mp4')
    graph=first[first.index('-filter_complex')+1]
    assert 'ass=subtitles.ass[body_video]' in graph
    if audio_edit:
        assert first[first.index('-i',first.index('-i')+1)+1]==str(assembled)
        assert '[0:v]' in graph and '[1:a]aresample=' in graph
    assert receipt['evidence_events']==[]
    assert receipt['delivery_policy']=={'extra_visual_overlays':False,'dialogue_subtitles':True,'interaction_card':True}
    assert any('anullsrc=r=48000:cl=stereo' in command for command in commands)
    assert measured==[(tmp_path/'delivery/episode01.mp4',{'silent_outro_seconds':3,'subtitles_required':False})]
    assert receipt['production_media_passed'] is media_ok
    assert receipt['content_passed'] is content_ok
    assert receipt['source_assembly_media_passed'] is False
    assert receipt['status']==('accepted' if content_ok and media_ok else 'candidate')



from support.delivery_cases import labelled_delivery


@pytest.mark.parametrize('audio_input',[[],['old.wav']])
def test_ordinary_delivery_checks_the_selected_speech_contract(labelled_delivery,audio_input):
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
    from novel_manga.util import atomic_write_json
    case=labelled_delivery;episode=case.ctx.episode_dir
    atomic_write_json(episode.parent/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    cue='Her voice stays low and steady.'
    prompt=f'detailed_description:\n[Shot 1] {cue} She says <d>[Chinese] 等等。</d>\noverall_soundscape: Water.'
    plan={'clips':[{'clip_id':'clip_01','kind':'video','h3_speech_policy':NATIVE_SPEECH_POLICY,
        'dialogue_bindings':[{'stage':1,'text':'等等。','emotion':cue,'delivery_mode':'visible_dialogue'}]}]}
    atomic_write_json(episode/'clip_plan.json',plan)
    raw=episode/'work/clips/clip_01/attempt_01';raw.mkdir(parents=True)
    atomic_write_json(raw/'request.json',{'prompt':prompt,'reference_audios':audio_input})
    case.report['clips']=[{'clip_id':'clip_01','selected':{'video':str(raw/'clip.mp4')}}]
    receipt=delivery.deliver(SimpleNamespace(context=case.ctx),case.report,case.destination)
    assert receipt['technical_passed']is True and receipt['production_media_passed']is True
    assert receipt['status']==('candidate'if audio_input else'accepted')
    assert receipt['content_passed']==(not audio_input)
    assert bool(receipt.get('speech_policy_errors'))==bool(audio_input)


def test_approved_short_screenplay_keeps_media_and_exact_card_gates(labelled_delivery,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    groups=case.ctx.episode_dir/'authored_groups.json'
    groups.write_text(json.dumps([{'shot_indexes':[1,2]}]))
    final_frames=1257
    def probe(command,**kwargs):
        frames=final_frames if command[-1].endswith('/episode01.mp4') else 1182
        return json.dumps({'streams':[
            {'codec_type':'video','codec_name':'h264','width':1080,'height':1920,
             'r_frame_rate':'25/1','nb_frames':str(frames)},
            {'codec_type':'audio','codec_name':'aac'}]})
    monkeypatch.setattr(delivery.subprocess,'check_output',probe)
    runner=SimpleNamespace(context=case.ctx)
    receipt=delivery.deliver(runner,case.report,case.destination)
    assert receipt['duration']==50.28
    assert receipt['technical_passed'] is True
    final_frames=1256
    assert delivery.deliver(runner,case.report,case.destination)['technical_passed'] is False
    final_frames=1257
    groups.unlink()
    assert delivery.deliver(runner,case.report,case.destination)['technical_passed'] is False


def test_cached_short_delivery_rechecks_old_technical_failure_without_encoding(labelled_delivery,monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    (case.ctx.episode_dir/'authored_groups.json').write_text('[]')
    def probe(command,**kwargs):
        return json.dumps({'streams':[
            {'codec_type':'video','codec_name':'h264','width':1080,'height':1920,
             'r_frame_rate':'25/1','nb_frames':'1257' if command[-1].endswith('/episode01.mp4') else '1182'},
            {'codec_type':'audio','codec_name':'aac'}]})
    monkeypatch.setattr(delivery.subprocess,'check_output',probe)
    runner=SimpleNamespace(context=case.ctx)
    receipt=delivery.deliver(runner,case.report,case.destination)
    movie=Path(receipt['final_video']);movie.write_bytes(b'original encoded movie')
    receipt.update(technical_passed=False,export_context=delivery.export_context(runner,case.report))
    (case.destination/'delivery.json').write_text(json.dumps(receipt))
    monkeypatch.setattr('novel_manga.application.review.final_cut.review_assembly',
        lambda *a,**k:{'movie':str(movie),'complete':True,'passed':True})
    before=len(case.commands)
    delivery.reviewed_delivery(runner,case.report,case.destination)
    assert json.loads((case.destination/'delivery.json').read_text())['technical_passed'] is True
    assert movie.read_bytes()==b'original encoded movie'
    assert not any('-c:v' in c for c in case.commands[before:])


@pytest.mark.parametrize('manifest',[None,[],{}, {'surfaces':[]}])
def test_empty_evidence_surfaces_keep_authored_labels_and_actual_clip_timing(labelled_delivery,monkeypatch,manifest):
    import json
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    if manifest is not None:
        (case.ctx.episode_dir/'evidence_surfaces.json').write_text(json.dumps(manifest))
    monkeypatch.setattr('novel_manga.interactive.evidence.prepare_evidence',
                        lambda *a:pytest.fail('Empty evidence must not run observation/compositing'))
    receipt=delivery.deliver(SimpleNamespace(context=case.ctx),case.report,case.destination)
    assert receipt['status']=='accepted'
    assert receipt['evidence_events']==[{'text':'仍在池室','placement':'panel','start':6.08,'end':15.2}]
    assert receipt['overlay_timing']=='authored timing scaled to actual compiled clip spans'
    assert 'final_evidence_review_pending' not in receipt
    assert 'postproduction' not in receipt
    first=next(c for c in case.commands if '-filter_complex' in c)
    assert first[first.index('-i')+1]==case.report['assembly']['final_video']
    assert 'ass=evidence.ass[body_video]' in first[first.index('-filter_complex')+1]
    assert '仍在池室' in (case.destination/'evidence.ass').read_text()


def test_nonempty_evidence_keeps_observed_timing_and_final_review(labelled_delivery,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    manifest={'surfaces':[{'id':'note','kind':'text_panel','shot_ids':['S02'],'text':'证据'}]}
    (case.ctx.episode_dir/'evidence_surfaces.json').write_text(json.dumps(manifest))
    composited=case.ctx.episode_dir/'composited.mp4';composited.write_bytes(b'composited')
    calls=[]
    postproduction={'body':str(composited),'passed':True,
                    'shot_spans':[{'index':2,'start':8.0,'end':12.0}],'timing_method':'observed frames'}
    def prepare(ctx,report,destination):
        calls.append((ctx,report,destination))
        return postproduction
    monkeypatch.setattr('novel_manga.interactive.evidence.prepare_evidence',prepare)
    receipt=delivery.deliver(SimpleNamespace(context=case.ctx),case.report,case.destination)
    assert calls==[(case.ctx,case.report,case.destination)]
    assert receipt['evidence_events']==[{'text':'仍在池室','placement':'panel','start':8.0,'end':12.0}]
    assert receipt['postproduction']==postproduction
    assert receipt['final_evidence_review_pending'] is True
    assert receipt['status']=='candidate'
    first=next(c for c in case.commands if '-filter_complex' in c)
    assert first[first.index('-i')+1]==str(composited)
    assert 'ass=evidence.ass,ass=subtitles.ass[body_video]' in first[first.index('-filter_complex')+1]


def test_nonempty_evidence_composite_error_cannot_be_accepted(labelled_delivery,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    (case.ctx.episode_dir/'evidence_surfaces.json').write_text(json.dumps({'surfaces':[{'id':'note'}]}))
    def failed(*args):
        raise RuntimeError('Unable to observe prop surface')
    monkeypatch.setattr('novel_manga.interactive.evidence.prepare_evidence',failed)
    receipt=delivery.deliver(SimpleNamespace(context=case.ctx),case.report,case.destination)
    assert receipt['status']=='candidate'
    assert receipt['postproduction']['passed'] is False
    assert receipt['postproduction']['errors']['composite']=='RuntimeError: Unable to observe prop surface'
    assert receipt['final_evidence_review_pending'] is True
    assert json.loads((case.destination/'evidence-review.json').read_text())['passed'] is False


def test_independent_ambience_uses_asset_instead_of_spoken_body_window(labelled_delivery,monkeypatch):
    import json,wave
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    case=labelled_delivery
    audio=case.ctx.episode_dir/'room.wav'
    with wave.open(str(audio),'wb') as f:
        f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*144000)
    (case.ctx.episode_dir/'interaction.json').write_text(json.dumps({'seconds':3,'question':'choose','options':[],'ambience_shot_index':19}))
    (case.ctx.episode_dir/'delivery_policy.json').write_text(json.dumps({'interaction_ambience':{
        'path':str(audio),'review':{'speech_free':True,'method':'local_asr'}}}))
    monkeypatch.setattr(delivery,'ambience_window',lambda *a:pytest.fail('mixed audio window must not be used'))
    receipt=delivery.deliver(SimpleNamespace(context=case.ctx),case.report,case.destination)
    assert receipt['ambience_source']['kind']=='independent_asset'
    extraction=[c for c in case.commands if str(audio) in c]
    assert len(extraction)==1 and '-ss' not in extraction[0]


def test_world_card_change_invalidates_cached_export(labelled_delivery,monkeypatch):
    import json
    from novel_manga.interactive import delivery
    from types import SimpleNamespace
    case=labelled_delivery;runner=SimpleNamespace(context=case.ctx)
    monkeypatch.setattr('novel_manga.application.review.final_cut.context',lambda *a:{'constant':'same movie'})
    before=delivery.export_context(runner,case.report)
    (case.ctx.episode_dir/'publication.json').write_text(json.dumps({'world_card':{'text':'new card'}}))
    after=delivery.export_context(runner,case.report)
    assert before!=after


def _first_frame_terminal_case(tmp_path, monkeypatch, agent_result):
    from types import SimpleNamespace
    from novel_manga.application.agents.episode import context, flow
    from novel_manga.interactive import delivery, first_frame_entry, runtime
    from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
    from novel_manga.util import atomic_write_json

    novel = tmp_path/'book'
    episode = novel/'book_1'
    episode.mkdir(parents=True)
    run = tmp_path/'agent-run'
    ctx = SimpleNamespace(novel_dir=novel, episode_dir=episode, run_dir=run,
                          work_dir=run/'sandbox', source_novel_dir=novel)
    atomic_write_json(novel/'profile.json', {'h3_input_mode':'first-frame',
                                            'h3_speech_policy':NATIVE_SPEECH_POLICY})
    atomic_write_json(episode/'thin_media_report.json', {
        'assembly':{'final_video':'existing.mp4'}})
    monkeypatch.setattr(runtime, 'configure', lambda *args, **kwargs:None)
    monkeypatch.setattr(context, 'create_context', lambda *args, **kwargs:ctx)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway',
                        lambda *args:None)
    monkeypatch.setattr(flow, 'run_episode', lambda *args, **kwargs:agent_result)
    runner = SimpleNamespace(context=SimpleNamespace(episode_dir=episode))
    monkeypatch.setattr(runtime, 'runner_for', lambda *args, **kwargs:runner)
    calls = []
    monkeypatch.setattr(delivery, 'finish_reviewed_delivery', lambda *args:(
        calls.append(1) or {'status':'accepted', 'content_passed':True,
                            'run_dir':str(run), 'final_video':'existing.mp4'}))
    return first_frame_entry, run, calls


def test_blocked_host_cannot_be_overridden_by_an_existing_assembly(tmp_path, monkeypatch):
    entry, _, calls = _first_frame_terminal_case(tmp_path, monkeypatch, {
        'status':'blocked', 'passed':False, 'retryable':False,
        'invalid_fields':{'clip_01':['image_references']},
        'argument_errors':{'clip_01':['registered reference is missing']}})
    result = entry.run(tmp_path, tmp_path/'book', project='adventure',
                       destination=tmp_path/'delivery', config={})
    assert result['status'] == 'blocked'
    assert result['invalid_fields'] == {'clip_01':['image_references']}
    assert result['argument_errors'] == {'clip_01':['registered reference is missing']}
    assert calls == []


def test_outer_acceptance_requires_current_formal_acceptance_status(tmp_path, monkeypatch):
    entry, run, _ = _first_frame_terminal_case(tmp_path, monkeypatch, {
        'status':'finished', 'passed':True})
    monkeypatch.setattr('novel_manga.review.acceptance.acceptance_status', lambda *_: {
        'status':'candidate', 'passed':False, 'reason':'formal acceptance bundle is missing'})
    result = entry.run(tmp_path, tmp_path/'book', project='adventure',
                       destination=tmp_path/'delivery', config={})
    assert result['status'] == 'candidate'
    assert result['content_passed'] is False
    assert result['acceptance_status']['reason'] == 'formal acceptance bundle is missing'
    assert result['agent_run_dir'] == str(run)
