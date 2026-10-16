from pathlib import Path
import json
from types import SimpleNamespace

from novel_manga.interactive.suspense import suspense_material
from novel_manga.providers.phanrouter_video import video_payload

ROOT = Path(__file__).resolve().parents[1]


def test_approved_script_keeps_dialogue_sources_and_information_order():
    source = json.loads((ROOT/'projects/suspense/revisions/2026-10-13-investigation/storyboard.review.json').read_text())
    material = suspense_material(ROOT)
    shots = material['script']['shots']
    assert len(shots) == len(source['shots']) == 32
    for shot, authored in zip(shots, source['shots']):
        assert shot['source_shots'] == authored['source_shots']
        spoken = [t['text'] for t in shot['turns'] if t['delivery_mode'] != 'silent_action']
        assert ''.join(spoken) == authored['dialogue']
    assert not any('017' in e['text'] for e in material['overlays'] if e['index'] < 24)
    assert material['interaction']['seconds'] == 3
    assert material['interaction']['selected'] is None
    assert [o['id'] for o in material['interaction']['options']] == ['A', 'B', 'C']


def test_offscreen_voice_stays_shen_and_xu_stays_silent():
    shots = suspense_material(ROOT)['script']['shots']
    shot = shots[30]
    assert shot['in_frame'] == ['徐程']
    assert shot['turns'][0]['speaker_name'] == '沈序'
    assert shot['turns'][0]['delivery_mode'] == 'offscreen_dialogue'
    assert all('徐程' not in s['in_frame'] for s in shots if s['location'] == '801室内')


def test_requests_keep_one_male_voice_and_supported_lengths():
    material = suspense_material(ROOT)
    shots = material['script']['shots']
    groups = material['groups']
    assert [i for g in groups for i in g['shot_indexes']] == list(range(1, 33))
    for group in groups:
        chosen = [shots[i-1] for i in group['shot_indexes']]
        speakers = {t['speaker_name'] for s in chosen for t in s['turns'] if t['delivery_mode'] != 'silent_action'}
        assert len(speakers) <= 1
        assert len({s['location'] for s in chosen}) == 1
        assert 4 <= group['request_seconds'] <= 15
    assert sum(g['request_seconds'] for g in groups) == 179


def test_groups_do_not_drop_a_second_authored_prop():
    material = suspense_material(ROOT)
    for group in material['groups']:
        props = {p for i in group['shot_indexes'] for p in material['script']['shots'][i-1]['props']}
        assert len(props) <= 1, 'The shared compiler admits one prop reference per request'


def test_adjacent_door_direction_reaches_camera_field():
    shot = suspense_material(ROOT)['script']['shots'][28]
    assert '对面门' not in shot['camera']
    assert '相邻802门' in shot['camera']


def test_compiled_requests_carry_supplied_full_wardrobe(tmp_path,monkeypatch):
    from novel_manga.interactive.suspense import pack_suspense
    target=tmp_path/'suspense-master'
    episode=target/'suspense-master_1'
    episode.mkdir(parents=True)
    material=suspense_material(ROOT)
    for name,key in [('profile','profile'),('story_bible','bible')]:
        (target/(name+'.json')).write_text(json.dumps(material[key],ensure_ascii=False))
    for name,key in [('chapter_script','script'),('suspense_groups','groups'),('segments','segments')]:
        (episode/(name+'.json')).write_text(json.dumps(material[key],ensure_ascii=False))
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(ROOT))
    plan=pack_suspense(episode)
    for clip in plan['clips']:
        if '沈序' in clip['cast']:
            assert '炭灰哑光及大腿中部羊毛外套' in clip['prompt']
        assert '中国国漫短剧' not in clip['prompt']
        assert '对面门内' not in clip['prompt']


def test_480p_reaches_video_payload_and_preserves_portrait():
    profile = suspense_material(ROOT)['profile']
    payload = video_payload(SimpleNamespace(video_model='sd2.5'), 'trial', None, 6,
                            ratio=profile['frame'], resolution=profile['resolution'])
    assert payload['resolution'] == '480p'
    assert payload['ratio'] == '9:16'


def test_tail_ambience_comes_from_authored_silent_shot_only():
    from novel_manga.interactive.delivery import ambience_window
    script = {'shots':[{'index':18, 'duration_seconds':7,
                       'turns':[{'delivery_mode':'silent_action', 'text':'徐程沉默'}]}]}
    plan = {'clips':[{'clip_id':'clip_14','shot_indexes':[18]}]}
    timeline = [{'unit_id':'clip_14','start':93,'end':100}]
    assert ambience_window(18, script, plan, timeline) == (94.0, 3.0)
    script['shots'][0]['turns'] = [{'delivery_mode':'offscreen_dialogue','text':'不能拿对白循环'}]
    import pytest
    with pytest.raises(ValueError,match='silent'):
        ambience_window(18, script, plan, timeline)
