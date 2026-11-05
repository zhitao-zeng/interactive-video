import copy
import json

import pytest

from novel_manga.application.repair import agent


def source_case(tmp_path):
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    script={'shots':[{'index':1,'props':['key'],'turns':[{'text':'Keep these words'}]},
                     {'index':2,'props':[],'turns':[]}]}
    (directory/'chapter_script.json').write_text(json.dumps(script))
    (directory.parent/'story_bible.json').write_text(json.dumps({
        'novel_title':'book','genre':'generic','visual_style':'photo','palette':'natural',
        'style_fingerprint':'same','props':[{'name':'key'},{'name':'monitor'}]}))
    clip={'clip_id':'a','kind':'video','shot_indexes':[1],'request_seconds':5,'prompt':'source direction','prompt_h3':'saved',
          'shot_timing':[{'seconds':5}],'lines':[{'text':'Keep these words'}]}
    plan={'clips':[clip,{'clip_id':'b','kind':'video','shot_indexes':[2],'prompt_h3':'unrelated'}]}
    return directory,script,clip,plan


@pytest.mark.parametrize('patch,error',[
    ({'index':2,'props':['monitor']},'outside'),
    ({'index':1,'turns':[{'speaker_name':'a','delivery_mode':'visible_dialogue','text':'new'}]},'preserves'),
    ({'index':1,'props':['unregistered']},'registered'),
])
def test_source_binding_repair_rejects_wrong_scope_speech_and_unknown_asset(tmp_path,monkeypatch,patch,error):
    directory,script,clip,plan=source_case(tmp_path)
    monkeypatch.setattr(agent,'request_lane',lambda c:'h3')
    with pytest.raises(ValueError,match=error):
        agent.source_proposal(directory,plan,clip,{'reason':'source mismatch','source_patch':{'shots':[patch]}})
    assert json.loads((directory/'chapter_script.json').read_text())==script


def test_registered_source_binding_candidate_preserves_media_speech_and_other_clips(tmp_path,monkeypatch):
    directory,script,clip,plan=source_case(tmp_path)
    seen={}
    monkeypatch.setattr(agent,'request_lane',lambda c:'h3')
    def rebuild(d,bible,candidate,before,ids,**kw):
        seen['script']=candidate;assert ids=={'a'} and kw['preserve_timing']
        result=copy.deepcopy(before);result['clips'][0]['references']=[{'role':'prop','name':'monitor','path':'monitor.jpg'}]
        return result,['a']
    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips',rebuild)
    monkeypatch.setattr('novel_manga.application.preparation.readiness.plan_issues',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',lambda c,**kw:c.update(prompt_h3='translated'))
    monkeypatch.setattr(agent,'request_issues',lambda c:[])
    proposal=agent.source_proposal(directory,plan,clip,{'action':'input','reason':'source mismatch',
        'evidence':['source'],'source_patch':{'shots':[{'index':1,'props':['monitor']}]}})
    assert proposal.script['shots'][0]['props']==['monitor']
    assert proposal.script['shots'][0]['turns']==script['shots'][0]['turns']
    assert proposal.plan['clips'][1]==plan['clips'][1]
    assert proposal.plan['clips'][0]['repair_take']==1
    assert json.loads((directory/'chapter_script.json').read_text())==script
    assert clip['prompt_h3']=='saved'


def test_offered_detail_view_replaces_only_that_location_reference(tmp_path,monkeypatch):
    directory,script,clip,plan=source_case(tmp_path)
    clip['references']=[{'role':'location','name':'hall','path':'original.jpg'}]
    image=directory.parent/'series_assets/detail.jpg';image.parent.mkdir();image.write_bytes(b'reference fixture')
    choice={'name':'hall','asset_id':'existing-sign','source':str(image),'scope':'existing sign upper panel'}
    (directory/'episode_review.json').write_text(json.dumps({'clips':{'a':{'confirmed':{'reference_candidates':[choice]}}}}))
    monkeypatch.setattr(agent,'request_lane',lambda c:'h3')
    monkeypatch.setattr('novel_manga.application.preparation.readiness.plan_issues',lambda *a:{})
    def translate(c,**kw):
        assert 'existing sign upper panel' in kw['note']
        c.update(prompt_h3='translated')
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',translate)
    monkeypatch.setattr(agent,'request_issues',lambda c:[])
    proposal=agent.source_proposal(directory,plan,clip,{'action':'input','reason':'native sign needs supplied glyphs',
        'source_patch':{'locations':{'hall':{'reuse_asset_id':'existing-sign'}}}})
    assert proposal.script==script
    assert proposal.plan['clips'][0]['references'][0]['path']=='series_assets/detail.jpg'
    assert proposal.plan['clips'][1]==plan['clips'][1]
    assert clip['references'][0]['path']=='original.jpg'
    with pytest.raises(ValueError,match='supplied reference candidate'):
        agent.source_proposal(directory,plan,clip,{'reason':'bad','source_patch':{'locations':{'hall':{'reuse_asset_id':'invented'}}}})
