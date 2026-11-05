import pytest
from types import SimpleNamespace
from novel_manga.review import policy, prompts, contracts
from novel_manga.review.evidence import ClipEvidence


@pytest.mark.parametrize('kind',['proportion','split_view','body_motion','spatial'])
def test_obvious_visual_problem_cannot_be_excused_by_bad_authored_request(kind):
    verdict={'severity':'fail','visual_defects':True,'visual_defect_kind':kind,
             'scripted':{'evidence':'generated request said so'},'reasonable_deviation':True,
             'feedback':'Keep normal body scale and one continuous camera view.'}
    assert policy.fix_tier(verdict,SimpleNamespace(characters=[]))=='must_fix'
    assert policy.compose_feedback(verdict)==verdict['feedback']


def test_harmless_alternative_shot_does_not_request_retake():
    verdict={'severity':'fail','story_ok':False,'story_kind':'原文中有动作的人物缺席',
             'reasonable_deviation':True,'deviation_note':'采用反应镜头，画外对白交代同一件事，人物关系清楚',
             'identity_ok':True,'visual_defects':False}
    assert policy.fix_tier(verdict,SimpleNamespace(characters=[]))=='ignore'


def test_reasonable_deviation_does_not_hide_an_actual_missing_story_event():
    verdict={'severity':'fail','story_ok':False,'story_kind':'原文中有动作的人物缺席',
             'reasonable_deviation':False,'identity_ok':True,'visual_defects':False}
    assert policy.fix_tier(verdict,SimpleNamespace(characters=[]))=='must_fix'


def test_both_judges_receive_the_same_visible_problem_standard():
    evidence=ClipEvidence({},[],[],[],[],[],[],{},'','','','',{})
    for text in [prompts.classic_prompt({}, {}, '', evidence),prompts.verify_prompt({}, {}, evidence)]:
        assert '人物头身或肢体比例明显失常' in text
        assert '左右/上下分屏、重复双视图' in text
        assert '合理的机位、构图、动作方式、站位' in text
        assert '角色卡本身有多个视图不表示视频可以变为角色卡拼图' in text
        assert '即使实际生成请求误写了双视图' in text
        assert '这是一段动画短剧' not in text


def test_verify_maps_visual_layout_category_into_existing_repair_tier():
    answer={'people':[],'same_person_twice':False,'species_or_gender_wrong':False,
            'action_by_wrong_person':False,'actor_missing':False,'lead_face_swapped':False,
            'body_artifact':True,'ghost_text':False,'verdict':'obvious','evidence':'图3同时出现两幅并列画面',
            'visual_defect_kind':'split_view','reasonable_deviation':False,'deviation_note':''}
    verdict=policy.verify_to_verdict(answer)
    assert verdict['visual_defect_kind']=='split_view'
    assert policy.fix_tier({**verdict,'scripted':True},SimpleNamespace(characters=[]))=='must_fix'
    for schema in [contracts.CLIP_SCHEMA,contracts.VERIFY_SCHEMA]:
        assert 'reasonable_deviation' in schema['properties']


def test_visible_split_cannot_be_ignored_as_a_reasonable_action_change():
    from novel_manga.application.review.judges import merge_presentation
    before={'severity':'pass','reasonable_deviation':True,'visual_defects':False,'visual_defect_kind':'none'}
    after=merge_presentation(before,{'layout':'split','body_scale':'natural','space_motion':'natural','evidence':'帧2中线将同一广场分成昼夜两幅画面'})
    assert after['severity']=='fail' and after['visual_defect_kind']=='split_view'
    assert policy.fix_tier(after,SimpleNamespace(characters=[]))=='must_fix'
    assert before['severity']=='pass'


def test_two_different_rooms_in_a_single_camera_view_are_not_forced_to_retake():
    from novel_manga.application.review.judges import merge_presentation
    before={'severity':'pass','reasonable_deviation':True,'visual_defects':False}
    after=merge_presentation(before,{'layout':'single','body_scale':'natural','space_motion':'natural','evidence':'柱子隔开两个房间，透视相连'})
    assert after['severity']=='pass'


def test_native_dialogue_captions_require_repair_but_physical_text_does_not():
    physical={'severity':'minor','text_or_watermark':True,'identity_ok':True,'visual_defects':False}
    assert policy.fix_tier(physical,SimpleNamespace(characters=[]))=='ignore'
    caption={**physical,'unplanned_subtitles':True,'subtitle_issue':'帧2下方出现大字对白'}
    assert policy.fix_tier(caption,SimpleNamespace(characters=[]))=='must_fix'
    correction=policy.compose_feedback(caption)
    assert '对白字幕由宿主统一添加' in correction and '保留经批准的剧情内' in correction
    assert '不得出现任何文字' not in correction


def test_a_paper_that_adds_a_meeting_is_not_an_ordinary_background_text_deviation():
    changed={'severity':'fail','story_ok':False,'story_kind':'关键线索内容被改写',
             'reasonable_deviation':True,'identity_ok':True,'visual_defects':False}
    assert policy.fix_tier(changed,SimpleNamespace(characters=[]))=='must_fix'


def test_verify_keeps_native_captions_and_changed_clues_separate_from_generic_text():
    caption=policy.verify_to_verdict({'unplanned_subtitles':True,'subtitle_issue':'帧3对白固定于屏幕下方',
        'verdict':'clear','evidence':'帧3下方出现额外对白','people':[]})
    assert caption['severity']=='fail' and caption['unplanned_subtitles']
    assert policy.fix_tier(caption,SimpleNamespace(characters=[]))=='must_fix'
    clue=policy.verify_to_verdict({'clue_text_changed':True,'evidence':'纸条另写了新的会面约定','people':[]})
    assert clue['story_ok'] is False and clue['story_kind']=='关键线索内容被改写'
    assert policy.fix_tier(clue,SimpleNamespace(characters=[]))=='must_fix'


def test_a_positive_native_caption_observation_cannot_remain_a_pass(tmp_path,monkeypatch):
    from novel_manga.application.review import judges
    import novel_manga.application.packing.visor as visor
    monkeypatch.setattr(visor,'recorded_clip_states',lambda *a,**k:{})
    monkeypatch.setattr(judges.review_evidence,'review_mode',lambda *a:'verify')
    monkeypatch.setattr(judges,'judge_clip_verify',lambda *a,**k:{'severity':'pass','unplanned_subtitles':True})
    monkeypatch.setattr(judges,'presentation_check',lambda *a:None)
    monkeypatch.setattr(judges.cast_video,'enabled',lambda:False)
    verdict=judges.judge_clip({},tmp_path/'raw.mp4',None,{},'',tmp_path/'episode/work/review/clip')
    assert verdict['severity']=='fail' and verdict['reasonable_deviation'] is False
    assert '实体文字' in verdict['feedback']
