"""Shared service failure facts; independent of episode execution and media."""
from __future__ import annotations

class UnsupportedInput(ValueError):
    """A source decision was rejected, rather than interrupted by a service."""


import re

_SERVICE_STATUS = re.compile(r'\bHTTP[:\s]+([45]\d\d)\b')
# A payment, credential, quota or outage answer is about the shared service,
# never about this candidate's content.  It must not spend a content revision,
# a media take or an input attempt, and the rest of the batch must not be sent
# to a service already known to be unusable.
_SERVICE_STATUSES = {401, 402, 403, 407, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
_SERVICE_WORDS = ('quota_not_enough', 'insufficient', 'payment required', 'credential',
                  'unauthorized', 'api key', 'rate limit', 'service unavailable')


def service_interruption(error):
    """The HTTP status/text of an outage or account failure, or None.

    Task submission wraps its answer in RuntimeError texts such as
    ``task submission returned HTTP 402: ...``; direct httpx errors carry the
    response.  Content rejections never match here.
    """
    if isinstance(error, UnsupportedInput):
        return None
    from novel_manga.llm.client import ModelOutputMalformed
    if isinstance(error, ModelOutputMalformed):
        return {'http_status':None,'detail':str(error),'kind':'model_output_malformed'}
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    detail = getattr(response, 'text', None) or str(error)
    if status is None:
        match = _SERVICE_STATUS.search(str(error))
        status = int(match.group(1)) if match else None
    lowered = str(detail).lower()
    if status in _SERVICE_STATUSES or any(word in lowered for word in _SERVICE_WORDS):
        return {'http_status': status, 'detail': str(detail).strip().replace('\n', ' ')[:500]}
    return None


_TRANSIENT_SERVICE_STATUSES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _quota_exhausted(status, detail):
    if status == 402 or any(code in detail for code in ('quota_not_enough', 'insufficient_quota')):
        return True
    if 'insufficient_credits' not in detail:
        return False
    import json
    try:
        payload = json.loads(detail[detail.index('{'):detail.rindex('}') + 1])
        provider_error = payload.get('error', payload) if isinstance(payload, dict) else {}
        return (provider_error.get('code') == 'insufficient_credits'
                and provider_error.get('available_points') in (None, 0))
    except (AttributeError, TypeError, ValueError):
        return True


def _service_outcome(outage, *, network, uncertain, required_action=None):
    outage = dict(outage)
    status = outage.get('http_status')
    detail = str(outage.get('detail') or '').lower()
    if uncertain:
        outage['submission_uncertain'] = True
    retryable = (not uncertain and not _quota_exhausted(status, detail)
                 and status not in {401, 403, 407}
                 and (network or status in _TRANSIENT_SERVICE_STATUSES
                      or outage.get('kind') == 'model_output_malformed'))
    result = {'service_interrupted': outage, 'retryable': retryable}
    if required_action:
        result['required_action'] = required_action
    elif uncertain:
        result['required_action'] = 'reconcile_submission'
    return result


def service_failure(error):
    """Classify service failures without turning them into content findings."""
    import httpx
    from novel_manga.providers.phanrouter_tasks import SubmissionUncertain, http_status

    uncertain = isinstance(error, SubmissionUncertain)
    outage = service_interruption(error)
    network = isinstance(error, (TimeoutError, ConnectionError,
                                httpx.TimeoutException, httpx.NetworkError))
    if (outage and outage.get('http_status') is None and not uncertain and not network
            and outage.get('kind') != 'model_output_malformed'):
        detail = str(outage.get('detail') or '').lower()
        service_words = ('quota', 'payment', 'credential', 'unauthorized', 'api key',
                         'rate limit', 'service unavailable', 'insufficient_credits')
        if not any(word in detail for word in service_words):
            outage = None
    if outage is None and not uncertain and not network:
        return {'retryable': False}
    if outage is None:
        outage = {'http_status': http_status(error) if uncertain else None,
                  'detail': str(error).strip().replace('\n', ' ')[:500]}
    return _service_outcome(outage, network=network, uncertain=uncertain)


def service_failure_from_saved(outage, *, required_action=None):
    detail = str(outage.get('detail') or '').lower()
    network = outage.get('http_status') is None and any(
        word in detail for word in ('timeout', 'timed out', 'connection'))
    uncertain = required_action == 'reconcile_submission' or bool(outage.get('submission_uncertain'))
    return _service_outcome(outage, network=network, uncertain=uncertain,
                            required_action=required_action)


def is_terminal_failure(row):
    return ('retryable' in row and (row.get('ok') is False or row.get('error')
            or row.get('retake_error') or row.get('service_interrupted')))


def service_vote(rows):
    rows = list(rows)
    if not rows:
        return {}
    service_rows = [row for row in rows if row.get('service_interrupted')]
    result = {'retryable': all(row.get('retryable') is True for row in rows)}
    if service_rows:
        result['service_interrupted'] = dict(service_rows[0]['service_interrupted'])
    if any(row['service_interrupted'].get('submission_uncertain')
           or row.get('required_action') == 'reconcile_submission' for row in service_rows):
        result.update(retryable=False, required_action='reconcile_submission')
    return result


