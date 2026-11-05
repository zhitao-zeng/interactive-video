from types import SimpleNamespace
import pytest

from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json, read_json


def test_interactive_finish_without_video_returns_the_actual_frame_blocker(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.completion import EpisodeCompletion
    ctx=SimpleNamespace(novel_dir=tmp_path/'book',episode_dir=tmp_path/'book/book_1',
        run_dir=tmp_path/'run',work_dir=tmp_path/'work')
    atomic_write_json(ctx.novel_dir/'profile.json',{'sd_input_mode':'first-frame'})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'a','kind':'video'}]})
    atomic_write_json(ctx.run_dir/'interactive-delivery.json',{'destination':str(tmp_path/'delivery')})
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors',
        lambda *a: (_ for _ in ()).throw(AssertionError('No movie timeline exists to mix yet')))
    result=EpisodeCompletion(ctx,None,SimpleNamespace(unreviewed_clips=lambda *a:[])).finish({})
    assert result['required_action']=='first_frames' and result['clips']==['a']
    assert result['passed'] is False


def _finish_with_final_gate(tmp_path, monkeypatch, *, visual_passed, audio_review=None,
                            initial_audio_status=None):
    from novel_manga.application.agents.episode import completion
    from novel_manga.application.production import episode_state
    from novel_manga.application.repair import history
    from novel_manga.application.review import execution, final_cut
    from novel_manga.story import voice_delivery
    from novel_manga.application import music_selection

    episode=tmp_path/'episode'; run=tmp_path/'run'; work=tmp_path/'work'
    for path in (episode,run,work/'input'):
        path.mkdir(parents=True,exist_ok=True)
    movie=tmp_path/'movie.mp4'; movie.write_bytes(b'candidate')
    atomic_write_json(episode/'clip_plan.json', {'clips':[]})
    initial_report={
        'assembly':{'final_video':str(movie),'thin_passed':True},
        'quality_review':{'passed':True},'status':'assembled'}
    if initial_audio_status:
        initial_report['final_audio_review_status']=initial_audio_status
    atomic_write_json(episode/'thin_media_report.json', initial_report)
    atomic_write_json(episode/'episode_review.json', {'clips':{},'review_mode':'classic'})
    atomic_write_json(run/'prepared.json', {})
    destination=tmp_path/'delivery'; destination.mkdir()
    atomic_write_json(run/'interactive-delivery.json', {'destination':str(destination)})
    ctx=SimpleNamespace(episode_dir=episode,run_dir=run,work_dir=work)
    media=SimpleNamespace(refresh_technical_policy=lambda report,plan:report,
                          runner=lambda:SimpleNamespace(context=ctx))
    evidence=SimpleNamespace(unreviewed_clips=lambda *_:[],current_review=lambda *_:True,
        voice_recording_blocks=lambda *_:[],review_evidence=lambda *_:{})
    monkeypatch.setattr(voice_delivery,'episode_native_speech_errors',lambda *_:[])
    monkeypatch.setattr(music_selection,'episode_music_errors',lambda *_:[])
    monkeypatch.setattr(execution,'media_matches',lambda *_args,**_kwargs:True)
    saved_reports=[]
    def save_result(_d,report,*_args,**_kwargs):
        saved_reports.append(dict(report))
        return report
    monkeypatch.setattr(execution,'save_result',save_result)
    monkeypatch.setattr(final_cut,'apply_feedback',lambda _d,review,final:{**review,'final_cut_feedback':{}})
    audio=audio_review or {}
    monkeypatch.setattr('novel_manga.interactive.delivery.reviewed_delivery',lambda *_args,**_kwargs:{
        'movie':str(movie),'complete':True,'passed':visual_passed,
        'final_audio_review_required':True,'audio_review':audio})
    monkeypatch.setattr(history,'remaining_takes',lambda *_:0)
    monkeypatch.setattr(history,'generation_counts',lambda *_:{})
    monkeypatch.setattr(episode_state,'record',lambda *_args,**_kwargs:None)
    monkeypatch.setattr(completion,'record_state',lambda *_args,**_kwargs:None)
    return completion.EpisodeCompletion(ctx,media,evidence).finish({}), work, saved_reports


@pytest.mark.parametrize('visual_issues',[[],[{'severity':'minor','problem':'brief nonblocking jitter'}]])
def test_cached_delivery_cannot_finish_without_audio_for_the_same_export(tmp_path, monkeypatch, visual_issues):
    movie = tmp_path/'versions/export-008/episode01.mp4'
    movie.parent.mkdir(parents=True); movie.write_bytes(b'final exported movie')
    episode = tmp_path/'episode'; episode.mkdir()
    destination = tmp_path/'delivery'; destination.mkdir()
    ctx = SimpleNamespace(episode_dir=episode)
    runner = SimpleNamespace(context=ctx)
    final = {'movie':str(movie),'complete':True,'passed':True,'issues':visual_issues,'errors':[]}
    report = {'assembly':{'final_video':'/native/assembly.mp4','thin_passed':True},
        'quality_review':{'passed':True,'content_passed':True},
        'final_cut_review':final,'final_audio_review_required':True}
    atomic_write_json(destination/'delivery.json', {'export_context':{'current':True},
        'final_video':str(movie),'technical_passed':True,'status':'accepted'})
    monkeypatch.setattr(delivery, 'export_context', lambda *_:{'current':True})
    monkeypatch.setattr('novel_manga.application.review.final_cut.current', lambda *_:True)
    monkeypatch.setattr('novel_manga.story.voice_delivery.episode_native_speech_errors', lambda *_:[])
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors', lambda *_:[])

    incomplete = delivery.finish_reviewed_delivery(runner, report, destination)
    assert incomplete['status'] == 'candidate'
    assert incomplete['final_audio_review_passed'] is False

    report['final_audio_review'] = {'movie':str(movie),'complete':True,'passed':True,'issues':[],'errors':[]}
    accepted = delivery.finish_reviewed_delivery(runner, report, destination)
    assert accepted['status'] == 'accepted'
    assert accepted['final_audio_review_passed'] is True
    assert accepted['final_audio_review']['movie'] == str(movie)

    report['final_audio_review'] = {'movie':str(movie),'complete':False,'passed':False,
        'issues':[],'errors':['one window missing']}
    blocked = delivery.finish_reviewed_delivery(runner, report, destination)
    assert blocked['status'] == 'candidate'
    assert blocked['final_audio_review_errors'] == ['one window missing']


def test_visual_failure_finishes_unpassed_candidate_without_claiming_audio_failure(tmp_path, monkeypatch):
    result, work, _ = _finish_with_final_gate(tmp_path, monkeypatch, visual_passed=False)
    assert result['ok'] is True and result['passed'] is False
    assert 'error' not in result
    manifest=read_json(work/'input/latest-finish.json',{})
    assert manifest['passed'] is False
    assert manifest['final_audio_review_status']=='not_run_visual_gate'
    assert manifest.get('final_audio_review',{}).get('passed') is not True


def test_visual_pass_still_blocks_on_actual_failed_audio_review(tmp_path, monkeypatch):
    audio={'movie':str((tmp_path/'movie.mp4').resolve()),'complete':True,'passed':False,
           'issues':['声音尾部截断'],'errors':[]}
    result, _, _ = _finish_with_final_gate(tmp_path, monkeypatch, visual_passed=True,audio_review=audio)
    assert result['ok'] is False and result['passed'] is False
    assert result['error']=='final_audio_review_requires_repair'


def test_visual_and_audio_pass_clear_old_not_run_status_before_report_save(tmp_path, monkeypatch):
    audio={'movie':str((tmp_path/'movie.mp4').resolve()),'complete':True,'passed':True,
           'issues':[],'errors':[]}
    result, work, saved = _finish_with_final_gate(tmp_path, monkeypatch, visual_passed=True,
        audio_review=audio,initial_audio_status='not_run_visual_gate')
    assert result['ok'] is True and result['passed'] is True
    assert saved[-1].get('final_audio_review_status') is None
    manifest=read_json(work/'input/latest-finish.json',{})
    assert manifest.get('final_audio_review_status') is None
