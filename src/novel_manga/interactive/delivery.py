"""Deterministic evidence labels, three-second voting card and measured delivery receipt."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from novel_manga.media.quality import inspect_media
from fractions import Fraction
from PIL import Image, ImageDraw, ImageFont, ImageColor
from novel_manga.util import atomic_write_json, media_duration, run, read_json
from novel_manga.review.verdicts import invalid_verdict_ids


def delivery_status(report: dict, technical_passed: bool) -> str:
    return 'accepted' if (technical_passed and report.get('quality_review', {}).get('passed') is True
                          and report.get('assembly', {}).get('thin_passed') is True
                          and not report.get('failed_clips') and not report.get('gate_failed_clips')) else 'candidate'


def interaction_window(body_seconds: float) -> dict:
    return {'start':round(body_seconds, 3), 'end':round(body_seconds+3, 3), 'seconds':3.0}


def video_seconds(stream: dict) -> float:
    return int(stream['nb_frames']) / float(Fraction(stream['r_frame_rate']))


def cfr_body_frames(stream: dict) -> int:
    """Cover the video timeline at 25 fps, excluding container/audio padding.

    The native join can be VFR even when r_frame_rate advertises 25. Its stored
    frame count is not the number of frames after CFR delivery conversion.
    """
    rate=Fraction(stream['r_frame_rate'])
    average=Fraction(stream.get('avg_frame_rate') or stream['r_frame_rate'])
    if rate==average==25 and stream.get('nb_frames') not in (None,'N/A'):
        return int(stream['nb_frames'])
    if stream.get('duration_ts') is not None and stream.get('time_base'):
        seconds=Fraction(stream['duration_ts'])*Fraction(stream['time_base'])
    elif stream.get('duration') not in (None,'N/A'):
        seconds=Fraction(stream['duration'])
    else:
        seconds=Fraction(int(stream['nb_frames']),1)/rate
    return (seconds*25).__ceil__()


def technical_result(ctx, movie: Path, body_frames: int) -> tuple[float, bool]:
    decode=subprocess.run(['ffmpeg','-v','error','-i',str(movie),'-f','null','-'],capture_output=True,text=True)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(movie)],text=True))
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    duration=video_seconds(video)
    passed=(decode.returncode==0 and not decode.stderr.strip() and video['width']==ctx.settings.width and video['height']==ctx.settings.height
            and video['codec_name']=='h264' and video['r_frame_rate']=='25/1' and audio['codec_name']=='aac'
            and ((ctx.episode_dir/'authored_groups.json').is_file() or 180<=duration<=300)
            and int(video['nb_frames'])-body_frames==75)
    return duration,passed


def requested_sampling(provider,profile):
    # The framed H3 adapter owns the local sampler; its inherited image/video
    # profile resolution is the hosted lane's tier, not the H3 short edge.
    sampling=provider.sampling_parameters() if hasattr(provider,'sampling_parameters') else {}
    edge=sampling.get('short_edge') or getattr(provider,'short_edge',None)
    label=f'{edge}P' if edge else profile.get('resolution','720p')
    return label,not edge and label=='480p'


def evidence_events(overlays, script, plan, timeline, *, shot_spans=None):
    labels = {row['index']: row for row in overlays}
    if shot_spans is not None:
        return [{**{k:v for k,v in labels[span['index']].items() if k in {'text','placement'}},
                 'start':span['start'], 'end':span['end']}
                for span in shot_spans if span['index'] in labels]
    from novel_manga.story.timing import shot_spans as actual_shot_spans
    result = []
    for span in actual_shot_spans(script,plan,timeline):
        index=span['index']
        if index in labels:
            event={'text':labels[index]['text'],'start':round(span['start'],3),'end':round(span['end'],3)}
            if labels[index].get('placement'):event['placement']=labels[index]['placement']
            result.append(event)
    return result


def ambience_window(index, script, plan, timeline):
    """Use three seconds inside an authored silent shot, never a spoken tail."""
    shot = next(s for s in script['shots'] if s['index'] == index)
    if any(t.get('delivery_mode') != 'silent_action' for t in shot.get('turns', [])):
        raise ValueError('Interaction ambience must come from a silent shot')
    span = evidence_events([{'index':index,'text':''}], script, plan, timeline)
    if not span or span[0]['end'] - span[0]['start'] < 3:
        raise ValueError('Silent ambience shot has fewer than three delivered seconds')
    row=span[0]
    return row['start'] + min(1.0, row['end']-row['start']-3.0), 3.0



def _ass_time(seconds):
    centis = round(seconds*100)
    return f'{centis//360000}:{centis//6000%60:02}:{centis//100%60:02}.{centis%100:02}'


def _overlay_ass(path: Path, events, width: int, height: int):
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Evidence,Noto Sans CJK SC,42,&H00E5F3F1,&H00FFFFFF,&H0021251C,&H8021251C,-1,0,0,0,100,100,0,0,3,12,0,8,80,80,150,1
Style: EvidencePanel,Noto Sans CJK SC,{min(round(width*.055),round(height*.04))},&H00E5F3F1,&H00FFFFFF,&H0021251C,&H0021251C,-1,0,0,0,100,100,0,0,3,18,0,2,80,80,{round(height*.25)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[]
    for event in events:
        text=event['text'].replace('{','').replace('}','').replace('\n',r'\N')
        style='EvidencePanel' if event.get('placement')=='panel' else 'Evidence'
        lines.append(f'Dialogue: 1,{_ass_time(event["start"])},{_ass_time(event["end"])},{style},,0,0,0,,{text}')
    path.write_text(header+'\n'.join(lines)+'\n')


def _card(path: Path, interaction: dict, font: Path, width: int, height: int):
    theme=interaction.get('theme',{})
    background=ImageColor.getrgb(theme.get('background','#1B2520'))
    foreground=ImageColor.getrgb(theme.get('foreground','#F2ECD9'))
    secondary=ImageColor.getrgb(theme.get('secondary','#ADBDA2'))
    image=Image.new('RGB',(width,height),background);draw=ImageDraw.Draw(image)
    def centered(text, y, size, fill):
        face=ImageFont.truetype(str(font),size)
        box=draw.textbbox((0,0),text,font=face)
        draw.text(((width-(box[2]-box[0]))/2,y),text,font=face,fill=fill)
    centered(interaction.get('series_title','林野探秘档案'),height*.20,42,secondary)
    centered(interaction['question'],height*.32,68,foreground)
    complete = any(option.get('reason') for option in interaction['options'])
    for i,option in enumerate(interaction['options']):
        y=height*(.46+i*(.15 if complete else .10))
        centered(f'{option["id"]}  {option["label"]}',y,56,foreground)
        if option.get('reason'):
            centered(option['reason'],y+80,38,secondary)
    centered('评论区留下你的选择',height*.84,38,secondary)
    image.save(path,quality=95)


def deliver(runner, report: dict, destination: Path) -> dict:
    ctx=runner.context
    assembly=report.get('assembly') or {}
    source=Path(assembly.get('final_video') or '')
    destination.mkdir(parents=True,exist_ok=True)
    if not source.is_file():
        receipt={'status':'incomplete','reason':'No assembled episode',
                 'failed_clips':report.get('failed_clips',[]),'blocked_clips':report.get('blocked_clips',{}),
                 'report':str(ctx.episode_dir/'thin_media_report.json')}
        atomic_write_json(destination/'delivery.json',receipt)
        return receipt
    interaction=json.loads((ctx.episode_dir/'interaction.json').read_text())
    if interaction.get('art_direction'):
        from .interaction_card import prepare as prepare_artwork
        from novel_manga.application.configuration import project_root
        prepare_artwork(project_root(),ctx.novel_dir,interaction['art_direction'])
        interaction=read_json(ctx.episode_dir/'interaction.json')
    review_path=ctx.episode_dir/'episode_review.json'
    review=json.loads(review_path.read_text()) if review_path.is_file() else {}
    review_errors=invalid_verdict_ids(review.get('clips',{}))
    if review_errors:
        report={**report,'quality_review':{**report.get('quality_review',{}),'passed':False,'content_passed':False,
                                          'review_errors':review_errors}}
    if float(interaction['seconds']) != 3:
        raise ValueError('This release requires a 3-second interaction card')
    policy_path=ctx.episode_dir/'delivery_policy.json'
    policy=json.loads(policy_path.read_text()) if policy_path.is_file() else {}
    extra_overlays=policy.get('extra_visual_overlays',True)
    postproduction = None
    if extra_overlays and (ctx.episode_dir/'evidence_surfaces.json').is_file():
        from novel_manga.interactive.evidence import prepare_evidence
        try:
            manifest=json.loads((ctx.episode_dir/'evidence_surfaces.json').read_text())
            if manifest and manifest['surfaces']:
                postproduction=prepare_evidence(ctx,report,destination)
                source=Path(postproduction['body'])
        except Exception as error:
            postproduction={'passed':False, 'shot_spans':[],
                            'errors':{'composite':f'{type(error).__name__}: {str(error)[:250]}'}}
            atomic_write_json(destination/'evidence-review.json',postproduction)
    events=evidence_events(json.loads((ctx.episode_dir/'overlays.json').read_text()) if extra_overlays else [],ctx.script,
                           ctx.clip_plan,assembly.get('timeline',[]),
                           shot_spans=postproduction['shot_spans'] if postproduction is not None else None)
    overlay=destination/'evidence.ass'
    if extra_overlays:_overlay_ass(overlay,events,ctx.settings.width,ctx.settings.height)
    # cwd-relative fixed filename avoids filesystem path escaping in the filter language.
    filters='ass=evidence.ass' if extra_overlays else 'ass=subtitles.ass'
    ass=Path(assembly.get('ass',''))
    audio_source=source
    if not extra_overlays:
        # The pre-caption join has native audio, no loudness processing or scene overlays.
        source=ctx.work/'joined.mp4'
        audio_source=source
        if not source.is_file() or not ass.is_file():
            raise FileNotFoundError('Clean delivery requires the native joined video and dialogue subtitles')
        if assembly.get('audio_repair') or assembly.get('episode_ambience_mix') or assembly.get('episode_music_mix'):
            audio_source=Path(assembly['final_video'])
        shutil.copy2(ass,destination/'subtitles.ass')
    if postproduction is not None and ass.is_file():
        shutil.copy2(ass,destination/'subtitles.ass')
        filters+=',ass=subtitles.ass'
    source_info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                         '-show_streams','-of','json',str(source)],text=True))['streams'][0]
    source_frames=cfr_body_frames(source_info)
    source_seconds=source_frames/25
    body_frames=source_frames
    publication_path=ctx.episode_dir/'publication.json'
    publication=None
    if publication_path.is_file():
        from novel_manga.interactive.packaging import prepare_publication
        publication_config=json.loads(publication_path.read_text())
        boundary=next(c for c in ctx.clip_plan['clips'] if c['clip_id']==publication_config['world_card']['after_clip_id'])
        boundary_has_speech=any(t.get('text') for s in ctx.script['shots'] if s['index'] in boundary['shot_indexes']
                                for t in s.get('turns',[]))
        publication=prepare_publication(publication_config,assembly.get('timeline',[]),
                                    destination,ctx.settings.font_path,ctx.settings.width,ctx.settings.height,
                                    boundary_has_speech=boundary_has_speech)
        body_frames+=round(publication['world_card']['seconds']*25)
        from novel_manga.interactive.packaging import shift_events
        events=shift_events(events,publication['world_card']['start'],publication['world_card']['seconds'])
    card=destination/'interaction.jpeg'
    if interaction.get('artwork'):
        from .interaction_card import render as render_artwork
        render_artwork(card,interaction,ctx.novel_dir,ctx.settings.width,ctx.settings.height)
    else:
        _card(card,interaction,ctx.settings.font_path,ctx.settings.width,ctx.settings.height)
    tail=destination/'interaction.mp4'
    tail_audio=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    ambience_source=None
    if policy.get('interaction_ambience'):
        from novel_manga.application.repair.ambience import validate
        asset=policy['interaction_ambience']
        independent_audio=validate(asset)
        ambient=destination/'interaction-ambience.wav'
        run(['ffmpeg','-y','-v','error','-i',str(independent_audio),'-t','3',
             '-ar','48000','-ac','2','-c:a','pcm_s16le',str(ambient)])
        ambience_source={**asset,'kind':'independent_asset','exported_audio':str(ambient),
                         'clip_id':next((c['clip_id'] for c in ctx.clip_plan['clips']
                            if interaction.get('ambience_shot_index') in c.get('shot_indexes',[])),None)}
        tail_audio=['-i',str(ambient)]
    elif interaction.get('ambience_shot_index'):
        start,length=ambience_window(interaction['ambience_shot_index'],ctx.script,ctx.clip_plan,assembly.get('timeline',[]))
        source_start=start
        if publication and start>=publication['world_card']['start']:
            start+=publication['world_card']['seconds']
        ambience_source={'start':start,'end':start+length,
                         'clip_id':next(c['clip_id'] for c in ctx.clip_plan['clips']
                                        if interaction['ambience_shot_index'] in c.get('shot_indexes',[]))}
        ambient=destination/'interaction-ambience.wav'
        run(['ffmpeg','-y','-v','error','-ss',str(source_start),'-i',str(audio_source),'-t',str(length),
             '-vn','-ar','48000','-ac','2','-c:a','pcm_s16le',str(ambient)])
        tail_audio=['-i',str(ambient)]
    run(['ffmpeg','-y','-v','error','-threads','4','-loop','1','-i',str(card),
         *tail_audio,'-t','3','-r','25',
         '-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',str(tail)])
    final=destination/'episode01.mp4'
    packaging=policy.get('packaging') or {}
    watermark=packaging.get('watermark')
    if watermark:(destination/'watermark.txt').write_text(watermark['text'])
    body_seconds=body_frames/25
    inputs=['-i',str(source)];audio_index=0
    if audio_source!=source:
        inputs+=['-i',str(audio_source)];audio_index=1
    tail_index=audio_index+1
    inputs+=['-i',str(tail)]
    # Normalize the actual video timeline before burning source-time captions.
    # Retain a final partial VFR frame; never use AAC/container padding as footage.
    graph=(f'[0:v]setpts=PTS-STARTPTS,fps=25:round=up,tpad=stop_mode=clone:stop_duration=0.04,'
           f'trim=end_frame={source_frames},format=yuv420p,setsar=1,{filters}[body_video];'
           f'[{audio_index}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=end={source_seconds},'
           f'asetpts=PTS-STARTPTS,apad,atrim=end={source_seconds}[body_audio];')
    body_video='body_video';body_audio='body_audio'
    if publication:
        from novel_manga.interactive.packaging import publication_graph
        inputs+=['-loop','1','-framerate','25','-i',str(destination/'world-introduction.jpeg'),
                 '-loop','1','-framerate','25','-i',str(destination/'series-watermark.png')]
        graph+=publication_graph(publication,tail_index+1,tail_index+2)+';'
        body_video='published_video';body_audio='published_audio'
    # Trim audio padding at each join so the card occupies exactly the final 75 frames.
    graph+=(f'[{body_video}]setpts=PTS-STARTPTS[v0];'
                          f'[{body_audio}]atrim=0:{body_seconds},asetpts=PTS-STARTPTS[a0];'
                          f'[{tail_index}:v]trim=end_frame=75,setpts=PTS-STARTPTS[v1];'
                          f'[{tail_index}:a]atrim=duration=3,asetpts=PTS-STARTPTS[a1];'
                          f'[v0][a0][v1][a1]concat=n=2:v=1:a=1[{"plain" if watermark else "v"}][a]')
    if watermark:
        graph+=f';[plain]drawtext=fontfile={watermark["fontfile"]}:textfile={destination/"watermark.txt"}:'
        graph+=':'.join(f'{k}={watermark[k]}' for k in ('fontsize','fontcolor','borderw','bordercolor','x','y'))+'[v]'
    subprocess.run(['ffmpeg','-y','-v','error','-threads','4',*inputs,
         '-filter_complex',graph,
         '-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','19','-r','25','-pix_fmt','yuv420p',
         '-c:a','aac','-ar','48000','-ac','2','-b:a','192k','-movflags','+faststart',str(final)],cwd=destination,check=True)
    run(['ffmpeg','-y','-v','error','-i',str(final),'-vn','-c:a','pcm_s16le',str(destination/'episode_audio.wav')])
    ass=Path(assembly.get('ass',''))
    if ass.is_file():
        if publication:
            from novel_manga.interactive.packaging import shift_subtitles
            shift_subtitles(ass,destination/'subtitles.ass',publication['world_card']['start'],publication['world_card']['seconds'])
        else:
            shutil.copy2(ass,destination/'subtitles.ass')
        ass=destination/'subtitles.ass'
        run(['ffmpeg','-y','-v','error','-i',str(ass),str(destination/'subtitles.srt')])
    cover=Path(assembly.get('cover',''))
    if cover.is_file():shutil.copy2(cover,destination/'cover.jpeg')
    if packaging.get('cover'):shutil.copy2(packaging['cover'],destination/'cover.jpeg')
    elif publication:shutil.copy2(destination/'world-introduction.jpeg',destination/'cover.jpeg')
    duration,technical=technical_result(ctx,final,body_frames)
    # Clean delivery uses the native join, and publication inserts its own cards.
    # The assembly's normalized audio is a different track, not this film's QC.
    media_report=destination/'media-quality.json'
    delivered_media=inspect_media(final,destination/'cover.jpeg',card,destination/'subtitles.ass',
                                  ctx.settings,media_report,silent_outro_seconds=3,
                                  subtitles_required=any(t.get('text') for s in ctx.script['shots']
                                                         for t in s.get('turns',[])))
    delivered_report={**report,'assembly':{**assembly,'thin_passed':delivered_media['passed']},
                      'quality_review':{**report.get('quality_review',{}),
                          'passed':report.get('quality_review',{}).get('content_passed') is True
                                   and delivered_media['passed'] is True}}
    planned_body=sum(float(c['request_seconds']) for c in ctx.clip_plan['clips'])
    receipt={'status':delivery_status(delivered_report,technical),'final_video':str(final),'duration':duration,
             'technical_passed':technical,'content_passed':report.get('quality_review',{}).get('content_passed',False),
             'production_media_passed':delivered_media['passed'],
             'production_media_report':str(media_report),
             'source_assembly_media_passed':assembly.get('thin_passed',False),
             'production_media_issues':[k for k,v in delivered_media.get('checks',{}).items()
                                        if isinstance(v,dict) and v.get('passed') is False],
             'interaction':{**interaction,**interaction_window(body_seconds)},'ambience_source':ambience_source,'evidence_events':events,
             'planned_body_seconds':planned_body,'actual_body_seconds':body_seconds,
             'body_duration_delta':round(body_seconds-planned_body,3),'interaction_frames':75,
             'review_errors':review_errors,
             'overlay_timing':'authored timing scaled to actual compiled clip spans',
             'audio':'native mixed episode audio, not isolated vocals',
             'delivery_policy':{'extra_visual_overlays':extra_overlays,'dialogue_subtitles':True,'interaction_card':True},
             'source_report':str(ctx.episode_dir/'thin_media_report.json'),'packaging':packaging}
    if policy.get('episode_music'):
        receipt['music_selection']=policy['episode_music']
        from novel_manga.application.music_selection import selected_assets
        assets=selected_assets(policy['episode_music'])
        credits=destination/'music-credits.txt'
        credits.write_text('\n\n'.join(asset.get('attribution')or'\n'.join(str(asset.get(k)or'')for k in ('title','artist','source_url','license_id','license_url'))for asset in assets)+'\n\nActual edits:\n'+json.dumps({k:policy['episode_music'].get(k)for k in ('source_interval','cues','silences')},ensure_ascii=False)+'\n')
        receipt['music_credits']=str(credits)
    if publication:
        receipt['publication']=publication
        receipt['planned_body_seconds']+=publication['world_card']['seconds']
        receipt['body_duration_delta']=round(body_seconds-receipt['planned_body_seconds'],3)
    if not extra_overlays:
        receipt['overlay_timing']='disabled by user; dialogue subtitles and interaction card retained'
        receipt['audio_level_processing']=False
    if postproduction is not None:
        native=[]
        for row in report.get('clips',[]):
            selected=row.get('selected') or {}
            video_path=Path(selected.get('video',''))
            if not video_path.is_file():continue
            streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                                 '-show_streams','-of','json',str(video_path)],text=True))['streams']
            native.append({'clip_id':row['clip_id'],'width':streams[0]['width'],'height':streams[0]['height'],
                           'fps':streams[0]['r_frame_rate']})
        requested,is_trial=requested_sampling(ctx.provider,ctx.profile)
        receipt.update(postproduction=postproduction,
                       overlay_timing=postproduction.get('timing_method','observations unavailable'),
                       native_sampling={'requested':requested,'observed':native,
                                        'trial':is_trial,
                                        'export':f'{ctx.settings.width}x{ctx.settings.height}',
                                        'note':'Delivery resize does not increase native generation detail.'})
        # The native take's verdict does not certify newly composited evidence.
        receipt['status']='candidate'
        receipt['final_evidence_review_pending']=True
    from novel_manga.story.voice_delivery import episode_native_speech_errors
    speech_errors=episode_native_speech_errors(ctx.episode_dir,report)
    if speech_errors:
        receipt.update(status='candidate',content_passed=False,speech_policy_errors=speech_errors)
    from novel_manga.application.music_selection import episode_music_errors
    music_errors=episode_music_errors(ctx.episode_dir,report)
    if music_errors:receipt.update(status='candidate',content_passed=False,music_selection_errors=music_errors)
    atomic_write_json(destination/'interactions.json',receipt['interaction'])
    atomic_write_json(destination/'delivery.json',receipt)
    return receipt


def reviewed_delivery(runner,report,destination):
    """Export the actual captions/card first; return its failures to the shared loop."""
    from novel_manga.application.review.final_cut import review_assembly, blocking_issues
    from novel_manga.util import read_json
    destination=Path(destination)
    pending={**report,'quality_review':{**report.get('quality_review',{}),'passed':False,'content_passed':False}}
    inputs=export_context(runner,report)
    receipt=read_json(destination/'delivery.json',{})
    if receipt.get('export_context')!=inputs or not Path(receipt.get('final_video','')).is_file():
        versions=destination/'versions';versions.mkdir(parents=True,exist_ok=True)
        version=versions/f'export-{len(list(versions.glob("export-*")))+1:03d}'
        receipt=deliver(runner,pending,version)
        receipt['export_context']=inputs
    elif receipt.get('technical_passed') is False:
        # Recheck a cached failure under the current rules without re-encoding its movie.
        _,receipt['technical_passed']=technical_result(runner.context,Path(receipt['final_video']),
            round(receipt['actual_body_seconds']*25))
    if not receipt.get('final_video'):
        return {'movie':str(Path(destination)/'episode01.mp4'),'complete':False,'passed':False,
                'issues':[],'frames':[],'errors':['No current assembled movie available for delivered review']}
    # Final acceptance belongs to finish_reviewed_delivery after visual and audio gates.
    receipt.update(status='candidate',content_passed=False)
    final=review_assembly(runner,report,receipt=receipt)
    final['final_audio_review_required'] = True
    receipt['final_audio_review_required'] = True
    if final.get('complete') is True and final.get('passed') is True and not blocking_issues(final.get('issues') or []) and not final.get('errors'):
        movie = Path(final.get('movie') or '')
        try:
            from novel_manga.application.review.final_audio import review_audio
            from novel_manga.application.music_selection import configuration
            code_root = Path(__file__).resolve().parents[3]
            runtime_root = code_root if (code_root/'.runtime/music-selection/runtime.json').is_file() else code_root.parent/'main'
            configured = configuration(runtime_root)
            runtime = {key: configured[key] for key in ('python','audio_source_root','audio_model_dir')}
            audio = review_audio(code_root, runner.context.episode_dir, movie, runtime=runtime)
        except Exception as error:
            from novel_manga.service_errors import service_failure
            audio = {'movie':str(movie.resolve()) if movie else '', 'complete':False, 'passed':False,
                     'issues':[], 'errors':[f'{type(error).__name__}: {error}'], 'coverage':[],
                     **service_failure(error)}
        final['audio_review'] = audio
        receipt['final_audio_review'] = audio
        receipt['final_audio_review_passed'] = bool(audio.get('movie') == final.get('movie')
            and audio.get('complete') is True and audio.get('passed') is True
            and not audio.get('issues') and not audio.get('errors'))
        if not receipt['final_audio_review_passed']:
            receipt['status'] = 'candidate'
            receipt['final_audio_review_errors'] = audio.get('errors') or audio.get('issues') or [
                'final audio report is incomplete or bound to a different movie']
    else:
        receipt['final_audio_review_passed'] = False
        receipt['final_audio_review_errors'] = ['full movie visual review must pass before final audio acceptance']
    receipt.update(final_cut_review_passed=final.get('passed') is True,
                   content_review_status='completed' if final.get('complete') else 'incomplete',
                   final_cut_review=str(runner.context.episode_dir/'final_cut_review.json'))
    atomic_write_json(Path(destination)/'delivery.json',receipt)
    atomic_write_json(Path(destination)/'final-cut-review.json',final)
    return final


def export_context(runner,report):
    """Reuse a movie only while its assembly, captions and packaging inputs agree."""
    from novel_manga.application.review.final_cut import context
    from novel_manga.review.storage import take_identity
    from novel_manga.util import read_json
    ctx=runner.context
    inputs=context(ctx.episode_dir,report,(report.get('assembly') or {}).get('final_video',''))
    inputs.pop('native_judgments',None)
    inputs['assembly']=report.get('assembly')
    inputs['delivery_inputs']={name:read_json(ctx.episode_dir/name,{}) for name in
                              ('interaction.json','delivery_policy.json','overlays.json','evidence_surfaces.json','publication.json')}
    inputs['captions']=take_identity(Path((report.get('assembly') or {}).get('ass','')))
    inputs['frame']=[ctx.settings.width,ctx.settings.height,str(ctx.settings.font_path)]
    return inputs


def record_native_publication(runner,report,destination,previous_inputs):
    """Rebind cached export inputs after the publisher's unchanged native-movie copy."""
    from novel_manga.application.review.final_cut import current, blocking_issues
    from novel_manga.util import read_json
    path=Path(destination)/'delivery.json';receipt=read_json(path,{})
    final=report.get('final_cut_review')or{}
    if (receipt.get('export_context')==previous_inputs and receipt.get('final_video')==final.get('movie')
            and current(runner.context.episode_dir,report,final)):
        receipt['export_context']=export_context(runner,report)
        atomic_write_json(path,receipt)


def finish_reviewed_delivery(runner,report,destination):
    """Close the receipt for the exact reviewed movie without encoding it again."""
    from novel_manga.application.review.final_cut import current, blocking_issues
    from novel_manga.util import read_json
    destination=Path(destination);receipt=read_json(destination/'delivery.json',{})
    final=report.get('final_cut_review') or {}
    valid=(receipt.get('export_context')==export_context(runner,report)
           and receipt.get('final_video')==final.get('movie')
           and current(runner.context.episode_dir,report,final)
           and final.get('complete') is True and final.get('passed') is True
           and not blocking_issues(final.get('issues') or []) and not final.get('errors'))
    audio_review = (report.get('final_audio_review') or final.get('audio_review')
                    or receipt.get('final_audio_review') or {})
    audio_required = (report.get('final_audio_review_required') is True
                      or final.get('final_audio_review_required') is True
                      or receipt.get('final_audio_review_required') is True)
    audio_valid = not audio_required
    if audio_required:
        from novel_manga.application.review.final_audio import freshness as _audio_freshness
        audio_valid = (audio_review.get('movie') == final.get('movie')
            and audio_review.get('complete') is True and audio_review.get('passed') is True
            and not audio_review.get('issues') and not audio_review.get('errors')
            and _audio_freshness(runner.context, audio_review, final.get('movie')).get('fresh') is True)
    valid = valid and audio_valid
    if audio_required:
        receipt['final_audio_review'] = audio_review
        receipt['final_audio_review_passed'] = bool(audio_valid)
        if not audio_valid:
            receipt['final_audio_review_errors'] = audio_review.get('errors') or audio_review.get('issues') or ['final audio review is incomplete or bound to a different movie']
        else:
            receipt.pop('final_audio_review_errors', None)
    from novel_manga.story.voice_delivery import episode_native_speech_errors
    speech_errors=episode_native_speech_errors(runner.context.episode_dir,report)
    from novel_manga.application.music_selection import episode_music_errors
    music_errors=episode_music_errors(runner.context.episode_dir,report)
    if music_errors:
        receipt['music_selection_errors']=music_errors;valid=False
    else:receipt.pop('music_selection_errors',None)
    if speech_errors:
        receipt['speech_policy_errors']=speech_errors
        valid=False
    else:receipt.pop('speech_policy_errors',None)
    movie = Path(receipt.get('final_video') or final.get('movie') or '')
    cover = movie.parent/'cover.jpeg'
    frozen = receipt.get('accepted_export') or {}
    if frozen:
        current_hashes = {
            'video_sha256': hashlib.sha256(movie.read_bytes()).hexdigest() if movie.is_file() else '',
            'cover_sha256': hashlib.sha256(cover.read_bytes()).hexdigest() if cover.is_file() else '',
        }
        if any(frozen.get(key) != value for key, value in current_hashes.items()):
            valid = False
    if not cover.is_file():
        valid = False
    receipt['content_passed']=bool(valid and report.get('quality_review',{}).get('content_passed'))
    receipt['final_cut_review_passed']=bool(valid)
    receipt['status']=delivery_status(report,receipt.get('technical_passed') is True) if valid else 'candidate'
    receipt['episode_dir'] = str(runner.context.episode_dir)
    receipt['run_dir'] = str(getattr(runner.context, 'run_dir', ''))
    receipt['acceptance_protocol'] = 'current-export-v1'
    if receipt['status'] == 'accepted':
        receipt['accepted_export'] = {
            'movie': str(movie),
            'video_sha256': hashlib.sha256(movie.read_bytes()).hexdigest(),
            'cover_sha256': hashlib.sha256(cover.read_bytes()).hexdigest(),
            'visual_context': final.get('context'),
            'audio_movie': audio_review.get('movie'),
        }
    if final.get('complete'):receipt['final_evidence_review_pending']=False
    atomic_write_json(destination/'delivery.json',receipt)
    return receipt
