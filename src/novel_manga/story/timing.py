"""Map authored shots to the measured clip timeline shared by captions and music."""


def shot_spans(script,plan,timeline):
    seconds={s.get('index',i):float(s['duration_seconds'])for i,s in enumerate(script['shots'],1)}
    clips={c['clip_id']:c for c in plan['clips']};result=[]
    for span in timeline:
        clip=clips.get(span['unit_id'])
        if not clip:continue
        total=sum(seconds[i]for i in clip['shot_indexes']);cursor=float(span['start'])
        for index in clip['shot_indexes']:
            end=cursor+(float(span['end'])-float(span['start']))*seconds[index]/total
            result.append({'index':index,'start':round(cursor,6),'end':round(end,6)});cursor=end
    return result


def music_plan_issues(plan,shots):
    """Validate creative cue anchors before files/assets exist."""
    if not isinstance(plan,dict)or not isinstance(plan.get('cues'),list)or not isinstance(plan.get('silences',[]),list):
        return ['music_plan requires cues and optional silences lists']
    import math
    seconds={s['index']:float(s['duration_seconds'])for s in shots};issues=[];ids=[]
    for kind,rows in [('cue',plan['cues']),('silence',plan.get('silences',[]))]:
        for row in rows:
            if not isinstance(row,dict):issues.append('music_plan '+kind+' requires an object');continue
            if kind=='cue':
                value=row.get('id');ids.append(value if isinstance(value,str)else None)
                if not isinstance(value,str)or not value.strip()or not isinstance(row.get('query'),str)or not row['query'].strip():issues.append('music_plan cue requires id and music query')
            if not row.get('reason')or not row.get('source_quote'):issues.append('music_plan '+kind+' needs reason/source_quote')
            for edge in ('start','end'):
                anchor=row.get(edge)
                if not isinstance(anchor,dict)or type(anchor.get('shot'))is not int or anchor['shot']not in seconds or anchor.get('edge')not in {'start','end'}:
                    issues.append('music_plan '+kind+' requires current shot start/end anchors');continue
                offset=anchor.get('offset_seconds',0)
                if type(offset)not in {int,float}or not math.isfinite(offset):issues.append('music_plan anchor offset must be finite')
    if len(set(ids))!=len(ids):issues.append('music_plan cue ids must be distinct')
    return issues
