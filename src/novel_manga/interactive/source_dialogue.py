"""Verbatim dialogue from an explicitly selected Markdown screenplay source."""
from itertools import zip_longest
import re


def extract_markdown_dialogue(text):
    """Read every **speaker：** line, including unnamed/offscreen source roles."""
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r'\s*\*\*([^*：:]+)[：:]\*\*\s*(\S.*)', line)
        if match:
            rows.append({'speaker_name': match[1].strip(), 'text': match[2].strip(),
                         'source_line': number})
    if not rows:
        raise ValueError('Selected dialogue source has no **speaker：** dialogue lines')
    return rows


def dialogue_issues(shots, source_rows):
    # Character ownership permits a long original utterance to span two shots.
    expected = [(row['speaker_name'], char, number, row['text'])
                for number, row in enumerate(source_rows, 1)
                for char in row['text'] if not char.isspace()]
    actual = [(turn.get('speaker_name'), char, shot.get('index', '?'), number, turn.get('text', ''))
              for shot in shots for number, turn in enumerate(shot.get('turns', []), 1)
              for char in turn.get('text', '') if not char.isspace()]
    for position, (wanted, found) in enumerate(zip_longest(expected, actual), 1):
        if wanted is not None and found is not None and wanted[:2] == found[:2]:
            continue
        source = (f'source turn {wanted[2]} {wanted[0]}: {wanted[3][:40]!r}'
                  if wanted else 'end of source dialogue')
        candidate = (f'shot {found[2]} turn {found[3]} {found[0]}: {found[4][:40]!r}'
                     if found else 'end of candidate dialogue')
        return [f'source dialogue mismatch at character {position}: expected {source}; '
                f'actual {candidate}. Preserve every speaker, word and order; '
                'splitting an original turn across shots is allowed']
    return []
