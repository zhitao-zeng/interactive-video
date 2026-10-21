"""List supplied evidence or decode a new frame at a requested time."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


def inspect(work, take, seconds=None):
    work = Path(work)
    evidence = json.loads((work/'input/evidence.json').read_text())
    matches = [r for r in evidence['takes'] if take in {
        r['path'], str(Path(r['path']).relative_to('input')), Path(r['path']).name}]
    if not matches:
        raise ValueError('Unknown take; choose one listed in input/evidence.json')
    if len(matches) > 1:
        raise ValueError('ambiguous take; use the listed clip/take name')
    row = matches[0]
    frames = row.get('frames', [])
    if seconds is not None:
        if seconds < 0:
            raise ValueError('Requested time must be nonnegative')
        decoder = work/'tools/bin/ffmpeg'
        executable = str(decoder) if decoder.is_file() else shutil.which('ffmpeg')
        if not executable:
            raise RuntimeError('Video decoder unavailable; the host must stage its configured decoder')
        target = work/'output/frames'/Path(row['path']).relative_to('input')/f'frame_{round(seconds*1000):07d}.jpg'
        target.parent.mkdir(parents=True, exist_ok=True)
        video = work/row['path']/'clip.mp4'
        result = subprocess.run([
            executable, '-hide_banner', '-loglevel', 'info', '-threads', '1', '-i', str(video), '-an',
            '-vf', f'select=gte(t\\,{seconds:.6f}),showinfo,scale=960:-2',
            '-frames:v', '1', '-fps_mode', 'vfr', '-q:v', '3', '-update', '1', '-y', str(target),
        ], capture_output=True, text=True, timeout=45)
        times = re.findall(r'\bpts_time:([\d.]+)', result.stderr)
        if result.returncode or not target.is_file() or not times:
            raise RuntimeError('Frame extraction failed: '+result.stderr[-600:])
        actual = float(times[0])
        frame = {'path': str(target.relative_to(work)), 'seconds': actual}
        record = {'take': str(Path(row['path']).relative_to('input')),
                  'selection': 'first decoded frame at or after requested time',
                  'requested_seconds': seconds, 'actual_seconds': actual,
                  'path': frame['path'], 'source_video': str(video.relative_to(work)),
                  'recorded_at': time.time()}
        with (work/'output/frame_requests.jsonl').open('a') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        return {**record, 'frames': [frame], 'current': row['current'],
                'video': record['source_video'], 'instruction': 'Read the decoded JPEG; actual_seconds is its source PTS.'}
    return {'take': take, 'current': row['current'], 'video': row['path']+'/clip.mp4',
            'request': row.get('request'), 'asr': row.get('asr'),
            'requested_seconds': None, 'selection': 'all supplied frames',
            'frames': frames, 'instruction': 'Read these JPEGs; --seconds decodes a new frame from this take.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('take')
    parser.add_argument('--seconds', type=float)
    args = parser.parse_args()
    work = Path(__file__).resolve().parents[1]
    print(json.dumps(inspect(work, args.take, args.seconds), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
