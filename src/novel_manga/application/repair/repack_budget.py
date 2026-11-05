"""Budget constraints for adopted recuts, without changing physical take counts."""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from novel_manga.util import read_json

FIELD='_repack_budget'


def _groups(record):
    return tuple(group for trial in reversed(record.get('trials',[]))
                 for group in (trial.get('changes')or{}).get('groups',[]))


_READ_SCOPE = ContextVar('repack_group_reads', default=None)


@contextmanager
def scope_reads(directory):
    """One read-only status uses one group snapshot; generation stays live."""
    token = _READ_SCOPE.set((Path(directory), {}))
    try:
        yield
    finally:
        _READ_SCOPE.reset(token)


def group_for(directory, cid, record=None):
    if record is not None:
        groups = _groups(record)
    else:
        directory = Path(directory)
        scope = _READ_SCOPE.get()
        if scope is not None and scope[0] == directory:
            snapshot = scope[1]
            if 'groups' not in snapshot:
                snapshot['groups'] = _groups(read_json(directory/'repair_history/history.json', {}))
            groups = snapshot['groups']
        else:
            groups = _groups(read_json(directory/'repair_history/history.json', {}))
    for group in groups:
        budget=group.get('source_budget') or {}
        if cid in budget.get('children',{}) or cid in budget.get('roots',{}):return group
    return None


def balances(directory, cid, counts, grants, limits, *, preparation=False):
    group=group_for(directory,cid)
    if not group:return {'roots':[],'constraints':{},'remaining':None}
    budget=group['source_budget'];children=budget['children']
    roots=children.get(cid,{}).get('roots') or [cid]
    constraints={}
    for root in roots:
        original=budget['roots'][root]
        used=int(original['used'])+sum(max(0,counts.get(child,0)-int(info['baseline_count']))
            for child,info in children.items() if root in info['roots'])
        pending=0
        for path in (Path(directory)/'work/clips').glob('*/attempt_*/request.json'):
            request=read_json(path,{});reserved=request.get(FIELD)or{}
            if (root in reserved.get('roots',[]) and reserved.get('status') in {'reserved','unknown'}
                    and counts.get(request.get('clip_id'),0)<=int(reserved.get('before_count',0))):pending+=1
        grant=grants.get(root,{})
        limit=limits[root] if grant.get('reason') and 'limit' in grant else min(int(original['limit']),limits[root])
        extra=grant.get('conditional_extra')or{}
        if preparation and extra.get('authorization') and extra.get('reason'):limit=max(limit,int(extra['limit']))
        constraints[root]={'limit':limit,'used':used,'pending':pending,'remaining':max(0,limit-used-pending)}
    return {'roots':roots,'constraints':constraints,'remaining':min(c['remaining']for c in constraints.values())}


def members(directory,cid,record=None):
    group=group_for(directory,cid,record)
    return set(group['old'])|set(group['new']) if group else {cid}
