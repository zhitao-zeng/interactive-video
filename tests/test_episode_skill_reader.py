"""Long UTF-8 source text remains complete when read through bounded pages."""
import json
from pathlib import Path
import runpy


READER = Path(__file__).resolve().parents[1]/'skills/episode-production/scripts/read-text.py'


def test_reader_pages_reconstruct_long_chinese_paragraphs_without_gaps(tmp_path):
    reader = runpy.run_path(str(READER))
    source = tmp_path/'scene.md'
    text = ('一个很长的原文段落，包含“对白”、湿痕与转角。😀' * 450) + '\n末尾事实仍须保留。'
    source.write_text(text)
    cursor, parts = 0, []
    while True:
        page = reader['read_page'](source, start=cursor)
        assert len(page['text'].encode('utf-8')) <= 6000
        assert page['end'] > cursor
        assert len(json.dumps(page, ensure_ascii=False).encode('utf-8')) < 10000
        parts.append(page['text'])
        if page['complete']:
            break
        cursor = page['end']
    assert ''.join(parts) == text
    assert source.read_text() == text


def test_reader_named_section_keeps_children_and_stops_at_next_peer(tmp_path):
    reader = runpy.run_path(str(READER))
    source = tmp_path/'camera.md'
    source.write_text('# 摄影\n## 1. 推近\n其他\n## 2. 低位跟拍\n路径\n### 停点\n保持\n## 3. 拉焦\n下一段\n')
    page = reader['read_page'](source, section='2. 低位跟拍')
    assert '路径' in page['text'] and '### 停点' in page['text']
    assert '其他' not in page['text'] and '下一段' not in page['text']
    assert page['complete'] is True


def test_installed_reader_is_recognized_by_actual_native_read_evidence(tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace
    from novel_manga.application.agents.episode.inputs import _install_skill
    from novel_manga.interactive.creative_contract import read_evidence
    work = tmp_path/'work'
    source = work/'input/story.md'; source.parent.mkdir(parents=True)
    source.write_text('旅人停在转角前，摄影机随之停住。')
    (work/'input/manifest.json').write_text(json.dumps({'required_reads':['input/story.md'],
                                                       'skill_references':{}}))
    skill, _ = _install_skill(SimpleNamespace(work_dir=work))
    helper = (work/skill).parent/'scripts/read-text.py'
    command = subprocess.run([sys.executable, str(helper), str(source)], capture_output=True, text=True, check=True)
    attempt = work/'attempts/native'; attempt.mkdir(parents=True)
    event = {'type':'item.completed','item':{'type':'command_execution','exit_code':0,
        'command':'python3 /work/.claude/skills/episode-production/scripts/read-text.py /work/input/story.md',
        'aggregated_output':command.stdout}}
    (attempt/'codex-events.jsonl').write_text(json.dumps(event))
    assert 'input/story.md' in {r['path'] for r in read_evidence(work)}
