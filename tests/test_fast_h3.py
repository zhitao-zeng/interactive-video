import base64
import hashlib
import json
from pathlib import Path
import wave

import httpx
from PIL import Image
import pytest

from novel_manga.config import Settings
from novel_manga.providers.fast_h3 import FastH3MediaProvider, TaskExpiredError


def provider_for(handler):
    provider = FastH3MediaProvider(Settings(request_timeout=1, poll_timeout=1), 'http://uniserve.test:30027', ratio='9:16')
    provider.fast_client.close()
    provider.fast_client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def read_receipt(output):
    return json.loads(output.with_suffix(output.suffix + '.task.json').read_text())


def inputs(tmp_path):
    first = tmp_path / 'first.png'
    second = tmp_path / 'second.png'
    Image.new('RGB', (3, 5), 'red').save(first)
    Image.new('RGB', (4, 6), 'blue').save(second)
    audio = tmp_path / 'voice.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0' * 320)
    return first, second, audio


def decode(uri):
    return base64.b64decode(uri.split(',', 1)[1], validate=True)


def test_payload_preserves_reference_bytes_order_seed_and_target(tmp_path):
    first, second, audio = inputs(tmp_path)
    provider = provider_for(lambda request: pytest.fail('no network expected'))
    payload = provider._payload('original prompt', [first, second], (audio,), 5.0, seed=2101, short_edge=768)
    assert payload['model'] == 'FastH3-Omni'
    assert payload['task'] == 'ref2va'
    assert payload['prompt'] == 'original prompt'
    assert payload['seed'] == 2101
    assert payload['target'] == {'short_edge': 768, 'aspect_ratio': '9:16', 'duration_seconds': 5.0}
    conditions = payload['conditions']
    assert [row['role'] for row in conditions] == ['reference', 'reference', 'reference']
    assert decode(conditions[0]['uri']) == first.read_bytes()
    assert decode(conditions[1]['uri']) == second.read_bytes()
    assert decode(conditions[2]['uri']) == audio.read_bytes()
    assert all(row.get('frame_index') is None for row in conditions)


def test_in_progress_to_completed_download_and_same_task_resume(tmp_path):
    first, second, audio = inputs(tmp_path)
    output = tmp_path / 'nested' / 'clip.mp4'
    calls = {'post': 0, 'status': 0}

    def handler(request):
        if request.method == 'POST':
            calls['post'] += 1
            return httpx.Response(202, json={'id': 'task-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'local fake MP4', headers={'Content-Type': 'video/mp4'})
        calls['status'] += 1
        status = 'in_progress' if calls['status'] == 1 else 'completed'
        return httpx.Response(200, json={'id': 'task-A', 'status': status, 'inference_time_s': 3.25})

    provider = provider_for(handler)
    result = provider.create_video('original prompt', None, output, 5.0,
        additional_images=(first, second), reference_audios=(audio,), seed=2101)
    assert result == output and output.read_bytes() == b'local fake MP4'
    assert not output.with_suffix('.mp4.partial').exists()
    assert calls == {'post': 1, 'status': 2}
    receipt = read_receipt(output)
    assert receipt['task_id'] == 'task-A'
    assert receipt['seed'] == 2101
    assert receipt['target']['aspect_ratio'] == '9:16'
    assert [row['status'] for row in receipt['status_history']] == ['in_progress', 'completed']
    assert receipt['attachments'][0]['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert receipt['attachments'][1]['sha256'] == hashlib.sha256(second.read_bytes()).hexdigest()
    assert receipt['attachments'][2]['sha256'] == hashlib.sha256(audio.read_bytes()).hexdigest()
    provider.create_video('original prompt', None, output, 5.0,
        additional_images=(first, second), reference_audios=(audio,), seed=2101)
    assert calls['post'] == 1


def test_expired_task_never_posts_again(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'expired.mp4'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
            return httpx.Response(202, json={'id': 'expired-A'})
        return httpx.Response(404, json={'error': {'message': 'expired'}})

    provider = provider_for(handler)
    for _ in range(2):
        with pytest.raises(TaskExpiredError, match='expired-A'):
            provider.create_video('prompt', None, output, 5.0,
                additional_images=(first,), seed=2101)
    assert len(posts) == 1


def test_http_400_body_is_retained(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'bad.mp4'

    def handler(request):
        return httpx.Response(400, json={'error': {'message': 'conditions[0] invalid'}})

    provider = provider_for(handler)
    with pytest.raises(RuntimeError, match=r'conditions\[0\] invalid'):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    evidence = json.loads(output.with_suffix('.http-error.json').read_text())
    assert evidence['status'] == 400
    assert evidence['body']['error']['message'] == 'conditions[0] invalid'
    assert 'headers' not in evidence


def test_failed_take_is_retained_and_polled_without_repost(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'failed.mp4'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
            return httpx.Response(202, json={'id': 'failed-A'})
        return httpx.Response(200, json={'id': 'failed-A', 'status': 'failed', 'error': 'worker failed'})

    provider = provider_for(handler)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='worker failed'):
            provider.create_video('prompt', None, output, 5.0,
                additional_images=(first,), seed=2101)
    assert len(posts) == 1
    receipt = read_receipt(output)
    assert receipt['task_id'] == 'failed-A'
    assert receipt['status'] == 'failed'


def test_unknown_status_is_an_error_not_a_failure_take(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'unknown.mp4'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
            return httpx.Response(202, json={'id': 'unknown-A'})
        return httpx.Response(200, json={'id': 'unknown-A', 'status': 'mystery'})

    provider = provider_for(handler)
    with pytest.raises(RuntimeError, match='mystery'):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    assert len(posts) == 1
    assert read_receipt(output)['status'] == 'mystery'


def test_resume_rejects_changed_request_instead_of_repost(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'changed.mp4'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
            return httpx.Response(202, json={'id': 'same-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake MP4', headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={'id': 'same-A', 'status': 'completed'})

    provider = provider_for(handler)
    provider.create_video('prompt', None, output, 5.0,
        additional_images=(first,), seed=2101)
    with pytest.raises(ValueError, match='new output'):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2102)
    assert len(posts) == 1


@pytest.mark.parametrize('endpoint', ['', 'pool', 'http://user:pass@example.test', 'http://example.test/?token=secret'])
def test_endpoint_must_be_explicit_and_safe_to_record(endpoint):
    with pytest.raises(ValueError):
        FastH3MediaProvider(Settings(), endpoint)


def test_http_download_error_body_is_retained(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'download-error.mp4'

    def handler(request):
        if request.method == 'POST':
            return httpx.Response(202, json={'id': 'download-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(503, text='artifact temporarily unavailable')
        return httpx.Response(200, json={'id': 'download-A', 'status': 'completed'})

    provider = provider_for(handler)
    with pytest.raises(RuntimeError, match='artifact temporarily unavailable'):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    assert not output.exists()
    assert json.loads(output.with_suffix('.http-error.json').read_text())['status'] == 503


def test_default_seed_and_final_server_fields(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'default-seed.mp4'
    posted = []

    def handler(request):
        if request.method == 'POST':
            posted.append(json.loads(request.content))
            return httpx.Response(202, json={'id': 'server-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake MP4', headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={
            'id': 'server-A', 'model': 'FastH3-Omni', 'status': 'completed',
            'seconds': '5.166667', 'actual_seconds': '5.166667', 'size': '768x1344',
            'num_frames': 124, 'expires_at': 123456789, 'inference_time_s': 2.5})

    provider = provider_for(handler)
    provider.create_video('prompt', None, output, 5.0, additional_images=(first,))
    assert posted[0]['seed'] == 42
    receipt = read_receipt(output)
    assert receipt['seed'] == 42
    assert receipt['server_task_id'] == 'server-A'
    assert receipt['server_model'] == 'FastH3-Omni'
    assert receipt['actual_seconds'] == '5.166667'
    assert receipt['size'] == '768x1344'
    assert receipt['expires_at'] == 123456789
    assert receipt['server_response']['num_frames'] == 124


@pytest.mark.parametrize('response', [
    httpx.Response(302, text='<html>redirect</html>'),
    httpx.Response(204, content=b''),
])
def test_submit_requires_2xx_json_with_task_id(tmp_path, response):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'submit-protocol.mp4'
    posts = []

    def handler(request):
        posts.append(request)
        return response

    provider = provider_for(handler)
    with pytest.raises((RuntimeError, ValueError)):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    assert len(posts) == 1
    assert read_receipt(output)['status'] == 'submitting'
    assert not output.exists()


def test_state_requires_2xx_json(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'state-protocol.mp4'

    def handler(request):
        if request.method == 'POST':
            return httpx.Response(202, json={'id': 'state-A'})
        return httpx.Response(204, content=b'')

    provider = provider_for(handler)
    with pytest.raises((RuntimeError, ValueError)):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    assert not output.exists()


@pytest.mark.parametrize('status,content,content_type', [
    (302, b'<html>redirect</html>', 'text/html'),
    (204, b'', 'video/mp4'),
    (200, b'', 'video/mp4'),
    (200, b'not an mp4', 'text/plain'),
])
def test_download_cannot_replace_good_file_with_invalid_response(tmp_path, status, content, content_type):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'good.mp4'
    output.write_bytes(b'known good')

    def handler(request):
        if request.method == 'POST':
            return httpx.Response(202, json={'id': 'download-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(status, content=content, headers={'Content-Type': content_type})
        return httpx.Response(200, json={'id': 'download-A', 'status': 'completed'})

    provider = provider_for(handler)
    with pytest.raises(RuntimeError):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    assert output.read_bytes() == b'known good'


def test_timeout_retains_submitting_receipt_and_never_reposts(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'timeout.mp4'
    posts = []

    def handler(request):
        posts.append(request)
        raise httpx.ReadTimeout('reply lost after submission', request=request)

    provider = provider_for(handler)
    for _ in range(2):
        with pytest.raises((httpx.ReadTimeout, RuntimeError)):
            provider.create_video('prompt', None, output, 5.0,
                additional_images=(first,), seed=2101)
    assert len(posts) == 1
    receipt = read_receipt(output)
    assert receipt['status'] == 'submitting'
    assert receipt['task_id'] is None


def test_wire_response_recovers_task_after_receipt_crash_without_post(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'recover-wire.mp4'
    posts = []

    def handler(request):
        if request.method == 'POST':
            posts.append(request)
            return httpx.Response(202, json={'id': 'wire-A'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake MP4', headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={'id': 'wire-A', 'status': 'completed'})

    provider = provider_for(handler)
    original_submit = provider._submit

    def crash_after_response(out, payload):
        original_submit(out, payload)
        raise RuntimeError('crash before canonical receipt update')

    provider._submit = crash_after_response
    with pytest.raises(RuntimeError, match='canonical receipt'):
        provider.create_video('prompt', None, output, 5.0,
            additional_images=(first,), seed=2101)
    request_sha256 = read_receipt(output)['request_sha256']
    output.with_suffix('.mp4.task.json').unlink()
    folder = output.with_suffix('.mp4.wire')
    folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({
        'kind': 'fasth3', 'request_sha256': request_sha256,
        'endpoint': 'http://uniserve.test:30027/v1/videos'}))
    (folder / 'response.json').write_text(json.dumps({'http_status': 202, 'task_id': 'wire-A'}))
    provider._submit = original_submit
    provider.create_video('prompt', None, output, 5.0,
        additional_images=(first,), seed=2101)
    assert len(posts) == 1
    assert output.read_bytes() == b'fake MP4'
    assert read_receipt(output)['task_id'] == 'wire-A'


def test_resume_uses_receipt_endpoint_not_current_provider_endpoint(tmp_path):
    first, _, _ = inputs(tmp_path)
    output = tmp_path / 'original-endpoint.mp4'
    endpoint_a = 'http://endpoint-a.test:30027'
    endpoint_b = 'http://endpoint-b.test:30027'
    posts = []
    gets = []

    def handler(request):
        url = str(request.url)
        if request.method == 'POST':
            posts.append(url)
            return httpx.Response(202, json={'id': 'original-A'})
        gets.append(url)
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake MP4', headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={'id': 'original-A', 'status': 'completed'})

    provider = FastH3MediaProvider(Settings(request_timeout=1, poll_timeout=1), endpoint_b, ratio='9:16')
    payload = provider._payload('prompt', [first], (), 5.0, seed=2101, short_edge=768)
    output.with_suffix('.mp4.task.json').write_text(json.dumps({
        'kind': 'fasth3', 'model': 'FastH3-Omni',
        'endpoint': endpoint_a + '/v1/videos',
        'request_sha256': provider._request_digest(payload),
        'seed': 2101, 'target': payload['target'],
        'attachments': [], 'task_id': 'original-A', 'status': 'accepted',
        'status_history': [],
    }))
    provider.fast_client.close()
    provider.fast_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider.create_video('prompt', None, output, 5.0,
        additional_images=(first,), seed=2101)
    assert output.read_bytes() == b'fake MP4'
    assert posts == []
    assert gets
    assert all(url.startswith(endpoint_a) for url in gets)
    assert all(not url.startswith(endpoint_b) for url in gets)
    assert read_receipt(output)['endpoint'] == endpoint_a + '/v1/videos'
