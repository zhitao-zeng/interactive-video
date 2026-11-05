from types import SimpleNamespace
import pytest

from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json, read_json


@pytest.mark.parametrize('route', [
    {'action': 'first_frames', 'reason': 'opening needs a current review',
     'blocked': {'a': 'opening is stale'}},
    {'action': 'agent', 'reason': 'source opening conflict requires an authored repair',
     'blocked': {'a': ['opening source conflict']}},
])
def test_interactive_finish_without_video_returns_the_latest_route(tmp_path,monkeypatch,route):
    from novel_manga.application.agents.episode.completion import EpisodeCompletion
    ctx=SimpleNamespace(novel_dir=tmp_path/'book',episode_dir=tmp_path/'book/book_1',
        run_dir=tmp_path/'run',work_dir=tmp_path/'work')
    atomic_write_json(ctx.novel_dir/'profile.json',{'sd_input_mode':'first-frame'})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'a','kind':'video'}]})
    atomic_write_json(ctx.run_dir/'interactive-delivery.json',{'destination':str(tmp_path/'delivery')})
    monkeypatch.setattr('novel_manga.application.agents.episode.readiness.next_action',lambda *_:route)
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors',
        lambda *a: (_ for _ in ()).throw(AssertionError('No movie timeline exists to mix yet')))
    result=EpisodeCompletion(ctx,None,SimpleNamespace(unreviewed_clips=lambda *a:[])).finish({})
    assert result['required_action']==route['action'] and result['clips']==['a']
    assert result['reason']==route['reason'] and result['blocked']==route['blocked']
    assert result['passed'] is False
    assert not (ctx.work_dir/'input/latest-finish.json').exists()


def test_interactive_finish_missing_video_uses_real_source_conflict_status(tmp_path, monkeypatch):
    from support.first_frame_cases import case
    from novel_manga.application.agents.episode import first_frames, readiness
    from novel_manga.application.agents.episode.completion import EpisodeCompletion
    from novel_manga.application.agents.episode.input_repair import repair_status

    ctx, clips = case(tmp_path, monkeypatch)
    clips[0]['shots'][0]['opening_frame']['reference_subjects'] = ['not-a-registered-name']
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    atomic_write_json(ctx.run_dir/'interactive-delivery.json', {
        'destination': str(tmp_path/'delivery')})
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors',
        lambda *a: (_ for _ in ()).throw(AssertionError('No movie timeline exists to mix yet')))

    frames = first_frames.status(ctx, {'clips': clips})
    expected = readiness.next_action(ctx, {
        'first_frames': frames, 'input_repairs': repair_status(ctx), 'job': None})
    assert expected['action'] == 'agent'
    result = EpisodeCompletion(ctx, None, SimpleNamespace(unreviewed_clips=lambda *a: [])).finish({})
    assert result['required_action'] == 'agent'
    assert result['reason'] == expected['reason']
    assert result['blocked'] == expected['blocked']


def _current_audio(ctx, movie_path, **values):
    from novel_manga.application.review import final_audio

    model_dir = ctx.episode_dir.parent/'audio-model'
    model_dir.mkdir(exist_ok=True)
    (model_dir/'weights.bin').write_bytes(b'weights')
    source_identity, _ = final_audio._source_facts(ctx.episode_dir)
    audio = {'movie':str(movie_path.resolve()), 'complete':True, 'passed':True,
             'issues':[], 'errors':[], 'policy':final_audio.POLICY,
             'identity':{'movie':final_audio._file_state(movie_path.resolve()),
                         'model':final_audio._model_state(model_dir),
                         'audio_source_root':str(ctx.episode_dir.parent/'audio-source')},
             'source_identity':source_identity}
    return {**audio, **values}


def _finish_with_final_gate(tmp_path, monkeypatch, *, visual_passed, audio_review=None,
                            initial_audio_status=None, audio_identity=True):
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
    audio = (_current_audio(ctx, movie, **audio_review) if audio_review and audio_identity
             else audio_review or {})
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
    (movie.parent/'cover.jpeg').write_bytes(b'cover')
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

    report['final_audio_review'] = _current_audio(ctx, movie)
    accepted = delivery.finish_reviewed_delivery(runner, report, destination)
    assert accepted['status'] == 'accepted'
    assert accepted['final_audio_review_passed'] is True
    assert accepted['final_audio_review']['movie'] == str(movie)
    assert accepted['acceptance_protocol'] == 'current-export-v1'
    assert len(accepted['accepted_export']['video_sha256']) == 64
    assert len(accepted['accepted_export']['cover_sha256']) == 64

    original = accepted['accepted_export']['video_sha256']
    movie.write_bytes(b'REPLACED export bytes')
    tampered = delivery.finish_reviewed_delivery(runner, report, destination)
    assert tampered['status'] == 'candidate'
    assert tampered['accepted_export']['video_sha256'] == original

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


def test_delivery_pass_audio_without_identity_cannot_be_accepted(tmp_path, monkeypatch):
    movie = tmp_path/'versions/export-001/episode01.mp4'
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b'final exported movie')
    (movie.parent/'cover.jpeg').write_bytes(b'cover')
    episode = tmp_path/'episode'
    episode.mkdir()
    destination = tmp_path/'delivery'
    destination.mkdir()
    runner = SimpleNamespace(context=SimpleNamespace(episode_dir=episode))
    final = {'movie':str(movie), 'complete':True, 'passed':True, 'issues':[], 'errors':[]}
    report = {'assembly':{'final_video':'/native/assembly.mp4', 'thin_passed':True},
              'quality_review':{'passed':True, 'content_passed':True},
              'final_cut_review':final, 'final_audio_review_required':True,
              'final_audio_review':{'movie':str(movie), 'complete':True, 'passed':True,
                                    'issues':[], 'errors':[]}}
    atomic_write_json(destination/'delivery.json', {
        'export_context':{'current':True}, 'final_video':str(movie), 'technical_passed':True})
    monkeypatch.setattr(delivery, 'export_context', lambda *_:{'current':True})
    monkeypatch.setattr('novel_manga.application.review.final_cut.current', lambda *_:True)
    monkeypatch.setattr('novel_manga.story.voice_delivery.episode_native_speech_errors', lambda *_:[])
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors', lambda *_:[])

    receipt = delivery.finish_reviewed_delivery(runner, report, destination)
    assert receipt['status'] == 'candidate'
    assert receipt['final_audio_review_passed'] is False


def test_completion_pass_audio_without_identity_cannot_be_accepted(tmp_path, monkeypatch):
    audio = {'movie':str((tmp_path/'movie.mp4').resolve()), 'complete':True, 'passed':True,
             'issues':[], 'errors':[]}
    result, work, _ = _finish_with_final_gate(
        tmp_path, monkeypatch, visual_passed=True, audio_review=audio, audio_identity=False)
    assert result['ok'] is False
    assert result['passed'] is False
    assert result['error'] == 'final_audio_review_requires_repair'
    assert not (work/'input/latest-finish.json').exists()


@pytest.mark.parametrize(('http_status','retryable'), [(503,True), (401,False)])
def test_final_cut_preserves_typed_service_failure(tmp_path, monkeypatch, http_status, retryable):
    import json
    from pathlib import Path
    from novel_manga.application.review import final_cut
    from support.delivery_cases import episode

    runner, media, _ = episode(tmp_path)
    monkeypatch.setattr(final_cut.subprocess, 'check_output',
                        lambda *args, **kwargs:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess, 'run',
                        lambda command, **kwargs:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model, 'image_part',
                        lambda *args:{'type':'text', 'text':'image placeholder'})
    def unavailable(*args, **kwargs):
        raise RuntimeError(f'task submission returned HTTP {http_status}: review unavailable')
    monkeypatch.setattr(final_cut.model, 'ask_json', unavailable)

    result = final_cut.review_assembly(runner, media)
    assert result['complete'] is False
    assert result['service_interrupted']['http_status'] == http_status
    assert result['retryable'] is retryable


@pytest.mark.parametrize(('http_status','retryable'), [(503,True), (402,False)])
def test_final_audio_preserves_typed_service_failure(tmp_path, monkeypatch, http_status, retryable):
    from novel_manga.application.review import final_audio

    episode = tmp_path/'episode'
    episode.mkdir()
    movie = tmp_path/'movie.mp4'
    movie.write_bytes(b'movie')
    model_dir = tmp_path/'model'
    source_dir = tmp_path/'audio-source'
    model_dir.mkdir()
    source_dir.mkdir()
    (model_dir/'weights.bin').write_bytes(b'weights')
    runtime = {'python':'python', 'audio_source_root':str(source_dir),
               'audio_model_dir':str(model_dir)}
    monkeypatch.setattr(final_audio, '_duration', lambda *_:1.0)
    def decode(movie, directory, identity, duration):
        pcm = tmp_path/'decoded.pcm'
        pcm.write_bytes(b'\0' * 4)
        return pcm, final_audio.SAMPLE_RATE
    monkeypatch.setattr(final_audio, '_decode', decode)
    monkeypatch.setattr(final_audio, '_windows', lambda *args:[])
    def unavailable(*args, **kwargs):
        raise RuntimeError(f'task submission returned HTTP {http_status}: audio review unavailable')
    monkeypatch.setattr(final_audio, '_judge', unavailable)

    result = final_audio.review_audio(tmp_path, episode, movie, runtime=runtime)
    assert result['complete'] is False
    assert result['service_interrupted']['http_status'] == http_status
    assert result['retryable'] is retryable
    assert read_json(episode/'final_audio_review.json')['service_interrupted']['http_status'] == http_status
