from types import SimpleNamespace

import pytest

from novel_manga.interactive import film, creation, creative_material, authored, runtime, production, first_frame_entry
from novel_manga.application.agents.episode import context, flow
from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
from novel_manga.util import atomic_write_json, read_json


def test_produce_honors_explicit_fast_lane_from_creation_to_production(tmp_path, monkeypatch):
    calls = []
    task = tmp_path/'task.json'
    original = {'mode': 'original', 'project': 'adventure', 'brief': 'A short original film.'}
    atomic_write_json(task, original)

    def create(path, directory, **kwargs):
        frozen = read_json(path)
        assert frozen['video_lane'] == 'sd20-fast'
        assert frozen.get('h3_input_mode') != 'first-frame'
        atomic_write_json(directory/'sandbox/input/manifest.json', {'profile': {}})
        atomic_write_json(directory/'sandbox/output/episode.json', {})
        return {'status': 'creative_candidate'}

    def prepare(material, book, **kwargs):
        atomic_write_json(book/(book.name+'_1')/'clip_plan.json', {'clips': []})

    monkeypatch.setattr(creation, 'run', create)
    monkeypatch.setattr(creative_material, 'write_material', lambda *args, **kwargs: tmp_path/'material')
    monkeypatch.setattr(authored, 'prepare_authored', prepare)
    monkeypatch.setattr(runtime, 'configure', lambda root, lane, **kwargs: calls.append(('configure', lane)))
    monkeypatch.setattr(runtime, 'runner_for', lambda root, book, lane, **kwargs: SimpleNamespace(build_assets=lambda: calls.append(('assets', lane))))
    monkeypatch.setattr(production, 'run_episode', lambda root, lane, **kwargs: calls.append(('production', lane)) or {'status': 'accepted'})
    result = film.run(tmp_path, task, tmp_path/'run', 'new-fast-film', lane='sd20-fast')
    assert result['status'] == 'accepted'
    assert calls == [('configure', 'sd20-fast'), ('assets', 'sd20-fast'), ('production', 'sd20-fast')]
    assert read_json(task) == original


def test_fast_story_music_uses_the_existing_whole_episode_agent(tmp_path, monkeypatch):
    book = tmp_path/'outputs/fast-film'; episode = book/'fast-film_1'
    atomic_write_json(book/'profile.json', {'h3_speech_policy': NATIVE_SPEECH_POLICY, 'sd_input_mode':'first-frame'})
    atomic_write_json(episode/'chapter_script.json', {'music_plan': {'cues': [{'id': 'm1'}]}})
    atomic_write_json(episode/'clip_plan.json', {'clips': []})
    monkeypatch.setattr(production, 'edition_directory', lambda *args: book)
    monkeypatch.setattr(production, 'runner_for', lambda *args, **kwargs: SimpleNamespace())
    calls = []
    monkeypatch.setattr(first_frame_entry, 'run', lambda *args, **kwargs: calls.append(kwargs) or {'status': 'candidate'})
    monkeypatch.setattr('novel_manga.interactive.sd_first_frames.prepare',lambda *a,**k:pytest.fail('Persistent host must own opening review and its repair'))
    monkeypatch.setattr('novel_manga.interactive.agent.run', lambda *args, **kwargs: pytest.fail('Story music needs the persistent Agent tools'))
    result = production.run_episode(tmp_path, 'sd20-fast', edition=book.name, project='adventure', agent_config={'model': 'selected-agent'})
    assert result['status'] == 'candidate'
    assert calls[0]['lane'] == 'sd20-fast'
    assert calls[0]['config']['model'] == 'selected-agent'


@pytest.mark.parametrize('remaining', [17, 0, None])
def test_standard_stopped_resume_does_not_renew_configured_time(tmp_path, monkeypatch, remaining):
    novel = tmp_path/'book'; run = tmp_path/'run'; episode = run/'book/book_1'
    ctx = SimpleNamespace(run_dir=run, novel_dir=run/'book', episode_dir=episode,
                          work_dir=run/'sandbox', source_novel_dir=novel)
    atomic_write_json(novel/'profile.json', {'h3_speech_policy': NATIVE_SPEECH_POLICY})
    import time
    saved=({'status':'running','deadline':time.time()+25} if remaining is None else
           {'status':'blocked','remaining_seconds':remaining})
    atomic_write_json(run/'agent-run.json', saved)
    monkeypatch.setattr(context, 'create_context', lambda *args, **kwargs: ctx)
    monkeypatch.setattr(runtime, 'configure', lambda *args, **kwargs: None)
    monkeypatch.setattr('novel_manga.application.agents.episode.postproduction.transfer_cutaway', lambda *args: None)
    called = []
    monkeypatch.setattr(flow, 'run_episode', lambda *args, **kwargs: called.append(kwargs) or {'status': 'blocked'})
    first_frame_entry.run(tmp_path, novel, project='adventure', destination=tmp_path/'delivery', resume=True,
                          config={'timeout_seconds': 999, 'model': 'selected'})
    if remaining is None:
        assert 20<=called[0]['timeout']<=25
    elif remaining:
        assert called[0]['timeout'] == remaining
    else:
        assert called == []
    assert read_json(run/'agent-run.json') == saved
