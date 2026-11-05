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


def test_fast_keeps_its_own_validation_without_compiling_an_unused_h3_request():
    clip,shot=sample();shot['authored_frame']='甲 stands at the counter. <DIALOGUE_1> <DIALOGUE_2> <DIALOGUE_3>'
    with pytest.raises(ValueError,match='Chinese outside bound dialogue'):
        compile_conversation_clip(clip,shot,performance={'正常接话':'Reply naturally.'})
    fast=compile_conversation_clip(clip,shot,performance={},include_h3=False)
    assert 'prompt_h3'not in fast
    for turn in shot['turns']:assert turn['text'] in fast['prompt']
    shot['authored_frame']='An incomplete draft with no dialogue slots.'
    with pytest.raises(ValueError,match='dialogue slots'):
        compile_conversation_clip(clip,shot,performance={})
    assert compile_conversation_clip(clip,shot,performance={},include_h3=False)['prompt']==fast['prompt']
    shot.pop('authored_frame')
    assert compile_conversation_clip(clip,shot,performance={},include_h3=False)['prompt']==fast['prompt']
    with pytest.raises(ValueError,match='authored_frame'):
        compile_conversation_clip(clip,shot,performance={})


def test_offscreen_announcer_is_not_looked_up_as_a_pictured_character(tmp_path,monkeypatch):
    from novel_manga.application.packing import service
    from novel_manga.models.bible import StoryBible,Character
    from tests.test_scene_compilation import options
    _,shot=sample();shot.update(location='房间',duration_seconds=13,props=[],characters=['甲','乙','丙','节目组'])
    shot['turns']=[{'speaker_name':'节目组','delivery_mode':'offscreen_dialogue','text':'请慢慢选择。'}]
    packed={'kind':'video','location':'房间','shots':[shot],'seconds':13,'request_seconds':13,
            'shot_timing':[{'seconds':13}],'reference_cast':['甲','乙','丙']}
    bible=StoryBible(novel_title='测试',genre='generic',visual_style='真人',palette='木色',style_fingerprint='test',
        locations=['房间'],characters=[Character(name=name,appearance='成年嘉宾',wardrobe='普通衬衫')for name in ['甲','乙','丙']])
    class BoundaryReached(Exception):pass
    def references(pictured,*args,**kwargs):
        assert '节目组'not in pictured
        assert '节目组'in kwargs['speakers']
        raise BoundaryReached
    monkeypatch.setattr(service,'build_references',references)
    with pytest.raises(BoundaryReached):
        service.clip_entry(packed,'clip_01',{'bible':bible,'compiler_options':options(15),'overrides':{},
            'profile':{'h3_speech_policy':'no_reference_performance_first'},'location_map':{},'episode_dir':tmp_path/'book_1'})


def test_explicit_visible_cast_fills_room_inventory_without_changing_source():
    clip,shot=sample();shot['characters']=['甲','乙'];before=copy.deepcopy(shot)
    out=compile_conversation_clip(clip,shot,performance={},reference_cast=['甲','乙','丙'],include_h3=False)
    assert '【房间在场】甲、乙、丙。'in out['prompt']
    assert shot==before and out['reference_cast']==['甲','乙','丙']


def test_visible_person_still_needs_a_real_registered_reference():
    clip,shot=sample();shot['in_frame'].append('丁')
    with pytest.raises(ValueError,match='Visible participant reference is missing'):
        compile_conversation_clip(clip,shot,performance={},include_h3=False)


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


def test_actual_requests_keep_adopted_cg_style_without_forcing_live_action(tmp_path):
    c,s=sample()
    c.update(render_family='3d',h3_style_line='Beautified semi-realistic 3D CG in a 3D otome-game cutscene style.')
    out=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'})
    for ref in out['references']:(tmp_path/ref['path']).write_bytes(b'reference')
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url=''),feedback={},novel_dir=tmp_path,
        state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False)),
        options=SimpleNamespace(voice_budget=15),provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':768}))
    sd,_,_,_=build_request(ctx,out,1)
    assert '真人' not in sd['prompt']
    ctx.settings.local_h3_base_url='pool'
    h3,_,_,_=build_request(ctx,out,1)
    assert c['h3_style_line'] in h3['prompt']
    assert 'Photorealistic live-action' not in h3['prompt']
    assert h3['h3_sampling']['short_edge']==768
    for turn in s['turns']:
        assert h3['prompt'].count(dialogue_punctuation(turn['text']))==1


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


def test_camera_reference_scope_keeps_world_state_and_actual_voice_bindings(tmp_path):
    c,s=sample()
    s['in_frame']=['甲','乙']
    s['props'].append('水杯')
    c['references'].append({'role':'prop','name':'水杯','path':'glass.jpg'})
    c['references'] += [{'role':'voice','name':n,'path':f'{n}.mp3'} for n in ['乙','甲']]
    before=copy.deepcopy((c,s))
    out=compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'},
        reference_cast=['甲','乙'],reference_props=['水杯'])
    assert (c,s)==before
    assert [r['name'] for r in out['references'] if r['role']!='voice']==['甲','乙','房间','水杯']
    assert '丙' in out['prompt'] and '三箱在北墙' in out['prompt']
    for ref in out['references']:
        if ref['role']=='voice':
            import wave
            with wave.open(str(tmp_path/ref['path']),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
                audio.writeframes(b'\0\0'*16000)
        else:(tmp_path/ref['path']).write_bytes(b'reference')
    ctx=SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),feedback={},novel_dir=tmp_path,
        state=SimpleNamespace(clip=lambda _:SimpleNamespace(softened=False,compliance=False)),
        options=SimpleNamespace(voice_budget=15),provider=SimpleNamespace(sampling_parameters=lambda:{'short_edge':768}))
    request,refs,_,_=build_request(ctx,out,1)
    assert len(refs)==4
    assert '<Subject 1>' in request['prompt'] and '<Subject 2>' in request['prompt']
    for turn in s['turns']:assert request['prompt'].count(dialogue_punctuation(turn['text']))==1
    assert tmp_path/'bags.jpg' not in refs


@pytest.mark.parametrize('scope',[{'reference_cast':['甲']},{'reference_cast':['甲','乙','丁']},{'reference_props':['不存在']}])
def test_camera_reference_scope_rejects_missing_visible_or_unknown_names(scope):
    c,s=sample()
    with pytest.raises(ValueError,match='reference|Reference'):
        compile_conversation_clip(c,s,performance={'正常接话':'Respond naturally.'},**scope)


@pytest.mark.parametrize('location_view',[None,'series_assets/locations/west-coverage.png'])
@pytest.mark.parametrize('camera_scope',[False,True])
def test_conversation_pack_restores_second_authored_prop(tmp_path, monkeypatch,location_view,camera_scope):
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
    group={'shot_indexes':[1],'request_seconds':13,'source_segment':'one'}
    if camera_scope:
        s['in_frame']=['甲','乙']
        group.update(reference_cast=['甲','乙'],reference_props=['水杯'])
    if location_view:
        camera=tmp_path/location_view;camera.parent.mkdir(parents=True,exist_ok=True);camera.write_bytes(b'camera-reference')
        group['location_reference']=location_view
    for name,value in [('chapter_script',{'shots':[s]}),('authored_groups',[group])]:
        (episode/(name+'.json')).write_text(json.dumps(value))
    (tmp_path/'performance_en.json').write_text(json.dumps({'正常接话':'Respond naturally.'}))
    monkeypatch.setattr(context,'load_context',lambda *a,**k:{'compiler_options':options(15),'bible':bible,'location_map':{}})
    monkeypatch.setattr(service,'prepared_shots',lambda *a,**k:[s])
    monkeypatch.setattr(service,'clip_entry',lambda *a,**k:copy.deepcopy(c))
    monkeypatch.setattr(compilation,'plan_totals',lambda *a,**k:{})
    monkeypatch.setattr(readiness,'plan_issues',lambda *a,**k:[])
    result=pack_conversation(episode)['clips'][0]
    assert {r['name'] for r in result['references'] if r['role']=='prop'}==({'水杯'} if camera_scope else {'三只行李箱','水杯'})
    assert {r['name'] for r in result['references'] if r['role']=='character'}==({'甲','乙'} if camera_scope else {'甲','乙','丙'})
    assert '丙' in result['prompt'] and '三箱在北墙' in result['prompt']
    cup=next(r for r in result['references'] if r['name']=='水杯')
    assert (tmp_path/cup['path']).is_file()
    assert '水杯' in result['prompt']
    if location_view:
        locations=[r for r in result['references'] if r['role']=='location']
        assert len(locations)==1
        assert locations[0]['name']=='房间' and locations[0]['path']==location_view
        assert 'geometry_only' not in result['prompt_h3']


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
