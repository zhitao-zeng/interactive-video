"""ModelScope SDK worker; run with the existing dedicated ModelScope Python."""
import argparse
import logging
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from novel_manga.util import read_json, atomic_write_json
from novel_manga.interactive.publication import publish_deliveries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args();spec=read_json(args.spec)
    for key in list(os.environ):
        if key.lower() in {'http_proxy','https_proxy','all_proxy'}:del os.environ[key]
    from modelscope_hub import HubApi
    logging.disable(logging.CRITICAL)
    api=HubApi()
    if api.whoami().username!=spec['repository'].split('/')[0]:raise ValueError('Unexpected ModelScope account')
    for attempt in range(3):
        try:
            result=publish_deliveries(ROOT,spec,args.state,api)
            return 0 if result['status']=='complete' else 2
        except Exception as error:
            kind=type(error).__name__
            atomic_write_json(args.state.parent/f'error-{attempt+1}.json',{'type':kind})
            if kind in {'AuthenticationError','AuthorizationError'} or attempt==2:return 1
            time.sleep(30)


if __name__=='__main__':raise SystemExit(main())
