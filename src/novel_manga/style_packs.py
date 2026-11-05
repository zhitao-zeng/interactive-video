"""Art rules and one selected pack's local reference snapshot."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

from novel_manga.util import atomic_write_json


def asset_direction(style: dict, kind: str, base: str = '') -> str:
    """Use appearance rules for this asset, not a scene's actors or story actions."""
    art = style.get('art_direction') or {}
    if not art:
        return base
    palette = art.get('palette') or {}
    rules = [palette.get('use', ''), *art.get('lighting', []),
             *(art.get('asset_guidance') or {}).get(kind, [])]
    rules = [str(rule).strip() for rule in rules if str(rule).strip()]
    if not rules:
        return base
    return '\n'.join(filter(None, [base,
        '画风规则服从本次素材明确的人物身份、衣装、物件、空间与时段。', *rules]))


def frame_style(novel_dir: Path) -> dict:
    """Snapshot only the selected appearance rules, excluding examples and audit metadata."""
    from novel_manga.application.profiles import load_style
    from novel_manga.util import read_json
    profile=read_json(novel_dir/'profile.json',{})
    if not profile.get('style') and not (novel_dir/'style.json').is_file():
        return {}
    style=load_style(profile,novel_dir)
    result={k:copy.deepcopy(style[k]) for k in
            ('name','render_family','visual_style','h3_style_line') if style.get(k)}
    art=style.get('art_direction') or {}
    result['art_direction']={k:copy.deepcopy(art[k]) for k in
        ('intent','palette','lighting','composition','asset_guidance','pitfalls') if art.get(k)}
    return result


def style_example_references(novel_dir):
    """Read only this edition's frozen style attachments, in their authored order."""
    path=Path(novel_dir)/'style.json'
    if not path.is_file():return []
    style=json.loads(path.read_text(encoding='utf-8'));result=[]
    for index,example in enumerate(style.get('examples') or [],1):
        if (example.get('kind') not in {'original_style_board','historical_style_frame'}
                or not example.get('path') or example.get('status') in {'pending','blocked_quota','needs_revision'}):
            continue
        row={'path':example['path'],'role':'style-reference',
             'name':example.get('name') or f"{style.get('name','Selected style')} example {index}",
             'reference_scope':'style_only_not_character_identity','view':'style_example'}
        for key in ('hash','sha256'):
            if example.get(key):row[key]=example[key]
        result.append(row)
    return result


def opening_appearance(style: dict) -> dict:
    # The full pack remains available to the author/reviewer. The image request
    # gets appearance cues, not episode-wide staging, future shots or asset tasks.
    appearance={k:copy.deepcopy(style[k]) for k in
                ('render_family','visual_style','h3_style_line') if style.get(k)}
    palette=(style.get('art_direction')or{}).get('palette')
    if palette:appearance['palette']=copy.deepcopy(palette)
    return appearance


def first_frame_prompt(authored: str, style: dict) -> str:
    appearance=opening_appearance(style)
    if not appearance:
        return authored
    return (authored+'\n\nSelected visual style (appearance only):\n'
            +json.dumps(appearance,ensure_ascii=False)
            +'\nApply relevant appearance cues only. Preserve the authored current state, '
            'identity, wardrobe, material facts, geometry, light and framing.')


def freeze_selected_style(key: str, work: Path) -> tuple[dict, list[dict]]:
    """Copy a chosen package and its style-only images into this new author's input."""
    from novel_manga.application import profiles

    if key not in profiles.style_names():
        raise ValueError(f'unknown style: {key}')
    style = copy.deepcopy(profiles.load_style({'style': key}))
    root = profiles.STYLES_DIR.parent.parent
    pictures = []
    for index, example in enumerate(style.get('examples', []), 1):
        if example.get('kind') not in {'original_style_board', 'historical_style_frame'}:
            continue
        for field in ('review', 'evidence'):
            if not example.get(field):
                continue
            evidence = root / example[field]
            if evidence.is_file():
                evidence_target = Path('input/style-examples') / f'{key}-{field}-{index:02d}.json'
                (work / evidence_target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(evidence, work / evidence_target)
                example[field] = evidence_target.as_posix()
            else:
                example[field] = None
        if example.get('status') in {'pending', 'blocked_quota', 'needs_revision'}:
            example['path'] = None
            continue
        if not example.get('path'):
            raise FileNotFoundError(f'style example has no path: {key}')
        source = Path(example['path'])
        source = source if source.is_absolute() else root / source
        if not source.is_file():
            if not Path(root).exists():
                # A fresh checkout has no style-library at all (gitignored media); the
                # text rules still bind the style, so stay usable without the examples.
                example['path'] = None
                example['missing_example_note'] = f'style library absent in this checkout: {Path(root).name}'
                continue
            raise FileNotFoundError(f'style example is missing: {source}')
        pictures.append((example, source, Path('input/style-examples') / f'{key}-{index:02d}{source.suffix}'))
    references = []
    for example, source, relative in pictures:
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        example['path'] = relative.as_posix()
        references.append({'path': relative.as_posix(), 'role': 'style-reference',
            'name': style['name'], 'reference_scope': 'style_only_not_character_identity',
            'generated': False})
    atomic_write_json(work / 'input/style.json', style)
    return {'id': key, 'version': style.get('pack_version', 'legacy'),
            'file': 'input/style.json',
            'scope': 'Visual rules only. Source identities, story facts, wardrobe and authored POV remain authoritative.'}, references


def _style_source_file(style_source, relative):
    relative=Path(relative)
    if relative.is_absolute():return relative
    if style_source.parent.name=='input':
        bases=(style_source.parent.parent,style_source.parent)
    else:
        bases=(style_source.parent,style_source.parent.parent)
    for base in bases:
        candidate=base/relative
        if candidate.is_file():return candidate
    return bases[0]/relative


def normalized_style(style_source):
    style_source=Path(style_source)
    style=json.loads(style_source.read_text())
    attachments={}
    for example in style.get('examples',[]):
        if example.get('kind') in {'original_style_board','historical_style_frame'} and example.get('path'):
            source=_style_source_file(style_source,example['path'])
            relative=Path('style-examples')/source.name
            attachments[relative.as_posix()]=source
            example['path']=relative.as_posix()
        for field in ('review','evidence'):
            if example.get(field):
                source=_style_source_file(style_source,example[field])
                relative=Path('style-examples')/source.name
                attachments[relative.as_posix()]=source
                example[field]=relative.as_posix()
    return style,attachments


def export_style(style_source, destination):
    import shutil
    destination=Path(destination);style,attachments=normalized_style(style_source)
    destination.mkdir(parents=True,exist_ok=True)
    for relative,source in attachments.items():
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    (destination/'style.json').write_text(json.dumps(style,ensure_ascii=False,indent=2)+'\n')
