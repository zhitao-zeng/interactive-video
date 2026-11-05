from pathlib import Path
from PIL import Image

from novel_manga.application.packing.assets import build_references
from novel_manga.config import Settings
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.models.bible import StoryBible
from novel_manga.providers.base import ImageResult


def test_repeated_place_uses_the_same_number_as_the_actual_asset_factory(tmp_path):
    bible=StoryBible(novel_title='test',genre='generic',visual_style='2d',palette='blue',
        style_fingerprint='test',locations=['海边：浅水和台阶','灯塔：石墙','海边：浅水和台阶'])
    refs,_,_=build_references([],'海边',bible,{'海边':'海边：浅水和台阶','灯塔':'灯塔：石墙'})
    ids={r['asset_id'] for r in refs if r['role']=='location'}
    calls=[]
    class Provider:
        def create_image(self,prompt,output,**kwargs):
            calls.append(str(output));output.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(20,20),'blue').save(output)
            return ImageResult(path=output)
    factory=FramedAssetFactory(Settings(reuse_existing_assets=True),Provider())
    factory.build_selected(tmp_path/'series_assets',bible,set(),ids,expressions=False)
    assert ids=={'location_001'}
    assert all((tmp_path/ref['path']).is_file() for ref in refs)
    factory.build_selected(tmp_path/'series_assets',bible,set(),ids,expressions=False)
    assert len(calls)==1
