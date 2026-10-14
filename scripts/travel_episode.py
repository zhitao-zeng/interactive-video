"""Prepare and run the adopted travel episode as one complete H3 production."""
from pathlib import Path
import argparse
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    from novel_manga.interactive.authored import prepare_authored, pack_authored, build_authored_voices
    from novel_manga.interactive.runtime import runner_for
    from novel_manga.interactive.production import run_episode
    from novel_manga.media.asset_builder import FramedAssetFactory, load_location_time
    from novel_manga.util import atomic_write_json, read_json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    project=ROOT/'projects/adventure/laughing-country'
    master=ROOT/'outputs/adventure-laughing-country-master'
    episode=master/(master.name+'_1')
    edition=ROOT/'outputs/adventure-laughing-country-h3-768'
    if not master.exists():prepare_authored(project,master)
    elif (episode/'authored_source.md').read_text() != (project/'source.md').read_text():
        raise ValueError('Keep the frozen edition; changed source requires a separately named edition')
    if args.prepare_only:
        plan=pack_authored(episode)
        print({'generation_units':len(plan['clips']),'body_seconds':sum(c['request_seconds'] for c in plan['clips'])},flush=True)
        return 0
    if not edition.exists():
        pack_authored(episode)
        runner=runner_for(ROOT,master,'h3',project='adventure')
        ctx=runner.context
        factory=FramedAssetFactory(ctx.settings,ctx.provider,style=ctx.asset_style,location_time=load_location_time(master))
        factory.build_selected(master/'series_assets',ctx.bible,{'character_001'},
            {f'location_{i:03d}' for i in range(1,len(ctx.bible.locations)+1)},expressions=False,
            prop_ids={f'prop_{i:03d}' for i in range(1,len(ctx.bible.props)+1)})
        build_authored_voices(ROOT,master)
        plan=pack_authored(episode)
        missing={r['path'] for c in plan['clips'] for r in c['references'] if not (master/r['path']).is_file()}
        if missing:raise FileNotFoundError(f'Common assets not ready: {sorted(missing)}')
        shutil.copytree(master,edition,ignore=shutil.ignore_patterns('work','probe','*.log'))
        (edition/(master.name+'_1')).rename(edition/(edition.name+'_1'))
        (edition/'owner').write_text('Codex 荒野会话 01a13346 / 用户选定笑之国首集完整H3 768P / 2026-10-13\n')
        atomic_write_json(edition/'comparison.json',{'provider':'h3','native_short_edge':768,
            'source_master':str(master),'source_script':str(project/'source.md'),
            'requested_body_seconds':sum(c['request_seconds'] for c in plan['clips']),
            'interaction_seconds':3,'sd_status':'not_generated_user_no_topup',
            'old_editions_preserved':True,'history_policy':'Existing bounded retries; no manual partial-shot changes'})
    receipt=run_episode(ROOT,'h3',project='adventure',edition=edition.name,resume=args.resume)
    return 0 if receipt['status']=='accepted' else 2


if __name__=='__main__':raise SystemExit(main())
