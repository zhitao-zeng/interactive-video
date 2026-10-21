"""A translation retry must distinguish extra array entries from real source shots."""
import pytest
from novel_manga.application.rendering import h3


@pytest.mark.parametrize('count',[1,2])
def test_shot_count_and_soundscape_contract_reaches_json_object_writer(monkeypatch,count):
    clip={'clip_id':'clip_11','request_seconds':count*4,'references':[],
          'prompt':'\n'.join(f'【阶段{i}】水面轻晃。结束时：水面平静。画面呈现' for i in range(1,count+1))}
    calls=[]
    def write(parts,schema,**kwargs):
        calls.append(parts[0]['text'])
        if len(calls)==1:
            return {'shots':['Water ripples.']*(count+1),'soundscape':'None.','conflicts':[]}
        return {'shots':['Water ripples.']*count,'soundscape':'None.','conflicts':[]}
    monkeypatch.setattr(h3,'ask_json',write)
    assert h3.convert(clip,tries=2)
    assert len(calls)==2
    assert f'exactly {count} item' in calls[0]
    assert 'top-level soundscape' in calls[0]
    assert f'received {count+1}' in calls[1]
    assert clip['prompt_h3'].count('[Shot ')==count


def test_extra_directions_are_rejected_instead_of_truncated(monkeypatch):
    clip={'clip_id':'clip_11','request_seconds':4,'references':[],
          'prompt':'【阶段1】水面轻晃。结束时：水面平静。画面呈现'}
    monkeypatch.setattr(h3,'ask_json',lambda *a,**kw:{'shots':['Water ripples.','A new action.'],'soundscape':'None.','conflicts':[]})
    errors=[]
    assert h3.convert(clip,tries=1,problems=errors) is False
    assert not clip.get('prompt_h3')
    assert 'received 2' in errors[0]
