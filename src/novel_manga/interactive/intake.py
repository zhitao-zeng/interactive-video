"""Common handoff for approved adventure, romance and suspense inputs."""
from pathlib import Path
import shutil
from novel_manga.util import read_json, atomic_write_json

PROJECTS = ('adventure', 'romance', 'suspense')


def finalize(directory: Path, project: str, *, documents=()) -> dict:
    """Keep native contracts and original documents; never infer missing story facts."""
    if project not in PROJECTS:
        raise ValueError(f'Unknown interactive project: {project}')
    script = read_json(directory/'chapter_script.json')
    segments = read_json(directory/'segments.json')
    trace = read_json(directory/'source_trace.json')
    interaction = read_json(directory/'interaction.json')
    read_json(directory.parent/'story_bible.json')
    read_json(directory.parent/'profile.json')
    ids = {s['segment_id'] for s in segments}
    for shot in script['shots']:
        retained = shot.get('segment_ids') or ([shot['segment_id']] if shot.get('segment_id') else [])
        if not retained or not set(retained) <= ids:
            raise ValueError('Every shot needs its retained source segment')
    groups = read_json(directory/'authored_groups.json', None)
    if groups is not None and [i for g in groups for i in g['shot_indexes']] != [s['index'] for s in script['shots']]:
        raise ValueError('Authored groups must retain every shot exactly once in order')
    if not trace or not interaction.get('options'):
        raise ValueError('Source trace and final interactive choices are required')
    sources = []
    for number, source in enumerate(documents, 1):
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(f'Original document missing: {source}')
        target = directory/'source_documents'/f'{number:02d}-{source.name}'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        sources.append({'original': str(source), 'path': str(target.relative_to(directory))})
    result = {'version': 1, 'project': project, 'documents': sources,
              'authority': 'Approved screenplay plus retained original documents; derived speaker bindings are not verbatim source.',
              'repair': 'Use the existing bounded production repair tools in this edition; preserve tasks, takes and counts.'}
    atomic_write_json(directory/'intake.json', result)
    return result
