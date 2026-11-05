"""The full entry preserves work and never spends media after a failed author."""
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.interactive import film,creation,production


def task(tmp_path):
    path=tmp_path/'request.json'
    atomic_write_json(path,{'mode':'original','project':'suspense','brief':'Original test','target_seconds':[45,60]})
    return path


def test_failed_creation_stops_before_media_and_freezes_input(tmp_path,monkeypatch):
    source=task(tmp_path);calls=[]
    def create(path,directory,**kwargs):
        calls.append(read_json(path))
        return {'status':'time_budget_used'}
    monkeypatch.setattr(creation,'run',create)
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:pytest.fail('media must not start'))
    result=film.run(tmp_path,source,tmp_path/'run','new-film')
    assert result['status']=='blocked' and not(tmp_path/'outputs/new-film').exists()
    assert calls[0]['production_handoff'] is True
    assert read_json(source).get('production_handoff') is None
    assert film.run(tmp_path,source,tmp_path/'run','new-film')==result
    assert len(calls)==1


def test_existing_edition_is_refused_before_calling_author(tmp_path,monkeypatch):
    source=task(tmp_path);(tmp_path/'outputs/old').mkdir(parents=True)
    monkeypatch.setattr(creation,'run',lambda *a,**k:pytest.fail('existing edition must be preserved'))
    with pytest.raises(ValueError,match='Existing edition'):
        film.run(tmp_path,source,tmp_path/'run','old')


def test_unsupported_world_card_is_rejected_before_spending_author_budget(tmp_path,monkeypatch):
    source=task(tmp_path)
    data=read_json(source);data.update(world_card_seconds=3,world_card_text='Required world fact')
    atomic_write_json(source,data)
    monkeypatch.setattr(creation,'run',lambda *a,**k:pytest.fail('do not accept a card that cannot be delivered'))
    with pytest.raises(ValueError,match='world.card'):
        film.run(tmp_path,source,tmp_path/'run','new')
