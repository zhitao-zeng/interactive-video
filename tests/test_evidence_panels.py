from novel_manga.interactive.delivery import evidence_events, _overlay_ass


def test_evidence_panel_keeps_its_placement_and_actual_timing():
    events=evidence_events([{'index':2,'text':'801 开门记录\n01:52','placement':'panel'}],
        {'shots':[{'index':1,'duration_seconds':4},{'index':2,'duration_seconds':6}]},
        {'clips':[{'clip_id':'clip_01','shot_indexes':[1,2]}]},
        [{'unit_id':'clip_01','start':20,'end':30}])
    assert events == [{'text':'801 开门记录\n01:52','start':24.0,'end':30.0,'placement':'panel'}]


def test_panel_is_large_and_sits_between_face_and_dialogue(tmp_path):
    target=tmp_path/'evidence.ass'
    _overlay_ass(target,[{'text':'801 开门记录\n01:52','start':2,'end':8,'placement':'panel'}],1080,1920)
    text=target.read_text()
    assert ',EvidencePanel,,' in text
    style=next(s for s in text.splitlines() if s.startswith('Style: EvidencePanel,')).split(',')
    assert int(style[2]) > 42
    assert int(style[18]) == 2
    assert 1920*.20 <= int(style[21]) <= 1920*.35
    assert '801 开门记录\\N01:52' in text
