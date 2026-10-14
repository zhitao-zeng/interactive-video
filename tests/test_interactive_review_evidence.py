from novel_manga.review.acceptance import evaluate_acceptance
import json


def test_empty_model_verdict_cannot_count_as_reviewed():
    media={'assembly':{'thin_passed':True},'failed_clips':[],'gate_failed_clips':[]}
    review={'clips':{'clip_01':{'video':'clip.mp4','take':[1,2,3]}},'feedback':{}}
    result=evaluate_acceptance(['clip_01'],media,review)
    assert result.passed is False
    assert result.review_errors==['clip_01']


def test_empty_verdict_is_retried_on_normal_review_resume(tmp_path, monkeypatch):
    from novel_manga.application.review import episode, judges
    novel=tmp_path/'n';ep=novel/'n_1';take=ep/'work/clips/clip_01/attempt_01/clip.mp4'
    take.parent.mkdir(parents=True);take.write_bytes(b'unchanged take')
    (novel/'story_bible.json').write_text(json.dumps({'novel_title':'n','genre':'g','visual_style':'v','palette':'p','style_fingerprint':'f'}))
    (ep/'clip_plan.json').write_text(json.dumps({'clips':[{'clip_id':'clip_01','kind':'video'}]}))
    monkeypatch.setattr(judges,'judge_clip',lambda *a,**kw:{})
    first=episode.review_episode(ep)
    assert first['clips']['clip_01']['severity']=='review_error'
    calls=[]
    def valid(*a,**kw):
        calls.append(1)
        return {'severity':'pass','story_ok':True,'identity_ok':True,'feedback':'','visible_people':0,
                'location_ok':True,'time_of_day_ok':True,'visual_defects':False,'text_or_watermark':False}
    monkeypatch.setattr(judges,'judge_clip',valid)
    second=episode.review_episode(ep)
    assert calls==[1] and second['clips']['clip_01']['severity']=='pass'
