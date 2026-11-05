from types import SimpleNamespace

from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json, read_json


def test_cached_delivery_cannot_finish_without_audio_for_the_same_export(tmp_path, monkeypatch):
    movie = tmp_path/'versions/export-008/episode01.mp4'
    movie.parent.mkdir(parents=True); movie.write_bytes(b'final exported movie')
    episode = tmp_path/'episode'; episode.mkdir()
    destination = tmp_path/'delivery'; destination.mkdir()
    ctx = SimpleNamespace(episode_dir=episode)
    runner = SimpleNamespace(context=ctx)
    final = {'movie':str(movie),'complete':True,'passed':True,'issues':[],'errors':[]}
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
