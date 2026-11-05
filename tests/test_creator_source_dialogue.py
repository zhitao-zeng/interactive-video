"""Preserve source speakers outside the regular cast before a creator is adopted."""
import json
import zipfile

import pytest

from novel_manga.interactive import creation
from novel_manga.interactive.source_dialogue import extract_markdown_dialogue, dialogue_issues


SOURCE = '**乔伊：** 我能看看吗？\n**摊主：** 可以，三十五。\n**陆川：** 透明那只口正。\n'


def source_task(tmp_path):
    package = tmp_path/'source.zip'
    with zipfile.ZipFile(package, 'w') as z:
        z.writestr('EP02-B.md', SOURCE)
    task = tmp_path/'task.json'
    task.write_text(json.dumps({'mode':'adaptation', 'project':'romance',
        'brief':'保留全部原话，包括画外摊主。', 'source_archive':str(package),
        'dialogue_source':'input/package/EP02-B.md', 'target_seconds':[4,30]}))
    return task


def candidate():
    rows = extract_markdown_dialogue(SOURCE)
    # Splitting one utterance is legal; character ownership and order stay exact.
    turns = [{**rows[0], 'text':'我能'}, {**rows[0], 'text':' 看看吗？'}, *rows[1:]]
    for turn in turns:
        turn.update(delivery_mode='offscreen_dialogue', emotion='Calmly answering the previous question.')
    return {'chapter_script':{'shots':[{'index':1, 'scene_id':'stall', 'location':'摊位',
        'duration_seconds':15, 'purpose':'询价', 'camera':'固定空镜', 'start_state':'器皿在桌上',
        'event':'画外询价', 'end_state':'仍未拿起器皿', 'in_frame':[], 'turns':turns,
        'source_quote':'可以，三十五。'}]}, 'clips':[{'shot_indexes':[1], 'request_seconds':15}],
        'interaction':{'selected':None,'question':'看哪只？','options':[
            {'id':'A','label':'蓝瓶','reason':'正在看'}, {'id':'B','label':'透明瓶','reason':'旁边有'}]},
        'spatial_plan':{'scenes':[{'scene_id':'stall','shot_indexes':[1], 'anchors':['桌'],
            'placed':[], 'assumptions':[]}]}, 'self_review':{'open_issues':[]}}


def test_selected_original_supplies_all_speakers_and_stale_ledger_cannot_overrule_it(tmp_path):
    run = tmp_path/'run'; manifest = creation.prepare(source_task(tmp_path), run)
    work = run/'sandbox'; protected = work/'input/protected-dialogue.json'
    rows = json.loads(protected.read_text())
    assert [row['speaker_name'] for row in rows] == ['乔伊','摊主','陆川']
    assert rows[1]['source_line'] == 2
    assert 'input/protected-dialogue.json' in manifest['required_reads']
    # Reproduce the actual omission without accepting the incorrect derived list.
    protected.write_text(json.dumps([row for row in rows if row['speaker_name'] != '摊主']))
    draft = candidate()
    assert creation.validate(draft, manifest, work) == []
    draft['chapter_script']['shots'][0]['turns'].pop(2)
    errors = creation.validate(draft, manifest, work)
    assert any('source turn 2 摊主' in e and 'shot 1 turn 3 陆川' in e for e in errors)


@pytest.mark.parametrize('change', ['new_word','wrong_owner','reorder'])
def test_verbatim_check_accepts_split_turns_but_blocks_changed_words_owners_or_order(change):
    shots = candidate()['chapter_script']['shots']; rows = extract_markdown_dialogue(SOURCE)
    assert dialogue_issues(shots, rows) == []
    if change == 'new_word': shots[0]['turns'][-1]['text'] += '好。'
    elif change == 'wrong_owner': shots[0]['turns'][2]['speaker_name'] = '陆川'
    else: shots[0]['turns'][2:4] = reversed(shots[0]['turns'][2:4])
    assert dialogue_issues(shots, rows)


def test_explicit_source_must_be_an_imported_dialogue_document(tmp_path):
    task = source_task(tmp_path); value = json.loads(task.read_text())
    value['dialogue_source'] = 'some-other-document.md'; task.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='one imported source document'):
        creation.prepare(task, tmp_path/'run')
    with pytest.raises(ValueError, match='no .*dialogue lines'):
        extract_markdown_dialogue('一份没有标注对白的散文。')


def test_empty_spatial_placement_is_valid_for_empty_view_and_offscreen_speech(tmp_path):
    run = tmp_path/'run'; manifest = creation.prepare(source_task(tmp_path), run)
    draft = candidate(); work = run/'sandbox'
    assert creation.validate(draft, manifest, work) == []
    draft['chapter_script']['shots'][0]['in_frame'] = ['乔伊']
    errors = creation.validate(draft, manifest, work)
    assert any("spatial scene 'stall' (shots [1])" in e and 'visible bodies' in e for e in errors)
    draft['chapter_script']['shots'][0]['in_frame'] = []
    draft['chapter_script']['shots'][0]['turns'][0]['delivery_mode'] = 'visible_dialogue'
    assert any('visible bodies' in e for e in creation.validate(draft, manifest, work))


def test_spatial_feedback_names_the_scene_shots_and_actual_missing_field(tmp_path):
    run = tmp_path/'run'; manifest = creation.prepare(source_task(tmp_path), run)
    draft = candidate(); scene = draft['spatial_plan']['scenes'][0]
    scene.pop('assumptions')
    errors = creation.validate(draft, manifest, run/'sandbox')
    assert errors == ["spatial scene 'stall' (shots [1]): missing explicit assumptions"]
    scene['assumptions'] = []; scene.pop('placed')
    errors = creation.validate(draft, manifest, run/'sandbox')
    assert len(errors) == 1 and "'stall'" in errors[0] and 'missing placed list' in errors[0]
