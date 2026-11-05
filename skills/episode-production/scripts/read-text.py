"""Read one bounded UTF-8 page or Markdown section without changing the source."""
from pathlib import Path
import argparse
import json
import re


MAX_BYTES = 6000


def read_page(path, *, start=0, section=None):
    text = Path(path).read_text(encoding='utf-8')
    if section:
        headings = list(re.finditer(r'^(#{1,6})\s+(.+?)\s*$', text, re.M))
        selected = next((i for i, m in enumerate(headings) if m.group(2) == section), None)
        if selected is None:
            raise ValueError(f'Markdown section not found: {section}')
        heading = headings[selected]
        end = next((m.start() for m in headings[selected + 1:]
                    if len(m.group(1)) <= len(heading.group(1))), len(text))
        text = text[heading.start():end]
    if not 0 <= start <= len(text):
        raise ValueError('start must be a character offset in the selected text')
    chars, size = [], 0
    for char in text[start:]:
        width = len(char.encode('utf-8'))
        if size + width > MAX_BYTES:
            break
        chars.append(char)
        size += width
    end = start + len(chars)
    return {'path': str(path), 'section': section, 'start': start, 'end': end,
            'total': len(text), 'complete': end == len(text), 'text': ''.join(chars)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--section')
    args = parser.parse_args()
    print(json.dumps(read_page(args.path, start=args.start, section=args.section), ensure_ascii=False))


if __name__ == '__main__':
    main()
