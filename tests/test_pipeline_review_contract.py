"""Regress observed opening/whole-movie review contradictions without media or API."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import confirmed, final_cut
from novel_manga.story.h3 import first_frame_prompt
from novel_manga.util import atomic_write_json, read_json
from support.delivery_cases import episode


@pytest.mark.parametrize('opening', ['Seated POV with her grey trouser legs and boots.',
                                    'An empty pool; no person or body part is visible.'])
def test_unlisted_speaker_keeps_only_what_the_actual_opening_shows(opening):
    clip={'references':[{'role':'character','name':'A','path':'a.png'},
                        {'role':'location','name':'pool','path':'pool.png'}],
          'first_frame':{'visible_characters':[],'extra_references':[], 'prompt':opening}}
    prompt=('subject_definitions:\n<Subject 1> is A in <Picture 1>.\n'
            '<Subject 2> is the setting in <Picture 2>.\n\nsummary:\nScene.\n\n'
            'retention_analysis:\n<Subject 1>: fully_preserved - identity from <Picture 1>.\n'
            '<Subject 2>: fully_preserved - setting from <Picture 2>.\n\n'
            'detailed_description:\n'+opening+' <Subject 1> says <d>[Chinese] 两条路。</d>\n\n'
            'overall_soundscape:\nWater.\n\nnon_diegetic_music:\nNone.')
    before=copy.deepcopy(clip)
    result=first_frame_prompt(clip,prompt)
    assert 'voice only' not in result and 'no visual appearance' not in result
    assert 'only as actually visible in the opening frame' in result
    assert opening in result and '<d>[Chinese] 两条路。</d>' in result
    assert '<Subject 1>: fully_preserved' in result
    assert '<Subject 2>: fully_preserved - setting from <Picture 1>.' in result
    assert clip==before


@pytest.fixture
def movie_review(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path)
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':27}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'mock frame'})
    return runner,media,movie


def issue(problem='无问题：服装与原文一致。'):
    return {'clip_ids':['a'],'frame_numbers':[1],'severity':'must_fix','problem':problem,
            'evidence':'actual visible frame','source_basis':'approved source'}


@pytest.mark.parametrize('problem', ['无问题：服装与原文一致。',
    '未发现明确违反批准源文或连续性约束的问题。', '未发现明确违规；仅记录为对照镜头。',
    '未发现违反批准源文的问题；字幕与 expected_captions 一致，画面为同一手机后置第一人称，无第二机位或手机本体入画。'])
def test_nonproblem_in_issues_gets_one_protocol_correction_with_raw_retained(movie_review,monkeypatch,problem):
    runner,media,_=movie_review;calls=[]
    genuine=issue('The open bag visibly carries the headlamp outside.')
    def judge(parts,*a,**k):
        calls.append(parts[-1]['text'])
        if len(calls)==1:
            # The problem wording is observed; the mixed passed=false answer is
            # a local variant, not a copy of review007's original passed=true raw.
            return {'passed':False,'observations':['visible bag'],'issues':[issue(problem),genuine.copy()]}
        return {'passed':False,'observations':['visible bag'],'issues':[genuine.copy()]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert len(calls)==2 and result['complete'] and not result['passed']
    assert result['responses'][0]['answer']['issues'][0]['problem']==problem
    assert result['responses'][0]['protocol_error']
    assert problem in calls[1] and genuine['problem'] in calls[1]
    assert result['issues'][0]['problem']==genuine['problem']
    assert 'issues=[]' in calls[0] and 'passed=true' in calls[0]


def test_repeated_nonproblem_response_remains_incomplete_with_both_raw_answers(movie_review,monkeypatch):
    runner,media,_=movie_review;calls=[]
    def judge(*a,**k):
        calls.append(1)
        return {'passed':False,'observations':['actual frames'],'issues':[issue()]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert len(calls)==2 and not result['complete'] and not result['passed']
    assert result['errors'] and len(result['responses'])==2
    assert all(r['answer']['issues'][0]['problem']==issue()['problem'] for r in result['responses'])


def test_nonproblem_can_be_corrected_to_a_legal_empty_issue_array(movie_review,monkeypatch):
    runner,media,_=movie_review;calls=[]
    def judge(*a,**k):
        calls.append(1)
        return ({'passed':False,'observations':['normal scene'],'issues':[issue()]}
                if len(calls)==1 else {'passed':True,'observations':['normal scene'],'issues':[]})
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert len(calls)==2 and result['complete'] and result['passed'] and result['issues']==[]
    assert result['responses'][0]['answer']['issues'][0]['problem']==issue()['problem']


@pytest.mark.parametrize('problem', ['未发现出口，人物却声称已经安全抵达。',
                                    '未发现批准动作：人物没有踏上既定平台。',
                                    '未按要求出现道具：可见空手与持杯要求冲突。'])
def test_genuine_missing_requirement_is_not_a_nonproblem_protocol_label(problem):
    final_cut.validate_group({'passed':False,'observations':['actual frames'],
        'issues':[issue(problem)]},[{'clip_id':'a'}])


@pytest.mark.parametrize('problem', [None,'无问题：服装与原文一致。',
                                    '未发现违反批准源文的问题；字幕与 expected_captions 一致。'])
def test_only_explicitly_contradictory_cached_automatic_review_is_retried(movie_review,monkeypatch,problem):
    runner,media,movie=movie_review;directory=runner.context.episode_dir
    nonproblem=problem is not None
    old_issue=issue(problem) if nonproblem else issue('The visible bag carries the headlamp outside.')
    # Construct a prior completed passed=false variant to exercise reuse; the
    # real review007 passed=true/nonempty-issues raw already fails bool validation.
    automatic={'complete':True,'passed':False,'issues':[old_issue],'errors':[]}
    cached={'policy':final_cut.POLICY,'movie':str(movie),'context':final_cut.context(directory,media,movie),
            **automatic,'automatic_review':copy.deepcopy(automatic),'frames':[],'groups':[],
            'responses':[{'group':1,'revision':1,'answer':{'passed':False,
                'observations':['old actual frames'],'issues':[copy.deepcopy(old_issue)]}}]}
    old=directory/'work/final_cut_review/review-001/review.json'
    atomic_write_json(old,cached);before=old.read_bytes()
    atomic_write_json(directory/'final_cut_review.json',cached)
    calls=[]
    def judge(*a,**k):
        calls.append(1)
        return {'passed':True,'observations':['actual current frames'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert calls==([1] if nonproblem else [])
    assert result['passed'] is nonproblem and old.read_bytes()==before
    assert (old.parent.parent/'review-002/review.json').exists() is nonproblem


def test_each_group_receives_bound_source_shots_and_keeps_global_story(movie_review,monkeypatch):
    runner,media,movie=movie_review;ctx=runner.context
    ctx.clip_plan['clips']=[{'clip_id':f'clip_{n}','kind':'video','shot_indexes':[n+15],'references':[]}
                            for n in (20,21,22)]
    ctx.script={'shots':[{'index':n+15,'origin_index':5,'shot_id':f'current-{n}',
                          'camera':f'approved camera {n}'} for n in (20,21,22)]}
    ctx.bible=SimpleNamespace(model_dump=lambda **k:{'continuity_rules':['same phone throughout']})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',ctx.clip_plan)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',ctx.script)
    media['clips']=[{'clip_id':c['clip_id'],'selected':{'video':str(movie),'chunks':[]}}
                    for c in ctx.clip_plan['clips']]
    media['assembly']['timeline']=[{'unit_id':c['clip_id'],'start':i*9,'end':(i+1)*9}
                                   for i,c in enumerate(ctx.clip_plan['clips'])]
    contexts=[];mappings=[]
    def judge(parts,*a,**k):
        text=parts[-1]['text']
        contexts.append(json.loads(text.split('来源：',1)[1].split('\nclip与参考映射',1)[0]))
        mappings.append(json.loads(text.split('\nclip与参考映射（生成请求不作为成片验收依据）：',1)[1]))
        assert '没展示不等于不存在' in text and '自己的手脚' in text
        return {'passed':True,'observations':['current frames'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['complete'] and len(contexts)==2
    assert all(c['script']==ctx.script for c in contexts)
    assert [[row['clip_id'] for row in group] for group in mappings]==[
        ['clip_20','clip_21','clip_22'],['clip_22']]
    assert [[[s['index'] for s in row['source_shots']] for row in group] for group in mappings]==[
        [[35],[36],[37]],[[37]]]
    assert mappings[1][0]['source_shots'][0]==ctx.script['shots'][2]
    assert all(c['bible']['continuity_rules']==['same phone throughout'] for c in contexts)


def test_confirmed_clothing_check_receives_adopted_camera_and_keeps_original_scope(tmp_path,monkeypatch):
    directory=tmp_path/'book_1'
    atomic_write_json(directory/'segments.json',[{'segment_id':'s','text':'看向亮廊，插入走廊。'}])
    camera='同一手机前置胸像，右廊只作为视线方向；走廊插镜由后期另处理。'
    atomic_write_json(directory/'chapter_script.json',{'shots':[{'index':42,'camera':camera,
        'visual_prompt':'坐姿自拍，灰色长裤自然可见。'}]})
    monkeypatch.setattr(confirmed,'clip_frames',lambda *a:[tmp_path/'frame.jpg'])
    monkeypatch.setattr(confirmed,'image_part',lambda *a:{'type':'text','text':'mock frame'})
    seen=[]
    def judge(parts,*a,**k):
        seen.append(parts[-1]['text'])
        return {'premise_valid':True,'premise_evidence':'current source','observations':['grey long trousers'],
                'checks':[{'criterion':'clothing only','scope':'visual','met':True,'evidence':'long trousers visible'}]}
    monkeypatch.setattr(confirmed,'ask_json',judge)
    result=confirmed.judge_finding({'shot_indexes':[42],'segment_ids':['s']},tmp_path/'video.mp4',
        {'issue':'shorts instead of long trousers','instruction':'Check naturally visible clothing only.'},
        directory/'work/confirmed_review/a')
    assert camera in seen[0] and '不扩大成新的画面重拍条件' in seen[0]
    assert result['result']=='resolved'
