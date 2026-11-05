"""Shared first-frame test factories (moved out of test modules for the
tests-use-support rule; behavior unchanged)."""
from types import SimpleNamespace as NS
import copy

from PIL import Image

from novel_manga.media.first_frames import opening_contract
from novel_manga.util import atomic_write_json, read_json


def stub_host_h3(monkeypatch):
    from novel_manga.application.profiles import h3_stamp
    def ensure(ctx,clip):
        clip['prompt_h3']=clip.get('prompt_h3') or 'English request'
        clip['prompt_h3_of']=h3_stamp(clip)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.ensure_current_h3',ensure)


def case(tmp_path, monkeypatch, *, host_h3=False):
    ctx = NS(novel_dir=tmp_path/'book', episode_dir=tmp_path/'book/book_1',
             run_dir=tmp_path/'run', work_dir=tmp_path/'run/sandbox')
    ctx.episode_dir.mkdir(parents=True); (ctx.work_dir/'output').mkdir(parents=True)
    Image.new('RGB', (8, 16)).save(ctx.novel_dir/'scene.png')
    ref = {'role': 'location', 'path': 'scene.png', 'name': 'pool'}
    opening = {'image_prompt': 'empty pool', 'visible_characters': [], 'reference_subjects': ['pool']}
    clips = [{'clip_id': f'clip_{i:02d}', 'kind': 'video', 'shot_indexes': [i],
              'segment_ids': [str(i)], 'references': [ref],
              'shots': [{'index': i, 'start_state': 'empty pool', 'turns': [],
                         'opening_frame': copy.deepcopy(opening)}]} for i in (1, 2)]
    atomic_write_json(ctx.novel_dir/'profile.json', {'h3_input_mode': 'first-frame'})
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips})
    atomic_write_json(ctx.episode_dir/'segments.json', [{'segment_id': str(i), 'text': 'empty pool'} for i in (1, 2)])
    atomic_write_json(ctx.run_dir/'catalog.json', [ref])
    rows = [{'clip_id': c['clip_id'], 'image_prompt': 'empty pool', 'image_references': ['scene.png'],
             'visible_characters': [], 'extra_references': [], 'prompt_h3': 'English request'} for c in clips]
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': rows})
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.validate_existing_request', lambda *a: None)
    monkeypatch.setattr('novel_manga.application.agents.episode.first_frames.review_opening_sources',
                        lambda *a: {}, raising=False)
    if not host_h3:stub_host_h3(monkeypatch)
    return ctx, clips


def character_case(tmp_path, monkeypatch):
    ctx, clips = case(tmp_path, monkeypatch)
    ref = {'role': 'character', 'name': 'A', 'path': 'actor.png'}
    Image.new('RGB', (8, 16)).save(ctx.novel_dir/'actor.png')
    bible = {'characters': [{'name': 'A', 'appearance': 'black hair',
                             'wardrobe': 'grey long trousers and grey hiking shoes'},
                            {'name': 'unselected', 'wardrobe': 'red dress'}]}
    atomic_write_json(ctx.novel_dir/'story_bible.json', bible)
    clip = clips[0]
    clip['references'].append(ref)
    clip['shots'][0].update(in_frame=[], start_state='A films the pool; headlamp is inside the closed backpack.',
                            device_state='headlamp stays inside the backpack', wears={'A': 'backpack'})
    clip['shots'][0]['opening_frame']['reference_subjects']=['pool','A']
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [clip]})
    atomic_write_json(ctx.run_dir/'catalog.json', clip['references'])
    row = read_json(ctx.work_dir/'output/first-frames.json')['frames'][0]
    row['image_references'].append('actor.png')
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {'frames': [row]})
    return ctx, clip, row, bible


def fixture_book(tmp_path):
    book = tmp_path/'romance-fast'; episode = book/(book.name+'_1'); episode.mkdir(parents=True)
    Image.new('RGB', (18, 32)).save(book/'actor.png')
    atomic_write_json(book/'profile.json', {'sd_input_mode': 'first-frame', 'aspect_ratio': '9:16'})
    opening = {'image_prompt': 'One actor standing by the table.', 'visible_characters': ['A'], 'reference_subjects': ['A']}
    shot = {'index': 1, 'camera': 'wide', 'in_frame': ['A'], 'opening_frame': opening}
    clip = {'clip_id': 'clip_01', 'kind': 'video', 'request_seconds': 12, 'shot_indexes': [1], 'shots': [shot],
            'h3_speech_policy': 'no_reference_performance_first',
            'references': [{'role': 'character', 'name': 'A', 'path': 'actor.png'}]}
    atomic_write_json(episode/'chapter_script.json', {'shots': [shot]})
    atomic_write_json(episode/'clip_plan.json', {'clips': [clip]})
    return book, episode


def clip_case(tmp_path):
    original = [{'role': 'location', 'path': 'scene.png', 'name': 'pool'},
                {'role': 'character', 'path': 'actor.png', 'name': 'A'},
                {'role': 'character', 'path': 'new.png', 'name': 'B'},
                {'role': 'voice', 'path': 'voice.wav', 'name': 'A'}]
    clip = {'clip_id': 'clip_01', 'references': original,
            'shots': [{'start_state': 'A alone', 'camera': 'front', 'location': 'pool',
                       'in_frame': ['A'], 'event': 'B enters', 'turns': []}]}
    for name in ['scene.png', 'actor.png', 'new.png', 'frame.png']:
        Image.new('RGB', (4, 8)).save(tmp_path/name)
    ctx = NS(novel_dir=tmp_path, profile={'h3_input_mode': 'first-frame'})
    atomic_write_json(tmp_path/'profile.json', ctx.profile)
    clip['first_frame'] = {'path': 'frame.png', 'extra_references': ['new.png'], 'visible_characters': ['A'],
                           'review': {'consistent': True, 'problems': []},
                           'source_contract': opening_contract(clip, ['scene.png', 'actor.png'])}
    return ctx, clip
