"""Adopt a reviewed independent ambience file without changing any video request."""
from pathlib import Path
from novel_manga.util import run
import shutil
import wave
from novel_manga.util import atomic_write_json, read_json


def validate(entry):
    review=entry.get('review')or{}
    asr=review.get('ASR')or{}
    if (review.get('speech_free') is not True or not (review.get('method') or asr.get('backend'))
            or str(asr.get('hypothesis')or'').strip()):
        raise ValueError('ambience needs actual speech-free evidence and review method')
    path=Path(entry['path'])
    with wave.open(str(path),'rb') as stream:
        seconds=stream.getnframes()/stream.getframerate()
    if seconds<3:raise ValueError('ambience audio has fewer than three seconds')
    return path


def adopt(directory,clip,decision):
    catalog=read_json(directory/'repair_candidates.json',{})
    entry=next((a for a in catalog.get('audio',[]) if a['id']==decision.get('candidate_id')),None)
    if entry is None:raise ValueError('audio candidate is not registered')
    source=validate(entry)
    target_kind=decision.get('target') or ('interaction' if (directory/'interaction.json').is_file() else 'clip')
    if target_kind not in {'interaction','clip'}:raise ValueError('audio target must be interaction or clip')
    if target_kind=='interaction' and not (directory/'interaction.json').is_file():
        raise ValueError('interaction audio requires an existing interaction card')
    if target_kind=='clip':_silent_clip(directory,clip)
    policy=read_json(directory/'delivery_policy.json',{})
    target=directory/'work/ambience-assets'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()!=target.resolve():shutil.copy2(source,target)
    value={**entry,'path':str(target.resolve()),'source_path':str(source.resolve())}
    previous=policy.get('interaction_ambience') if target_kind=='interaction' else (policy.get('clip_ambience')or{}).get(clip['clip_id'])
    if previous==value:raise ValueError('ambience candidate already adopted')
    if target_kind=='interaction':policy['interaction_ambience']=value
    else:policy.setdefault('clip_ambience',{})[clip['clip_id']]=value
    atomic_write_json(directory/'delivery_policy.json',policy)
    return {'assembly_changed':True,'audio_asset':value,'changed':[]}


def _apply_clip_ambience(directory,report,*,context=None):
    """Replace only authored silent clips in a novel assembly; copy video unchanged."""
    import copy
    from novel_manga.review.storage import take_identity
    policy=read_json(directory/'delivery_policy.json',{}).get('clip_ambience')or{}
    if not policy:return report
    assembly=report.get('assembly')or{}
    if not assembly.get('final_video') or not Path(assembly['final_video']).is_file():return report
    previous=assembly.get('audio_repair')or{}
    source=Path(previous.get('source')or (assembly.get('episode_ambience_mix')or{}).get('source')or assembly.get('final_video',''))
    signature={'source':str(source),'source_take':take_identity(source),'policy':policy,
               'assets':{key:take_identity(validate(asset)) for key,asset in policy.items()},
               'timeline':assembly.get('timeline',[])}
    if previous.get('inputs')==signature and Path(assembly.get('final_video','')).is_file():
        if context is not None and not previous.get('qc_checked'):
            result=copy.deepcopy(report);_refresh_checks(context,result['assembly']);return result
        return report
    plan=read_json(directory/'clip_plan.json',{})
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    spans={s['unit_id']:s for s in assembly.get('timeline',[])}
    graph=[];command=['ffmpeg','-y','-v','error','-threads','4','-i',str(source)];base='0:a';labels=[]
    for number,(cid,asset) in enumerate(policy.items(),1):
        if cid not in clips or cid not in spans:raise ValueError('ambience target missing from assembly')
        _silent_clip(directory,clips[cid])
        start,end=float(spans[cid]['start']),float(spans[cid]['end'])
        if end<=start:raise ValueError('ambience target has no actual duration')
        path=validate(asset);command+=['-stream_loop','-1','-i',str(path)]
        graph.append(f"[{base}]volume=0:enable='between(t,{start},{end})'[base{number}]")
        base=f'base{number}'
        graph.append(f'[{number}:a]atrim=duration={end-start},asetpts=PTS-STARTPTS,aresample=48000,adelay={round(start*1000)}:all=1[room{number}]')
        labels.append(f'[room{number}]')
    graph.append(f'[{base}]'+''.join(labels)+f'amix=inputs={len(labels)+1}:duration=first:normalize=0[audio]')
    # Retain an immutable pre-edit movie for restart/cache and normal controlled publication.
    folder=directory/'repair_candidate';folder.mkdir(parents=True,exist_ok=True)
    version=len(list(folder.glob('audio-assembly-*.mp4')))+1
    if not previous:
        saved=folder/f'audio-source-{version:03d}.mp4';shutil.copy2(source,saved)
        source=saved;command[command.index('-i')+1]=str(source)
        signature.update(source=str(source),source_take=take_identity(source))
    output=folder/f'audio-assembly-{version:03d}.mp4'
    command+=['-filter_complex',';'.join(graph),'-map','0:v:0','-map','[audio]','-c:v','copy','-c:a','aac','-b:a','192k',str(output)]
    run(command)
    result=copy.deepcopy(report);result['assembly'].update(final_video=str(output),
        pending_publish=True,thin_passed=False,media_qc_passed=False,
        media_qc={'passed':False,'checks':{},'reason':'Changed audio requires current media checks'},
        audio_repair={'source':str(source),'inputs':signature,'video_reencoded':False,'qc_checked':False})
    if context is not None:_refresh_checks(context,result['assembly'])
    return result


def _refresh_checks(context,assembly):
    from novel_manga.media.quality import inspect_media
    from novel_manga.application.profiles import media_qc_ignores,MAX_HOLD_SECONDS
    output=Path(assembly['final_video'])
    required=any(c.get('spoken_text') or c.get('lines') for c in context.clip_plan.get('clips',[]))
    required|=any(t.get('text') and t.get('delivery_mode') in {'visible_dialogue','offscreen_dialogue','singing'}
                  for shot in context.script.get('shots',[]) for t in shot.get('turns',[]))
    qc=inspect_media(output,Path(assembly.get('cover','')),Path(assembly.get('ending','')),
        Path(assembly.get('ass','')),context.settings,output.with_suffix('.media-qc.json'),
        silent_outro_seconds=assembly.get('silent_outro_seconds',0),
        ignore_checks=tuple(media_qc_ignores(context.novel_dir,context.episode_dir)),
        subtitles_required=bool(required or assembly.get('subtitle_events')))
    freeze=float(qc.get('checks',{}).get('long_freeze',{}).get('detail',{}).get('max_freeze_seconds',0))
    other=[row.get('passed') for key,row in qc.get('checks',{}).items() if key!='long_freeze']
    assembly.update(media_qc=qc,media_qc_passed=qc.get('passed') is True,max_hold_seconds=freeze,
                    thin_passed=bool(other) and all(other) and freeze<=MAX_HOLD_SECONDS)
    if assembly.get('audio_repair'):assembly['audio_repair']['qc_checked']=True
    if assembly.get('episode_ambience_mix'):assembly['episode_ambience_mix']['qc_checked']=True
    if assembly.get('episode_music_mix'):assembly['episode_music_mix']['qc_checked']=True


def _silent_clip(directory,clip):
    if any(str(row.get('text')or'').strip() and row.get('delivery_mode')!='silent_action' for row in clip.get('lines',[])):
        raise ValueError('ambience cannot replace a clip with bound dialogue')
    script=read_json(directory/'chapter_script.json',{})
    shots=[s for i,s in enumerate(script.get('shots',[]),1) if s.get('index',i) in clip.get('shot_indexes',[])]
    if not shots or any(any(t.get('delivery_mode')!='silent_action' for t in s.get('turns',[])) for s in shots):
        raise ValueError('clip ambience requires source silent_action and no dialogue')


def apply_assembly(directory,report,*,context=None):
    """Reapply adopted layers from their retained source, without stacking old mixes."""
    import copy
    assembly=report.get('assembly')or{};music=assembly.get('episode_music_mix')or{}
    if music and assembly.get('final_video')==music.get('output'):
        report=copy.deepcopy(report);report['assembly']['final_video']=music.get('input_source',music['source'])
    report=_apply_clip_ambience(directory,report,context=context)
    policy=read_json(directory/'delivery_policy.json',{});bed=policy.get('episode_ambience')
    current=(report.get('assembly')or{}).get('final_video')
    if not current or not Path(current).is_file():return report
    if bed:report=_mix_episode(directory,report,bed,context=context)
    if policy.get('episode_music'):
        music=policy['episode_music']
        if 'cues'in music:
            from novel_manga.application.music_timeline import prepare_bed
            music=prepare_bed(directory,report,music)
        report=_mix_episode(directory,report,music,context=context,music=True)
    return report


def _mix_episode(directory,report,bed,*,context=None,music=False):
    import copy,math
    from novel_manga.review.storage import take_identity
    if music:
        if bed['asset'].get('review',{}).get('status')!='accepted':raise ValueError('music needs an accepted Agent review')
        asset=Path(bed['asset']['path'])
        if not asset.is_file():raise ValueError('adopted music asset is missing')
    else:asset=validate(bed['asset'])
    key='episode_music_mix'if music else'episode_ambience_mix'
    prefix='episode-music'if music else'episode-ambience'
    gain=bed['gain_db']
    if type(gain)not in {int,float} or not math.isfinite(gain) or not -36<=gain<=0:
        raise ValueError('environment bed gain_db must be between -36 and 0')
    assembly=report.get('assembly')or{};current=Path(assembly.get('final_video')or'')
    if not current.is_file():raise ValueError('assemble the native episode before mixing an environmental bed')
    previous=assembly.get(key)or{}
    same_input=(str(current)==previous.get('input_source')and take_identity(current)==previous.get('input_take'))
    reuse_source=bool(previous and (str(current)==previous.get('output')or same_input))
    source=Path(previous['source'])if reuse_source else current
    signature={'source':str(source),'source_take':take_identity(source),'asset':take_identity(asset),
               'asset_id':bed['asset']['id'],'gain_db':gain,'timeline':assembly.get('timeline',[])}
    if music:signature['processing']='music-ebur128-reference--5-v1'
    if bed.get('music_timeline')is not None:signature['music_timeline']=bed['music_timeline']
    if previous.get('inputs')==signature and Path(previous.get('output','')).is_file():
        result=copy.deepcopy(report);result['assembly']['final_video']=previous['output']
        if context is not None and not previous.get('qc_checked'):_refresh_checks(context,result['assembly'])
        return result
    folder=directory/'repair_candidate';folder.mkdir(parents=True,exist_ok=True)
    version=len(list(folder.glob(prefix+'-*.mp4')))+1
    # Revisions always start from an immutable pre-bed movie, never stack another bed.
    if not reuse_source:
        saved=folder/f'{prefix}-source-{version:03d}.mp4';shutil.copy2(source,saved);source=saved
        signature.update(source=str(source),source_take=take_identity(source))
    from novel_manga.util import media_duration
    duration=media_duration(source);output=folder/f'{prefix}-{version:03d}.mp4'
    if music and bed.get('music_timeline')is None:
        level=f'loudnorm=I=-5:TP=-1.5:LRA=11,volume={gain:g}dB,aresample=48000'
    else:level=f'volume={gain:g}dB'
    graph=(f'[1:a]aresample=48000,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,{level}[bed];'
           '[0:a][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.98:level=false[audio]')
    loop=[]if bed.get('music_timeline')is not None else['-stream_loop','-1']
    command=['ffmpeg','-y','-v','error','-threads','4','-i',str(source),*loop,'-i',str(asset),
             '-filter_complex',graph,'-map','0:v:0','-map','[audio]','-c:v','copy','-c:a','aac','-b:a','192k',
             '-t',f'{duration:.6f}','-movflags','+faststart',str(output)]
    run(command)
    result=copy.deepcopy(report);result['assembly'].update(final_video=str(output),pending_publish=True,
        thin_passed=False,media_qc_passed=False,
        media_qc={'passed':False,'checks':{},'reason':'Changed audio requires current media checks'})
    result['assembly'][key]={'source':str(source),'output':str(output),'inputs':signature,'video_reencoded':False,
            'input_source':previous.get('input_source',str(current))if str(current)==previous.get('output')else str(current),
            'input_take':previous.get('input_take')if str(current)==previous.get('output')else take_identity(current),
            'original_audio':'retained additively','gain_db':gain,'qc_checked':False,'command':command,
            **({'target_lufs':-5+gain,'loudness_reference_lufs':-5}if music and bed.get('music_timeline')is None else{})}
    if context is not None:_refresh_checks(context,result['assembly'])
    return result
