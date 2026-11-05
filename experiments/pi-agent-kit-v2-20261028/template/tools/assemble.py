"""Optional editable ffmpeg example: explicit cut list, no clip IDs/stages/cache/finish.

python tools/assemble.py cuts.json output/film-v1.mp4
cuts.json: {"clips":[{"path":"media/take.mp4","start":0,"end":6}],"width":1080,"height":1920,"fps":25}
Optional music: {"music":{"path":"/licensed/track.wav","gain_db":-24,"loop":true,
  "fade_in":1,"fade_out":1.5,"review":{"status":"accepted","reason":"why it fits"}}}
The search candidates from tools/media.py music are unreviewed; adoption requires an accepted review.
"""
import json
from pathlib import Path
import subprocess
import sys
from media import file, probe


def _mix_music(joined, output, music):
    track = file(music.get('path', ''))
    if not track.is_file():
        raise ValueError(f'Music track not found: {track}')
    review = music.get('review') or {}
    if review.get('status') != 'accepted' or not str(review.get('reason') or '').strip():
        raise ValueError('Music requires review.status=accepted and a reason; search candidates are unreviewed')
    gain = float(music.get('gain_db', -24))
    if not -36 <= gain <= 0:
        raise ValueError('music gain_db must be from -36 to 0')
    film = float(probe(joined)['format']['duration'])
    start = float(music.get('start', 0))
    seconds = music.get('seconds')
    loop = bool(music.get('loop', True))
    fade_in = min(float(music.get('fade_in', .8)), film)
    fade_out = min(float(music.get('fade_out', 1.2)), film)
    if start < 0 or (seconds is not None and float(seconds) <= 0):
        raise ValueError('music start/seconds invalid')
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
    if loop:
        cmd += ['-stream_loop', '-1']
    if start:
        cmd += ['-ss', str(start)]
    if seconds is not None and not loop:
        cmd += ['-t', str(float(seconds))]
    cmd += ['-i', str(track), '-i', str(joined)]
    fade_out_at = max(0., film - fade_out)
    chain = (f'[0:a]volume={gain}dB,afade=t=in:st=0:d={fade_in},'
             f'afade=t=out:st={fade_out_at}:d={fade_out}[bgm];'
             '[1:a]volume=1.0[voice];[voice][bgm]amix=inputs=2:duration=first:'
             'dropout_transition=2:normalize=0[a]')
    cmd += ['-filter_complex', chain, '-map', '1:v:0', '-map', '[a]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
            '-t', f'{film}', '-movflags', '+faststart', str(output)]
    subprocess.run(cmd, check=True)
    return {'track': str(track), 'gain_db': gain, 'loop': loop, 'start': start,
            'seconds': seconds, 'film_duration': film}


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
    joined = folder / 'joined.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-c', 'copy', '-movflags', '+faststart', '-y', str(joined)], check=True)
    music = spec.get('music')
    mixed = _mix_music(joined, output, music) if music else None
    if not music:
        joined.replace(output)
    output.with_suffix(output.suffix + '.cuts.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    if mixed:
        output.with_suffix(output.suffix + '.music.json').write_text(json.dumps(
            {'movie': str(output), 'music': music, 'mix': mixed}, ensure_ascii=False, indent=2) + '\n')
    return {'movie': str(output), 'duration': probe(output)['format']['duration'], 'cuts': len(parts),
            'music': bool(mixed)}


if __name__ == '__main__':
    print(json.dumps(assemble(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]), ensure_ascii=False))
