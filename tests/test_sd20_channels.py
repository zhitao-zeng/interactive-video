"""Fast/Mini routing is verified offline; no sampling or model calls."""
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novel_manga.config import Settings
from novel_manga.interactive import runtime
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_video import video_payload
from novel_manga.util import atomic_write_json


CHANNELS = [('sd20-fast', 'sd2.0-fast'), ('sd20-mini', 'sd2.0-mini')]
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_channel_selects_image_key_and_shared_asset_library(tmp_path, monkeypatch, lane, model):
    atomic_write_json(tmp_path/'configs/interactive.json', {'services': {'text_endpoint': 'phanthy-ds41'}})
    atomic_write_json(tmp_path/'.runtime/romance-demo/asset-channel.json',
                      {'public_base': 'https://assets.invalid/cards'})
    monkeypatch.setattr(runtime, 'environment', lambda *args: {})
    monkeypatch.setattr('novel_manga.application.configuration.model_resources_environment', lambda *args: {})
    with patch.dict(os.environ, {'PHANROUTER_IMAGE_API_KEY': 'image-test-key',
                                'PHANROUTER_SD25_KEY': 'sd25-test-key'}, clear=True):
        runtime.configure(tmp_path, lane, project='romance')
        settings = Settings.from_env(provider='phanrouter', admission_mode='preview')
        assert settings.video_model == model
        assert settings.phanrouter_api_key == 'image-test-key'
        assert settings.local_h3_base_url is None
        assert settings.reference_images_via_assets
        assert not settings.inline_reference_images
        assert settings.phanrouter_asset_public_base == 'https://assets.invalid/cards'
        assert os.environ['NOVEL_CLIP_SECONDS_MAX'] == '15'
        assert os.environ['NOVEL_QUOTA_STOP_FILE'] == str(tmp_path/'.runtime/romance-demo/sd20-quota.stop')
        provider = PhanRouterMediaProvider(settings)
        try:
            assert provider.video_headers['Authorization'] == 'Bearer image-test-key'
        finally:
            provider.client.close()


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_settings_accepts_model_without_creating_provider_or_task(monkeypatch, lane, model):
    with patch.dict(os.environ, {'NOVEL_VIDEO_MODEL': model, 'PHANROUTER_VIDEO_KEY_VAR': 'PHANROUTER_IMAGE_API_KEY',
                                'PHANROUTER_IMAGE_API_KEY': 'image-test-key'}, clear=True):
        settings = Settings.from_env(provider='phanrouter', admission_mode='preview')
    assert settings.video_model == model


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_payload_preserves_references_native_audio_and_conservative_duration(lane, model):
    payload = video_payload(SimpleNamespace(video_model=model), 'Approved scene.', 'asset://character', 15,
                            ('asset://room',), ('data:audio/wav;base64,TEST',), ratio='16:9', resolution='480p')
    assert payload['model'] == model
    assert payload['duration'] == 15
    assert payload['resolution'] == '480p' and payload['ratio'] == '16:9'
    assert payload['generate_audio'] is True
    assert [r['image_url']['url'] for r in payload['content'] if r['type'] == 'image_url'] == [
        'asset://character', 'asset://room']
    assert payload['content'][-1]['role'] == 'reference_audio'


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_long_groups_are_not_silently_truncated_or_submitted(lane, model):
    with pytest.raises(ValueError, match='at most 15 seconds'):
        video_payload(SimpleNamespace(video_model=model), 'Approved scene.', None, 30)


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_cli_passes_explicit_lane_to_existing_production(monkeypatch, lane, model):
    from novel_manga.interactive import production
    seen = []
    monkeypatch.setattr(production, 'run_episode', lambda *args, **kwargs: seen.append((args, kwargs)) or {'status': 'accepted'})
    monkeypatch.setattr(sys, 'argv', ['interactive_video.py', 'run', '--project', 'romance',
                                    '--edition', 'romance-new-test', '--lane', lane])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(ROOT/'scripts/interactive_video.py'), run_name='__main__')
    assert stopped.value.code == 0
    assert seen[0][0][1] == lane
    assert seen[0][1]['edition'] == 'romance-new-test'


@pytest.mark.parametrize('lane,model', CHANNELS)
def test_new_channels_keep_sd_repair_contract_even_with_old_h3_prompt(monkeypatch, lane, model):
    from novel_manga.application.repair import agent
    from novel_manga.interactive.agent import repair_lane
    seen = []
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',
                        lambda clip, directory: seen.append(clip['prompt_h3']) or {'consistent': True})
    monkeypatch.setattr('novel_manga.application.repair.sd_request.validate',
                        lambda clip, prompt: seen.append(prompt))
    monkeypatch.delenv('NOVEL_INTERACTIVE_REPAIR_LANE', raising=False)
    with repair_lane(lane):
        assert agent.enabled(Path('/unused'))
        agent.check_request({'prompt_h3': 'Historical H3 format.'}, Path('/unused'))
        agent.validate_request({}, 'SD request.')
    assert seen == ['', 'SD request.']
    assert 'NOVEL_INTERACTIVE_REPAIR_LANE' not in os.environ


def test_existing_h3_and_sd25_routes_remain_explicit(tmp_path, monkeypatch):
    atomic_write_json(tmp_path/'configs/interactive.json', {'services': {'text_endpoint': 'phanthy-ds41'}})
    monkeypatch.setattr(runtime, 'environment', lambda *args: {})
    monkeypatch.setattr('novel_manga.application.configuration.model_resources_environment', lambda *args: {})
    with patch.dict(os.environ, {}, clear=True):
        runtime.configure(tmp_path, 'h3')
        assert os.environ['NOVEL_VIDEO_MODEL'] == 'minimax-h3-ref2va-turbo'
        assert os.environ['NOVEL_LOCAL_H3_URL'] == 'pool'
        runtime.configure(tmp_path, 'sd25')
        assert os.environ['NOVEL_VIDEO_MODEL'] == 'sd2.5'
        assert os.environ['PHANROUTER_VIDEO_KEY_VAR'] == 'PHANROUTER_SD25_KEY'
        assert os.environ['NOVEL_QUOTA_STOP_FILE'].endswith('sd25-quota.stop')


@pytest.mark.parametrize('lane', ['sd25', 'sd20-fast', 'sd20-mini'])
def test_sd_cannot_silently_enter_h3_via_saved_first_frame_mode(tmp_path, monkeypatch, lane):
    from novel_manga.interactive import production
    edition = 'romance-frozen-h3'
    book = tmp_path/'outputs'/edition
    atomic_write_json(book/(edition+'_1')/'clip_plan.json', {'clips': []})
    atomic_write_json(book/'profile.json', {'h3_input_mode': 'first-frame'})
    original = (book/'profile.json').read_bytes()
    monkeypatch.setattr(production, 'runner_for', lambda *args, **kwargs: pytest.fail('Do not create a renderer'))
    with pytest.raises(ValueError, match='first-frame mode requires H3'):
        production.run_episode(tmp_path, lane, project='romance', edition=edition)
    assert (book/'profile.json').read_bytes() == original
