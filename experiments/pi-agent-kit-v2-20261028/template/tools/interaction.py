"""Append the series' fixed 3-second interaction page to an existing film.

    python tools/interaction.py request.json
    {"film":"output/film-v11.mp4","output":"output/film-v12.mp4",
     "title":"林野探秘档案","question":"下一站，你想和我去哪里探险？",
     "options":[{"id":"A","label":"A，空椅剧场","reason":"..."}, ...],
     "footer":"评论区留下你的选择","seconds":3}

The film and page are concatenated; the source film is never overwritten. Writes
<output>.interaction.json with question, options and the actual start/end times.
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_FONT = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'


def probe(path):
    return json.loads(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def _wrap(draw, text, font, width):
    lines = []
    for paragraph in str(text).splitlines() or ['']:
        current = ''
        for char in paragraph:
            candidate = current + char
            box = draw.textbbox((0, 0), candidate, font=font)
            if box[2] - box[0] <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
        lines.append(current)
    return lines or ['']


def _centered(draw, text, y, font, fill, width, line_gap=10):
    for line in _wrap(draw, text, font, width):
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((1080 - (box[2] - box[0])) / 2, y), line, font=font, fill=fill)
        y += (box[3] - box[1]) + line_gap
    return y


def render_page(specification, path):
    width, height = 1080, 1920
    margin = 80
    background = Image.new('RGB', (width, height), '#1B2520')
    draw = ImageDraw.Draw(background)
    font_path = specification.get('font') or DEFAULT_FONT
    title_font = ImageFont.truetype(font_path, 40)
    question_font = ImageFont.truetype(font_path, 62)
    label_font = ImageFont.truetype(font_path, 48)
    reason_font = ImageFont.truetype(font_path, 32)
    footer_font = ImageFont.truetype(font_path, 32)
    _centered(draw, specification.get('title', '林野探秘档案'), height * 0.16,
              title_font, '#ADBDA2', width - 2 * margin)
    y = _centered(draw, specification['question'], height * 0.25,
                  question_font, '#F2ECD9', width - 2 * margin) + 50
    for option in specification['options']:
        label = str(option['label'])
        if not label.startswith(option['id']):
            label = f'{option["id"]}  {label}'
        y = _centered(draw, label, y, label_font, '#F2ECD9', width - 2 * margin, 6) + 8
        if option.get('reason'):
            y = _centered(draw, option['reason'], y, reason_font, '#ADBDA2',
                          width - 2 * margin, 4) + 28
        else:
            y += 28
    _centered(draw, specification.get('footer', '评论区留下你的选择'), height * 0.88,
              footer_font, '#ADBDA2', width - 2 * margin)
    background.save(path, 'PNG')


def append_page(film, output, page_png, page_mp4, seconds):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-loop', '1', '-i', str(page_png),
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', str(seconds), '-r', '25',
                    '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-crf', '18',
                    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest', str(page_mp4)],
                   check=True)
    listing = output.with_suffix(output.suffix + '.concat.txt')
    listing.write_text("file '" + str(film).replace("'", "'\\''") + "'\n"
                       "file '" + str(page_mp4).replace("'", "'\\''") + "'\n")
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-c', 'copy', '-movflags', '+faststart', '-y', str(output)], check=True)


def build(specification):
    film = Path(specification['film']).resolve()
    output = Path(specification['output']).resolve()
    if not film.is_file():
        raise FileNotFoundError(film)
    if output == film:
        raise ValueError('Use a new output filename; keep the source film.')
    if output.exists():
        raise FileExistsError(f'Keep the previous output and choose a new name: {output}')
    options = specification.get('options') or []
    if len(options) != 3 or [o.get('id') for o in options] != ['A', 'B', 'C']:
        raise ValueError('Exactly three options with ids A, B, C are required.')
    seconds = float(specification.get('seconds', 3))
    if seconds != 3:
        raise ValueError('This series requires an exactly 3-second interaction page.')
    before = float(probe(film)['format']['duration'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='interaction-') as temporary:
        folder = Path(temporary)
        page_png, page_mp4 = folder / 'page.png', folder / 'page.mp4'
        render_page(specification, page_png)
        append_page(film, output, page_png, page_mp4, seconds)
    after = float(probe(output)['format']['duration'])
    expected = before + seconds
    if after > expected + 0.005:
        trimmed = output.with_suffix(output.suffix + '.trim.mp4')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(output), '-t', f'{expected:.6f}',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                        '-movflags', '+faststart', '-y', str(trimmed)], check=True)
        trimmed.replace(output)
        after = float(probe(output)['format']['duration'])
    metadata = {
        'movie': str(output), 'source_film': str(film),
        'interaction': {
            'question': specification['question'],
            'options': options,
            'selected': None,
            'seconds': seconds,
            'start': round(before, 3),
            'end': round(before + seconds, 3),
        },
        'duration': round(after, 3),
    }
    output.with_suffix(output.suffix + '.interaction.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return metadata


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(build(request), ensure_ascii=False))
