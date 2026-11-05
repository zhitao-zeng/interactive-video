"""An original author can enter production without host-written story decisions."""
import copy
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json


def authored():
    return {'title':'fixture', 'logline':'A visitor waits.', 'chapter_script':{'shots':[
        {'index':1,'scene_id':'room','location':'门厅','characters':['访客'],'in_frame':['访客'],
         'purpose':'观察门','start_state':'站在门旁','event':'访客抬头看门','end_state':'停住',
         'camera':'固定近景','shot_scale':'近景','duration_seconds':6,'sfx':'鞋底停下的摩擦声',
         'opening_frame':{'image_prompt':'An adult stands beside a closed door.','visible_characters':['访客']},
         'turns':[{'speaker_name':'访客','text':'门刚才动了吗？','delivery_mode':'visible_dialogue',
                   'emotion':'The question ends quietly as the visitor notices the door.'}]},
        {'index':2,'scene_id':'room','location':'门厅','characters':['访客'],'in_frame':['访客'],
         'purpose':'等待回应','start_state':'停住','event':'访客安静看着门','end_state':'仍未开门',
         'camera':'固定近景','shot_scale':'近景','duration_seconds':6,'sfx':'远处风声','turns':[],
         'opening_frame':{'image_prompt':'The same adult watches the closed door.','visible_characters':['访客']}}]},
        'clips':[{'shot_indexes':[1],'request_seconds':6},{'shot_indexes':[2],'request_seconds':6}],
        'interaction':{'question':'下一步？','selected':None,'options':[
            {'id':'A','label':'等候','reason':'听回应','next_step':'继续等待'},
            {'id':'B','label':'离开','reason':'出口在身后','next_step':'离开门厅'}]},
        'spatial_plan':{'scenes':[{'scene_id':'room','anchors':['门'],'placed':['访客'],
                                  'assumptions':[],'shot_indexes':[1,2]}]},
        'production_design':{'palette':'纸白、靛青','characters':[{'name':'访客','appearance':'成年，短黑发',
            'wardrobe':'靛青外套'}],'locations':[{'name':'门厅','description':'一扇关闭木门，左侧有窗'}],
            'props':[],'continuity_rules':['门保持关闭']}}


def manifest():
    return {'mode':'original','project':'suspense','task':{'brief':'原创试制','production_handoff':True},
            'profile':{'style':'paper-folklore','frame':'9:16','h3_input_mode':'first-frame',
                       'h3_speech_policy':'no_reference_performance_first'}}


def test_handoff_preserves_authored_content_and_is_ready_for_existing_packer(tmp_path):
    from novel_manga.interactive.creative_material import material, write_material
    from novel_manga.interactive.authored import prepare_authored, pack_authored
    draft=authored();original=copy.deepcopy(draft)
    value,source=material(draft,manifest())
    assert draft==original
    for old,new in zip(draft['chapter_script']['shots'],value['script']['shots']):
        for key in ('turns','camera','event','start_state','end_state','opening_frame','duration_seconds'):
            assert new[key]==old[key]
        assert new['source_quote'] in source
    assert value['voice_specs']=={} and not value.get('reference_audios')
    write_material(draft,manifest(),tmp_path/'material')
    episode=prepare_authored(tmp_path/'material',tmp_path/'edition',family='suspense')
    plan=pack_authored(episode)
    assert len(plan['clips'])==2
    assert not any(r['role']=='voice' for c in plan['clips'] for r in c['references'])
    assert all(c['h3_style_line'] for c in plan['clips'])


def test_missing_actor_and_location_design_return_creator_feedback():
    from novel_manga.interactive.creative_material import design_issues
    draft=authored();draft['production_design']['characters']=[]
    draft['production_design']['locations']=[]
    errors=design_issues(draft,manifest())
    assert any('访客' in e for e in errors) and any('门厅' in e for e in errors)


def test_material_retry_never_overwrites_a_different_adopted_draft(tmp_path):
    from novel_manga.interactive.creative_material import write_material
    import pytest
    draft=authored();write_material(draft,manifest(),tmp_path/'material')
    before=(tmp_path/'material/material.json').read_bytes()
    write_material(draft,manifest(),tmp_path/'material')
    draft['chapter_script']['shots'][0]['turns'][0]['text']='different'
    with pytest.raises(ValueError,match='existing'):
        write_material(draft,manifest(),tmp_path/'material')
    assert (tmp_path/'material/material.json').read_bytes()==before


def test_full_entry_resumes_same_edition_and_passes_config_without_rebuilding_assets(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.interactive import film,creation,runtime,production
    task=tmp_path/'task.json'
    atomic_write_json(task,{'mode':'original','project':'suspense','brief':'fixture','target_seconds':[12,20]})
    def create(path,directory,**kwargs):
        atomic_write_json(directory/'sandbox/input/manifest.json',manifest())
        atomic_write_json(directory/'sandbox/output/episode.json',authored())
        atomic_write_json(directory/'creator-state.json',{'status':'creative_candidate'})
        return {'status':'creative_candidate'}
    monkeypatch.setattr(creation,'run',create)
    monkeypatch.setattr(runtime,'configure',lambda *a,**k:None)
    assets=[];calls=[]
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(build_assets=lambda:assets.append('built')))
    def produce(root,lane,**kwargs):
        calls.append(kwargs)
        plan=root/'outputs/pilot/pilot_1/clip_plan.json'
        value=read_json(plan)
        if len(calls)==1:
            value['retained_marker']='keep';atomic_write_json(plan,value)
            return {'status':'blocked','reason':'fixture retry'}
        assert value['retained_marker']=='keep'
        return {'status':'accepted'}
    monkeypatch.setattr(production,'run_episode',produce)
    config={'model':'selected-test-model'}
    first=film.run(tmp_path,task,tmp_path/'run','pilot',config=config)
    assert first['status']=='blocked'
    second=film.run(tmp_path,task,tmp_path/'run','pilot',config=config,resume=True)
    assert second['status']=='accepted' and len(assets)==1
    assert [r['resume']for r in calls]==[False,True]
    assert all(r['agent_config']==config for r in calls)


def test_handoff_spatial_and_style_design_reach_production_and_authoritative_inputs(tmp_path):
    from novel_manga.interactive.creative_material import write_material
    from novel_manga.interactive.authored import prepare_authored,pack_authored
    from novel_manga.application.agents.episode.context import create_context
    from novel_manga.application.agents.episode.inputs import prepare_inputs
    draft=authored()
    draft['spatial_plan']['scenes'][0]['anchors'].append('unique east window anchor')
    frozen=tmp_path/'style.json'
    atomic_write_json(frozen,{'name':'paper','visual_style':'Paper design','render_family':'3d','h3_style_line':'Paper cinema.'})
    write_material(draft,manifest(),tmp_path/'material',style_source=frozen)
    episode=prepare_authored(tmp_path/'material',tmp_path/'edition',family='suspense')
    pack_authored(episode)
    ctx=create_context(episode.parent,episode.name,output_root=tmp_path/'agents',input_mode='script')
    prepared=prepare_inputs(ctx)
    assert read_json(ctx.novel_dir/'layout.json')==draft['spatial_plan']
    assert read_json(ctx.work_dir/'input/layout.json')==draft['spatial_plan']
    assert 'input/layout.json' in prepared['authoritative_inputs']
    assert 'input/style.json' in prepared['authoritative_inputs']


def test_name_only_assets_are_returned_to_author_instead_of_generated():
    from novel_manga.interactive.creative_material import design_issues
    draft=authored()
    draft['production_design']['characters'][0]['appearance']=' '
    draft['production_design']['props']=[{'name':'纸鹤'}]
    draft['chapter_script']['shots'][0]['props']=['纸鹤']
    errors=design_issues(draft,manifest())
    assert any('appearance' in e and '访客' in e for e in errors)
    assert any('appearance' in e and '纸鹤' in e for e in errors)
