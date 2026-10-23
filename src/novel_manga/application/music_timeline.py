"""Render a source-anchored music bus; silences affect only added music."""
from pathlib import Path
import math

from novel_manga.story.timing import shot_spans
from novel_manga.review.storage import take_identity
from novel_manga.util import read_json,atomic_write_json,media_duration,run


def planned_timeline(directory):
    result=[];cursor=0.
    for clip in read_json(Path(directory)/'clip_plan.json',{}).get('clips',[]):
        seconds=float(clip.get('request_seconds')or clip.get('seconds')or 0)
        result.append({'unit_id':clip['clip_id'],'start':cursor,'end':cursor+seconds});cursor+=seconds
    return result


def _number(value,label):
    if type(value)not in {int,float}or not math.isfinite(value):raise ValueError(label+' must be finite')
    return float(value)


def resolve(directory,policy,timeline):
    directory=Path(directory)
    script=read_json(directory/'chapter_script.json',{});plan=read_json(directory/'clip_plan.json',{})
    spans={}
    for row in shot_spans(script,plan,timeline):
        old=spans.get(row['index'])
        spans[row['index']]={'start':min(old['start'],row['start'])if old else row['start'],
                             'end':max(old['end'],row['end'])if old else row['end']}
    if not spans:raise ValueError('Music needs an actual shot timeline before assembly')
    def point(anchor):
        if not isinstance(anchor,dict)or type(anchor.get('shot'))is not int or anchor.get('shot')not in spans:raise ValueError('music anchor requires a current shot')
        span=spans[anchor['shot']];edge=anchor.get('edge')
        if edge not in {'start','end'}:raise ValueError('music anchor edge must be start or end')
        value=span[edge]+_number(anchor.get('offset_seconds',0),'anchor offset')
        if not span['start']-1e-6<=value<=span['end']+1e-6:raise ValueError('music anchor offset leaves its shot')
        return round(value,6)
    cues=[]
    for row in policy.get('cues',[]):
        start,end=point(row['start']),point(row['end'])
        if end<=start:raise ValueError('music cue end must follow start')
        asset=row['asset'];path=Path(asset['path'])
        if asset.get('review',{}).get('status')!='accepted'or not path.is_file():raise ValueError('cue asset needs an accepted review and actual file')
        gain=_number(row.get('gain_db'),'gain');fade_in=_number(row.get('fade_in_seconds',0),'fade');fade_out=_number(row.get('fade_out_seconds',0),'fade')
        if not -36<=gain<=0:raise ValueError('cue gain_db must be between-36 and0')
        if min(fade_in,fade_out)<0 or fade_in+fade_out>end-start+1e-6:raise ValueError('cue fade exceeds its actual duration')
        loop=row.get('loop',False)
        if type(loop)is not bool:raise ValueError('cue loop must be boolean')
        available=float(asset.get('selected_seconds')or media_duration(path))
        if not loop and available+.02<end-start:
            raise ValueError(f'music cue {row["id"]}: selected {available:g}s, but its resolved shot interval needs {end-start:.6f}s '
                             f'({start:.6f} to {end:.6f}). Select a longer source range, or explicitly loop only if consistent with the music design.')
        cues.append({'id':row['id'],'asset_id':asset['id'],'asset_path':str(path),'asset':take_identity(path),
            'start':start,'end':end,'gain_db':gain,'fade_in_seconds':fade_in,'fade_out_seconds':fade_out,'loop':loop})
    if not cues or len({r['id']for r in cues})!=len(cues):raise ValueError('music needs nonempty distinct cue ids')
    silences=[]
    # Source-authored listening gaps cannot be accidentally filled by a later cue edit.
    gaps=[*policy.get('silences',[]),*(script.get('music_plan')or{}).get('silences',[])]
    for row in gaps:
        start,end=point(row['start']),point(row['end'])
        if end<=start:raise ValueError('music silence end must follow start')
        interval={'start':start,'end':end}
        if interval not in silences:silences.append(interval)
    return {'cues':cues,'silences':silences,'duration':max(s['end']for s in spans.values()),'timeline':timeline}


def prepare_bed(directory,report,policy):
    directory=Path(directory);assembly=report.get('assembly')or{}
    state=resolve(directory,policy,assembly.get('timeline',[]))
    folder=directory/'work/music-assets/timeline';folder.mkdir(parents=True,exist_ok=True)
    for record in folder.glob('bed-*.json'):
        if read_json(record,{})==state and record.with_suffix('.wav').is_file():
            return _bed(record.with_suffix('.wav'),state)
    number=len(list(folder.glob('bed-*')))+1;output=folder/f'bed-{number:03d}.wav'
    command=['ffmpeg','-nostdin','-y','-v','error','-threads','4'];filters=[];labels=[]
    duration=state['duration']
    filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[zero]')
    for i,row in enumerate(state['cues']):
        if row['loop']:command+=['-stream_loop','-1']
        command+=['-i',row['asset_path']]
        length=row['end']-row['start']
        chain=f'[{i}:a]atrim=duration={length:.6f},asetpts=PTS-STARTPTS,aresample=48000,volume={row["gain_db"]:g}dB'
        if row['fade_in_seconds']:chain+=f',afade=t=in:st=0:d={row["fade_in_seconds"]:.6f}'
        if row['fade_out_seconds']:chain+=f',afade=t=out:st={length-row["fade_out_seconds"]:.6f}:d={row["fade_out_seconds"]:.6f}'
        chain+=f',adelay={round(row["start"]*1000)}:all=1[cue{i}]';filters.append(chain);labels.append(f'[cue{i}]')
    filters.append('[zero]'+''.join(labels)+f'amix=inputs={len(labels)+1}:duration=first:normalize=0[bus]')
    if state['silences']:
        expression='+'.join(f'between(t,{s["start"]:.6f},{s["end"]:.6f})'for s in state['silences'])
        filters.append(f"[bus]volume=0:enable='{expression}'[out]");label='[out]'
    else:label='[bus]'
    temporary=output.with_suffix('.partial.wav')
    command+=['-filter_complex',';'.join(filters),'-map',label,'-t',str(duration),'-ar','48000','-ac','2','-c:a','pcm_s16le',str(temporary)]
    run(command);temporary.replace(output);atomic_write_json(output.with_suffix('.json'),state)
    return _bed(output,state)


def _bed(path,state):
    return {'asset':{'id':'story-music','path':str(path),'review':{'status':'accepted'}},'gain_db':0,'music_timeline':state}
