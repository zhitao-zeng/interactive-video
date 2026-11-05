"""Register a supplied BGM archive into the current studio's real CPU music index."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    args=parser.parse_args()
    from novel_manga.application.music_library import register
    result=register(ROOT,args.archive,args.destination,args.work_dir)
    print(json.dumps({'tracks_imported':len(result['tracks']),'total_registered':result['total_registered'],
        'original_trials_preserved':len(result['original_trials']),'result':str(args.work_dir/'result.json')},ensure_ascii=False))
