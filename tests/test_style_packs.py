"""Selected art direction must reach the author and assets without changing old runs."""
import json
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.application import profiles
from novel_manga.interactive import creation
from novel_manga.media.asset_style import AssetStyle


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    image = root / 'assets/example.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (96, 64), 'teal').save(image)
    write(root / 'assets/review.json', {'status': 'reviewed', 'limits': '静帧，不是视频验收'})
    pack = {'name': '测试画风', 'pack_version': '1', 'render_family': 'photo',
            'visual_style': '真实材质', 'render_direction': '自然皮肤',
            'scene_direction': '自然窗光',
            'art_direction': {'intent': '让信件成为视觉中心',
                'palette': {'base': ['米白'], 'accent': ['深青'], 'use': '强调色留给当前线索'},
                'lighting': ['光源服从当前场景时刻'],
                'composition': ['关键物件前后留空'],
                'asset_guidance': {'character': ['衣领轮廓清楚'],
                                   'location': ['入口方向清楚'], 'prop': ['纸张折痕可辨认']}},
            'examples': [{'kind': 'original_style_board', 'path': 'assets/example.png',
                          'scope': 'style_only_not_character_identity', 'status': 'reviewed',
                          'review': 'assets/review.json'}]}
    write(root / 'configs/styles/test-look.json', pack)
    monkeypatch.setattr(profiles, 'STYLES_DIR', root / 'configs/styles')
    return root, pack


def test_asset_kind_receives_its_own_art_direction_and_old_style_stays_identical(library):
    _, pack = library
    style = AssetStyle.for_genre({}, style=pack)
    assert '衣领轮廓清楚' in style.render_direction
    assert '入口方向清楚' in style.scene_direction
    assert '纸张折痕可辨认' in style.prop_direction
    assert '衣领轮廓清楚' not in style.scene_direction
    assert '衣领轮廓清楚' not in style.prop_direction
    assert '强调色留给当前线索' in style.scene_direction
    legacy = AssetStyle.for_genre({}, style={'render_direction': '柔和光', 'scene_direction': '旧木'})
    assert legacy.render_direction == '柔和光' and legacy.scene_direction == '旧木'


def test_creator_freezes_only_selected_pack_and_original_example(library, tmp_path):
    root, pack = library
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'project': 'romance', 'brief': '两个成年人交换一封信。',
                 'style': 'test-look', 'target_seconds': [20, 30]})
    run = tmp_path / 'run'
    first = creation.prepare(task, run)
    work = run / 'sandbox'
    assert first['selected_style']['id'] == 'test-look'
    assert 'input/style.json' in first['required_reads']
    frozen = json.loads((work / 'input/style.json').read_text())
    assert frozen['art_direction'] == pack['art_direction']
    sample = next(r for r in first['asset_catalog'] if r['role'] == 'style-reference')
    assert sample['reference_scope'] == 'style_only_not_character_identity'
    assert (work / sample['path']).read_bytes() == (root / 'assets/example.png').read_bytes()
    assert sample['path'] in first['required_reads']
    assert sample['path'] not in first['source_documents']
    assert len([r for r in first['asset_catalog'] if r['role'] == 'style-reference']) == 1
    before = (work / 'input/style.json').read_bytes()
    pack['pack_version'] = '2'
    pack['art_direction']['intent'] = '新项目的另一套设计'
    write(root / 'configs/styles/test-look.json', pack)
    assert creation.prepare(task, run) == first
    assert (work / 'input/style.json').read_bytes() == before
    later = creation.prepare(task, tmp_path / 'later')
    assert later['selected_style']['version'] == '2'


def test_explicit_unknown_style_is_not_silently_replaced(library, tmp_path):
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '交换一封信。', 'style': 'misspelled'})
    with pytest.raises(ValueError, match='style'):
        creation.prepare(task, tmp_path / 'run')


def test_missing_selected_example_is_reported_before_author_session(library, tmp_path):
    root, _ = library
    (root / 'assets/example.png').unlink()
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '交换一封信。', 'style': 'test-look'})
    with pytest.raises(FileNotFoundError, match='example'):
        creation.prepare(task, tmp_path / 'run')


def test_draft_pack_without_generated_image_remains_explicitly_unverified(library, tmp_path):
    root, pack = library
    pack['examples'][0]['status'] = 'blocked_quota'
    pack['validation'] = {'image': 'blocked_quota', 'video': 'not_tested'}
    write(root / 'configs/styles/test-look.json', pack)
    (root / 'assets/example.png').unlink()
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '交换一封信。', 'style': 'test-look'})
    manifest = creation.prepare(task, tmp_path / 'run')
    assert not [r for r in manifest['asset_catalog'] if r['role'] == 'style-reference']
    frozen = json.loads((tmp_path / 'run/sandbox/input/style.json').read_text())
    assert frozen['validation']['image'] == 'blocked_quota'
    assert frozen['examples'][0]['path'] is None
    assert frozen['examples'][0]['status'] == 'blocked_quota'


def test_historical_example_is_copied_as_style_only_not_a_story_source(library, tmp_path):
    root, pack = library
    pack['examples'][0]['kind'] = 'historical_style_frame'
    pack['examples'][0]['note'] = '只核对历史画风，未验证本版新规则'
    write(root / 'configs/styles/test-look.json', pack)
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '新故事。', 'style': 'test-look'})
    manifest = creation.prepare(task, tmp_path / 'run')
    assert len(manifest['asset_catalog']) == 1
    image = manifest['asset_catalog'][0]
    assert image['role'] == 'style-reference'
    assert image['reference_scope'] == 'style_only_not_character_identity'
    assert image['path'] not in manifest['source_documents']


@pytest.mark.parametrize('status', ['needs_revision', 'pending', 'blocked_quota'])
def test_unready_board_is_kept_as_evidence_but_not_attached_to_new_author(library, tmp_path, status):
    root, pack = library
    pack['examples'][0]['status'] = status
    write(root / 'configs/styles/test-look.json', pack)
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '新故事。', 'style': 'test-look'})
    manifest = creation.prepare(task, tmp_path / 'run')
    assert manifest['asset_catalog'] == []
    frozen = json.loads((tmp_path / 'run/sandbox/input/style.json').read_text())
    assert frozen['examples'][0]['status'] == status
    assert frozen['examples'][0]['path'] is None
    assert (root / 'assets/example.png').is_file()
    assert (tmp_path / 'run/sandbox' / frozen['examples'][0]['review']).is_file()


@pytest.mark.parametrize('kind', ['original_style_board', 'historical_style_frame'])
def test_style_and_example_export_without_creative_notes(library, tmp_path, kind):
    from novel_manga.interactive.creative_contract import export

    root, pack = library
    pack['examples'][0]['kind'] = kind
    write(root / 'configs/styles/test-look.json', pack)
    task = tmp_path / 'task.json'
    write(task, {'mode': 'original', 'brief': '交换一封信。', 'style': 'test-look'})
    run = tmp_path / 'run'
    manifest = creation.prepare(task, run)
    draft = {'title': '交换信件', 'chapter_script': {'shots': [
        {'index': 1, 'duration_seconds': 10, 'location': '柜台', 'purpose': '等待回应',
         'start_state': '信件尚未接收', 'event': '递信后停手', 'end_state': '等待',
         'camera': '双人中景', 'light': '原场景窗光', 'visual_prompt': '信件前方留空', 'turns': []}]},
        'interaction': {'question': '下一步？', 'options': [
            {'id': 'A', 'label': '等回应', 'reason': '信还没被接走'},
            {'id': 'B', 'label': '解释来意', 'reason': '对方尚不知道来意'}]},
        'spatial_plan': {'scenes': []}, 'clips': [], 'self_review': {}}
    destination = tmp_path / 'delivery'
    export(draft, manifest, destination, style_source=run / 'sandbox/input/style.json')
    frozen = json.loads((destination / 'style.json').read_text())
    image = destination / frozen['examples'][0]['path']
    assert image.is_file()
    assert frozen['pack_version'] == '1'
    assert json.loads((destination / frozen['examples'][0]['review']).read_text())['status'] == 'reviewed'
    assert '信件前方留空' in (destination / '分镜.md').read_text()
    assert '原场景窗光' in (destination / '分镜.md').read_text()
