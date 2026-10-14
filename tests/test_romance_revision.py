from pathlib import Path
from novel_manga.interactive.romance import romance_material
from novel_manga.interactive.romance_revision import single_take_material

PACKAGE=Path(__file__).parents[1]/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2'


def test_new_edition_preserves_every_source_event_and_speaker_in_order():
    original=romance_material(PACKAGE);revision=single_take_material(PACKAGE)
    old=original['script']['shots'];new=revision['script']['shots']
    assert len(new)==63
    for a,b in zip(old,new):
        assert (a['shot_id'],a['source_scene'],a['source_beat'],a['characters'],a['turns'])==(b['shot_id'],b['source_scene'],b['source_beat'],b['characters'],b['turns'])
    assert all(g['shot_indexes']==[s['index']] for g,s in zip(revision['groups'],new))
    assert all(s['camera']=='Static Shot' for s in new)
    assert 180<=sum(g['request_seconds'] for g in revision['groups'])+3<=300


def test_revision_resolves_camera_and_interview_clothing_in_source_fields():
    revision=single_take_material(PACKAGE);shots=revision['script']['shots']
    addressed=next(s for s in shots if '固定摄像机' in s['turns'][0]['emotion'])
    assert 'looking at the fixed camera' in addressed['authored_frame']
    assert '正反打' not in '\n'.join(s['text'] for s in revision['segments'])
    assert any('正反打' in s['source_director_note'] for s in shots)
    interview=[s for s in shots if s['source_scene']==4]
    assert all('overshirt' not in s['authored_frame'] for s in interview)
    assert all('unseen interviewer' not in s['authored_frame'] for s in interview)
    assert 'puts the loose charcoal garment' in interview[0]['authored_frame']


def test_lu_waits_for_permission_before_the_source_sitting_action():
    shots=single_take_material(PACKAGE)['script']['shots']
    asking=next(s for s in shots if s['source_scene']==2 and s['turns'][0]['text']=='我能坐这儿吗？')
    after=next(s for s in shots if s['source_scene']==2 and s['turns'][0]['text']=='记住了，随心所欲。')
    assert 'The subject is standing in the living room' in asking['authored_frame']
    assert 'The subject is seated on the northwest chair' in after['authored_frame']
