"""Composite authored evidence on observed prop planes, after full episode production."""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
import re
import subprocess
import copy
from PIL import Image, ImageDraw, ImageFont
from novel_manga.util import atomic_write_json, read_json


def _solve(matrix, values):
    rows=[list(row)+[value] for row,value in zip(matrix,values)]
    for col in range(len(values)):
        pivot=max(range(col,len(values)),key=lambda i:abs(rows[i][col]))
        rows[col],rows[pivot]=rows[pivot],rows[col]
        if abs(rows[col][col])<1e-9:raise ValueError('Degenerate evidence plane')
        divisor=rows[col][col];rows[col]=[v/divisor for v in rows[col]]
        for i in range(len(values)):
            if i!=col:
                weight=rows[i][col];rows[i]=[a-weight*b for a,b in zip(rows[i],rows[col])]
    return tuple(row[-1] for row in rows)


def validated_region(region):
    """The actual geometry model sometimes flattens four point pairs into eight numbers."""
    result=copy.deepcopy(region)
    def quad(value):
        if len(value)==8 and all(isinstance(v,(int,float)) for v in value):
            value=[value[i:i+2] for i in range(0,8,2)]
        if (len(value)!=4 or any(not isinstance(p,(list,tuple)) or len(p)!=2
                or any(not isinstance(v,(int,float)) or not 0<=v<=1000 for v in p) for p in value)):
            raise ValueError('Invalid observed prop/occlusion corners')
        return value
    result['quad']=quad(result['quad'])
    occlusions=[]
    for polygon in result.get('occlusions',[]):
        if len(polygon)==4 and all(isinstance(v,(int,float)) for v in polygon):
            x1,y1,x2,y2=polygon
            if x2<=x1 or y2<=y1:raise ValueError('Invalid observed occlusion box')
            polygon=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        occlusions.append(quad(polygon))
    result['occlusions']=occlusions
    for anchor in result.get('anchors',[]):
        point=anchor['point']
        if len(point)!=2 or any(not isinstance(v,(int,float)) or not 0<=v<=1000 for v in point):
            raise ValueError('Invalid observed pointing anchor')
    return result


def paste_surface(frame, layer, region):
    """Normalized visible prop corners in TL, TR, BR, BL order; keep hands in front."""
    width,height=frame.size
    quad=[(x*width/1000,y*height/1000) for x,y in region['quad']]
    corners=[(0,0),(layer.width,0),(layer.width,layer.height),(0,layer.height)]
    matrix=[];values=[]
    for (x,y),(u,v) in zip(quad,corners):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]])
        values.extend([u,v])
    warped=layer.transform(frame.size,Image.Transform.PERSPECTIVE,_solve(matrix,values),Image.Resampling.BICUBIC)
    mask=Image.new('L',frame.size,0);draw=ImageDraw.Draw(mask)
    draw.polygon(quad,fill=255)
    for polygon in region.get('occlusions',[]):
        draw.polygon([(x*width/1000,y*height/1000) for x,y in polygon],fill=0)
    # Multiply plane/occlusion mask by the layer's own transparent regions.
    from PIL import ImageChops
    warped.putalpha(ImageChops.multiply(warped.getchannel('A'),mask))
    return Image.alpha_composite(frame.convert('RGBA'),warped).convert('RGB')


def _local_anchors(region,size):
    width,height=size
    corners=[(0,0),(width,0),(width,height),(0,height)]
    matrix=[];values=[]
    for (x,y),(u,v) in zip(region['quad'],corners):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]])
        values.extend([u,v])
    a,b,c,d,e,f,g,h=_solve(matrix,values)
    result={}
    for anchor in region.get('anchors',[]):
        x,y=anchor['point'];denominator=g*x+h*y+1
        u,v=(a*x+b*y+c)/denominator,(d*x+e*y+f)/denominator
        if not 0<=u<=width or not 0<=v<=height:raise ValueError('Anchor outside observed prop plane')
        result[anchor['label']]=(u,v)
    return result


def surface_image(cue,font,region=None):
    size=(800,500);kind=cue['kind']
    background={'old_map':(207,184,132,255),'map_note':(207,184,132,255),
                'new_map':(213,224,207,255),'nodes':(190,192,174,255),
                'notebook':(226,220,200,255),'text_panel':(232,230,215,255)}.get(kind,(0,0,0,0))
    image=Image.new('RGBA',size,background);draw=ImageDraw.Draw(image)
    ink=(31,37,29,255)
    def text(words,center,y,size):
        face=ImageFont.truetype(str(font),size)
        box=draw.textbbox((0,0),words,font=face)
        draw.text((center-(box[2]-box[0])/2,y),words,font=face,fill=ink)
    if kind=='new_map':
        # An unlabelled terrain screen: no invented places, GPS values or hidden routes.
        for i in range(7):
            draw.arc((30+i*30,15+i*22,760-i*20,475-i*20),25,300,fill=(153,174,146,255),width=3)
    elif kind=='old_map':
        anchors=_local_anchors(region or {},size)
        if '青溪村' not in anchors:raise ValueError('Missing observed village pointing anchor')
        village=anchors['青溪村']
        points=[(120,120),(350,95),(635,100),(200,265),(505,235),(115,380),(635,385)]
        points=sorted(points,key=lambda p:math.dist(p,village),reverse=True)[:6]+[village]
        for x,y in points:draw.ellipse((x-8,y-8,x+8,y+8),fill=ink)
        x,y=village;text('青溪村',max(95,min(705,x)),min(425,y+20),50)
    elif kind=='nodes':
        anchors=_local_anchors(region or {},size)
        if set(anchors)!={'谷底站','山腰入口'}:raise ValueError('Missing observed station pointing anchors')
        for label,(x,y) in anchors.items():
            draw.ellipse((x-12,y-12,x+12,y+12),fill=ink)
            text(label,max(120,min(680,x)),min(425,y+30),58)
        # Deliberately no connecting line: the story has not confirmed that the routes connect.
    elif kind=='text_panel':
        lines=cue['text'].split('\n')
        size=min(60, int(720/max(1,max(map(len,lines)))))
        line_height=size+30
        start=(500-line_height*len(lines))/2
        for i,line in enumerate(lines):text(line,400,start+i*line_height,size)
    elif kind=='notebook':
        for i,line in enumerate(cue['text'].split('\n')):text(line,400,40+i*100,46)
    else:
        text(cue['text'],400,170,72 if len(cue['text'])<6 else 60)
    return image


def regions_at(observed,time):
    """Interpolate only between observations inside the same actual camera shot."""
    cuts=[0,*observed.get('cuts',[]),observed['duration']]
    segment=max(0,bisect.bisect_right(cuts,time)-1)
    if segment>=len(cuts)-1:return []
    frames=[f for f in observed['frames'] if cuts[segment]<=f['time']<cuts[segment+1]]
    if not frames:return []
    times=[f['time'] for f in frames];right=bisect.bisect_left(times,time)
    if right<len(frames) and abs(times[right]-time)<1e-6:return frames[right]['regions']
    if right==0 or right==len(frames):return []
    first,last=frames[right-1],frames[right]
    ratio=(time-first['time'])/(last['time']-first['time'])
    a={r['cue_id']:r for r in first['regions']};b={r['cue_id']:r for r in last['regions']}
    result=[]
    for key in a.keys()&b.keys():
        old_occlusions=a[key].get('occlusions',[]);new_occlusions=b[key].get('occlusions',[])
        if len(old_occlusions)!=len(new_occlusions):continue
        masks=[];reliable=True
        for old,new in zip(old_occlusions,new_occlusions):
            ax0,ax1=min(p[0] for p in old),max(p[0] for p in old)
            ay0,ay1=min(p[1] for p in old),max(p[1] for p in old)
            bx0,bx1=min(p[0] for p in new),max(p[0] for p in new)
            by0,by1=min(p[1] for p in new),max(p[1] for p in new)
            if min(ax1,bx1)<max(ax0,bx0) or min(ay1,by1)<max(ay0,by0):
                reliable=False;break
            # The swept box protects both sampled positions and the motion between them.
            x0,x1=min(ax0,bx0),max(ax1,bx1);y0,y1=min(ay0,by0),max(ay1,by1)
            masks.append([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
        if not reliable:continue
        old_anchors={r['label']:r['point'] for r in a[key].get('anchors',[])}
        new_anchors={r['label']:r['point'] for r in b[key].get('anchors',[])}
        if old_anchors.keys()!=new_anchors.keys():continue
        result.append({**a[key],'occlusions':masks,
            'anchors':[{'label':label,'point':[x+(new_anchors[label][0]-x)*ratio,y+(new_anchors[label][1]-y)*ratio]}
                       for label,(x,y) in old_anchors.items()],
            'quad':[[x+(u-x)*ratio,y+(v-y)*ratio] for (x,y),(u,v) in zip(a[key]['quad'],b[key]['quad'])]})
    return result


def evidence_complete(cues,coverage):
    return all(coverage.get(cue['id'],0)>=1 for cue in cues)


def _media(video):
    result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],text=True))
    stream=next(s for s in result['streams'] if s['codec_type']=='video')
    return stream,float(stream.get('duration') or result['format']['duration'])


def _cuts(video):
    proc=subprocess.run(['ffmpeg','-v','info','-threads','2','-i',str(video),'-an',
        '-vf',"scale=216:-2,select='gt(scene,0.30)',showinfo",'-f','null','-'],capture_output=True,text=True,check=True)
    return sorted(set(float(t) for t in re.findall(r'pts_time:([0-9.]+)',proc.stderr)))


def observe_clip(video,shots,cues,directory):
    from novel_manga.llm.client import ask_json,image_part,obj
    from novel_manga.review.endpoints import judge_settings
    directory.mkdir(parents=True,exist_ok=True)
    stat=video.stat();identity={'video':str(video),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'shots':shots,'cues':cues}
    path=directory/'observations.json';saved=read_json(path,{})
    if saved.get('input')==identity:
        for frame in saved.get('frames',[]):
            frame['regions']=[validated_region(r) for r in frame['regions']]
        return saved
    _,duration=_media(video);cuts=[t for t in _cuts(video) if 0<t<duration]
    interval=.25 if any(c['kind'] in {'old_map','nodes','map_note','notebook'} for c in cues) else 1
    times=sorted(set(round(t,3) for t in [.08,max(.08,duration-.08),
         *[i*interval for i in range(1,math.ceil(duration/interval))],
         *[max(0,t-.08) for t in cuts],*[min(duration-.04,t+.08) for t in cuts]] if 0<=t<duration))
    parts=[]
    for i,time in enumerate(times):
        frame=directory/f'frame-{i:03d}.jpeg'
        subprocess.run(['ffmpeg','-y','-v','error','-threads','2','-ss',str(time),'-i',str(video),
            '-frames:v','1','-vf','scale=432:-2',str(frame)],check=True)
        parts.extend([{'type':'text','text':f'frame_index={i}; seconds={time}'},image_part(frame,768)])
    point={'type':'array','items':{'type':'number','minimum':0,'maximum':1000},'minItems':2,'maxItems':2}
    quad={'type':'array','items':point,'minItems':4,'maxItems':4}
    labels=sorted({label for cue in cues for label in cue.get('anchor_labels',[])}) or ['none']
    region=obj({'cue_id':{'type':'string','enum':[c['id'] for c in cues] or ['none']},'quad':quad,
                'occlusions':{'type':'array','items':quad},
                'anchors':{'type':'array','items':obj({'label':{'type':'string','enum':labels},'point':point})}})
    frame_schema=obj({'frame_index':{'type':'integer'},'shot_index':{'type':['integer','null'],'enum':[*[s['index'] for s in shots],None]},
                      'regions':{'type':'array','items':region}})
    schema=obj({'frames':{'type':'array','items':frame_schema,'minItems':len(times),'maxItems':len(times)}})
    parts.insert(0,{'type':'text','text':
        'Inspect actual frames for postproduction geometry, not artistic quality. '
        'Match each frame to the supplied authored shot by visible composition and action, never by planned seconds. '
        'Use null if its intended shot is absent or cannot be identified. Return no regions when the required physical object is absent, '
        'too small, folded away, or cannot support legible text. For each applicable cue give the visible physical surface corners '
        'in TL, TR, BR, BL order, with x/y normalized 0..1000. Do not box the full picture or draw a subtitle box. '
        'Include visible hands/objects that occlude the plane as tight axis-aligned boxes in TL,TR,BR,BL order; sort hands left to right. '
        'For each anchor_labels cue, locate the actual node/point that is labelled or indicated by the authored pointing sequence. '
        'Use the actual visible node and fingertip targets, never a default layout. Associate station names using the supplied first/second '
        'pointing sequence when visible. If the named targets cannot be reliably identified, omit the entire region. '
        'A cue may appear only during its listed shot_ids. Background signs need not be redrawn in phone shots. '
        'Return one entry per frame_index, in the supplied order. Do not fabricate map geography or infer hidden plot.\n'
        +json.dumps({'shots':shots,'cues':cues},ensure_ascii=False)})
    result={'input':identity,'duration':duration,'cuts':cuts,'frames':[],'error':'','unresolved_anchors':[]}
    try:
        answer=ask_json(parts,schema,name='adventure_evidence_geometry',max_tokens=max(3000,len(times)*300),
                        timeout=300,settings=judge_settings(),json_object=True)
        rows=answer.get('frames',[])
        if [r.get('frame_index') for r in rows]!=list(range(len(times))):raise ValueError('Missing/reordered actual frame observations')
        lookup={s['index']:s['shot_id'] for s in shots};cue_lookup={c['id']:c for c in cues}
        for time,row in zip(times,rows):
            index=row.get('shot_index')
            if index is not None and index not in lookup:raise ValueError('Observation outside clip source shots')
            accepted=[]
            for region in row.get('regions',[]):
                region=validated_region(region)
                cue=cue_lookup[region['cue_id']]
                if lookup.get(index) not in cue['shot_ids']:raise ValueError('Surface inferred before/after its observed shot')
                if len(region['quad'])!=4 or any(len(p)!=2 or any(not 0<=v<=1000 for v in p) for p in region['quad']):
                    raise ValueError('Invalid observed prop corners')
                if {a['label'] for a in region.get('anchors',[])}!=set(cue.get('anchor_labels',[])):
                    result['unresolved_anchors'].append({'time':time,'cue_id':cue['id']});continue
                accepted.append(region)
            result['frames'].append({'time':time,'shot_index':index,'regions':accepted})
    except Exception as error:
        result['frames']=[];result['error']=f'{type(error).__name__}: {str(error)[:250]}'
    atomic_write_json(path,result)
    return result


def observed_shot_spans(observed,offset):
    frames=observed['frames'];result=[]
    for frame,next_frame in zip(frames,frames[1:]):
        index=frame['shot_index'];left,right=frame['time'],next_frame['time']
        if index is None or next_frame['shot_index']!=index:continue
        if any(left<t<right for t in observed['cuts']):continue
        if result and result[-1]['index']==index and abs(result[-1]['end']-(offset+left))<.001:
            result[-1]['end']=round(offset+right,3)
        else:result.append({'index':index,'start':round(offset+left,3),'end':round(offset+right,3)})
    return result


def composite_evidence(source,output,observations,cues,font):
    stream,_=_media(source);size=(stream['width'],stream['height']);width,height=size
    cue_lookup={c['id']:c for c in cues}
    layers={c['id']:surface_image(c,font) for c in cues if c['kind'] not in {'nodes','old_map'}}
    coverage={c['id']:0 for c in cues}
    spans=sorted(observations,key=lambda s:s['start']);starts=[s['start'] for s in spans]
    error_path=output.with_suffix('.encode.log')
    with error_path.open('w') as log:
        decode=subprocess.Popen(['ffmpeg','-v','error','-threads','2','-i',str(source),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE,stderr=log)
        encode=subprocess.Popen(['ffmpeg','-y','-v','error','-threads','4','-f','rawvideo','-pix_fmt','rgb24',
            '-s',f'{width}x{height}','-r','25','-i','pipe:0','-i',str(source),'-map','0:v:0','-map','1:a:0',
            '-c:v','libx264','-threads','4','-preset','fast','-crf','19','-pix_fmt','yuv420p','-c:a','copy',str(output)],stdin=subprocess.PIPE,stderr=log)
        try:
            index=0
            while raw:=decode.stdout.read(width*height*3):
                if len(raw)!=width*height*3:raise ValueError('Incomplete decoded frame')
                time=index/25;position=bisect.bisect_right(starts,time)-1
                if position>=0 and time<spans[position]['end']:
                    regions=regions_at(spans[position]['observed'],time-spans[position]['start'])
                    if regions:
                        frame=Image.frombytes('RGB',size,raw)
                        for region in regions:
                            cue=cue_lookup[region['cue_id']]
                            layer=surface_image(cue,font,region) if cue['kind'] in {'nodes','old_map'} else layers[cue['id']]
                            frame=paste_surface(frame,layer,region);coverage[region['cue_id']]+=1/25
                        raw=frame.tobytes()
                encode.stdin.write(raw);index+=1
            encode.stdin.close()
            if decode.wait()!=0 or encode.wait()!=0:raise RuntimeError('Evidence composite encoding failed; see local encode log')
        finally:
            # Closing both pipe ends releases ffmpeg workers blocked on IO after
            # a geometry exception. A terminate followed by an unbounded wait
            # deadlocked the real 1080x1920 episode before this cleanup.
            for pipe in (decode.stdout,encode.stdin):
                if not pipe.closed:
                    try:pipe.close()
                    except BrokenPipeError:pass
            for process in (decode,encode):
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    return output,{k:round(v,3) for k,v in coverage.items()}


def prepare_evidence(ctx,report,destination):
    manifest=read_json(ctx.episode_dir/'evidence_surfaces.json',{})
    cues=manifest['surfaces'];shots={s['index']:s for s in ctx.script['shots']}
    plans={c['clip_id']:c for c in ctx.clip_plan['clips']}
    selected={r['clip_id']:r.get('selected') or {} for r in report.get('clips',[])}
    folder=destination/'postproduction';folder.mkdir(parents=True,exist_ok=True)
    observations=[];shot_spans=[];errors={}
    for span in report['assembly']['timeline']:
        cid=span['unit_id'];clip=plans.get(cid)
        if not clip:continue
        video=Path(selected.get(cid,{}).get('video',''))
        if not video.is_file():errors[cid]='No adopted video';continue
        indexes=clip['shot_indexes'];source=[{k:shots[i][k] for k in ['index','shot_id','visual_prompt','motion_prompt','camera']} for i in indexes]
        applicable=[c for c in cues if set(c['shot_ids']).intersection(s['shot_id'] for s in source)]
        observed=observe_clip(video,source,applicable,folder/cid)
        if observed['error']:errors[cid]=observed['error']
        observations.append({**span,'observed':observed})
        shot_spans.extend(observed_shot_spans(observed,span['start']))
    body,coverage=composite_evidence(Path(report['assembly']['final_video']),destination/'evidence-body.mp4',observations,cues,ctx.settings.font_path)
    missing=[c['id'] for c in cues if coverage.get(c['id'],0)<1]
    observed_pairs={(r['cue_id'],next(s['shot_id'] for s in ctx.script['shots'] if s['index']==f['shot_index']))
                    for span in observations for f in span['observed']['frames'] if f['shot_index'] is not None
                    for r in f['regions']}
    missing_shots=[f'{c["id"]}@{sid}' for c in cues for sid in c.get('required_shots',[])
                   if (c['id'],sid) not in observed_pairs]
    result={'passed':not errors and not missing_shots and evidence_complete(cues,coverage),'body':str(body),'coverage_seconds':coverage,
            'missing_surfaces':missing,'errors':errors,'shot_spans':shot_spans,
            'missing_required_shots':missing_shots,
            'timing_method':'Conservative first-to-last observed intervals (0.25s for handled paper/panels, 1s otherwise), plus actual scene cuts; no extrapolation',
            'geometry_method':'Observed prop planes and pointing anchors; swept foreground masks within a camera shot; untrackable intervals omitted',
            'requires_final_visual_review':True}
    atomic_write_json(destination/'evidence-review.json',result)
    return result
