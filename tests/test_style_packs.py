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


def test_style_examples_reach_material_edition_and_agent_novel_dir(library,tmp_path):
    def authored():
        return {'title':'fixture','logline':'A visitor waits.','chapter_script':{'shots':[
            {'index':1,'scene_id':'room','location':'门厅','characters':['访客'],'in_frame':['访客'],
             'purpose':'观察门','start_state':'站在门旁','event':'访客抬头看门','end_state':'停住',
             'camera':'固定近景','shot_scale':'近景','duration_seconds':6,'sfx':'鞋底停下的摩擦声',
             'opening_frame':{'image_prompt':'An adult stands beside a closed door.','visible_characters':['访客']},
             'turns':[{'speaker_name':'访客','text':'门刚才动了吗？','delivery_mode':'visible_dialogue',
                       'emotion':'The question ends quietly as the visitor notices the door.'}]},
            {'index':2,'scene_id':'room','location':'门厅','characters':['访客'],'in_frame':['访客'],
             'purpose':'等待回应','start_state':'停住','event':'访客安静看着门','end_state':'仍未开门',
             'camera':'固定近景','shot_scale':'近景','duration_seconds':6,'sfx':'远处风声','turns':[],
             'opening_frame':{'image_prompt':'The same adult watches the closed door.','visible_characters':['访客']}}]},
            'clips':[{'shot_indexes':[1],'request_seconds':6},{'shot_indexes':[2],'request_seconds':6}],
            'interaction':{'question':'下一步？','selected':None,'options':[
                {'id':'A','label':'等候','reason':'听回应','next_step':'继续等待'},
                {'id':'B','label':'离开','reason':'出口在身后','next_step':'离开门厅'}]},
            'spatial_plan':{'scenes':[{'scene_id':'room','anchors':['门'],'placed':['访客'],
                                      'assumptions':[],'shot_indexes':[1,2]}]},
            'production_design':{'palette':'纸白、靛青','characters':[{'name':'访客','appearance':'成年，短黑发',
                'wardrobe':'靛青外套'}],'locations':[{'name':'门厅','description':'一扇关闭木门，左侧有窗'}],
                'props':[],'continuity_rules':['门保持关闭']}}
    from novel_manga.application.agents.episode.context import create_context
    from novel_manga.application.agents.episode.inputs import prepare_inputs
    from novel_manga.interactive import creative_material
    from novel_manga.interactive.authored import pack_authored,prepare_authored
    root,_=library
    task=tmp_path/'task.json'
    write(task,{'mode':'original','project':'romance','brief':'两个成年人交换一封信。',
        'style':'test-look','h3_input_mode':'first-frame','target_seconds':[12,20]})
    run=tmp_path/'creator'
    manifest=creation.prepare(task,run)
    material=creative_material.write_material(authored(),manifest,tmp_path/'material',
        style_source=run/'sandbox/input/style.json')
    frozen=json.loads((material/'style.json').read_text())
    relative=frozen['examples'][0]['path']
    expected=(root/'assets/example.png').read_bytes()
    assert (material/relative).read_bytes()==expected

    def snapshot():
        files=[material/'style.json',*sorted(path for path in (material/'style-examples').rglob('*') if path.is_file())]
        return {str(path.relative_to(material)):(path.read_bytes(),path.stat().st_mtime_ns) for path in files}
    import os
    os.utime(material/'style.json',(1,1))
    before=snapshot()
    creative_material.write_material(authored(),manifest,material,style_source=run/'sandbox/input/style.json')
    assert snapshot()==before
    source_image=run/'sandbox/input/style-examples/test-look-01.png'
    Image.new('RGB',(96,64),'red').save(source_image)
    with pytest.raises(ValueError,match='new edition'):
        creative_material.write_material(authored(),manifest,material,style_source=run/'sandbox/input/style.json')
    assert snapshot()==before
    source_image.write_bytes(expected)
    extra=material/'style-examples/unexpected.json';extra.write_text('{}')
    with pytest.raises(ValueError,match='new edition'):
        creative_material.write_material(authored(),manifest,material,style_source=run/'sandbox/input/style.json')
    extra.unlink()

    episode=prepare_authored(material,tmp_path/'edition',family='romance')
    book=episode.parent
    assert (book/relative).read_bytes()==expected
    pack_authored(episode)
    ctx=create_context(book,1,output_root=tmp_path/'agent-output',input_mode='script')
    prepare_inputs(ctx)
    assert (ctx.novel_dir/relative).read_bytes()==expected
    assert json.loads((ctx.novel_dir/'style.json').read_text())['examples'][0]['path']==relative


def test_export_style_resolves_input_and_root_snapshot_layouts(library,tmp_path):
    from novel_manga.interactive.creative_contract import export_style
    root,_=library
    task=tmp_path/'task.json'
    write(task,{'mode':'original','project':'romance','brief':'交换一封信。','style':'test-look'})
    run=tmp_path/'creator';creation.prepare(task,run)
    first=tmp_path/'first';second=tmp_path/'second'
    export_style(run/'sandbox/input/style.json',first)
    export_style(first/'style.json',second)
    assert json.loads((first/'style.json').read_text())==json.loads((second/'style.json').read_text())
    relative=json.loads((second/'style.json').read_text())['examples'][0]['path']
    assert (second/relative).read_bytes()==(root/'assets/example.png').read_bytes()


def test_profile_only_edition_does_not_expand_global_style_examples(library,tmp_path,monkeypatch):
    from support.first_frame_cases import case
    from novel_manga.media.first_frames import compile_opening
    ctx,clips=case(tmp_path,monkeypatch);clip=clips[0]
    write(ctx.novel_dir/'profile.json',{'style':'test-look','h3_input_mode':'first-frame'})
    assert not (ctx.novel_dir/'style.json').exists()
    compiled=compile_opening(ctx,clip,{'clip_id':'clip_01'})
    assert [ref.path for ref in compiled.references]==['scene.png']
    assert all(ref.role!='style-reference' for ref in compiled.references)
