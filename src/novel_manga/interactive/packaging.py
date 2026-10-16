"""Opt-in world introduction and series mark for authored travel episodes."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from novel_manga.util import run


def shift_events(events, cut, seconds):
    """Keep approved scene labels on their footage, excluding the inserted card."""
    shifted=[]
    for event in events:
        start,end=event['start'],event['end']
        if end<=cut:
            shifted.append(dict(event))
        elif start>=cut:
            shifted.append({**event,'start':start+seconds,'end':end+seconds})
        else:
            shifted.extend([{**event,'end':cut},
                            {**event,'start':cut+seconds,'end':end+seconds}])
    return shifted


def world_card_boundary(publication: dict, timeline: list[dict]) -> tuple[float, float]:
    card = publication['world_card']
    row = next(row for row in timeline if row['unit_id'] == card['after_clip_id'])
    # The published body uses 25 fps; align the insertion to a real frame.
    return round(float(row['end']) * 25) / 25, round(float(card['seconds']) * 25) / 25


def _seconds(value: str) -> float:
    hours, minutes, seconds = value.split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _time(value: float) -> str:
    cs = round(value * 100)
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def shift_subtitles(source: Path, target: Path, cut: float, seconds: float) -> None:
    result = []
    for line in source.read_text().splitlines():
        if not line.startswith('Dialogue:'):
            result.append(line)
            continue
        fields = line.split(',', 9)
        start, end = _seconds(fields[1]), _seconds(fields[2])
        spans = [(start, end)] if end <= cut else ([(start + seconds, end + seconds)]
                    if start >= cut else [(start, cut), (cut + seconds, end + seconds)])
        for first, last in spans:
            copy = fields.copy()
            copy[1], copy[2] = _time(first), _time(last)
            result.append(','.join(copy))
    target.write_text('\n'.join(result) + '\n')


def package_body(source: Path, publication: dict, timeline: list[dict], destination: Path,
                 font: Path, width: int, height: int, *, boundary_has_speech: bool) -> tuple[Path, dict]:
    cut, seconds = world_card_boundary(publication, timeline)
    card = publication['world_card']
    image = Image.new('RGB', (width, height), '#172623')
    draw = ImageDraw.Draw(image)
    for text, y, size, color in [
        (card['series_title'] + ' · ' + card['episode'], .23, .044, '#B4C4BA'),
        (card['title'], .37, .10, '#F3EAD6'),
        (card['premise'], .51, .047, '#F3EAD6'),
        (card.get('time_note', ''), .73, .035, '#B4C4BA'),
    ]:
        face = ImageFont.truetype(str(font), round(width * size))
        box = draw.textbbox((0, 0), text, font=face)
        draw.text(((width - box[2] + box[0]) / 2, height * y), text, font=face, fill=color)
    card_path = destination / 'world-introduction.jpeg'
    image.save(card_path, quality=95)
    mark = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(mark).text((width * .055, height * .035), publication.get('watermark', ''),
        font=ImageFont.truetype(str(font), round(width * .029)), fill=(240, 240, 230, 155),
        stroke_width=1, stroke_fill=(0, 0, 0, 90))
    mark_path = destination / 'series-watermark.png'
    mark.save(mark_path)
    # Reusing a spoken cold-open's tail can replay its final syllables over the
    # title. Only a wordless boundary clip can supply the existing ambience tail.
    audio = ('[0:a]asplit=2[a_before][a_after];'
             f'anullsrc=r=48000:cl=stereo,atrim=duration={seconds}[a1];'
             if boundary_has_speech else
             '[0:a]asplit=3[a_before][a_card][a_after];'
             f'[a_card]atrim=start={max(0,cut-.6)}:end={cut},asetpts=PTS-STARTPTS,'
             f'afade=t=out:st=0:d=0.6,apad,atrim=duration={seconds}[a1];')
    graph = (f'[0:v]split=2[before][after];'
        f'[before]trim=end={cut},setpts=PTS-STARTPTS[v0];'
        f'[after]trim=start={cut},setpts=PTS-STARTPTS[v2];'
        f'[1:v]trim=duration={seconds},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v1];'
        + audio +
        f'[a_before]atrim=end={cut},asetpts=PTS-STARTPTS[a0];'
        f'[a_after]atrim=start={cut},asetpts=PTS-STARTPTS[a2];'
        '[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[body][a];'
        '[body][2:v]overlay=0:0:shortest=1[v]')
    body = destination / 'published-body.mp4'
    run(['ffmpeg','-y','-v','error','-threads','4','-i',str(source),'-loop','1','-framerate','25',
         '-i',str(card_path),'-loop','1','-framerate','25','-i',str(mark_path),'-filter_complex',graph,
         '-map','[v]','-map','[a]','-r','25','-c:v','libx264','-preset','fast','-crf','19',
         '-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-b:a','192k',str(body)])
    return body, {'world_card': {**card, 'start':cut, 'end':cut+seconds, 'seconds':seconds},
                  'watermark':publication.get('watermark', ''), 'scene_evidence_overlays':False,
                  'world_card_audio':'silence' if boundary_has_speech else 'wordless_boundary_tail'}
