"""Add a reviewed music bed to an existing movie without re-encoding its video.

    python tools/add_music.py request.json
    {"film":"output/film.mp4","output":"output/film-with-music-v1.mp4","music":{...}}

The output must be a new filename; the original film is kept. `music` has the same schema as
assemble.py: path, track_id, title, artist, license_id, attribution, gain_db, loop, fades,
and review.status="accepted" + reason. Writes <output>.music.json.
"""
import json
from pathlib import Path
import sys
from assemble import _mix_music
from media import file


def add(spec):
    film = file(spec['film'])
    output = file(spec['output'])
    if not film.is_file():
        raise ValueError(f'Existing film not found: {film}')
    if output == film:
        raise ValueError('Use a new output filename; keep the original film.')
    if output.exists():
        raise ValueError('Keep the previous output; use a new filename for a different mix.')
    mixed = _mix_music(film, output, spec.get('music') or {})
    output.with_suffix(output.suffix + '.music.json').write_text(json.dumps(
        {'film': str(film), 'output': str(output), 'music': spec['music'], 'mix': mixed},
        ensure_ascii=False, indent=2) + '\n')
    return {'movie': str(output), 'music': True, 'mix': mixed}


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(add(request), ensure_ascii=False))
