import copy
import json
from types import SimpleNamespace

import pytest

from novel_manga.media.first_frames import current_contract, matches_current_contract, request_prompt
from novel_manga.util import atomic_write_json


def case(tmp_path):
    ctx=SimpleNamespace(novel_dir=tmp_path/'book',episode_dir=tmp_path/'book/book_1',run_dir=tmp_path/'run')
    shot={'index':1,'start_state':'知遥持杆站在岸边；背包尚未完全靠岸。',
        'props':['滑向海面的深灰双肩背包（手机、证件在内）'],
        'scene_objects':['海面上的深灰双肩背包'],'in_frame':['知遥'],
        'camera':'Start at the shore. Endpoint: later reveal the compass.',
        'event':'Later take out the compass.',
        'opening_frame':{'image_prompt':'Zhiyao waits beside the floating backpack.',
                         'visible_characters':['知遥'],'reference_subjects':[]}}
    bible={'characters':[{'name':'知遥','wardrobe':'jacket, backpack and pocket watch'}],
        'props':[{'name':'深灰双肩背包','aliases':['旧包'],'owner':'知遥',
                  'appearance':'深灰织物、内侧有知遥缝的补丁。','material':'织物、塑料扣具'},
                 {'name':'compass','owner':'潮叔','appearance':'brass'},
                 {'name':'pocket watch','owner':'知遥','appearance':'silver'}]}
    clip={'clip_id':'clip_01','kind':'video','shot_indexes':[1,2],'references':[],
          'shots':[shot,{'index':2,'start_state':'The compass is on the table.','props':['compass']}]}
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':clip['shots']})
    atomic_write_json(ctx.novel_dir/'story_bible.json',bible)
    return ctx,clip,bible


def test_b7_registered_bag_identity_is_projected_without_a_prop_reference_image(tmp_path):
    ctx,clip,bible=case(tmp_path)
    before=copy.deepcopy(clip)
    contract=current_contract(ctx,clip,[])
    props=contract['prop_facts']
    assert [row['name'] for row in props]==['深灰双肩背包']
    assert props[0]['facts']['owner']=={'value':'知遥','source':'story_bible.json:props[0].owner'}
    assert props[0]['facts']['appearance']=={'value':bible['props'][0]['appearance'],
                                           'source':'story_bible.json:props[0].appearance'}
    assert props[0]['facts']['name']['source']=='story_bible.json:props[0].name'
    assert not {'count','unique','position','wearing'}.intersection(props[0]['facts'])
    assert contract['image_references']==[] and clip==before


def test_generation_and_image_review_receive_the_same_prop_facts(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.first_frames import check
    ctx,clip,_=case(tmp_path)
    contract=current_contract(ctx,clip,[])
    row={'image_prompt':clip['shots'][0]['opening_frame']['image_prompt'],
         'image_references':[],'visible_characters':['知遥']}
    prompt=request_prompt(ctx,row,contract)
    approved=json.loads(prompt.rsplit('\n',1)[1])
    captured=[]
    monkeypatch.setattr('novel_manga.llm.client.image_part',lambda *a:{'type':'text','text':'image boundary'})
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda parts,*a,**k:captured.append(parts) or {'observations':[],'findings':[]})
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings',lambda:None)
    check(ctx,tmp_path/'opening.png',row,contract)
    supplied=json.loads(captured[0][0]['text'].rsplit('\n',1)[1])
    assert approved['prop_facts']==supplied['opening']['prop_facts']==contract['prop_facts']
    assert 'ownership' in prompt and 'current wearing' in prompt and 'duplicate' in prompt


@pytest.mark.parametrize('field,value',[('owner','潮叔'),('appearance','red fabric')])
def test_relevant_prop_fact_changes_invalidate_review_freshness(tmp_path,field,value):
    ctx,clip,bible=case(tmp_path)
    contract=current_contract(ctx,clip,[])
    assert matches_current_contract(ctx,clip,contract)
    bible['props'][0][field]=value
    atomic_write_json(ctx.novel_dir/'story_bible.json',bible)
    assert not matches_current_contract(ctx,clip,contract)


def test_unrelated_future_and_global_wardrobe_props_do_not_change_this_contract(tmp_path):
    ctx,clip,bible=case(tmp_path)
    contract=current_contract(ctx,clip,[])
    bible['props'][1]['owner']='different future owner'
    bible['props'][2]['appearance']='different offscreen watch'
    atomic_write_json(ctx.novel_dir/'story_bible.json',bible)
    assert matches_current_contract(ctx,clip,contract)
    assert [row['name'] for row in contract['prop_facts']]==['深灰双肩背包']


def test_only_registered_identity_or_explicit_alias_can_supply_facts(tmp_path):
    ctx,clip,bible=case(tmp_path)
    shot=clip['shots'][0]
    shot.update(start_state='An ordinary grey sack floats.',props=['ordinary grey sack'],scene_objects=[])
    shot['opening_frame']['image_prompt']='An ordinary grey sack floats.'
    contract=current_contract(ctx,clip,[],shots=[shot])
    assert 'prop_facts' not in contract
    shot['props']=['旧包']
    assert current_contract(ctx,clip,[],shots=[shot])['prop_facts'][0]['name']=='深灰双肩背包'
    atomic_write_json(ctx.novel_dir/'story_bible.json',{'props':[]})
    assert 'prop_facts' not in current_contract(ctx,clip,[],shots=[shot])
