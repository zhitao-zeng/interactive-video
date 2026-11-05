"""Clip retry decisions; no generation, asset repair, publication or file writes."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from .policy import PRIVACY_MARKER
from . import policy
from .cache import CacheMiss


@dataclass
class RetryState:
    attempt: int
    limit: int
    privacy_repairs: int = 0


def starting_attempt(work: Path, clip: dict) -> int:
    """A new authorized retake must not overwrite an older unknown submission."""
    from novel_manga.util import read_json
    attempt = 1
    root = work / 'clips' / clip['clip_id']
    for path in sorted(root.glob('attempt_*/clip.mp4.task.json')):
        task = read_json(path, {})
        request = read_json(path.parent / 'request.json', {})
        if (task.get('submit_uncertain_at') and not task.get('task_id')
                and not (path.parent / 'clip.mp4').is_file()
                and int(request.get('repair_take', 0)) < int(clip.get('repair_take', 0))):
            attempt = max(attempt, int(path.parent.name.rsplit('_', 1)[1]) + 1)
    return attempt


@dataclass(frozen=True)
class GenerationOutcome:
    video: Path | None = None
    error: RuntimeError | None = None


@dataclass(frozen=True)
class Recovery:
    action: str
    reference_slot: str | None = None


def recovery_steps(error: RuntimeError, runtime, state: RetryState, *, moderation_repair: bool) -> list[Recovery]:
    text = str(error)
    steps = []
    if PRIVACY_MARKER in text and state.privacy_repairs < 2:
        index = re.search(r'content\[(\d+)\]', text)
        steps.append(Recovery('reference', index.group(1) if index else None))
    if policy.INPUT_TEXT_MARKER in text and not runtime.softened:
        steps.append(Recovery('soften'))
    if policy.INPUT_TEXT_MARKER in text and moderation_repair and not runtime.repaired:
        steps.append(Recovery('rewrite'))
    if any(marker in text for marker in policy.OUTPUT_MODERATION_MARKERS) and not runtime.compliance:
        steps.append(Recovery('compliance'))
    return steps


@dataclass(frozen=True)
class NextAttempt:
    action: str
    attempt: int
    limit: int


def after_analysis(state: RetryState, analysis: dict, *, generated: bool, free_retries: bool,
                   next_cached: bool | None = None, local_h3: bool = False) -> NextAttempt:
    if analysis['passed']:
        return NextAttempt('stop', state.attempt, state.limit)
    limit = state.limit
    if not generated and limit < policy.MAX_ATTEMPTS_FREE:
        if not free_retries and next_cached is None:
            return NextAttempt('check_cache', state.attempt, limit)
        if free_retries or next_cached:
            limit += 1
    attempt = state.attempt + 1
    if local_h3 and {'excess_unplanned_speech', 'unscripted_speech'}.intersection(analysis.get('issues', [])) and attempt <= limit:
        return NextAttempt('correct_request', attempt, limit)
    return NextAttempt('continue' if attempt <= limit else 'stop', attempt, limit)


def selected_take(attempts):
    return next((row for row in attempts if row['passed']), attempts[-1])


def failure_result(clip_id, attempts, error):
    from novel_manga.service_errors import UnsupportedInput, service_failure
    message = f'{type(error).__name__}: {str(error)[:600]}'
    rejection=getattr(error,'request_check',None)
    if isinstance(error,UnsupportedInput) and isinstance(rejection,dict):
        failure={'request_check':rejection,'blocked':['request: '+p for p in rejection['problems']]}
    else:
        failure=service_failure(error)
    if attempts and not isinstance(error, CacheMiss):
        return {'clip_id': clip_id, 'attempts': attempts, 'retake_error': message,
                'selected': selected_take(attempts), **failure}
    return {'clip_id': clip_id, 'attempts': attempts,
            'selected': attempts[-1] if attempts else None, 'error': message, **failure}
