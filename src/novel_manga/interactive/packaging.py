"""Opt-in world introduction and series mark for authored travel episodes."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


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


def prepare_publication(publication: dict, timeline: list[dict], destination: Path,
                        font: Path, width: int, height: int, *, boundary_has_speech: bool) -> dict:
    """Prepare the existing card/mark and receipt without encoding the movie."""
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
    return {'world_card': {**card, 'start':cut, 'end':cut+seconds, 'seconds':seconds},
            'watermark':publication.get('watermark', ''), 'scene_evidence_overlays':False,
            'world_card_audio':'silence' if boundary_has_speech else 'wordless_boundary_tail'}


def publication_graph(publication: dict, card_input: int, mark_input: int) -> str:
    """Insert the card into filtered body_video/body_audio in the final encode."""
    cut=publication['world_card']['start'];seconds=publication['world_card']['seconds']
    cut_frames=round(cut*25);card_frames=round(seconds*25)
    # Reusing a spoken cold-open's tail can replay its final syllables over the
    # title. Only a wordless boundary clip can supply the existing ambience tail.
    audio = ('[body_audio]asplit=2[pub_a_before][pub_a_after];'
             f'anullsrc=r=48000:cl=stereo,atrim=duration={seconds}[pub_a1];'
             if publication['world_card_audio']=='silence' else
             '[body_audio]asplit=3[pub_a_before][pub_a_card][pub_a_after];'
             f'[pub_a_card]atrim=start={max(0,cut-.6)}:end={cut},asetpts=PTS-STARTPTS,'
             f'afade=t=out:st=0:d=0.6,apad,atrim=duration={seconds}[pub_a1];')
    return ('[body_video]split=2[pub_before][pub_after];'
        f'[pub_before]trim=end_frame={cut_frames},setpts=PTS-STARTPTS[pub_v0];'
        f'[pub_after]trim=start_frame={cut_frames},setpts=PTS-STARTPTS[pub_v2];'
        f'[{card_input}:v]trim=end_frame={card_frames},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[pub_v1];'
        + audio +
        f'[pub_a_before]atrim=end={cut},asetpts=PTS-STARTPTS[pub_a0];'
        f'[pub_a_after]atrim=start={cut},asetpts=PTS-STARTPTS[pub_a2];'
        '[pub_v0][pub_a0][pub_v1][pub_a1][pub_v2][pub_a2]concat=n=3:v=1:a=1[pub_body][published_audio];'
        f'[pub_body][{mark_input}:v]overlay=0:0:shortest=1[published_video]')
