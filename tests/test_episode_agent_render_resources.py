from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""Episode media admission settings without constructing any service clients."""
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.rendering import flow
from novel_manga.config import Settings
from novel_manga.models.bible import StoryBible
from novel_manga.util import atomic_write_json


@pytest.fixture
def runner_arguments(tmp_path, monkeypatch):
    ctx = SimpleNamespace(novel_dir=tmp_path / 'book', episode_dir=tmp_path / 'book/book_1', run_dir=tmp_path, project_root=tmp_path / 'runtime')
    ctx.episode_dir.mkdir(parents=True)
    atomic_write_json(ctx.novel_dir / 'story_bible.json', {'novel_id': 'book'})
    atomic_write_json(ctx.novel_dir / 'profile.json', {'speech_gate': 'enforce', 'frame': 'portrait'})
    monkeypatch.setattr(Settings, 'from_env', classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(StoryBible, 'model_validate', classmethod(lambda cls, value: value))
    arguments = []

    def runner(**kwargs):
        arguments.append(kwargs)
        return SimpleNamespace(context=RenderContext(options=RenderOptions(workers=kwargs['workers'])))

    monkeypatch.setattr(flow, 'ThinMediaRunner', runner)
    for key in ('NOVEL_RENDER_WORKERS', 'NOVEL_RENDER_INFLIGHT', 'NOVEL_AGENT_MANAGED'):
        monkeypatch.delenv(key, raising=False)
    return EpisodeTools(ctx), arguments


def test_standalone_retains_two_workers_and_existing_retry_profile(runner_arguments):
    tools, calls = runner_arguments
    runner = tools.media.runner()
    assert calls[0]['workers'] == 2 and calls[0]['inflight'] == 0
    assert calls[0]['max_attempts'] == 1
    assert calls[0]['profile'] == {'speech_gate': 'enforce', 'frame': 'portrait',
                                   'repair_backend': 'sandbox_agent'}
    assert runner.context.asr_helper == tools.ctx.project_root / 'scripts/thin_asr_segments.py'


def test_conductor_settings_pass_shared_cap_and_zero_means_per_clip_workers(runner_arguments, monkeypatch):
    tools, calls = runner_arguments
    monkeypatch.setenv('NOVEL_AGENT_MANAGED', '1')
    monkeypatch.setenv('NOVEL_RENDER_WORKERS', '0')
    monkeypatch.setenv('NOVEL_RENDER_INFLIGHT', '24')
    monkeypatch.setenv('NOVEL_INFLIGHT_DIR', '/configured/shared/slots')
    tools.media.runner()
    assert calls[0]['workers'] == 0 and calls[0]['inflight'] == 24
    assert calls[0]['max_attempts'] == 1
    import os
    assert os.environ['NOVEL_INFLIGHT_DIR'] == '/configured/shared/slots'


@pytest.mark.parametrize('value', [None, '0'])
def test_managed_missing_or_zero_inflight_refuses_before_runner(runner_arguments, monkeypatch, value):
    tools, calls = runner_arguments
    monkeypatch.setenv('NOVEL_AGENT_MANAGED', '1')
    if value is not None:
        monkeypatch.setenv('NOVEL_RENDER_INFLIGHT', value)
    with pytest.raises(ValueError, match='NOVEL_RENDER_INFLIGHT'):
        tools.media.runner()
    assert not calls


def test_profile_background_music_reaches_the_existing_renderer_mix(runner_arguments, monkeypatch):
    tools, calls = runner_arguments
    music = tools.ctx.novel_dir/'music/approved.wav'
    music.parent.mkdir(); music.write_bytes(b'existing approved music')
    atomic_write_json(tools.ctx.novel_dir/'profile.json', {'bgm_path':'music/approved.wav'})
    settings_calls = []
    monkeypatch.setattr(Settings, 'from_env', classmethod(lambda cls, **kw: settings_calls.append(kw) or object()))
    tools.media.runner()
    assert settings_calls[0]['bgm_path'] == music
    assert calls[0]['max_attempts'] == 1


def test_missing_configured_music_fails_before_constructing_media_clients(runner_arguments):
    tools, calls = runner_arguments
    atomic_write_json(tools.ctx.novel_dir/'profile.json', {'bgm_path':'missing-approved.wav'})
    with pytest.raises(ValueError, match='background music is missing'):
        tools.media.runner()
    assert not calls


def test_native_h3_short_edge_is_applied_before_settings(runner_arguments):
    import os
    tools, _ = runner_arguments
    atomic_write_json(tools.ctx.novel_dir/'profile.json', {'native_h3_short_edge': 768})
    tools.media.runner()
    assert os.environ['NOVEL_H3_SHORT_EDGE'] == '768'
