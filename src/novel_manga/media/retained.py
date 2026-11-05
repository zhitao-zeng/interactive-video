"""The published duration of authored silent inserts shorter than H3's floor."""
from . import speech
from .issues import QualityIssue


def silent_insert_seconds(clip,profile,script):
    indexes=clip.get('shot_indexes',[])
    shots={s['index']:s for s in script.get('shots',[])}
    first_frame=any(profile.get(key)=='first-frame' for key in ('h3_input_mode','sd_input_mode'))
    if (not first_frame or len(indexes)!=1
            or indexes[0] not in shots or clip.get('spoken_text') or clip.get('dialogue_bindings')):
        return None
    shot=shots[indexes[0]]
    if any(t.get('text') for t in shot.get('turns',[])):return None
    seconds=float(shot.get('duration_seconds') or 0)
    return seconds if 0<seconds<4 and float(clip.get('request_seconds') or 0)>=4 else None


def speech_in_retained_window(analysis,seconds):
    """Keep overlapping speech conservative; wholly discarded tail cannot fail delivery."""
    if seconds is None:return analysis
    rows=analysis.get('chunks')or[]
    located=all(isinstance(r.get('start'),(int,float)) and isinstance(r.get('end'),(int,float)) for r in rows)
    located=located and (bool(rows) or not analysis.get('hypothesis'))
    window={'start':0.,'end':seconds,'complete':located}
    if not located:return {**analysis,'retained_speech_window':window}
    kept=[r for r in rows if r['start']<seconds and r['end']>0]
    checked=speech.evaluate('',kept,analysis.get('mean_volume_db'),analysis.get('max_volume_db'),
                            vocalizations=analysis.get('planned_vocalizations',[]))
    other=[i for i in analysis.get('issues',[]) if i!=QualityIssue.UNSCRIPTED_SPEECH.code]
    issues=list(dict.fromkeys([*other,*checked['issues']]))
    return {**analysis,**checked,'issues':issues,'passed':not issues,
            'retained_speech_window':window,'excluded_tail_speech_chunks':len(rows)-len(kept),
            'native_speech_issues':analysis.get('issues',[])}
