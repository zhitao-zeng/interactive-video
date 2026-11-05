"""Build an offline catalogue of the upgraded packs and their actual local examples."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
STATUS = {'reviewed': '静帧已审可用', 'needs_revision': '已生成，细节待重制',
          'blocked_quota': 'API额度不足，待补新样图', 'pending': '待生成与审图'}


def escaped(value):
    return html.escape(str(value), quote=True)


def lines(title, values):
    if not values:
        return ''
    return '<h3>' + escaped(title) + '</h3><ul>' + ''.join(
        '<li>' + escaped(value) + '</li>' for value in values) + '</ul>'


def build(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'owner').write_text('Style library / offline catalogue / no media generation.\n')
    cards = []
    inventory = []
    for source in sorted((ROOT / 'configs/styles').glob('*.json')):
        pack = json.loads(source.read_text())
        art = pack.get('art_direction')
        if not art:
            continue
        key = source.stem
        images = []
        for example in pack.get('examples', []):
            local = ROOT / (example.get('path') or '')
            if not local.is_file():
                continue
            relative = Path(key) / local.name
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, target)
            example['path'] = relative.as_posix()
            historical = example.get('kind') == 'historical_style_frame'
            caption = ('旧片例图 · ' + example.get('timecode', '') + ' · 不代表新版验证'
                       if historical else STATUS.get(example.get('status'), '状态待确认'))
            images.append('<figure><a href="' + escaped(relative.as_posix()) + '"><img src="'
                          + escaped(relative.as_posix()) + '" alt="' + escaped(pack['name'])
                          + '" loading="lazy"></a><figcaption>' + escaped(caption) + '</figcaption></figure>')
            # Review evidence stays local and is included without private provider receipts.
            for field in ('review', 'evidence'):
                evidence = ROOT / (example.get(field) or '')
                if evidence.is_file():
                    shutil.copy2(evidence, destination / key / evidence.name)
                    example[field] = (Path(key) / evidence.name).as_posix()
        (destination / key).mkdir(exist_ok=True)
        (destination / key / 'style.json').write_text(json.dumps(pack, ensure_ascii=False, indent=2) + '\n')
        validation = pack.get('validation', {})
        sample = validation.get('video_sample') or {}
        video_label = '尚未做视频小样验证'
        video_html = ''
        if sample.get('path'):
            video_source = ROOT / sample['path']
            if video_source.is_file():
                relative = Path(key) / 'h3-sample.mp4'
                shutil.copy2(video_source, destination / relative)
                sample['path'] = relative.as_posix()
                video_label = '5秒H3小样已取样审阅；整集未验'
                video_html = ('<figure><video controls playsinline preload="metadata" style="width:100%" src="'
                              + escaped(relative.as_posix()) + '"></video><figcaption>'
                              + escaped(sample.get('scope', '单种子短小样，非整集或声音验收'))
                              + '</figcaption></figure>')
                if sample.get('review'):
                    evidence = ROOT / sample['review']
                    if evidence.is_file():
                        shutil.copy2(evidence, destination / key / evidence.name)
                        sample['review'] = (Path(key) / evidence.name).as_posix()
                # Include the portable sample paths in the bundled configuration.
                (destination / key / 'style.json').write_text(json.dumps(pack, ensure_ascii=False, indent=2) + '\n')
        palette = art.get('palette', {})
        status = STATUS.get(validation.get('image'), '状态待确认')
        detail = lines('适合', art.get('suitable_for', []))
        detail += lines('色板', [*palette.get('base', []), *palette.get('accent', []), palette.get('use', '')])
        for title, field in [('光线', 'lighting'), ('构图', 'composition'), ('视觉母题', 'motifs'), ('避免', 'pitfalls')]:
            detail += lines(title, art.get(field, []))
        for kind, title in [('character', '人物'), ('location', '地点'), ('prop', '道具')]:
            detail += lines(title, art.get('asset_guidance', {}).get(kind, []))
        refs = ''.join('<li><a href="' + escaped(ref['url']) + '">' + escaped(ref['title'])
                       + '</a>：' + escaped(ref.get('lesson', '')) + '<br><small>'
                       + escaped(ref.get('observed_method', '')) + ' '
                       + escaped(ref.get('limits', '')) + '</small></li>' for ref in art.get('references', []))
        detail += '<h3>看片依据与转用边界</h3><ul>' + refs + '</ul>'
        detail += lines('验证范围', validation.get('notes', []))
        cards.append('<article id="' + escaped(key) + '"><h2>' + escaped(pack['name'])
                     + '</h2><p><code>' + escaped(key) + '</code> · ' + escaped(pack.get('pack_version', ''))
                     + '</p><p>' + escaped(art.get('intent', '')) + '</p><p class="status">'
                     + escaped(status) + ' · ' + escaped(video_label) + '</p><div class="images">'
                     + (''.join(images) or '<p class="empty">本包暂无样图，文字规则已备。</p>')
                     + '</div>' + video_html + '<details><summary>展开美术规则与依据</summary>' + detail
                     + '</details><p><a href="' + escaped(key) + '/style.json">完整画风包</a></p></article>')
        inventory.append({'id': key, 'name': pack['name'], 'version': pack.get('pack_version'),
                          'validation': validation, 'examples': pack.get('examples', [])})
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>互动短剧画风册</title><style>
body{margin:0;background:#171b20;color:#eee9df;font:16px/1.7 system-ui,sans-serif}main{max-width:1300px;margin:auto;padding:32px}
h1,h2,h3{line-height:1.3}h2{margin-top:0}h3{font-size:17px;color:#b9cbd1}a{color:#a9d9e4}code{color:#eed29c}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:24px}article{background:#242b33;padding:24px;border-radius:16px}
.images{display:flex;gap:12px;align-items:center}figure{margin:0;min-width:0;flex:1}img{width:100%;max-height:380px;object-fit:contain;background:#14171b}
figcaption,small{font-size:13px;color:#c0bdba}.status{color:#eed29c}.empty{padding:60px 12px;color:#bbb}summary{cursor:pointer;color:#a9d9e4}ul{padding-left:22px}
</style><main><h1>互动短剧画风册 · 2026-10-19</h1>
<p>7套可复用美术规则。画风决定色光、材质与视觉重点；人物、事实、衣装和空间由当前素材决定。</p>
<p>新任务填写 <code>"style": "画风代号"</code>。点击图可看原尺寸，展开可查规则与实际依据。</p>
<p>仅已审可用例图自动提供给主创。待重制图片保留作问题记录；旧片例图只展示历史画风。视频按包标注实测范围，短小样不代表整集或声音验收。</p>
<div class="grid">''' + ''.join(cards) + '</div></main></html>'
    (destination / 'index.html').write_text(page)
    (destination / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
    archive = shutil.make_archive(str(destination), 'zip', destination)
    return {'gallery': str(destination / 'index.html'), 'archive': archive, 'packs': len(inventory)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir.resolve()), ensure_ascii=False))
