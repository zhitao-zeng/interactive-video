from pathlib import Path
from PIL import Image
import pytest
from novel_manga.application.repair import reference_views


def test_selected_view_uses_existing_pixels_and_keeps_original(tmp_path,monkeypatch):
    source=tmp_path/'two-views.png';target=tmp_path/'selected.jpeg'
    image=Image.new('RGB',(100,200),'blue');image.paste(Image.new('RGB',(100,100),'red'),(0,0));image.save(source)
    before=source.read_bytes()
    monkeypatch.setattr(reference_views.client,'ask_json',lambda *a,**k:({'multiple_views':True,'bounds':[0,0,1,.5],'reason':'daytime upper view'} if k['name']=='repair_reference_view' else {'multiple_views':False,'bounds':[0,0,1,1],'reason':'one view'}))
    origin=reference_views.select_view(source,target,'daytime scene')
    assert source.read_bytes()==before
    assert origin['source_rectangle']==[0,0,100,100] and origin['redrawn'] is False
    with Image.open(target) as selected:
        assert selected.size==(100,100)
        red,green,blue=selected.getpixel((50,50));assert red>240 and blue<15


def test_single_view_is_reused_without_new_image(tmp_path,monkeypatch):
    monkeypatch.setattr(reference_views.client,'ask_json',lambda *a,**k:{'multiple_views':False,'bounds':[0,0,1,1],'reason':'one view'})
    monkeypatch.setattr(reference_views.client,'image_part',lambda *a:{})
    assert reference_views.select_view(tmp_path/'input.jpeg',tmp_path/'new.jpeg','scene') is None
    assert not (tmp_path/'new.jpeg').exists()


def test_invalid_model_region_is_not_published(tmp_path,monkeypatch):
    monkeypatch.setattr(reference_views.client,'ask_json',lambda *a,**k:{'multiple_views':True,'bounds':[.8,0,.2,1],'reason':'bad bounds'})
    monkeypatch.setattr(reference_views.client,'image_part',lambda *a:{})
    with pytest.raises(ValueError):reference_views.select_view(tmp_path/'input.jpeg',tmp_path/'new.jpeg','scene')
    assert not (tmp_path/'new.jpeg').exists()
