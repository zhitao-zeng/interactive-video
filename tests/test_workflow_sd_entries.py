"""Offline acceptance through the three supported SD production entries."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from PIL import Image

from novel_manga.interactive import production, runtime
from novel_manga.application.rendering import reviewed as reviewed_pipeline
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.util import atomic_write_json, read_json


SD_LANES = [('sd25', 'sd2.5'), ('sd20-fast', 'sd2.0-fast'), ('sd20-mini', 'sd2.0-mini')]


@pytest.fixture(scope='session')
def synthetic_video(tmp_path_factory):
    path = tmp_path_factory.mktemp('sd-provider-media') / 'provider-result.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=1080x1920:r=25:d=4', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=4', '-t', '4',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '32', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(path)], check=True)
    return path.read_bytes()


def _prepared(root: Path, edition: str):
    novel = root / 'outputs' / edition
    episode = novel / f'{edition}_1'
    episode.mkdir(parents=True)
    (novel / 'series_assets').mkdir()
    (novel / 'profile.json').write_text(json.dumps({
        'style': '2d', 'frame': '9:16', 'tier': 'fast', 'two_views': 'off',
        'repair_agent_model': 'profile-default-must-not-override-explicit-config'}))
    atomic_write_json(novel / 'story_bible.json', {
        'novel_title': '离线SD入口', 'genre': 'adventure', 'visual_style': '二维国漫',
        'palette': '青蓝', 'style_fingerprint': 'sd-entry-offline',
        'characters': [], 'locations': [], 'props': []})
    reference = novel / 'reference.jpeg'
    Image.new('RGB', (96, 128), (42, 90, 118)).save(reference, 'JPEG')
    voice = novel / 'voice.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=330:sample_rate=16000:duration=1', '-c:a', 'pcm_s16le',
                    str(voice)], check=True)
    atomic_write_json(episode / 'chapter_script.json', {
        'video_title': '房间里的灯', 'source_title': '离线原稿',
        'shots': [{'index': 1, 'origin_index': 1, 'segment_ids': ['s1'], 'location': '小房间',
                   'duration_seconds': 4, 'event': '灯光亮起。', 'sfx': '轻微电流声。',
                   'turns': []}]})
    atomic_write_json(episode / 'segments.json', [{'segment_id': 's1', 'text': '灯光亮起。'}])
    atomic_write_json(episode / 'clip_plan.json', {
        'policy': 'thin-clip-plan-v9-15s', 'limits': {'max_clip_seconds': 15},
        'clips': [{'clip_id': 'clip_01', 'kind': 'video', 'shot_indexes': [1],
                   'request_seconds': 4, 'seconds_estimate': 4, 'location': '小房间',
                   'scene_ids': ['room-1'], 'spoken_text': '', 'lines': [],
                   'prompt': '小房间里，灯光亮起，镜头缓慢推近。',
                   'references': [
                       {'role': 'reference_image', 'name': '场景参考', 'path': 'reference.jpeg'},
                       {'role': 'voice', 'name': '旁白音色', 'path': 'voice.wav'}]}]})
    atomic_write_json(episode / 'review_feedback.json', {})
    atomic_write_json(episode / 'overlays.json', [])
    atomic_write_json(episode / 'interaction.json', {
        'seconds': 3, 'question': '你会继续调查吗？',
        'options': [{'id': 'A', 'label': '继续调查'}, {'id': 'B', 'label': '暂时离开'}]})
    atomic_write_json(episode / 'authored_groups.json', {'schema': 'offline-test'})
    atomic_write_json(root / 'configs' / 'interactive.json', {
        'services': {'text_endpoint': 'phanthy-ds41', 'asset_publish_dir': 'outputs/public'},
        'production': {'workers_by_lane': {'sd25': 1, 'sd20-fast': 1, 'sd20-mini': 1}},
        'projects': {}})
    return novel, episode


@pytest.mark.parametrize('lane,model', SD_LANES)
def test_offline_sd_lane_runs_to_real_delivery_and_resumes_without_resubmit(
        tmp_path, monkeypatch, synthetic_video, lane, model):
    root = tmp_path / 'studio'
    root.mkdir()
    (root / 'skills' / 'video-repair').mkdir(parents=True)
    (root / 'skills' / 'video-repair' / 'SKILL.md').write_text('Offline repair boundary fixture.')
    edition = f'adventure-offline-{lane}'
    novel, episode = _prepared(root, edition)
    generated_video = synthetic_video
    requests = []
    # Earlier host fixtures can import this pipeline while replacing the
    # review service. Restore its import-time alias to the real builder here.
    from novel_manga.application.review.execution import review_saved
    monkeypatch.setattr(reviewed_pipeline, '_review', review_saved)
    json_reviews = []
    force_review_failure = False
    submit_count = 0

    def api(request):
        nonlocal submit_count
        if request.method == 'POST' and request.url.path.endswith('/api/v3/contents/generations/tasks'):
            requests.append(json.loads(request.content))
            submit_count += 1
            return httpx.Response(200, json={'task_id': f'fake-task-{lane}-{submit_count}'})
        if request.method == 'GET' and '/tasks/fake-task-' in request.url.path:
            return httpx.Response(200, json={'data': {
                'status': 'success', 'url': 'https://offline.invalid/provider-result.mp4'}})
        if request.method == 'GET' and request.url.host == 'offline.invalid':
            return httpx.Response(200, content=generated_video, headers={'content-type': 'video/mp4'})
        if request.method == 'POST':
            body = json.loads(request.content)
            name = body.get('response_format', {}).get('json_schema', {}).get('name')
            if name:
                json_reviews.append(body)
                if name == 'clip_review':
                    answer = {'visible_people': 0, 'identity_ok': True, 'identity_issue': '',
                              'location_ok': True, 'time_of_day_ok': True, 'location_issue': '',
                              'text_or_watermark': False, 'unplanned_subtitles': False, 'subtitle_issue': '',
                              'chat_text_ok': True, 'chat_text_issue': '', 'visual_defects': False,
                              'visual_defect_kind': 'none', 'reasonable_deviation': False,
                              'deviation_note': '', 'defect_issue': '', 'story_ok': True,
                              'story_kind': '无问题', 'story_issue': '', 'severity': 'pass', 'feedback': ''}
                    if force_review_failure:
                        answer.update(visual_defects=True, visual_defect_kind='spatial',
                                      defect_issue='Synthetic host review finding.', severity='fail',
                                      feedback='Repair the synthetic issue.')
                elif name == 'clip_presentation':
                    answer = {'layout': 'single', 'body_scale': 'natural',
                              'space_motion': 'natural', 'evidence': 'Frames show one complete scene.'}
                else:
                    pytest.fail(f'unexpected JSON review task: {name}')
                return httpx.Response(200, json={'choices': [{'message': {
                    'content': json.dumps(answer, ensure_ascii=False)}, 'finish_reason': 'stop'}]})
        pytest.fail(f'unexpected mocked provider request {request.method} {request.url}')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        lambda *a, **k: pytest.fail('unmocked sync network request'))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request',
                        lambda *a, **k: pytest.fail('unmocked async network request'))
    real_httpx_client = httpx.Client
    def offline_client(*args, **kwargs):
        kwargs.pop('transport', None)
        kwargs['transport'] = httpx.MockTransport(api)
        kwargs['trust_env'] = False
        return real_httpx_client(*args, **kwargs)
    monkeypatch.setattr(httpx, 'Client', offline_client)
    # External asset publication is the only image-service boundary faked. The real adapter still
    # builds the request payload and the MockTransport records the submitted JSON.
    monkeypatch.setattr(PhanRouterMediaProvider, '_restore_image_url',
                        lambda self, image: 'https://offline.invalid/reference.jpeg')
    monkeypatch.setattr(runtime, 'environment', lambda *args, **kwargs: {})
    monkeypatch.setattr('novel_manga.application.configuration.model_resources_environment', lambda *a: {})
    original_configure = runtime.configure
    def configure_without_streaming(*args, **kwargs):
        original_configure(*args, **kwargs)
        monkeypatch.setenv('QWEN38_LOCAL_STREAM', '0')
    monkeypatch.setattr(runtime, 'configure', configure_without_streaming)
    monkeypatch.setattr(production, 'configure', configure_without_streaming)
    monkeypatch.setenv('PHANROUTER_SD25_KEY', 'offline-video')
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY', 'offline-video')
    monkeypatch.setenv('PHANROUTER_API_KEY', 'offline-video')
    monkeypatch.setenv('NOVEL_PROBE', '0')
    monkeypatch.setenv('NOVEL_FONT_PATH', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    monkeypatch.setenv('QWEN38_LOCAL_STREAM', '0')

    # ASR, content judgment, visual judgment and Eureka are external evidence services.
    helper_root = tmp_path / 'host-tools'
    (helper_root / 'scripts').mkdir(parents=True)
    helper = helper_root / 'scripts' / 'thin_asr_segments.py'
    helper.write_text("""import json,sys\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index('--output')+1])\np.write_text(json.dumps({'segments':[]}))\n""")
    monkeypatch.setattr('novel_manga.application.rendering.setup.project_root', lambda: helper_root)
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', 'phanthy-ds41')
    # This provider fixture serves ordinary JSON, not SSE. The review route is
    # explicit now, so its own transport must match the mocked service too.
    from novel_manga.llm.config import ENDPOINTS
    monkeypatch.setitem(ENDPOINTS, 'phanthy-ds41', {**ENDPOINTS['phanthy-ds41'], 'QWEN38_LOCAL_STREAM':'0'})
    from novel_manga.application.review import final_cut
    def accepted_final_movie(runner, report, *, receipt=None):
        movie = (receipt or {}).get('final_video') or (report.get('assembly') or {}).get('final_video')
        return {'movie': str(Path(movie).resolve()), 'complete': True, 'passed': True,
                'issues': [], 'errors': [], 'frames': [], 'groups': []}
    monkeypatch.setattr(final_cut, 'review_assembly', accepted_final_movie)
    from novel_manga.application.review import final_audio
    audio_jobs = []
    def fake_eureka(runtime_config, code_root, job_path):
        job = json.loads(Path(job_path).read_text())
        audio_jobs.extend(job['jobs'])
        for row in job['jobs']:
            Path(row['output']).write_text(json.dumps({'response': 'A brief room tone and continuous sound.'}))
        return subprocess.CompletedProcess(['fake-eureka'], 0, '', '')
    judge_calls = []
    monkeypatch.setattr(final_audio, '_run_worker', fake_eureka)
    monkeypatch.setattr(final_audio, '_judge', lambda facts, windows:
                        judge_calls.append((facts, windows)) or {'summary': 'No evidenced audio fault.', 'issues': []})
    from novel_manga.application.rendering import h3
    monkeypatch.setattr(h3, 'convert', lambda *a, **k: pytest.fail('SD entry must not call H3.convert'))
    from novel_manga.providers.local_h3 import LocalH3MediaProvider
    monkeypatch.setattr(LocalH3MediaProvider, 'create_video',
                        lambda *a, **k: pytest.fail('SD entry must not call local H3'))
    repair_agent_configs = []
    from novel_manga.application.repair import agent as repair_agent
    from novel_manga.application.agents.sandbox import Attempt
    def fake_repair_agent(run_name, *, config, timeout, **kwargs):
        repair_agent_configs.append(dict(config))
        work = Path(config['runs_root']) / run_name
        output = work / 'output' / 'decision.json'
        atomic_write_json(output, {'action': 'stop', 'reason': 'Offline boundary probe complete.',
                                  'evidence': 'The nested SD repair Agent received its selected config.'})
        return Attempt(run_name, work, 0, 0, produced=['output/decision.json'])
    monkeypatch.setattr(repair_agent, 'run_agent', fake_repair_agent)
    selected_agent_config = {'backend': 'codex', 'model': 'official-sd-repair-model',
                             'runs_root': str(tmp_path / 'official-repair-runs'), 'extra_args': []}

    first = production.run_episode(root, lane, project='adventure', edition=edition,
                                   agent_config=selected_agent_config)
    assert first['status'] == 'accepted', first
    assert submit_count == 1 and len(requests) == 1
    payload = requests[0]
    assert payload['model'] == model
    assert payload['generate_audio'] is True
    assert payload['duration'] == 4
    assert payload['ratio'] == '9:16'
    assert any(part.get('type') == 'text' and '小房间' in part.get('text', '') for part in payload['content'])
    assert any(part.get('type') == 'image_url' and part.get('role') == 'reference_image'
               and part['image_url']['url'] == 'https://offline.invalid/reference.jpeg' for part in payload['content'])
    audio_refs = [part for part in payload['content'] if part.get('type') == 'audio_url']
    assert len(audio_refs) == 1 and audio_refs[0]['role'] == 'reference_audio'
    assert audio_refs[0]['audio_url']['url'].startswith('data:audio/wav;base64,')
    assert not any(part.get('role') == 'first_frame' for part in payload['content'])
    assert len(audio_jobs) >= 1 and len(judge_calls) == 1
    assert {row['response_format']['json_schema']['name'] for row in json_reviews} == {
        'clip_review', 'clip_presentation'}
    assert all(row['messages'][0]['role'] == 'user' for row in json_reviews)
    assert all(any(part.get('type') == 'image_url' for part in row['messages'][0]['content'])
               for row in json_reviews)

    final_review = read_json(episode / 'final_cut_review.json')
    assert final_review['movie']
    final_movie = Path(final_review['movie'])
    receipt = read_json(final_movie.parent.parent.parent / 'delivery.json')
    assert receipt['final_video'] == str(final_movie)
    assert receipt['final_audio_review_passed'] is True
    streams = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                                         str(final_movie)], check=True, capture_output=True, text=True).stdout)['streams']
    video = next(row for row in streams if row['codec_type'] == 'video')
    assert video['r_frame_rate'] == '25/1'
    assert any(row['codec_type'] == 'audio' and row['codec_name'] == 'aac' for row in streams)

    # A normal continuation omits --config; the task must keep its original selected repair Agent.
    resumed = production.run_episode(root, lane, project='adventure', edition=edition, resume=True)
    assert resumed['status'] == 'accepted', resumed
    assert submit_count == 1
    assert len(requests) == 1
    assert len(audio_jobs) >= 1 and len(judge_calls) == 1
    assert len(json_reviews) == 2

    # An actual host review finding enters the normal managed repair route. Fake only the external
    # Agent process; assert the run_episode-selected official config reaches its API boundary.
    force_review_failure = True
    monkeypatch.setenv('NOVEL_REVIEW_FRESH', '1')
    from novel_manga.application.rendering import reviewed as reviewed_run
    from novel_manga.application.review.execution import review_saved
    monkeypatch.setattr(reviewed_run, '_review', lambda directory, **kwargs:
                        review_saved(directory, fresh=True))
    repaired = production.run_episode(root, lane, project='adventure', edition=edition, resume=True)
    assert repair_agent_configs
    assert all(config['model'] == selected_agent_config['model'] for config in repair_agent_configs)
    assert all(config['runs_root'] == selected_agent_config['runs_root'] for config in repair_agent_configs)
    assert submit_count == 1
