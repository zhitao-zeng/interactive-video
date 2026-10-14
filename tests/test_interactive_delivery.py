from novel_manga.interactive.delivery import delivery_status, interaction_window, evidence_events, video_seconds
from novel_manga.interactive.production import can_resume_unsubmitted
import pytest


def test_failed_content_cannot_be_accepted():
    report = {'quality_review': {'passed': False}, 'assembly': {'thin_passed': True}}
    assert delivery_status(report, True) == 'candidate'
    report['quality_review']['passed'] = True
    assert delivery_status(report, False) == 'candidate'
    assert delivery_status(report, True) == 'accepted'


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


def test_user_can_cancel_scene_overlays_without_removing_dialogue_or_choices(tmp_path,monkeypatch):
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
    monkeypatch.setattr(delivery.subprocess,'check_output',lambda *a,**k:json.dumps(streams))
    receipt=delivery.deliver(SimpleNamespace(context=ctx),{'assembly':{'final_video':str(assembled),'ass':str(ass)}},tmp_path/'delivery')
    first=commands[0]
    assert first[first.index('-i')+1]==str(work/'joined.mp4')
    assert first[first.index('-vf')+1]=='ass=subtitles.ass'
    assert receipt['evidence_events']==[]
    assert receipt['delivery_policy']=={'extra_visual_overlays':False,'dialogue_subtitles':True,'interaction_card':True}
    assert any('anullsrc=r=48000:cl=stereo' in command for command in commands)


@pytest.fixture
def labelled_delivery(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from novel_manga.interactive import delivery
    episode=tmp_path/'episode';episode.mkdir()
    work=episode/'work';work.mkdir()
    assembled=episode/'assembled.mp4';assembled.write_bytes(b'assembled with dialogue')
    ass=episode/'dialogue.ass';ass.write_text('approved dialogue')
    (episode/'interaction.json').write_text(json.dumps({'seconds':3,'question':'choose','options':[]}))
    (episode/'overlays.json').write_text(json.dumps([{'index':2,'text':'仍在池室','placement':'panel'}]))
    shots=[{'index':1,'duration_seconds':6},{'index':2,'duration_seconds':9},
           *[{'index':i,'duration_seconds':8} for i in range(3,23)],
           {'index':23,'duration_seconds':10}]
    clips=[{'clip_id':'clip_01','shot_indexes':[1,2],'request_seconds':15},
           *[{'clip_id':f'clip_{i-1:02d}','shot_indexes':[i],'request_seconds':s['duration_seconds']}
             for i,s in enumerate(shots[2:],3)]]
    timeline=[];cursor=0.0
    for i,clip in enumerate(clips):
        end=cursor+clip['request_seconds']+(.2 if i==0 else 0)
        timeline.append({'unit_id':clip['clip_id'],'start':cursor,'end':end})
        cursor=end
    ctx=SimpleNamespace(episode_dir=episode,work=work,script={'shots':shots},clip_plan={'clips':clips},
                        provider=SimpleNamespace(short_edge=768),profile={'resolution':'768p'},
                        settings=SimpleNamespace(width=1080,height=1920,font_path=tmp_path/'font'))
    report={'assembly':{'final_video':str(assembled),'ass':str(ass),'timeline':timeline,'thin_passed':True},
            'quality_review':{'passed':True,'content_passed':True},'clips':[]}
    commands=[]
    monkeypatch.setattr(delivery,'_card',lambda *a:None)
    monkeypatch.setattr(delivery,'run',lambda command:commands.append(command))
    monkeypatch.setattr(delivery.subprocess,'run',lambda command,**kw:commands.append(command) or SimpleNamespace(returncode=0,stderr=''))
    def probe(command,**kw):
        frames='4630' if command[-1].endswith('/body.mp4') else '4705'
        return json.dumps({'streams':[
            {'codec_type':'video','codec_name':'h264','width':1080,'height':1920,'r_frame_rate':'25/1','nb_frames':frames},
            {'codec_type':'audio','codec_name':'aac'}]})
    monkeypatch.setattr(delivery.subprocess,'check_output',probe)
    return SimpleNamespace(ctx=ctx,report=report,commands=commands,destination=tmp_path/'delivery')


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
    first=case.commands[0]
    assert first[first.index('-i')+1]==case.report['assembly']['final_video']
    assert first[first.index('-vf')+1]=='ass=evidence.ass'
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
    first=case.commands[0]
    assert first[first.index('-i')+1]==str(composited)
    assert first[first.index('-vf')+1]=='ass=evidence.ass,ass=subtitles.ass'


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
