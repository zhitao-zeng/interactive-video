"""Process-local service selection; private credentials stay in the existing .env."""
from pathlib import Path
import os
import json
from novel_manga.application.configuration import environment


def configure(root: Path, lane: str = 'h3', *, project: str = 'adventure') -> None:
    source = environment(root, {})
    os.environ.update(environment(root))
    services = json.loads((root/'configs/interactive.json').read_text()).get('services', {})
    text_endpoint = os.environ.get('NOVEL_INTERACTIVE_TEXT_ENDPOINT') or services.get('text_endpoint', 'phanthy-ds41')
    channel_path = root/f'.runtime/{project}-demo/asset-channel.json'
    channel = json.loads(channel_path.read_text()) if channel_path.is_file() else {}
    os.environ.update({
        'NOVEL_PROJECT_ROOT': str(root), 'NOVEL_CLIP_SECONDS_MAX': '15',
        'NOVEL_LOCAL_QWEN_IMAGE_URL': '', 'NOVEL_REUSE_EXISTING_ASSETS': '1',
        'NOVEL_INTRO_SECONDS': '0', 'NOVEL_OUTRO_SECONDS': '0',
        'NOVEL_PLANNER_ENDPOINT': text_endpoint, 'NOVEL_PREPARATION_ENDPOINT': text_endpoint,
        'NOVEL_H3_TRANSLATION_ENDPOINT': text_endpoint, 'NOVEL_H3_REQUEST_CHECK_ENDPOINT': text_endpoint,
        'SECOND_REVIEW_JUDGE': text_endpoint,
        'NOVEL_LOCAL_H3_URL': 'pool' if lane == 'h3' else '',
        'NOVEL_H3_SHORT_EDGE': str(json.loads((root/'configs/interactive.json').read_text()).get('production', {}).get('h3_short_edge', 544)),
        'NOVEL_VIDEO_MODEL': 'minimax-h3-ref2va-turbo' if lane == 'h3' else 'sd2.5',
        'NOVEL_QUOTA_STOP_FILE': str(root/f'.runtime/{project}-demo/sd25-quota.stop') if lane=='sd25' else '',
        'PHANROUTER_VIDEO_KEY_VAR': 'PHANROUTER_API_KEY' if lane == 'h3' else 'PHANROUTER_SD25_KEY',
        'PHANROUTER_REFERENCE_ASSETS': '1' if lane == 'sd25' else '0',
        'PHANROUTER_INLINE_REFERENCE_IMAGES': '0' if lane == 'sd25' else '1',
        'PHANROUTER_ASSET_PUBLIC_BASE': channel.get('public_base', source.get('PHANROUTER_ASSET_PUBLIC_BASE', '')) if lane == 'sd25' else '',
        'NOVEL_ASSET_PUBLISH_DIR': channel.get('publish_dir', services.get('asset_publish_dir', str(root/'outputs/.published'))),
    })
    # Reuse the existing shared LLM concurrency mechanism and H3 pool slot_dir.
    from novel_manga.llm.config import ENDPOINTS
    os.environ.update(ENDPOINTS[text_endpoint])
    from novel_manga.application.configuration import model_resources_environment
    os.environ.update(model_resources_environment(root))


def runner_for(root: Path, novel: Path, lane: str, *, project: str | None = None):
    configure(root, lane, project=project or novel.name.rsplit('-',1)[0])
    from novel_manga.config import Settings
    from novel_manga.models.bible import StoryBible
    from novel_manga.application.rendering.flow import ThinMediaRunner
    from novel_manga.application.profiles import load_profile
    settings = Settings.from_env(provider='phanrouter', output_root=novel.parent, admission_mode='preview')
    workers=json.loads((root/'configs/interactive.json').read_text())['production'].get('workers_by_lane', {}).get(lane, 1)
    return ThinMediaRunner(novel_dir=novel, episode_dir=novel/(novel.name+'_1'), settings=settings,
                           bible=StoryBible.model_validate_json((novel/'story_bible.json').read_text()),
                           profile=load_profile(novel), workers=workers, max_attempts=2)
