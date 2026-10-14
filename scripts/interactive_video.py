"""Interactive studio: import, prepare, assets, run and status."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    incoming=commands.add_parser('import')
    incoming.add_argument('package',choices=['adventure','suspense','romance'])
    incoming.add_argument('archive',type=Path)
    prep=commands.add_parser('prepare');prep.add_argument('--project',choices=['adventure','romance'],default='adventure')
    assets=commands.add_parser('assets')
    assets.add_argument('--kind',choices=['images','voice','all'],default='all')
    assets.add_argument('--project',choices=['adventure','romance'],default='adventure')
    production=commands.add_parser('run')
    production.add_argument('--lane',choices=['h3','sd25'],required=True)
    production.add_argument('--project',choices=['adventure','romance'],default='adventure')
    production.add_argument('--edition',help='Render a separately prepared output edition, retaining other versions')
    production.add_argument('--resume-unsubmitted',action='store_true',help='Retry infrastructure failure only when no video task or take exists')
    production.add_argument('--resume',action='store_true',help='Resume original task receipts, takes and bounded review history; never clear them')
    status=commands.add_parser('status');status.add_argument('--project',choices=['adventure','romance'],default='adventure')
    args=parser.parse_args()
    if args.command=='import':
        from novel_manga.interactive.importing import import_package
        result=import_package(args.archive,ROOT/'inputs/packages'/args.package)
        print(json.dumps({'package':args.package,'files':len(result['files'])},ensure_ascii=False))
    elif args.command=='prepare':
        from novel_manga.interactive.runtime import configure
        from novel_manga.interactive.preparation import prepare_episode
        from novel_manga.interactive.production import pack
        configure(ROOT,project=args.project)
        if args.project=='romance':
            from novel_manga.interactive.romance import prepare_romance
            directory=prepare_romance(ROOT)
        else:
            directory=prepare_episode(ROOT/'projects/adventure',ROOT/'outputs/adventure-master',ROOT/'inputs/packages/adventure')
        plan=pack(directory)
        print(json.dumps(plan['totals'],ensure_ascii=False,indent=2))
    elif args.command=='assets':
        if args.project=='romance':
            from novel_manga.interactive.romance import build_romance_voices
            if args.kind in ('all','voice'):build_romance_voices(ROOT)
            return 0
        from novel_manga.interactive.assets import build_images,build_voice
        for kind,fn in [('images',build_images),('voice',build_voice)]:
            if args.kind in ('all',kind):fn(ROOT,ROOT/'outputs/adventure-master')
    elif args.command=='run':
        from novel_manga.interactive.production import run_episode
        result=run_episode(ROOT,args.lane,project=args.project,resume_unsubmitted=args.resume_unsubmitted,resume=args.resume,edition=args.edition)
        return 0 if result['status']=='accepted' else 2
    else:
        from novel_manga.util import read_json
        from novel_manga.interactive.production import active_runner
        for lane in ['h3','sd25']:
            directory=ROOT/f'outputs/{args.project}-{lane}/{args.project}-{lane}_1'
            report=read_json(directory/'thin_media_report.json',{})
            delivered=read_json(ROOT/f'delivery/{args.project}/{lane}/delivery.json',{})
            pid=active_runner(ROOT,lane,args.project)
            print(json.dumps({'lane':lane,'status':'running' if pid else delivered.get('status') or report.get('status') or 'not_completed',
                              'pid':pid,'last_report_failed_clips':report.get('failed_clips',[]),
                              'delivery':delivered.get('final_video')},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
