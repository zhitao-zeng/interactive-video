from novel_manga.application.review.agent_inputs import add_boundary_frames


def test_formal_pack_covers_terminal_caption_window(tmp_path, monkeypatch):
    from novel_manga.application.repair import video_frames
    from novel_manga import util
    calls=[]
    monkeypatch.setattr(util, 'media_duration', lambda p: 8.032)
    def decode(work, take, seconds):
        calls.append((take, seconds))
        return {'frames':[{'path':f'output/{seconds}.jpg','seconds':seconds}]}
    monkeypatch.setattr(video_frames, 'inspect', decode)
    primary={'clips':{'a':{'current_video':'archive/a.mp4','current_take':'a/take-1',
                         'current_frames':[{'path':'input/last.jpg','seconds':7.229}]}}}
    add_boundary_frames(tmp_path, primary)
    assert calls == [('a/take-1',0.0),('a/take-1',7.832)]
    assert primary['clips']['a']['current_frames'][-1]['seconds'] > 7.8
    times=[f['seconds'] for f in primary['clips']['a']['current_frames']]
    assert times==sorted(times)
