import copy
from types import SimpleNamespace
import pytest
from novel_manga.application.agents.episode import input_request_recovery, input_repair, contracts
from novel_manga.util import atomic_write_json, read_json


def saved_candidate(tmp_path):
    ctx=SimpleNamespace(episode_dir=tmp_path)
    decision={'clip_id':'clip_04','action':'input','clip_ids':['clip_04','clip_14'],'shots':[{'index':45,'event':'source action'}]}
    record={'clip_id':'clip_04','status':'incomplete','step':'check:clip_14','error':'wrong pronoun',
            'source':{'consistent':True,'problems':[]},'decision':decision,'resume_count':1,'revision_count':1}
    receipt=tmp_path/'work/input_repairs/clip_04-3.json'
    atomic_write_json(receipt,record)
    atomic_write_json(receipt.with_suffix('')/'requests.json',{'clip_14':{'prompt_h3':'old English','prompt_h3_of':'same source stamp'}})
    atomic_write_json(tmp_path/'clip_plan.json',{'clips':[{'clip_id':'clip_14','prompt':'original source'}]})
    row={'clip_id':'clip_14','action':'edit','input_clip_id':'clip_04','reason':'fix derived pronoun','prompt_h3':'new English'}
    return ctx,receipt,record,row


def test_check_is_read_only_and_apply_continues_exact_accepted_source(tmp_path,monkeypatch):
    ctx,receipt,record,row=saved_candidate(tmp_path)
    monkeypatch.setattr(contracts,'validate_patch',lambda clip,prompt:None)
    calls=[]
    def repair(context,decision,**kwargs):
        calls.append((decision,kwargs))
        assert read_json(receipt.with_suffix('')/'requests.json')['clip_14']=={'prompt_h3':'new English','prompt_h3_of':'same source stamp'}
        return {'changed':['clip_14'],'blocked':{},'input_repaired':['clip_04']}
    monkeypatch.setattr(input_repair,'repair',repair)
    before=receipt.read_bytes()
    assert input_request_recovery.edit(ctx,row,check_only=True)['valid']
    assert receipt.read_bytes()==before and not calls
    assert input_request_recovery.edit(ctx,row)['ok']
    decision,kwargs=calls[0]
    assert decision==record['decision']
    continued=kwargs['request_receipt'][1]
    assert continued['source']==record['source']
    assert continued['resume_count']==continued['revision_count']==1
    assert continued['request_edits'][0]['previous_error']=='wrong pronoun'
    assert read_json(receipt.with_suffix('')/'requests.before-edit-001.json')['clip_14']['prompt_h3']=='old English'


@pytest.mark.parametrize('change',[{'source':{'consistent':False}},{'step':'source'},{'published':True}])
def test_rejected_or_published_source_cannot_get_request_continuation(tmp_path,change):
    ctx,receipt,record,row=saved_candidate(tmp_path)
    atomic_write_json(receipt,{**record,**change})
    before=receipt.read_bytes()
    with pytest.raises(ValueError,match='unfinished source-approved'):
        input_request_recovery.edit(ctx,row)
    assert receipt.read_bytes()==before


def test_source_fields_cannot_be_smuggled_in_english_edit(tmp_path):
    ctx,receipt,record,row=saved_candidate(tmp_path)
    row['shots']=[{'index':45,'event':'new source'}]
    with pytest.raises(ValueError,match='one English edit'):
        input_request_recovery.edit(ctx,row)
