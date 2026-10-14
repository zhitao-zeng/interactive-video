import json
from PIL import Image
from novel_manga.application.review import cast_video, evidence


def test_scope_change_invalidates_cached_card_description(tmp_path, monkeypatch):
    card = tmp_path / 'card.jpg'
    Image.new('RGB', (30, 40)).save(card)
    questions = []
    def ask(parts, *args, **kwargs):
        questions.append(parts[-1]['text'])
        return {'look': '深色翻领外套，可见胸口'}
    monkeypatch.setattr(cast_video.model_client, 'ask_json', ask)
    cast_video.card_look(tmp_path, '甲', card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope': 'identity_portrait'}))
    cast_video.card_look(tmp_path, '甲', card)
    assert len(questions) == 2
    assert '不能推断衣摆长度' in questions[-1]


def test_portrait_look_keeps_explicit_wardrobe_without_inventing_visibility(tmp_path, monkeypatch):
    card = tmp_path / 'card.jpg'
    Image.new('RGB', (30, 40)).save(card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope': 'identity_portrait'}))
    (tmp_path / 'story_bible.json').write_text(json.dumps({'characters': [{'name': '甲', 'wardrobe': '深灰及大腿中段羊毛长外套'}]}))
    monkeypatch.setattr(cast_video, 'card_look', lambda *args: '深色翻领外套，可见胸口')
    result = cast_video.looks({'cast': ['甲'], 'references': [{'name': '甲', 'role': 'character', 'path': 'card.jpg'}]}, tmp_path, tmp_path/'book_1')
    assert '深灰及大腿中段羊毛长外套' in result['甲']
    assert '不能推断衣摆长度' in result['甲']
    assert '不要求画外部位入镜' in result['甲']


def test_frame_review_carries_portrait_scope(tmp_path, monkeypatch):
    from novel_manga.models.bible import StoryBible, Character
    work = tmp_path/'book_1/work/review/clip_01'; work.mkdir(parents=True)
    card = tmp_path/'card.jpg'; Image.new('RGB', (30,40)).save(card)
    card.with_suffix('.jpg.origin.json').write_text(json.dumps({'reference_scope':'identity_portrait'}))
    monkeypatch.setattr(evidence, 'clip_frames', lambda *args: [])
    bible = StoryBible(novel_title='n', genre='g', visual_style='live', palette='', style_fingerprint='x', characters=[Character(name='甲', appearance='青年', wardrobe='长外套')])
    _, facts = evidence.collect_clip_evidence({'cast':['甲'], 'references':[{'name':'甲','role':'character','path':'card.jpg'}]}, tmp_path/'v.mp4', bible, work, verify=True)
    assert '不能推断衣摆长度' in ''.join(facts.legend)
