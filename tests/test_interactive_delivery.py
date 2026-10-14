from novel_manga.interactive.delivery import delivery_status, interaction_window, evidence_events, video_seconds
from novel_manga.interactive.production import can_resume_unsubmitted


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
