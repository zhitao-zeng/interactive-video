from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.agents.episode.readiness import review_is_current
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json


def test_current_review_policy_does_not_certify_an_old_selected_take(tmp_path, monkeypatch):
    episode = tmp_path/'episode'; work = episode/'work'; work.mkdir(parents=True)
    video = episode/'selected.mp4'; video.write_bytes(b'first native take')
    review = {'policy': 3, 'review_mode':'verify', 'clips':{
        'clip_01': {'video':str(video), 'take':take_identity(video)}}}
    atomic_write_json(episode/'thin_media_report.json', {'clips':[
        {'clip_id':'clip_01','selected':{'video':str(video)}}]})
    monkeypatch.setattr('novel_manga.application.review.evidence.review_mode', lambda *_:'verify')
    monkeypatch.setattr('novel_manga.review.contracts.current_review', lambda *_:True)
    ctx=SimpleNamespace(episode_dir=episode)

    assert review_is_current(ctx, review) is True
    video.write_bytes(b'replacement take from current selection')
    assert review_is_current(ctx, review) is False
