"""Read chapter identity inputs once per operation and retain the existing cache keys."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from novel_manga.util import read_json
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import POLICY, usable_reading

def read(path, default=None):
    return read_json(Path(path), default)


def data_files(novel):
    """Every file that can change how a chapter binds - the cache key is only as good as this list.

    reading_cast.json belongs here because map_source_reading() consults its aliases: without it, adding
    the alias that proves 蝙蝠侠 and 布鲁斯 are one man leaves the chapter's saved UNKNOWN binding in place
    and the plan keeps failing at a defect that was already fixed.  Re-binding is cheap: resolve_chapter's
    second early return reuses the saved source_actors whenever the segments are unchanged, so a new alias
    costs no model call - it only redoes the binding, which is exactly what changed.
    """
    novel = Path(novel).resolve()
    return [novel / name for name in ['story_bible.json', 'bible_aliases.json', 'entity_index.json',
            'reading_cast.json',
            'series_assets/phases.json', 'entity/entities.json', 'entity/claims.json', 'entity/types.json']]


def data_signature(novel):
    return {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in data_files(novel)}


def chapter_inputs(directory, *, script=None):
    directory = Path(directory).resolve()
    result = {'data': data_signature(directory.parent), 'segments': read(directory / 'segments.json', [])}
    authored = directory / 'authored_source.md'
    if authored.is_file():
        result['authored_source'] = authored.read_text(encoding='utf-8')
    script_path = directory / 'chapter_script.json'
    if script_path.is_file():
        script = read(script_path, {}) if script is None else script
        evidence = [{'index': shot.get('index'), 'segment_ids': shot.get('segment_ids'),
                     'characters': shot.get('characters'), 'in_frame': shot.get('in_frame'),
                     **({key: shot.get(key) for key in ('props', 'scene_objects')} if authored.is_file() else {}),
                     'turns': [{key: turn.get(key) for key in ('speaker_name', 'text')}
                               for turn in shot.get('turns', [])]}
                    for shot in script.get('shots', [])]
        result['dialogue_grounding'] = hashlib.sha256(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result



@dataclass
class BookIdentityData:
    catalog: IdentityCatalog
    index: dict
    types: dict


def load_book(novel: Path) -> BookIdentityData:
    novel = Path(novel)
    index = read(novel / 'entity_index.json', {})
    catalog = IdentityCatalog(read(novel / 'story_bible.json', {}),
        read(novel / 'entity/entities.json', []), read(novel / 'bible_aliases.json', {}), index,
        read(novel / 'entity/claims.json', []), read(novel / 'series_assets/phases.json', {}))
    return BookIdentityData(catalog, index, read(novel / 'entity/types.json', {}))


def load_catalog(novel: Path) -> IdentityCatalog:
    return load_book(novel).catalog


@dataclass
class ChapterIdentityData:
    directory: Path
    book: BookIdentityData
    expected: dict
    saved: dict

    @property
    def catalog(self):
        return self.book.catalog

    @property
    def segments(self):
        return self.expected['segments']

    @property
    def context(self):
        saved = self.saved
        return saved if (saved.get('policy') == POLICY and usable_reading(saved)
                         and saved.get('inputs') == self.expected) else {}


def load_chapter(directory: Path, *, script=None) -> ChapterIdentityData:
    directory = Path(directory).resolve()
    return ChapterIdentityData(directory, load_book(directory.parent), chapter_inputs(directory, script=script),
                               read(directory / 'identity_context.json', {}))


def current_context(directory, *, data: ChapterIdentityData | None = None):
    if data is not None:
        return data.context
    result = read(Path(directory) / 'identity_context.json', {})
    return result if result.get('policy') == POLICY and usable_reading(result) and result.get('inputs') == chapter_inputs(Path(directory)) else {}
