"""Optional editable ffmpeg example: explicit cut list, no clip IDs/stages/finish.

python tools/assemble.py cuts.json output/film-v1.mp4
cuts.json: {"clips":[{"path":"media/take.mp4","start":0,"end":6}],"width":1080,"height":1920,"fps":25}
Optional music: {"music":{"path":"/licensed/track.wav","gain_db":-24,"loop":true,
  "fade_in":1,"fade_out":1.5,"review":{"status":"accepted","reason":"why it fits"}}}
The search candidates from tools/media.py music are unreviewed; adoption requires an accepted review.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from media import WORK, file, probe


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


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
    chain = (f'[0:a]loudnorm=I=-5:TP=-1.5:LRA=11,volume={gain}dB,'
             f'afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_out_at}:d={fade_out}[bgm];'
             '[1:a]volume=1.0[voice];[voice][bgm]amix=inputs=2:duration=first:'
             'dropout_transition=2:normalize=0,alimiter=limit=0.98:level=false[a]')
    cmd += ['-filter_complex', chain, '-map', '1:v:0', '-map', '[a]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
            '-t', f'{film}', '-movflags', '+faststart', str(output)]
    subprocess.run(cmd, check=True)
    return {'track': str(track), 'gain_db': gain, 'target_lufs': -5 + gain,
            'loudness_reference_lufs': -5, 'loop': loop, 'start': start,
            'seconds': seconds, 'film_duration': film}


def assemble(spec, output):
    output = file(output)
    if output.exists():
        raise ValueError('Keep the previous movie; use a new output filename for changed cuts.')
    folder = output.with_suffix(output.suffix + '.parts')
    folder.mkdir(parents=True, exist_ok=True)
    cache = WORK / 'build' / 'assemble-cache'
    cache.mkdir(parents=True, exist_ok=True)
    width, height, fps = int(spec.get('width',1080)), int(spec.get('height',1920)), int(spec.get('fps',25))
    videos, audios, timeline = [], [], []
    cache_hits = cache_misses = 0
    total = 0.0
    for i, cut in enumerate(spec['clips']):
        source = file(cut['path']); meta=probe(source)
        duration=float(meta['format']['duration']);start=float(cut.get('start',0));end=float(cut.get('end',duration))
        if not 0 <= start < end <= duration + .05:
            raise ValueError(f'Cut outside actual source duration: {cut}')
        stat = source.stat()
        identity = {'source': str(source), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                    'start': start, 'end': end, 'zoom': cut.get('zoom'),
                    'x': float(cut.get('x', .5)), 'y': float(cut.get('y', .5)),
                    'width': width, 'height': height, 'fps': fps,
                    'preset': spec.get('preset', 'fast')}
        vout = cache / f'v-{_digest(identity)}.mp4'
        if not vout.is_file() or vout.stat().st_size == 0:
            filters=[f'trim=start={start}:end={end}','setpts=PTS-STARTPTS']
            if cut.get('zoom'):
                z=float(cut['zoom']);x=float(cut.get('x',.5));y=float(cut.get('y',.5))
                filters.append(f'crop=iw/{z}:ih/{z}:(iw-iw/{z})*{x}:(ih-ih/{z})*{y}')
            filters += [f'scale={width}:{height}:force_original_aspect_ratio=increase',f'crop={width}:{height}',
                        'setsar=1',f'fps={fps}','format=yuv420p']
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-vf',','.join(filters),'-an',
                '-c:v','libx264','-preset',spec.get('preset','fast'),'-crf','18','-y',str(vout)],check=True)
            cache_misses += 1
        else:
            cache_hits += 1
        video=next(s for s in probe(vout)['streams']if s['codec_type']=='video')
        frames=int(video['nb_frames']);length=frames/fps
        audio_identity={**identity,'length':round(length,9),'sample_rate':48000,'channels':2}
        aout=cache / f'a-{_digest(audio_identity)}.wav'
        if not aout.is_file() or aout.stat().st_size == 0:
            if any(s['codec_type']=='audio'for s in meta['streams']):
                command=['ffmpeg','-nostdin','-v','error','-i',str(source),'-vn','-af',
                    f'atrim=start={start}:end={start+length},asetpts=PTS-STARTPTS,apad=whole_dur={length},atrim=end={length}']
            else:
                command=['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',str(length)]
            subprocess.run([*command,'-c:a','pcm_s16le','-ar','48000','-ac','2','-y',str(aout)],check=True)
            cache_misses += 1
        else:
            cache_hits += 1
        videos.append(vout);audios.append(aout)
        timeline.append({**cut,'source':str(source),'start':round(total,6),'end':round(total+length,6),
                         'source_in':start,'requested_source_out':end,'actual_frames':frames})
        total+=length
    if not videos:raise ValueError('Supply at least one actual cut.')
    def concat(paths, name, audio=False):
        listing=folder/(name+'.txt')
        listing.write_text(''.join("file '"+str(p).replace("'", "'\\''")+"'\n"for p in paths))
        target=folder/(name+('.wav'if audio else'.mp4'))
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','concat','-safe','0','-i',str(listing),
                        '-c:a'if audio else'-c','pcm_s16le'if audio else'copy','-y',str(target)],check=True)
        return target
    video,audio=concat(videos,'joined-video'),concat(audios,'joined-audio',True)
    joined=folder/'joined.mp4'
    command=['ffmpeg','-nostdin','-v','error','-i',str(video),'-i',str(audio),'-map','0:v:0','-map','1:a:0']
    if spec.get('subtitles'):
        subtitle=str(file(spec['subtitles'])).replace('\\','\\\\').replace(':','\\:').replace("'","\\'")
        command+=['-vf',f"subtitles=filename='{subtitle}'",'-c:v','libx264','-preset',spec.get('preset','fast'),'-crf','18']
    else:command+=['-c:v','copy']
    if spec.get('loudnorm',True):command+=['-af','loudnorm=I=-16:TP=-1.5:LRA=11']
    subprocess.run([*command,'-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
                    '-t',str(total),'-movflags','+faststart','-y',str(joined)],check=True)
    music=spec.get('music');mixed=_mix_music(joined,output,music)if music else None
    if not music:joined.replace(output)
    output.with_suffix(output.suffix+'.cuts.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n')
    output.with_suffix(output.suffix+'.timeline.json').write_text(json.dumps({'duration':round(total,6),'fps':fps,'clips':timeline},ensure_ascii=False,indent=2)+'\n')
    if mixed:
        output.with_suffix(output.suffix+'.music.json').write_text(json.dumps({'movie':str(output),'music':music,'mix':mixed},ensure_ascii=False,indent=2)+'\n')
    return {'movie':str(output),'duration':probe(output)['format']['duration'],'cuts':len(videos),'music':bool(mixed),
            'cache_hits':cache_hits,'cache_misses':cache_misses}


if __name__ == '__main__':
    print(json.dumps(assemble(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]), ensure_ascii=False))
