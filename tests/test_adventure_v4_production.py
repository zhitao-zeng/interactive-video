from pathlib import Path
import copy,json
import pytest
from PIL import Image

from novel_manga.interactive.preparation import prepare_episode
from novel_manga.interactive.production import pack

ROOT=Path(__file__).resolve().parents[1]
REVISION=ROOT/'projects/adventure/revisions/2026-10-13-platform-rewrite'


def prepared(tmp_path):
    project=tmp_path/'project';project.mkdir()
    authored=json.loads((REVISION/'episode01.json').read_text())
    authored['profile']={'resolution':'480p','camera_policy':'authored'}
    (project/'episode01.json').write_text(json.dumps(authored,ensure_ascii=False))
    (project/'bible.json').write_text((REVISION/'bible.json').read_text())
    package=tmp_path/'package';(package/'角色资产').mkdir(parents=True)
    Image.new('RGB',(32,32),'grey').save(package/'角色资产/林野-身份基准-v1.0.png')
    original=package/authored['source']
    original.parent.mkdir(parents=True,exist_ok=True)
    original.write_text('原版：地图上不存在的村庄。')
    output=tmp_path/'adventure-v4'
    ep=prepare_episode(project,output,package)
    return ep,authored


def test_authored_performance_and_scenes_survive_preparation(tmp_path):
    ep,authored=prepared(tmp_path)
    script=json.loads((ep/'chapter_script.json').read_text())
    for shot,source in zip(script['shots'],authored['shots']):
        assert shot['scene_id']==source['scene_id']
        assert shot['location']==source['location']
        if source['dialogue']:
            assert source['voice_delivery'] in shot['turns'][0]['emotion']
            assert '成年男性中低音' in shot['turns'][0]['emotion']
            assert shot['turns'][0]['text']==source['dialogue']
    assert script['profile']['resolution']=='480p'
    assert script['profile']['camera_policy']=='authored'


def test_live_action_and_authored_camera_survive_pack_and_repack(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(ROOT));monkeypatch.setenv('NOVEL_CLIP_SECONDS_MAX','15')
    ep,authored=prepared(tmp_path)
    for attempt in range(2):
        plan=pack(ep)
        assert sum(c['request_seconds'] for c in plan['clips'])==177
        assert plan['limits']['camera_policy']=='authored'
        for clip in plan['clips']:
            assert '中国国漫' not in clip['prompt']
            assert '现代真人探险片段' in clip['prompt']
            assert '阶段内机位固定不运镜' not in clip['prompt']
        assert ''.join(c['spoken_text'] for c in plan['clips'])==''.join(s['dialogue'] for s in authored['shots'])


def test_legacy_episode_keeps_default_resolution_and_voice(tmp_path):
    ep,authored=prepared(tmp_path)
    project=tmp_path/'project'
    authored.pop('profile')
    for s in authored['shots']:s.pop('voice_delivery');s.pop('scene_id')
    (project/'episode01.json').write_text(json.dumps(authored,ensure_ascii=False))
    ep=prepare_episode(project,ep.parent,tmp_path/'package')
    script=json.loads((ep/'chapter_script.json').read_text())
    assert script['profile']['resolution']=='720p'
    assert script['shots'][0]['scene_id']==authored['shots'][0]['location']
    assert script['shots'][0]['turns'][0]['emotion']=='镇定自然，成年男性中低音，轻微呼吸感'


def test_rendered_episode_cannot_be_reprepared(tmp_path):
    ep,_=prepared(tmp_path)
    (ep/'thin_media_report.json').write_text('{"status":"candidate","generated":3}')
    old=(ep/'chapter_script.json').read_bytes()
    with pytest.raises(FileExistsError):prepare_episode(tmp_path/'project',ep.parent,tmp_path/'package')
    assert (ep/'chapter_script.json').read_bytes()==old


def test_revision_preparation_reuses_assets_and_never_overwrites_history(tmp_path,monkeypatch):
    from novel_manga.interactive.preparation import prepare_revision
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(ROOT));monkeypatch.setenv('NOVEL_CLIP_SECONDS_MAX','15')
    ep,authored=prepared(tmp_path)
    asset_source=ep.parent
    sentinel=asset_source/'series_assets/characters/character_001/turnaround.jpeg.asset.json'
    sentinel.write_text('{"asset_id":"existing","group_id":"group","sha256":"cached"}')
    old=tmp_path/'old-edition';old.mkdir();(old/'episode_execution.json').write_text('{"generated":23}')
    output=tmp_path/'adventure-master-v4'
    prepared_ep=prepare_revision(tmp_path/'project',output,tmp_path/'package',asset_source)
    assert (output/'series_assets/characters/character_001/turnaround.jpeg').read_bytes()==(asset_source/'series_assets/characters/character_001/turnaround.jpeg').read_bytes()
    assert (output/'series_assets/characters/character_001/turnaround.jpeg.asset.json').read_bytes()==sentinel.read_bytes()
    assert json.loads((prepared_ep/'clip_plan.json').read_text())['totals']
    assert (old/'episode_execution.json').read_text()=='{"generated":23}'
    (prepared_ep/'thin_media_report.json').write_text('{"generated":1}')
    with pytest.raises(FileExistsError):prepare_revision(tmp_path/'project',output,tmp_path/'package',asset_source)
    assert (prepared_ep/'thin_media_report.json').read_text()=='{"generated":1}'
