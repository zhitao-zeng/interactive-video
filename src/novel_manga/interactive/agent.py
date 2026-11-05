"""Select the request adapter for the existing bounded repair loop."""
from contextlib import contextmanager
import os


@contextmanager
def repair_lane(lane):
    if lane not in {'h3', 'sd25'}:
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


def run(runner, lane, *, resume=False):
    from novel_manga.application.rendering.reviewed import run as reviewed_run
    from novel_manga.application.review.execution import media_matches
    from novel_manga.util import read_json
    media = read_json(runner.context.episode_dir/'thin_media_report.json',{}) if resume else {}
    # A matching report can still contain missing clips after an infrastructure
    # failure. The renderer must resume tasks/fill gaps before content repair.
    reuse = resume and not media.get('failed_clips') and media_matches(
        runner.context.clip_plan, media, notes=runner.context.feedback)
    previous = runner.context.profile
    runner.context.profile = {**previous, 'repair_backend': 'sandbox_agent'}
    try:
        with repair_lane(lane):
            return reviewed_run(runner,repair_existing=True) if reuse else reviewed_run(runner)
    finally:
        runner.context.profile = previous
