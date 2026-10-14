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
