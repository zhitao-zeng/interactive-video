"""Select the request adapter for the existing bounded repair loop."""
from contextlib import contextmanager
import os
from .runtime import VIDEO_LANES


@contextmanager
def repair_lane(lane):
    if lane not in VIDEO_LANES:
        raise ValueError('Unsupported repair lane')
    key = 'NOVEL_INTERACTIVE_REPAIR_LANE'
    previous = os.environ.get(key)
    os.environ[key] = lane
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def run(runner, lane, *, resume=False, destination=None, independent_report=None, config=None):
    from novel_manga.application.rendering.reviewed import run as reviewed_run
    from novel_manga.application.review.execution import media_matches
    from novel_manga.util import read_json
    if independent_report is not None:
        from novel_manga.application.review.independent import register
        register(runner.context.episode_dir,
                 read_json(runner.context.episode_dir/'thin_media_report.json',{}),independent_report)
    media = read_json(runner.context.episode_dir/'thin_media_report.json',{}) if resume else {}
    reuse = resume and not media.get('failed_clips') and media_matches(
        runner.context.clip_plan, media, notes=runner.context.feedback)
    previous = runner.context.profile
    runner.context.profile = {**previous, 'repair_backend': 'sandbox_agent'}
    previous_agent_config = getattr(runner.context, 'repair_agent_config', None)
    runner.context.repair_agent_config = (dict(config) if config is not None else None)
    final_review=None
    if destination is not None:
        from novel_manga.interactive.delivery import reviewed_delivery
        final_review=lambda report: reviewed_delivery(runner,report,destination)
    try:
        with repair_lane(lane):
            options={'final_review':final_review} if final_review is not None else {}
            return reviewed_run(runner,repair_existing=True,**options) if reuse else reviewed_run(runner,**options)
    finally:
        runner.context.profile = previous
        if previous_agent_config is None:
            runner.context.__dict__.pop('repair_agent_config', None)
        else:
            runner.context.repair_agent_config = previous_agent_config
