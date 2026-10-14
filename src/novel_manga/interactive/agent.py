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


def run(runner, lane, *, resume=False, destination=None):
    from novel_manga.application.rendering.reviewed import run as reviewed_run
    from novel_manga.application.review.execution import media_matches
    from novel_manga.util import read_json
    reuse = resume and media_matches(runner.context.clip_plan,
        read_json(runner.context.episode_dir/'thin_media_report.json',{}), notes=runner.context.feedback)
    previous = runner.context.profile
    runner.context.profile = {**previous, 'repair_backend': 'sandbox_agent'}
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
