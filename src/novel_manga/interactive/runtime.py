"""Process-local service selection; private credentials stay in the existing .env."""
from pathlib import Path
import os
import json
from novel_manga.application.configuration import environment
from novel_manga.interactive.intake import PROJECTS


VIDEO_LANES = {
    'h3': {'model': 'minimax-h3-ref2va-turbo', 'key_var': 'PHANROUTER_API_KEY'},
    # Cloud MiniMax-H3 overflow lane (v3 tasks via the image key's group; verified
    # end-to-end 2026-10-22: 9s probe succeeded on both v2 and v3). Reference-image
    # mode only — the gateway path has no first-frame role; shares the image-key
    # quota with sd20 lanes and the image API.
    'h3cloud': {'model': 'MiniMax-H3', 'key_var': 'PHANROUTER_H3CLOUD_API_KEY'},
    'sd25': {'model': 'sd2.5', 'key_var': 'PHANROUTER_SD25_KEY'},
    'sd20-fast': {'model': 'sd2.0-fast', 'key_var': 'PHANROUTER_IMAGE_API_KEY'},
    'sd20-mini': {'model': 'sd2.0-mini', 'key_var': 'PHANROUTER_IMAGE_API_KEY'},
}
from novel_manga.llm.catalog import resource as _resource
for _lane, _values in VIDEO_LANES.items():
    _values.update({k:v for k,v in _resource(_lane).items() if k in {'model','key_var'}})


def project_config(root: Path, project: str) -> dict:
    """One maintained configuration with explicit per-project production choices."""
    if project not in PROJECTS:
        raise ValueError(f'Unknown interactive project: {project}')
    config=json.loads((root/'configs/interactive.json').read_text())
    selected=config.get('projects',{}).get(project,{})
    result={**config}
    for section in ('production','services'):
        result[section]={**config.get(section,{}),**selected.get(section,{})}
    result['production']['workers_by_lane']={
        **config.get('production',{}).get('workers_by_lane',{}),
        **selected.get('production',{}).get('workers_by_lane',{})}
    return result


def configure(root: Path, lane: str = 'h3', *, project: str = 'adventure') -> None:
    if lane not in VIDEO_LANES:
        raise ValueError(f'Unknown video lane: {lane}')
    selected=VIDEO_LANES[lane]
    from novel_manga.llm.catalog import quota_group
    remote=lane!='h3'
    quota_lane='sd25' if lane=='sd25' else 'h3cloud' if lane=='h3cloud' else 'sd20'
    source = environment(root, {})
    os.environ.update(environment(root))
    config=project_config(root,project)
    services = config.get('services', {})
    text_endpoint = os.environ.get('NOVEL_INTERACTIVE_TEXT_ENDPOINT')
    if not text_endpoint:
        from novel_manga.llm.catalog import catalogue, choose
        configured = services.get('text_endpoint', 'phanthy-ds41')
        text_endpoint = (choose(catalogue()['candidates']['json'], 'json')
                         if configured == 'phanthy-ds41' else configured)
        if not text_endpoint:
            raise ValueError('No qualified JSON resource available in the configured candidate order')
    from novel_manga.llm.catalog import review_defaults
    vision_default, video_default = review_defaults(text_endpoint)
    vision_endpoint = os.environ.get('SECOND_REVIEW_JUDGE') or vision_default
    if not vision_endpoint:
        raise ValueError('No qualified image-review endpoint; configure SECOND_REVIEW_JUDGE explicitly')
    channel_path = root/f'.runtime/{project}-demo/asset-channel.json'
    channel = json.loads(channel_path.read_text()) if channel_path.is_file() else {}
    os.environ.update({
        'NOVEL_PROJECT_ROOT': str(root), 'NOVEL_CLIP_SECONDS_MAX': '15',
        'NOVEL_LOCAL_QWEN_IMAGE_URL': '', 'NOVEL_REUSE_EXISTING_ASSETS': '1',
        'NOVEL_INTRO_SECONDS': '0', 'NOVEL_OUTRO_SECONDS': '0',
        'NOVEL_PLANNER_ENDPOINT': os.environ.get('NOVEL_PLANNER_ENDPOINT') or text_endpoint,
        'NOVEL_PREPARATION_ENDPOINT': os.environ.get('NOVEL_PREPARATION_ENDPOINT') or text_endpoint,
        'NOVEL_H3_TRANSLATION_ENDPOINT': os.environ.get('NOVEL_H3_TRANSLATION_ENDPOINT') or text_endpoint,
        'NOVEL_H3_REQUEST_CHECK_ENDPOINT': os.environ.get('NOVEL_H3_REQUEST_CHECK_ENDPOINT') or text_endpoint,
        'SECOND_REVIEW_JUDGE': vision_endpoint,
        'NOVEL_REVIEW_VIDEO_JUDGE': os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE') or video_default,
        'NOVEL_LOCAL_H3_URL': 'pool' if lane == 'h3' else '',
        'NOVEL_H3_SHORT_EDGE': str(config.get('production', {}).get('h3_short_edge', 544)),
        'NOVEL_VIDEO_MODEL': selected['model'],
        'NOVEL_RESOURCE_QUOTA_GROUP': quota_group(lane, source) or '',
        'NOVEL_QUOTA_STOP_FILE': str(root/f'.runtime/{project}-demo/{quota_lane}-quota.stop') if remote else '',
        'PHANROUTER_VIDEO_KEY_VAR': selected['key_var'],
        'PHANROUTER_REFERENCE_ASSETS': '1' if remote and lane != 'h3cloud' else '0',
        'PHANROUTER_INLINE_REFERENCE_IMAGES': '0' if remote else '1',
        'PHANROUTER_ASSET_PUBLIC_BASE': channel.get('public_base', source.get('PHANROUTER_ASSET_PUBLIC_BASE', '')) if remote else '',
        'NOVEL_ASSET_PUBLISH_DIR': str((root / channel.get('publish_dir', services.get('asset_publish_dir', 'outputs/.published'))).resolve()),
    })
    # Reuse the existing shared LLM concurrency mechanism and H3 pool slot_dir.
    from novel_manga.llm.config import ENDPOINTS
    os.environ.update(ENDPOINTS[text_endpoint])
    from novel_manga.application.configuration import model_resources_environment
    os.environ.update(model_resources_environment(root))


def runner_for(root: Path, novel: Path, lane: str, *, project: str | None = None):
    project=project or next((name for name in PROJECTS if novel.name.startswith(name+'-')),'adventure')
    configure(root, lane, project=project)
    from novel_manga.config import Settings
    from novel_manga.models.bible import StoryBible
    from novel_manga.application.rendering.flow import ThinMediaRunner
    from novel_manga.application.profiles import load_profile
    profile=load_profile(novel)
    if lane=='h3' and profile.get('native_h3_short_edge') is not None:
        os.environ['NOVEL_H3_SHORT_EDGE']=str(profile['native_h3_short_edge'])
    settings = Settings.from_env(provider='phanrouter', output_root=novel.parent, admission_mode='preview')
    defaults=project_config(root,project)['production'].get('workers_by_lane', {})
    workers=int(profile.get('workers_by_lane', {}).get(lane, defaults.get(lane, 1)))
    return ThinMediaRunner(novel_dir=novel, episode_dir=novel/(novel.name+'_1'), settings=settings,
                           bible=StoryBible.model_validate_json((novel/'story_bible.json').read_text()),
                           profile=profile, workers=workers, max_attempts=2,
                           inflight=int(profile.get('inflight',0)))
