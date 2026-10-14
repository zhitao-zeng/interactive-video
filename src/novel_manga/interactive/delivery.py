"""Deterministic evidence labels, three-second voting card and measured delivery receipt."""
from pathlib import Path
import json
import shutil
import subprocess
from fractions import Fraction
from PIL import Image, ImageDraw, ImageFont, ImageColor
from novel_manga.util import atomic_write_json, media_duration, run
from novel_manga.review.verdicts import invalid_verdict_ids


def delivery_status(report: dict, technical_passed: bool) -> str:
    return 'accepted' if (technical_passed and report.get('quality_review', {}).get('passed') is True
                          and report.get('assembly', {}).get('thin_passed') is True
                          and not report.get('failed_clips') and not report.get('gate_failed_clips')) else 'candidate'


def interaction_window(body_seconds: float) -> dict:
    return {'start':round(body_seconds, 3), 'end':round(body_seconds+3, 3), 'seconds':3.0}


def video_seconds(stream: dict) -> float:
    return int(stream['nb_frames']) / float(Fraction(stream['r_frame_rate']))


def evidence_events(overlays, script, plan, timeline):
    labels = {row['index']: row['text'] for row in overlays}
    seconds = {s.get('index', i): float(s['duration_seconds']) for i, s in enumerate(script['shots'], 1)}
    clips = {c['clip_id']:c for c in plan['clips']}
    result = []
    for span in timeline:
        clip = clips.get(span['unit_id'])
        if not clip:
            continue
        indexes = clip['shot_indexes']
        total = sum(seconds[i] for i in indexes)
        cursor = float(span['start'])
        for index in indexes:
            end = cursor + (span['end']-span['start'])*seconds[index]/total
            if index in labels:
                result.append({'text':labels[index], 'start':round(cursor,3), 'end':round(end,3)})
            cursor = end
    return result


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
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[]
    for event in events:
        text=event['text'].replace('{','').replace('}','').replace('\n',r'\N')
        lines.append(f'Dialogue: 1,{_ass_time(event["start"])},{_ass_time(event["end"])},Evidence,,0,0,0,,{text}')
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
    for i,option in enumerate(interaction['options']):
        centered(f'{option["id"]}  {option["label"]}',height*(.46+i*.10),56,foreground)
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
    review_path=ctx.episode_dir/'episode_review.json'
    review=json.loads(review_path.read_text()) if review_path.is_file() else {}
    review_errors=invalid_verdict_ids(review.get('clips',{}))
    if review_errors:
        report={**report,'quality_review':{**report.get('quality_review',{}),'passed':False,'content_passed':False,
                                          'review_errors':review_errors}}
    if float(interaction['seconds']) != 3:
        raise ValueError('This release requires a 3-second interaction card')
    events=evidence_events(json.loads((ctx.episode_dir/'overlays.json').read_text()),ctx.script,
                           ctx.clip_plan,assembly.get('timeline',[]))
    overlay=destination/'evidence.ass'
    _overlay_ass(overlay,events,ctx.settings.width,ctx.settings.height)
    body=destination/'body.mp4'
    # cwd-relative fixed filename avoids filesystem path escaping in the filter language.
    subprocess.run(['ffmpeg','-y','-v','error','-threads','4','-i',str(source),'-vf','ass=evidence.ass',
                    '-c:v','libx264','-preset','fast','-crf','19','-r','25','-pix_fmt','yuv420p',
                    '-c:a','aac','-ar','48000','-ac','2','-b:a','192k',str(body)],cwd=destination,check=True)
    card=destination/'interaction.jpeg'
    _card(card,interaction,ctx.settings.font_path,ctx.settings.width,ctx.settings.height)
    tail=destination/'interaction.mp4'
    run(['ffmpeg','-y','-v','error','-threads','4','-loop','1','-i',str(card),
         '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','3','-r','25',
         '-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',str(tail)])
    final=destination/'episode01.mp4'
    # Trim audio padding at each join so the card occupies exactly the final 75 frames.
    body_info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
                         '-show_streams','-of','json',str(body)],text=True))['streams'][0]
    body_seconds=video_seconds(body_info)
    run(['ffmpeg','-y','-v','error','-threads','4','-i',str(body),'-i',str(tail),
         '-filter_complex',f'[0:v]setpts=PTS-STARTPTS[v0];[0:a]atrim=0:{body_seconds},asetpts=PTS-STARTPTS[a0];'
                          '[1:v]trim=duration=3,setpts=PTS-STARTPTS[v1];[1:a]atrim=duration=3,asetpts=PTS-STARTPTS[a1];'
                          '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]',
         '-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','19','-r','25','-pix_fmt','yuv420p',
         '-c:a','aac','-ar','48000','-ac','2','-b:a','192k','-movflags','+faststart',str(final)])
    run(['ffmpeg','-y','-v','error','-i',str(final),'-vn','-c:a','pcm_s16le',str(destination/'episode_audio.wav')])
    ass=Path(assembly.get('ass',''))
    if ass.is_file():
        shutil.copy2(ass,destination/'subtitles.ass')
        run(['ffmpeg','-y','-v','error','-i',str(ass),str(destination/'subtitles.srt')])
    cover=Path(assembly.get('cover',''))
    if cover.is_file():shutil.copy2(cover,destination/'cover.jpeg')
    decode=subprocess.run(['ffmpeg','-v','error','-i',str(final),'-f','null','-'],capture_output=True,text=True)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)],text=True))
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    duration=video_seconds(video)
    technical=(decode.returncode==0 and not decode.stderr.strip() and video['width']==ctx.settings.width and video['height']==ctx.settings.height
               and video['codec_name']=='h264' and video['r_frame_rate']=='25/1' and audio['codec_name']=='aac'
               and 180<=duration<=300 and int(video['nb_frames'])-int(body_info['nb_frames'])==75)
    planned_body=sum(float(c['request_seconds']) for c in ctx.clip_plan['clips'])
    receipt={'status':delivery_status(report,technical),'final_video':str(final),'duration':duration,
             'technical_passed':technical,'content_passed':report.get('quality_review',{}).get('content_passed',False),
             'production_media_passed':assembly.get('thin_passed',False),
             'production_media_issues':[k for k,v in (assembly.get('media_qc') or {}).get('checks',{}).items()
                                        if isinstance(v,dict) and v.get('passed') is False],
             'interaction':{**interaction,**interaction_window(body_seconds)},'evidence_events':events,
             'planned_body_seconds':planned_body,'actual_body_seconds':body_seconds,
             'body_duration_delta':round(body_seconds-planned_body,3),'interaction_frames':75,
             'review_errors':review_errors,
             'overlay_timing':'authored timing scaled to actual compiled clip spans',
             'audio':'native mixed episode audio, not isolated vocals',
             'source_report':str(ctx.episode_dir/'thin_media_report.json')}
    atomic_write_json(destination/'interactions.json',receipt['interaction'])
    atomic_write_json(destination/'delivery.json',receipt)
    return receipt
