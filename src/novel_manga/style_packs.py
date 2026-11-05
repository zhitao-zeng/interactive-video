"""Art rules and one selected pack's local reference snapshot."""
from __future__ import annotations

import copy
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
