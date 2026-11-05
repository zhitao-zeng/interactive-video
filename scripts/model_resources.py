"""Read-only inventory of model resources, credential aliases and role policies."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=ROOT)
    args=parser.parse_args()
    from novel_manga.application.configuration import environment
    from novel_manga.llm.catalog import inventory,catalogue,choose
    from novel_manga.llm import quota
    env=environment(args.project_root)
    result=inventory(env)
    result['preferred']={role:choose(names,role) for role,names in catalogue()['candidates'].items()}
    result['preferred']['music']=choose(catalogue()['candidates']['json'],'http-chat')
    from novel_manga.llm.catalog import resource
    from novel_manga.llm.policy import modes
    result['preferred_reasoning']={}
    for role in catalogue()['role_modes']:
        family='agent' if role in {'author','repair','agent','independent_review'} else {'review':'vision','music':'music'}.get(role,'json')
        selected=result['preferred'][family]
        if selected is None:
            result['preferred_reasoning'][role]={'resource':None,'requested':catalogue()['role_modes'][role],'effective':None}
            continue
        row=resource(selected)
        requested,effective,_=modes(row['model'],role,base_url=row.get('base_url'))
        result['preferred_reasoning'][role]={'resource':selected,'requested':requested,'effective':effective}
    result['quota_stops']={}
    for name,row in result['resources'].items():
        key=env.get(row.get('key_var',''))
        group=quota.for_resource(name,key)
        if not group:continue
        path=quota.marker(group,key)
        if path.is_file():
            data=json.loads(path.read_text())
            result['quota_stops'][name]={k:data[k] for k in ('exhausted','code','status','observed_at_epoch','resets_at_epoch') if k in data}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
