"""The independent source review can return a grounded opening omission to the author."""
import copy

import pytest

from novel_manga.application.preparation import audit


def episode():
    return {'chapter_script': {'shots': [
        {'index':5, 'scene_id':'S1002', 'start_state':'她抱着背包。',
         'event':'她把背包放在台上。', 'end_state':'背包仍开在台上。',
         'opening_frame':{'image_prompt':'知遥抱着背包。'}},
        {'index':6, 'scene_id':'S1002', 'start_state':'知遥把背包合上但不背回肩上。',
         'event':'潮叔伸手向旧船灯。', 'end_state':'潮叔的手停在灯前。',
         'opening_frame':{'image_prompt':'知遥合上背包但不背回肩上。'}},
        {'index':7, 'scene_id':'S1002', 'start_state':'红色牌子亮在旧船灯罩上，潮叔收回手，知遥站在打捞台边。',
         'event':'潮叔承认认错物件。', 'end_state':'旧灯向海面退去。',
         'opening_frame':{'image_prompt':'知遥站在打捞台边，背包状态按场景保持。'}}]}}


def issue(data, first='chapter_script.shots[2].opening_frame.image_prompt',
          second='chapter_script.shots[1].start_state', **overrides):
    paths=audit._creative_evidence_paths(data)
    return {'kind':'continuity_omission','stage':None,'source_quote':'','source_segment':'',
        'problem':'同场上一镜明确背包未背回，本镜首帧笼统沿用场景状态而未说明可见人物的背包状态。',
        'correction':'由作者明确本镜是否可见背包及其状态，保持已写的连续性。',
        'evidence_origin':'authored_field','basis':[paths.index(first),paths.index(second)],
        'observed':'','expected':'','observed_time':'t=0','expected_time':'t=0',
        'relation':'underspecified',**overrides}


def ground(data,row):
    answer={'source_readable':True,'source_problem':'','issues':[row]}
    audit._ground_creative_answer(answer,data,{'source.md':'原始完整素材内容。'},
                                  evidence_paths=audit._creative_evidence_paths(data))
    return answer


def confirmation(decision):
    return {'decisions':[{'issue_index':0,'decision':decision,'reason':'依据两镜原句及完整场景独立判断。'}]}


def test_same_scene_adjacent_real_omission_is_grounded_and_only_independent_keep_reaches_author():
    data=episode();answer=ground(data,issue(data));row=answer['issues'][0]
    assert row['stage']==7
    assert row['observed']==data['chapter_script']['shots'][2]['opening_frame']['image_prompt']
    assert row['expected']==data['chapter_script']['shots'][1]['start_state']
    assert (row['observed_time'],row['expected_time'])==('t=0','t=0')
    assert audit._confirmed_answer(answer,confirmation('keep'))['issues']==[row]
    assert audit._confirmed_answer(answer,confirmation('withdraw'))['issues']==[]


def test_kind_is_available_only_for_authored_field_evidence():
    data=episode();schema=audit._creative_schema(audit._creative_evidence_paths(data),data,{'source.md':'真实原文'})
    rows=schema['properties']['issues']['items']['anyOf']
    matching=[]
    for row in rows:
        props=row['properties'];kinds=props['kind']['enum'];origins=props['evidence_origin']['enum']
        if 'continuity_omission' in kinds:
            assert origins==['authored_field']
            assert kinds==['continuity_omission']
            assert props['relation']=={'type':'string','enum':['underspecified']}
            matching.append(row)
    assert len(matching)==1


def test_previous_end_state_keeps_its_real_end_time():
    data=episode()
    row=issue(data,second='chapter_script.shots[1].end_state',expected_time='t=0')
    grounded=ground(data,row)['issues'][0]
    assert (grounded['observed_time'],grounded['expected_time'])==('t=0','t=end')


def test_adjacent_context_includes_explicit_scene_and_transition():
    data=episode()
    data['chapter_script']['shots'][1]['scene_transition']='数小时后'
    current=audit._adjacent_shots(data)[2]
    assert current['previous']['scene_id']=='S1002'
    assert current['previous']['scene_transition']=='数小时后'


@pytest.mark.parametrize('case',['different_scene','not_adjacent','current_camera','previous_event','wrong_origin','wrong_relation'])
def test_omission_rejects_wrong_evidence_scope_before_author_feedback(case):
    data=episode();kwargs={}
    if case=='different_scene':data['chapter_script']['shots'][2]['scene_id']='S-other'
    if case=='not_adjacent':kwargs['second']='chapter_script.shots[0].start_state'
    if case=='current_camera':
        data['chapter_script']['shots'][2]['camera']='侧面中景';kwargs['first']='chapter_script.shots[2].camera'
    if case=='previous_event':kwargs['second']='chapter_script.shots[1].event'
    if case=='wrong_origin':kwargs['evidence_origin']='original_text';kwargs['source_segment']='source.md'
    if case=='wrong_relation':kwargs['relation']='contradiction'
    with pytest.raises(ValueError,match='continuity'):
        ground(data,issue(data,**kwargs))


@pytest.mark.parametrize('situation',['offscreen','changed_by_action','jump_cut','missing_prior_state'])
def test_independent_withdraw_is_never_promoted_to_a_repair(situation):
    data=episode();previous,current=data['chapter_script']['shots'][1:]
    if situation=='offscreen':current['opening_frame']['image_prompt']='空中旧灯特写，人物与背包均在画外。'
    if situation=='changed_by_action':previous['event']='知遥把背包重新背回肩上。'
    if situation=='jump_cut':current['scene_transition']='数小时后，同一地点的跳切。'
    if situation=='missing_prior_state':previous['start_state']='知遥站在台边，背包状态未交代。'
    row=issue(data)
    answer=ground(data,row)
    result=audit._confirmed_answer(answer,confirmation('withdraw'))
    assert result['issues']==[]


def test_other_underspecified_does_not_become_blocking():
    data=episode();row=issue(data,kind='action')
    answer=ground(data,row)
    assert audit._confirmed_answer(answer,confirmation('keep'))['issues']==[]


def test_normal_audit_exposes_current_frame_boundary_to_initial_and_independent_confirmation(tmp_path,monkeypatch):
    data=episode();candidate=issue(data);requests=[]
    def ask(parts,schema,**options):
        name=options['name'];prompt=parts[0]['text'];requests.append((name,prompt))
        if name=='pre_render_story_audit':
            return {'source_readable':True,'source_problem':'','issues':[copy.deepcopy(candidate)]}
        assert name=='pre_render_audit_confirmation'
        return confirmation('keep')
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=audit.creative_audit(data,{'source.md':'原始完整素材内容。'},cache_path=tmp_path/'audit.json')
    assert [name for name,_ in requests]==['pre_render_story_audit','pre_render_audit_confirmation']
    initial,independent=(text for _,text in requests)
    assert '首帧生图和图审只接收本镜' in initial
    assert '不接收上一镜文本' in initial
    assert '按场景保持' in initial and '具体状态' in initial
    assert '只澄清前镜实际明确的同一状态维度' in initial
    assert '未写明的其他维度不能作为遗漏' in initial
    assert 'authored_field仅用于纯制作字段矛盾' not in initial
    assert '没有确定错误就issues=[]' not in initial
    assert '只报告有实际文本证据的确定性内容问题' not in initial
    assert '遗漏' in independent and '画外' in independent
    assert '可见人物的佩戴或持物属于人物可见状态' in independent
    assert '不能仅因物件本体可能画外就撤销' in independent
    assert '只涉及前镜实际明确的同一状态维度' in independent
    assert '撤销时须逐项说明两段原句为何能在所标时间同时成立' not in independent
    assert result['answer']['issues'][0]['kind']=='continuity_omission'
