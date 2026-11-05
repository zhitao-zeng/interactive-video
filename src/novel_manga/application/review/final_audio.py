"""Evidence-based, model-assisted review of the actual delivered movie audio."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess

from novel_manga.util import atomic_write_json, read_json

POLICY = 'eureka-final-movie-audio-review-v1'
WINDOW_SECONDS = 30
OVERLAP_SECONDS = 2
SAMPLE_RATE = 16000


def _file_state(path: Path):
    if not path.is_file():
        return None
    stat = path.stat()
    return {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _model_state(path: Path):
    files = []
    for item in sorted(path.rglob('*')):
        if item.is_file():
            stat = item.stat()
            files.append([str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns])
    return {'path': str(path.resolve()), 'files': files}


def _write_float_wav(path: Path, samples: bytes):
    count = len(samples) // 4
    data_size = count * 4
    with path.open('wb') as stream:
        stream.write(b'RIFF' + struct.pack('<I', 36 + data_size) + b'WAVE')
        stream.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 3, 1, SAMPLE_RATE,
                                            SAMPLE_RATE * 4, 4, 32))
        stream.write(b'data' + struct.pack('<I', data_size))
        stream.write(samples)


def _decode(movie: Path, directory: Path, identity: dict, duration: float):
    pcm = directory / 'decoded-f32le.pcm'
    stamp = directory / 'decode.json'
    if read_json(stamp, {}).get('identity') != identity or not pcm.is_file():
        pcm.unlink(missing_ok=True)
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(movie), '-map', '0:a:0',
                        '-vn', '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 'f32le', str(pcm)],
                       check=True, env={**os.environ, 'CUDA_VISIBLE_DEVICES': '',
                                        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
        atomic_write_json(stamp, {'identity': identity, 'sample_rate': SAMPLE_RATE,
                                  'samples': pcm.stat().st_size // 4, 'duration': duration})
    samples = pcm.stat().st_size // 4
    expected = round(duration * SAMPLE_RATE)
    # ffmpeg may differ by a sample due to container rounding; it must cover movie duration.
    if samples < expected - 1:
        raise ValueError('decoded audio is shorter than the final movie duration')
    return pcm, samples


def _windows(pcm: Path, samples: int, duration: float, directory: Path):
    max_samples = WINDOW_SECONDS * SAMPLE_RATE
    stride = (WINDOW_SECONDS - OVERLAP_SECONDS) * SAMPLE_RATE
    rows = []
    start_sample = 0
    with pcm.open('rb') as source:
        while start_sample < samples:
            end_sample = min(start_sample + max_samples, samples)
            start = start_sample / SAMPLE_RATE
            end = min(end_sample / SAMPLE_RATE, duration)
            path = directory / f'window-{len(rows):04d}.wav'
            source.seek(start_sample * 4)
            _write_float_wav(path, source.read((end_sample - start_sample) * 4))
            rows.append({'index': len(rows), 'start': round(start, 3), 'end': round(end, 3),
                         'path': str(path), 'output': str(directory / f'window-{len(rows):04d}.json')})
            if end_sample == samples:
                break
            start_sample += stride
    if not rows or rows[0]['start'] != 0 or rows[-1]['end'] < duration - .02:
        raise ValueError('audio windows do not cover the final movie')
    for left, right in zip(rows, rows[1:]):
        if right['start'] > left['end'] or right['end'] - right['start'] > WINDOW_SECONDS + .001:
            raise ValueError('audio window coverage has a gap or exceeds 30 seconds')
    return rows


def _source_facts(episode_dir: Path):
    script = read_json(episode_dir / 'chapter_script.json', {})
    shots = []
    for shot in script.get('shots') or []:
        turns = [{key: turn.get(key) for key in ('speaker_name', 'text', 'delivery_mode', 'emotion')}
                 for turn in (shot.get('turns') or [])]
        shots.append({key: shot.get(key) for key in
                      ('index', 'source_shot', 'segment_id', 'location', 'scene_id', 'scene_time',
                       'duration_seconds', 'sfx', 'audio_plan')} | {'turns': turns})
    clip_plan = read_json(episode_dir / 'clip_plan.json', {})
    clips = [{key: clip.get(key) for key in
              ('clip_id', 'kind', 'shot_indexes', 'seconds_estimate', 'music_delivery', 'shot_sound',
               'spoken_text', 'dialogue_bindings')}
             for clip in (clip_plan.get('clips') or [])]
    timeline = read_json(episode_dir / 'timeline.json', {})
    media = read_json(episode_dir / 'thin_media_report.json', {})
    assembly_timeline = ((media.get('assembly') or {}).get('timeline') or [])
    if not assembly_timeline:
        assembly_timeline = timeline.get('timeline') or timeline.get('clips') or []
    timeline_facts = [{key: row.get(key) for key in ('unit_id', 'clip_id', 'role', 'start', 'end')}
                      for row in assembly_timeline]
    policy = read_json(episode_dir / 'delivery_policy.json', {})
    music_policy = read_json(episode_dir / 'music_policy.json', {})
    facts = {
        'chapter_script': {'music_plan': script.get('music_plan'), 'shots': shots},
        'clip_plan': clips,
        'timeline': timeline_facts,
        'music_policy': music_policy,
        'delivery_audio_policy': {key: policy.get(key) for key in
                                  ('episode_music', 'episode_ambience', 'interaction_ambience')},
    }
    # Semantic values only: save_result rewrites the report's counters, status, timing and reviews.
    return facts, facts


def _duration(movie: Path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'json', str(movie)], check=True, capture_output=True, text=True)
    duration = float(json.loads(result.stdout)['format']['duration'])
    if duration <= 0:
        raise ValueError('final movie has no positive duration')
    return duration


def _cache_directory(parent: Path, movie: Path, identity: dict):
    movie_state = identity['movie']
    base = parent / f"{movie.name}-{movie_state['size']}-{movie_state['mtime_ns']}"
    candidate = base
    suffix = 1
    while candidate.exists():
        saved = read_json(candidate / 'identity.json', {})
        if saved.get('identity') == identity:
            return candidate
        suffix += 1
        candidate = Path(f'{base}-{suffix}')
    candidate.mkdir(parents=True)
    atomic_write_json(candidate / 'identity.json', {'identity': identity})
    return candidate


def _judge(facts, windows):
    from novel_manga.llm.client import ask_json, obj
    from novel_manga.review.endpoints import judge_settings
    prompt = ('按本集原稿、clip plan、时间线、配乐/声景意图和下列整片音频窗口原始Eureka响应，'
              '判断最终电影中可由证据确认的具体音频问题。每个问题必须有电影全局秒数、明确证据与原稿依据。'
              '没有配乐、原稿要求安静或声音模型未提及某事，都不能单独算错；不得推断声音真实/AI来源。'
              '不要把模型自然语言直接当成通过。无具体问题时issues为空。该模型分析不等于人工听审。\n' +
              json.dumps({'episode_facts': facts, 'audio_windows': windows}, ensure_ascii=False))
    issue = obj({'start': {'type': 'number'}, 'end': {'type': 'number'},
                 'problem': {'type': 'string'}, 'evidence': {'type': 'string'},
                 'source_basis': {'type': 'string'}})
    schema = obj({'summary': {'type': 'string'}, 'issues': {'type': 'array', 'items': issue}})
    return ask_json([{'type': 'text', 'text': prompt}], schema, name='final_movie_audio_review',
                    max_tokens=3000, timeout=240, retry_truncated=False, settings=judge_settings())


def _run_worker(runtime, root, job_path):
    env = {**os.environ, 'CUDA_VISIBLE_DEVICES': '', 'HF_HUB_OFFLINE': '1',
           'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '8', 'MKL_NUM_THREADS': '8'}
    script = root / 'scripts' / 'music_library_worker.py'
    return subprocess.run([runtime['python'], str(script), str(job_path)], cwd=root,
                          env=env, timeout=3600, capture_output=True, text=True)


def _timeline_rows(facts):
    return facts.get('timeline') or []


def _next_review(directory: Path):
    return directory / f"review_{len(list(directory.glob('review_[0-9][0-9][0-9].json'))) + 1:03d}.json"


def _save_judgment(directory: Path, record: dict, path: Path | None = None):
    path = path or _next_review(directory)
    atomic_write_json(path, record)
    return path


def _cached_review(directory: Path, identity: dict, sources: dict):
    for path in sorted(directory.glob('review_[0-9][0-9][0-9].json'), reverse=True):
        record = read_json(path, {})
        result = record.get('result') or {}
        if (record.get('policy') == POLICY and record.get('identity') == identity
                and record.get('source_identity') == sources and record.get('validation') == 'accepted'
                and result.get('complete') is True):
            return result
    return None


def review_audio(root, episode_dir, movie, *, runtime=None) -> dict:
    root, episode_dir, movie = Path(root).resolve(), Path(episode_dir).resolve(), Path(movie).resolve()
    report_path = episode_dir / 'final_audio_review.json'
    result = {'movie': str(movie), 'complete': False, 'passed': False, 'issues': [], 'errors': [],
              'coverage': [], 'report': str(report_path), 'model': 'Eureka-Audio-Instruct',
              'policy': POLICY,
              'scope': 'CPU model-assisted full final-movie audio review; not human listening'}

    def save():
        atomic_write_json(report_path, result)
        return result

    if not movie.is_file():
        result['errors'].append('final movie is unavailable')
        return save()
    try:
        if runtime is None:
            from novel_manga.application.music_selection import configuration
            configured = configuration(root)
        else:
            configured = runtime
        keys = ('python', 'audio_source_root', 'audio_model_dir')
        if not isinstance(configured, dict) or any(not isinstance(configured.get(key), str) or not configured[key]
                                                    for key in keys):
            raise ValueError('Eureka runtime requires python, audio_source_root and audio_model_dir')
        # Runtime files may contain unrelated private configuration; pass only these non-secret paths.
        runtime = {key: configured[key] for key in keys}
        model_dir = Path(runtime['audio_model_dir'])
        if not model_dir.is_dir() or not Path(runtime['audio_source_root']).is_dir():
            raise ValueError('configured Eureka model or source package directory is missing')
        identity = {'movie': _file_state(movie), 'model': _model_state(model_dir),
                    'audio_source_root': str(Path(runtime['audio_source_root']).resolve())}
        sources, facts = _source_facts(episode_dir)
        result['identity'] = identity
        previous = read_json(report_path, {})
        if (previous.get('policy') == POLICY and previous.get('identity') == identity
                and previous.get('source_identity') == sources
                and previous.get('complete') is True):
            return previous
        result['source_identity'] = sources
        directory = _cache_directory(episode_dir / 'work' / 'final-audio-review', movie, identity)
        cached = _cached_review(directory, identity, sources)
        if cached:
            cached['report'] = str(report_path)
            atomic_write_json(report_path, cached)
            return cached
        duration = _duration(movie)
        pcm, sample_count = _decode(movie, directory, identity, duration)
        rows = _windows(pcm, sample_count, duration, directory)
        result['coverage'] = [{'start': row['start'], 'end': row['end']} for row in rows]
        jobs = []
        for row in rows:
            if not Path(row['output']).is_file():
                jobs.append({'id': f"final-audio-{row['index']:04d}", 'audio': str(Path(row['path']).resolve()),
                             'output': row['output'], 'question': ('只依据实际音频，简要报告语言和一段简短可辨原话（或说明听不清）、'
                                 '说话人数与音色变化、环境声和音乐、背景声是否遮挡对白，以及突兀剪辑、爆音、失真、断音或异常静音。'
                                 '有问题时说明片段内的大致秒数；没有音乐或静音也可能是有意安排。不要推测剧情、声源身份或是否AI。'),
                             'source_windows': [{'start': row['start'], 'end': row['end']}],
                             'scope': 'final delivered movie window; not human listening', 'max_new_tokens': 160})
        if jobs:
            job_path = directory / 'worker-job.json'
            atomic_write_json(job_path, {'action': 'audio', 'runtime': runtime, 'jobs': jobs})
            done = _run_worker(runtime, root, job_path)
            if done.returncode:
                atomic_write_json(directory / 'worker-error.json', {'returncode': done.returncode,
                    'stdout': done.stdout, 'stderr': done.stderr})
                raise RuntimeError('Eureka audio worker failed; raw completed windows and worker-error.json retained')
        parsed = []
        for row in rows:
            output = Path(row['output'])
            if not output.is_file():
                raise RuntimeError(f"Eureka window {row['index']} is missing")
            item = read_json(output, {})
            if not isinstance(item, dict) or not item.get('response'):
                raise RuntimeError(f"Eureka window {row['index']} response is missing or malformed")
            parsed.append({'start': row['start'], 'end': row['end'], 'response': item['response']})
        history = {'policy': POLICY, 'identity': identity, 'source_identity': sources,
                   'source_facts': facts, 'windows': parsed, 'validation': 'pending'}
        history_path = _next_review(directory)
        try:
            judgment = _judge(facts, parsed)
            history['raw_judgment'] = judgment
            _save_judgment(directory, history, history_path)
            if (not isinstance(judgment, dict) or not isinstance(judgment.get('summary'), str)
                    or not isinstance(judgment.get('issues'), list)):
                raise ValueError('final audio text judge returned a malformed response')
            timeline = _timeline_rows(facts)
            issues = []
            for issue in judgment['issues']:
                if (not isinstance(issue, dict) or type(issue.get('start')) not in (int, float)
                        or type(issue.get('end')) not in (int, float) or not issue.get('problem')
                        or not issue.get('evidence') or not issue.get('source_basis')
                        or issue['start'] < 0 or issue['end'] <= issue['start'] or issue['end'] > duration + .02):
                    raise ValueError('final audio text judge returned an unbound or malformed issue')
                overlaps = []
                for row in timeline:
                    start, end = row.get('start'), row.get('end')
                    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and start < issue['end'] and end > issue['start']:
                        overlaps.append(row.get('clip_id') or row.get('unit_id') or row.get('id'))
                clip_ids = list(dict.fromkeys(value for value in overlaps if value))
                issues.append({**issue, 'start': round(issue['start'], 3), 'end': round(issue['end'], 3),
                               'clip_ids': clip_ids, **({'scope': 'final_movie_global'} if not clip_ids else {})})
            result.update({'complete': True, 'passed': not issues, 'issues': issues,
                           'summary': judgment['summary'], 'windows': parsed,
                           'coverage_seconds': duration})
            history.update({'validation': 'accepted', 'result': result})
            _save_judgment(directory, history, history_path)
            return save()
        except Exception as judgment_error:
            history.update({'validation': 'rejected', 'validation_error': f'{type(judgment_error).__name__}: {judgment_error}'})
            _save_judgment(directory, history, history_path)
            raise
    except Exception as error:
        result['errors'].append(f'{type(error).__name__}: {error}')
        return save()
