import json
from novel_manga.application.preparation import request_check


def test_identity_portrait_scope_reaches_checker_and_invalidates_old_result(tmp_path, monkeypatch):
    card=tmp_path/'portrait.jpeg';card.write_bytes(b'portrait')
    card.with_suffix('.jpeg.origin.json').write_text(json.dumps({'reference_scope':'identity_portrait'}))
    clip={'clip_id':'clip_01','prompt':'','references':[{'path':'portrait.jpeg','role':'character','name':'林野'}]}
    request={'prompt':'A person with equipment.','references':[str(card)],'reference_sha256':['same']}
    calls=[]
    monkeypatch.setattr(request_check,'image_part',lambda *a: {'type':'image'})
    def ask(parts,*a,**kw):
        calls.append(parts[-1]['text'])
        return {'observations':[],'findings':[]}
    monkeypatch.setattr(request_check,'ask_json',ask)
    request_check.cached_consistency(tmp_path,tmp_path/'work',clip,request)
    assert 'identity_portrait' in calls[0]
    assert '全身服饰' in calls[0]
    card.with_suffix('.jpeg.origin.json').write_text('{}')
    request_check.cached_consistency(tmp_path,tmp_path/'work',clip,request)
    assert len(calls)==2
