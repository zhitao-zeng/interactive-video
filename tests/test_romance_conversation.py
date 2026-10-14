import copy
from types import SimpleNamespace
import pytest
from novel_manga.interactive.conversation import compile_conversation_clip
from novel_manga.media.generation import build_request
from novel_manga.story.dialogue import dialogue_punctuation


def sample():
    refs=[{'role':'character','name':n,'path':f'{i}.jpg'} for i,n in enumerate(['甲','乙','丙'],1)]
    refs += [{'role':'location','name':'房间','path':'room.jpg'}, {'role':'prop','name':'三只行李箱','path':'bags.jpg'}]
    turns=[{'speaker_name':n,'text':t,'delivery_mode':'visible_dialogue','emotion':'正常接话'} for n,t in [('甲','杯子在哪？'),('乙','在桌上。'),('甲','看见了。')]]
    shot={'index':1,'characters':['甲','乙','丙'],'in_frame':['甲','乙','丙'],'props':['三只行李箱'],'visual_prompt':'甲左、乙中、丙右，三箱在北墙。','end_state':'甲左、乙中、丙右，三箱在北墙。','camera':'固定三人中景，不切镜','motion_prompt':'甲看桌面，乙示意杯子。丙静默听。','sfx':'室内底噪','turns':turns,'authored_frame':'A locked three-person shot in the same room. Three suitcases stay along the wall. <DIALOGUE_1> The listener points to the table. <DIALOGUE_2> <DIALOGUE_3> Everyone remains seated in the original positions.'}
    clip={'clip_id':'clip_01','kind':'video','request_seconds':13,'references':refs,'shot_indexes':[1],'shot_timing':[{'stage':1,'seconds':13}],'render_family':'photo','h3_style_line':'Photorealistic live-action footage.'}
    return clip,shot


def test_continuous_prompt_keeps_state_silent_person_and_every_line():
    c,s=sample();before=copy.deepcopy((c,s));out=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    assert (c,s)==before
    assert '剪辑切至' not in out['prompt']
    assert '丙' in out['prompt'] and '三箱在北墙' in out['prompt']
    for turn in s['turns']:
        assert out['prompt'].count(turn['text'])==1
        assert out['prompt_h3'].count(dialogue_punctuation(turn['text']))==1
    assert '<DIALOGUE_' not in out['prompt_h3']
    assert out['references']==c['references']


def test_reject_missing_end_or_unbound_dialogue_slot():
    c,s=sample();s['end_state']=''
    with pytest.raises(ValueError,match='end_state'):compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    c,s=sample();s['authored_frame']=s['authored_frame'].replace('<DIALOGUE_3>','')
    with pytest.raises(ValueError,match='dialogue slots'):compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})


def test_actual_request_builder_routes_sd_and_h3(tmp_path):
    c,s=sample();c=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    for ref in c['references']:(tmp_path/ref['path']).write_bytes(b'reference')
    state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False))
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url=''),state=state,feedback={},novel_dir=tmp_path,options=SimpleNamespace(voice_budget=15))
    sd,refs,_,_=build_request(ctx,c,1)
    assert sd['prompt']==c['prompt'] and len(refs)==5
    ctx.settings.local_h3_base_url='pool'
    ctx.provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':544})
    h3,_,_,_=build_request(ctx,c,1)
    assert h3['prompt']==c['prompt_h3']
    assert h3['h3_sampling']['short_edge']==544


def test_h3_request_contains_translated_performance_at_each_turn(tmp_path):
    c,s=sample()
    out=compile_conversation_clip(c,s,performance={'正常接话':'Respond conversationally to the partner, not to camera.'})
    for ref in out['references']:(tmp_path/ref['path']).write_bytes(b'reference')
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),feedback={},novel_dir=tmp_path,
        state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False)),
        options=SimpleNamespace(voice_budget=15),provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':544}))
    request,_,_,_=build_request(ctx,out,1)
    assert request['prompt'].count('Respond conversationally to the partner, not to camera.')==3
    with pytest.raises(ValueError,match='performance'):
        compile_conversation_clip(c,s,performance={})


def test_blender_reference_is_geometry_not_extra_people_or_second_room():
    c,s=sample()
    c['references'].append({'role':'location','name':'白模','path':'layout.png','reference_scope':'spatial_layout'})
    out=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    assert 'spatial layout guide' in out['prompt_h3']
    assert 'geometry_only' in out['prompt_h3']
    assert 'in <Subject 4> and <Subject 6>' not in out['prompt_h3']
    assert '不采用灰模外观' in out['prompt']


def test_programme_voiceover_binds_offscreen_without_guest_lip_sync():
    c,s=sample();s['turns']=[{'speaker_name':'节目解说','delivery_mode':'offscreen_dialogue','text':'今晚十点，选一个人聊聊。','emotion':'节目广播'}]
    s['authored_frame']='The seated guests read their blank cards. <DIALOGUE_1> All guests remain silent.'
    out=compile_conversation_clip(c,s,performance={'节目广播':'An in-room programme announcement, not a guest speaking.'})
    assert 'off-screen voiceover' in out['prompt_h3']
    assert '画外' in out['prompt']
    assert '今晚十点' in out['prompt_h3']


def test_conversation_pack_restores_second_authored_prop(tmp_path, monkeypatch):
    import json
    from novel_manga.interactive.conversation import pack_conversation
    from novel_manga.application.packing import context,service
    from novel_manga.story import compilation
    from novel_manga.application.preparation import readiness
    from novel_manga.models.bible import StoryBible,Prop
    from tests.test_scene_compilation import options
    c,s=sample();s.update(location='房间',duration_seconds=13,props=['三只行李箱','水杯'])
    bible=StoryBible(novel_title='测试',genre='generic',visual_style='真人',palette='木色',style_fingerprint='test',characters=[],locations=['房间'],props=[Prop(name='三只行李箱',appearance='三箱',category='其他'),Prop(name='水杯',appearance='透明杯',category='其他')])
    episode=tmp_path/'sample_1';episode.mkdir()
    for i in [1,2]:
        d=tmp_path/f'series_assets/props/prop_{i:03}';d.mkdir(parents=True);(d/'turnaround.jpeg').write_bytes(b'asset')
    for name,value in [('chapter_script',{'shots':[s]}),('authored_groups',[{'shot_indexes':[1],'request_seconds':13,'source_segment':'one'}])]:
        (episode/(name+'.json')).write_text(json.dumps(value))
    (tmp_path/'performance_en.json').write_text(json.dumps({'正常接话':'Respond naturally.'}))
    monkeypatch.setattr(context,'load_context',lambda *a,**k:{'compiler_options':options(15),'bible':bible,'location_map':{}})
    monkeypatch.setattr(service,'prepared_shots',lambda *a,**k:[s])
    monkeypatch.setattr(service,'clip_entry',lambda *a,**k:copy.deepcopy(c))
    monkeypatch.setattr(compilation,'plan_totals',lambda *a,**k:{})
    monkeypatch.setattr(readiness,'plan_issues',lambda *a,**k:[])
    result=pack_conversation(episode)['clips'][0]
    assert {r['name'] for r in result['references'] if r['role']=='prop'}=={'三只行李箱','水杯'}
    cup=next(r for r in result['references'] if r['name']=='水杯')
    assert (tmp_path/cup['path']).is_file()
    assert '水杯' in result['prompt']


def test_real_h3_repair_entry_accepts_conversation_without_novel_stage_markers(monkeypatch):
    from novel_manga.application.rendering import h3
    c,s=sample();c=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    captured=[]
    def answer(parts,*args,**kwargs):
        captured.append(parts)
        return {'shots':['The three people stay seated in the same room. <DIALOGUE_1> <DIALOGUE_2> <DIALOGUE_3>'], 'soundscape':'Quiet room tone.', 'conflicts':[]}
    monkeypatch.setattr(h3,'ask_json',answer)
    monkeypatch.setattr(h3,'h3_translation_endpoint',lambda:SimpleNamespace(model='offline-test'))
    problems=[]
    assert h3.convert(c,tries=1,note='Use explicit subject tags instead of pronouns.',problems=problems)
    assert captured and not problems
    for t in s['turns']:assert c['prompt_h3'].count(dialogue_punctuation(t['text']))==1
