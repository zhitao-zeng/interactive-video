"""Optional editable ffmpeg example: explicit cut list, no clip IDs/stages/cache/finish.

python tools/assemble.py cuts.json output/film-v1.mp4
cuts.json: {"clips":[{"path":"media/take.mp4","start":0,"end":6}],"width":1080,"height":1920,"fps":25}
"""
import json
from pathlib import Path
import subprocess
import sys
from media import WORK, file, probe


def assemble(spec, output):
    output = file(output)
    if output.exists():
        raise ValueError('Keep the previous movie; use a new output filename for changed cuts.')
    folder = output.with_suffix(output.suffix + '.parts')
    folder.mkdir(parents=True, exist_ok=True)
    width, height, fps = int(spec.get('width', 1080)), int(spec.get('height', 1920)), int(spec.get('fps', 25))
    parts = []
    for i, cut in enumerate(spec['clips']):
        source = file(cut['path'])
        duration = float(probe(source)['format']['duration'])
        start, end = float(cut.get('start', 0)), float(cut.get('end', duration))
        if not 0 <= start < end <= duration + .05:
            raise ValueError(f'Cut outside actual source duration: {cut}')
        target = folder / f'{i:04d}.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(source), '-t', str(end-start),
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-y', str(target)], check=True)
        parts.append(target)
    if not parts:
        raise ValueError('Supply at least one actual cut.')
    listing = folder / 'concat.txt'
    listing.write_text(''.join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in parts))
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-c', 'copy', '-movflags', '+faststart', '-y', str(output)], check=True)
    output.with_suffix(output.suffix + '.cuts.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    return {'movie': str(output), 'duration': probe(output)['format']['duration'], 'cuts': len(parts)}


if __name__ == '__main__':
    print(json.dumps(assemble(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]), ensure_ascii=False))
