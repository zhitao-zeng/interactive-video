"""Interactive studio: package/intent → creative Agent → preparation, production and review."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    creative=commands.add_parser('create',help='Write a complete screenplay/storyboard with one creator Agent')
    creative.add_argument('--task',type=Path,required=True,help='Creative intent or complete package adaptation task')
    creative.add_argument('--output-dir',type=Path,required=True)
    creative.add_argument('--config',type=Path)
    creative.add_argument('--resume',action='store_true')
    creative.add_argument('--feedback',type=Path)
    incoming=commands.add_parser('import')
    incoming.add_argument('package',choices=['adventure','suspense','romance'])
    incoming.add_argument('archive',type=Path)
    prep=commands.add_parser('prepare');prep.add_argument('--project',choices=['adventure','romance','suspense'],default='adventure')
    prep.add_argument('--material',type=Path,help='Directory containing approved material.json and source.md for any project')
    prep.add_argument('--revision',type=Path,help='Adopt a separately reviewed adventure revision')
    prep.add_argument('--edition',help='New master output directory; existing outputs are preserved')
    prep.add_argument('--assets-from',type=Path,help='Existing shared asset output directory')
    assets=commands.add_parser('assets')
    assets.add_argument('--edition',help='Prepared output edition using voice_specs.json and the existing asset factory')
    assets.add_argument('--kind',choices=['images','voice','all'],default='all')
    assets.add_argument('--project',choices=['adventure','romance','suspense'],default='adventure')
    production=commands.add_parser('run')
    production.add_argument('--lane',choices=['h3','sd25'],required=True)
    production.add_argument('--project',choices=['adventure','romance','suspense'],default='adventure')
    production.add_argument('--edition',help='Render a separately prepared output edition, retaining other versions')
    production.add_argument('--resume-unsubmitted',action='store_true',help='Retry infrastructure failure only when no video task or take exists')
    production.add_argument('--resume',action='store_true',help='Resume original task receipts, takes and bounded review history; never clear them')
    status=commands.add_parser('status');status.add_argument('--project',choices=['adventure','romance','suspense'],default='adventure')
    status.add_argument('--edition',help='Read one retained edition and its real generation history')
    args=parser.parse_args()
    if args.command=='create':
        from novel_manga.interactive.creation import run
        from novel_manga.application.agents.sandbox import load_config
        result=run(args.task,args.output_dir,resume=args.resume,feedback=args.feedback,
                   config=load_config(args.config) if args.config else None)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['status']=='creative_candidate' else 2
    elif args.command=='import':
        from novel_manga.interactive.importing import import_package
        result=import_package(args.archive,ROOT/'inputs/packages'/args.package)
        print(json.dumps({'package':args.package,'files':len(result['files'])},ensure_ascii=False))
    elif args.command=='prepare':
        from novel_manga.interactive.runtime import configure
        from novel_manga.interactive.preparation import prepare_episode
        from novel_manga.interactive.production import pack
        configure(ROOT,project=args.project)
        if args.material:
            if not args.edition or args.revision or args.assets_from:
                raise ValueError('--material requires --edition; references belong in the approved material')
            if Path(args.edition).name!=args.edition:raise ValueError('Edition must be an output directory name')
            from novel_manga.interactive.authored import prepare_authored
            directory=prepare_authored(args.material,ROOT/'outputs'/args.edition,family=args.project)
        elif args.revision or args.edition or args.assets_from:
            if args.project!='adventure' or not all([args.revision,args.edition,args.assets_from]):
                raise ValueError('Adventure revision preparation requires --revision, --edition and --assets-from')
            if Path(args.edition).name!=args.edition:raise ValueError('Edition must be an output directory name')
            from novel_manga.interactive.preparation import prepare_revision
            directory=prepare_revision(args.revision,ROOT/'outputs'/args.edition,ROOT/'inputs/packages/adventure',args.assets_from)
        elif args.project=='romance':
            from novel_manga.interactive.romance import prepare_romance
            directory=prepare_romance(ROOT)
        elif args.project=='suspense':
            from novel_manga.interactive.suspense import prepare_suspense
            directory=prepare_suspense(ROOT)
        else:
            directory=prepare_episode(ROOT/'projects/adventure',ROOT/'outputs/adventure-master',ROOT/'inputs/packages/adventure')
        plan=pack(directory)
        print(json.dumps(plan['totals'],ensure_ascii=False,indent=2))
    elif args.command=='assets':
        if args.edition:
            if Path(args.edition).name!=args.edition:raise ValueError('Edition must be an output directory name')
            from novel_manga.interactive.authored import build_authored_voices
            from novel_manga.interactive.runtime import runner_for
            from novel_manga.interactive.production import edition_directory
            novel=edition_directory(ROOT,args.project,args.edition)
            if args.kind in ('all','images'):
                runner=runner_for(ROOT,novel,'h3',project=args.project)
                runner.build_assets()
            if args.kind in ('all','voice'):build_authored_voices(ROOT,novel,family=args.project)
            return 0
        if args.project=='suspense':
            from novel_manga.interactive.suspense import build_suspense_voices
            if args.kind in ('all','voice'):build_suspense_voices(ROOT)
            return 0
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
        if args.edition:
            from novel_manga.interactive.production import edition_status
            print(json.dumps(edition_status(ROOT,args.project,args.edition),ensure_ascii=False))
            return 0
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
