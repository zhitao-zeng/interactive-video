import copy
import json
import pytest

from novel_manga.application.repair import authored_request, managed
from novel_manga.application.profiles import h3_stamp, h3_prompt_outdated
from novel_manga.story.h3 import compose


def episode(tmp_path):
    clip = {'clip_id':'a','kind':'video','prompt':'【阶段】甲站在门前。','request_seconds':5,
            'authored_english_source':'provided/storyboard.json',
            'references':[{'role':'character','name':'甲','path':'a.jpg'},
                          {'role':'voice','name':'甲','path':'a.wav'}]}
    clip['prompt_h3']=compose(clip,['<Subject 1> stands by the door.'],[('', [('甲','你好。',False,'平静')])])
    clip['prompt_h3_of']=h3_stamp(clip)
    plan={'clips':[clip,{**copy.deepcopy(clip),'clip_id':'b'}]}
    script={'shots':[{'index':1,'source_quote':'甲站在门前。'}]}
    for name,data in [('clip_plan',plan),('chapter_script',script),('authored_groups',[]),('review_feedback',{})]:
        (tmp_path/(name+'.json')).write_text(json.dumps(data))
    return plan,script


def test_imported_recompile_uses_source_action_instead_of_cast_appearance_as_event():
    script={'shots':[{'index':1,'source_quote':'甲脱下外套，在乙回答后坐下。',
                     'motion_prompt':'Photorealistic footage. A clean-shaven man in a coat.',
                     'authored_frame':'Photorealistic footage. A clean-shaven man in a coat.',
                     'camera':'Static Shot','turns':[{'text':'我能坐吗？','speaker_name':'甲'}]},
                    {'index':2,'source_quote':'乙继续看书。','motion_prompt':'Unchanged frame.'}]}
    before=copy.deepcopy(script)
    rebuilt=authored_request.event_source(script,[1])
    assert rebuilt['shots'][0]['motion_prompt']=='甲脱下外套，在乙回答后坐下。'
    assert rebuilt['shots'][0]['turns']==script['shots'][0]['turns']
    assert rebuilt['shots'][0]['camera']==script['shots'][0]['camera']
    assert rebuilt['shots'][1]==script['shots'][1]
    assert script==before


def test_authored_translation_repair_keeps_source_other_clips_and_history(tmp_path,monkeypatch):
    plan,script=episode(tmp_path)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    def convert(clip,**kwargs):
        assert kwargs['source_script']==script and kwargs['episode_dir']==tmp_path
        clip['prompt_h3']=clip['prompt_h3'].replace('stands by','stands calmly by')
        clip['prompt']+=' temporary director note'
        return True
    monkeypatch.setattr(authored_request,'convert',convert)
    monkeypatch.setattr(authored_request,'request_consistency',lambda *a:{'consistent':True,'problems':[]})
    monkeypatch.setattr('novel_manga.application.repair.flow.repair_episode',lambda *a,**k:pytest.fail('must not rewrite chapter identities'))
    result,checks=managed.reframe_candidate(tmp_path,'a','keep calm')
    candidate=result['proposal']
    assert candidate['script']==script and candidate['plan']['clips'][1]==plan['clips'][1]
    assert candidate['plan']['clips'][0]['prompt']==plan['clips'][0]['prompt']
    assert not h3_prompt_outdated(candidate['plan']['clips'][0],strict=True)
    assert all((tmp_path/name).read_bytes()==value for name,value in before.items())
    assert not (tmp_path/'repair_history').exists()


@pytest.mark.parametrize('fault',['dialogue','source','reference'])
def test_authored_repair_rejects_unresolved_or_contract_changing_candidate(tmp_path,monkeypatch,fault):
    plan,_=episode(tmp_path)
    def convert(clip,**kwargs):
        if fault=='source':kwargs['source_conflicts'].append('source contradictory')
        clip['prompt_h3']=clip['prompt_h3'].replace('你好.','再见.') if fault=='dialogue' else clip['prompt_h3'].replace('stands by','stands calmly by')
        return True
    monkeypatch.setattr(authored_request,'convert',convert)
    monkeypatch.setattr(authored_request,'request_consistency',lambda *a:{'consistent':False,'problems':['wrong clothes']})
    with pytest.raises(ValueError):authored_request.propose(tmp_path,'a','fix')
    assert json.loads((tmp_path/'clip_plan.json').read_text())==plan


def test_conversation_repair_keeps_its_compiled_request_and_turns(tmp_path,monkeypatch):
    from novel_manga.application.packing import service
    turn={'speaker_name':'甲','delivery_mode':'visible_dialogue','text':'你好。','emotion':'自然'}
    script={'production_mode':'romance_conversation','shots':[{'index':4,'authored_frame':'Frame.','source_quote':'甲问好','turns':[turn]}]}
    draft={'clip_id':'clip_04','location':'室内','request_seconds':8,'prompt_compiler':'romance_conversation_v1','shot_indexes':[4],'shot_timing':[{'stage':1,'seconds':8}],
           'prompt':'连续交流，保留开始和结束状态。','references':[], 'dialogue_bindings':[{**turn,'stage':1}]}
    before=copy.deepcopy(draft)
    monkeypatch.setattr(service,'clip_entry',lambda *a,**k:pytest.fail('conversation must not rebuild through novel stages'))
    authored_request.source_event_request(tmp_path,draft,script)
    assert draft==before
    draft['dialogue_bindings'][0]['text']='再见。'
    with pytest.raises(ValueError,match='protected dialogue'):
        authored_request.source_event_request(tmp_path,draft,script)
