from pathlib import Path
import json
from novel_manga.interactive.romance import romance_material

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2'


def test_romance_preserves_all_authored_beats_and_voice_ownership():
    data=romance_material(PACKAGE)
    source=json.loads((PACKAGE/'routes/shared/script.json').read_text())['episodes'][0]
    expected=[f['line'] for s in source['scenes'] for f in s['flow'] if 'line' in f]
    actual=[t['text'] for s in data['script']['shots'] for t in s['turns'] if t['delivery_mode']!='silent_action']
    assert actual==expected
    assert len(data['script']['shots'])==63
    voice=[(t['speaker_name'],t['delivery_mode']) for s in data['script']['shots'] for t in s['turns'] if t.get('text')=='我来之前，列了十条择偶标准。']
    assert voice==[('许知意','offscreen_dialogue')]
    assert data['profile']['frame']=='16:9'
    assert data['interaction']['seconds']==3
    assert [x['id'] for x in data['interaction']['options']]==['A','B','C']


def test_romance_generation_groups_keep_timing_and_distinguishable_speakers():
    data=romance_material(PACKAGE)
    groups=data['groups'];shots=data['script']['shots']
    assert [i for group in groups for i in group['shot_indexes']]==list(range(1,64))
    for g in groups:
        assert 4<=g['request_seconds']<=15
        assert abs(sum(shots[i-1]['duration_seconds'] for i in g['shot_indexes'])-g['request_seconds'])<.001
        genders={}
        for i in g['shot_indexes']:
            for t in shots[i-1]['turns']:
                if t['delivery_mode']=='silent_action':continue
                gender=data['voice_specs'][t['speaker_name']]['gender']
                genders.setdefault(gender,set()).add(t['speaker_name'])
        assert all(len(names)<=1 for names in genders.values())
    assert 180<=sum(g['request_seconds'] for g in groups)+3<=300
