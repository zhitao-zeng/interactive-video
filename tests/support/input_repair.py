"""Shared source input repair fixture, with fake providers and real budgets."""
import copy
import pytest
from novel_manga.application.agents.episode import input_repair
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared

@pytest.fixture
def setup(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    ctx.asset_snapshot_dir = ctx.run_dir.parent/'assets-snapshot'
    shared = ctx.asset_snapshot_dir/'series_assets'
    (shared/'characters').mkdir(parents=True)
    atomic_write_json(shared/'phases.json', {'policy': 'phase-cards-v1', 'characters': {}})
    (ctx.novel_dir/'series_assets').symlink_to(shared, target_is_directory=True)
    for root in [ctx.novel_dir, ctx.source_novel_dir]:
        (root/'cards').mkdir(exist_ok=True)
        (root/'voices').mkdir(exist_ok=True)
        for name in ['a.jpeg', 'b.jpeg', 'room.jpeg']:
            (root/'cards'/name).write_bytes(b'image')
        (root/'voices/a.wav').write_bytes(b'audio')
    atomic_write_json(ctx.novel_dir/'story_bible.json', {'characters': [{'name': '乙'}, {'name': '甲'}]})
    (ctx.run_dir/'source.txt').write_text('甲坐在宿舍，穿着黑色披风。甲说：你来啦。')
    monkeypatch.setattr(input_repair, 'source_check', lambda *a, **kw: {'consistent': True, 'problems': []})
    def translated(clip, **kwargs):
        from novel_manga.application.profiles import h3_stamp
        clip['prompt_h3_of'] = h3_stamp(clip, '')
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert', translated)
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda *a, **k: {'consistent': True, 'problems': []})
    monkeypatch.setattr('novel_manga.application.preparation.wearer_cards.check_references', lambda *a: None)

    def rebuild(directory, bible_path, script, before, targets, *, repack_report=None, preserve_timing=False, source_checked_turns=None):
        assert preserve_timing
        after = copy.deepcopy(before)
        ledger = read_json(ctx.novel_dir/'series_assets/phases.json', {})
        phases = ledger.get('characters', {})
        changed = []
        for clip in after['clips']:
            if clip['clip_id'] not in targets:
                continue
            clip['prompt'] = '修正后的地点与外貌'
            if phases:
                for ref in clip['references']:
                    if ref['name'] in phases:
                        asset = phases[ref['name']][0]['asset_id']
                        ref.update(asset_id=asset, path=f'series_assets/characters/{asset}/turnaround.jpeg')
            place = script['shots'][clip['shot_indexes'][0]-1]['location']
            location_phase = (ledger.get('locations', {}).get(place) or [None])[0]
            if location_phase:
                clip['location'] = place
                for ref in clip['references']:
                    if ref['role'] == 'location':
                        asset = location_phase['asset_id']
                        ref.update(name=place, asset_id=asset, path=f'series_assets/locations/{asset}/establishing.jpeg')
            changed.append(clip['clip_id'])
        return after, changed

    monkeypatch.setattr('novel_manga.application.repair.flow.rebuild_clips', rebuild)
    return ctx, plan, shared

