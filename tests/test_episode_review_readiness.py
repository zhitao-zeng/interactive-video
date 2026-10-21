from pathlib import Path
from types import SimpleNamespace
import pytest

from novel_manga.application.agents.episode.readiness import review_is_current, next_action
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


def _readiness_ctx(tmp_path, monkeypatch, *, visual_passed, audio, status_marker=None,
                   media_current=True, first_blocks=None):
    episode=tmp_path/'episode'; work=tmp_path/'work'
    (episode/'work').mkdir(parents=True); (work/'input').mkdir(parents=True)
    plan={'clips':[{'clip_id':'clip_01','kind':'video','first_frame':'frame.png'}]}
    media={'clips':[], 'assembly':{'final_video':'movie.mp4'},
        'quality_review':{'passed':visual_passed},
        'final_audio_review_required':True,
        'final_audio_review':audio,
        'final_cut_review':{'movie':'movie.mp4','complete':True,'passed':visual_passed}}
    if status_marker:
        media['final_audio_review_status']=status_marker
    atomic_write_json(episode/'clip_plan.json',plan)
    atomic_write_json(episode/'thin_media_report.json',media)
    atomic_write_json(episode/'episode_review.json',{'clips':[]})
    ctx=SimpleNamespace(episode_dir=episode,work_dir=work)
    monkeypatch.setattr('novel_manga.application.agents.episode.readiness.first_frame_blocks',
                        lambda *_: first_blocks or {})
    monkeypatch.setattr('novel_manga.application.agents.episode.readiness.media_started',lambda *_:True)
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**k:media_current)
    monkeypatch.setattr('novel_manga.application.music_selection.episode_music_errors',lambda *_:[])
    return ctx


def test_visual_failure_with_required_empty_audio_is_not_misreported_as_audio_block(tmp_path, monkeypatch):
    ctx=_readiness_ctx(tmp_path,monkeypatch,visual_passed=False,audio={})
    result=next_action(ctx)
    assert result['action']=='agent'
    assert 'audio' not in result['reason']


def test_busy_job_still_waits_before_audio_gate(tmp_path, monkeypatch):
    ctx=_readiness_ctx(tmp_path,monkeypatch,visual_passed=True,
        audio={'movie':'old.mp4','complete':False,'passed':False,'errors':['old audio review']})
    result=next_action(ctx,{'job':{'status':'running','action':'render','id':'job-1'}})
    assert result['action']=='wait'


def test_not_run_visual_gate_marker_does_not_block_on_missing_audio(tmp_path, monkeypatch):
    ctx=_readiness_ctx(tmp_path,monkeypatch,visual_passed=True,audio={},
        status_marker='not_run_visual_gate')
    assert next_action(ctx)['action']=='finish'


@pytest.mark.parametrize(('current','first_blocks','expected'),[
    (False,None,'render'),
    (True,{'clip_01':'stale opening'},'first_frames'),
])
def test_new_input_or_opening_precedes_old_audio_failure(tmp_path, monkeypatch, current, first_blocks, expected):
    ctx=_readiness_ctx(tmp_path,monkeypatch,visual_passed=True,
        audio={'movie':'old.mp4','complete':False,'passed':False,'errors':['old audio review']},
        media_current=current,first_blocks=first_blocks)
    result=next_action(ctx)
    assert result['action']==expected


def test_current_visual_pass_with_failed_audio_still_blocks_for_agent(tmp_path, monkeypatch):
    ctx=_readiness_ctx(tmp_path,monkeypatch,visual_passed=True,
        audio={'movie':'movie.mp4','complete':True,'passed':False,'issues':['audio dropout']})
    result=next_action(ctx)
    assert result['action']=='agent'
    assert 'full-audio review' in result['reason']
