"""The audio question reaches the existing model; no real inference or paid calls."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types

import pytest

sys.dont_write_bytecode = True


TOOLS = Path(__file__).parents[1] / 'examples/pi-video-agent/template/tools'


@pytest.mark.parametrize('question', [None, 'Describe the actual music; do not invent speech.'])
def test_resident_service_forwards_audio_and_optional_question(tmp_path, question):
    spec = importlib.util.spec_from_file_location('question_service', TOOLS / 'audio_service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / 'observation.json'
    row = {'audio': str(tmp_path / 'sample.wav'), 'output': str(output),
           'source_window': {'start': 0, 'end': 8}}
    if question:
        row['question'] = question
    job = {'id': 'one', 'jobs': [row], 'runtime': {'max_new_tokens': 256}}
    class Queue:
        used = False
        def get(self):
            if self.used:
                raise StopIteration
            self.used = True
            return job
        def task_done(self):
            pass
    captured = []
    class Model:
        def generate(self, messages, **kwargs):
            captured.append(messages)
            return 'fixture observation'
    service = module.AudioService.__new__(module.AudioService)
    service.allow_root, service.device, service.model = tmp_path, 'cuda', Model()
    module.QUEUE = Queue()
    with pytest.raises(StopIteration):
        service.worker()
    content = captured[0][0]['content']
    assert content[0]['audio_url']['url'] == row['audio']
    if question:
        assert content[1]['text'] == question
    else:
        assert '转录听清的中文' in content[1]['text']
    assert json.loads(output.read_text())['source_window'] == row['source_window']


def test_cpu_worker_bounds_threads_and_does_not_initialize_a_video_gpu(tmp_path, monkeypatch):
    output = tmp_path / 'observation.json'
    request = {'runtime': {'device': 'cpu', 'cpu_threads': 3,
                           'audio_source_root': str(tmp_path), 'audio_model_dir': str(tmp_path)},
               'jobs': [{'audio': str(tmp_path / 'sample.wav'), 'output': str(output),
                         'source_window': {'start': 0, 'end': 8}, 'question': 'Describe music.'}]}
    job = tmp_path / 'job.json'
    job.write_text(json.dumps(request))
    calls = []
    torch = types.SimpleNamespace(set_num_threads=lambda n: calls.append(n))
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def generate(self, messages, **kwargs):
            assert messages[0]['content'][1]['text'] == 'Describe music.'
            return 'fixture observation'
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setitem(sys.modules, 'torchaudio', types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'soundfile', types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'eureka_infer.api', types.SimpleNamespace(EurekaAudio=Model))
    monkeypatch.setattr(sys, 'argv', [str(TOOLS / 'audio_worker.py'), str(job)])
    for name in ['CUDA_VISIBLE_DEVICES', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS']:
        monkeypatch.setenv(name, 'inherited-value')
    runpy.run_path(str(TOOLS / 'audio_worker.py'), run_name='__main__')
    import os
    assert calls == [3]
    assert os.environ['CUDA_VISIBLE_DEVICES'] == ''
    assert os.environ['OMP_NUM_THREADS'] == '3'
    assert json.loads(output.read_text())['device'] == 'cpu'


def test_finished_owner_cleanup_stops_only_its_audio_worker_and_keeps_evidence(tmp_path):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from pi_episode_review import cleanup_local_audio
    owner=tmp_path/'owner';other=tmp_path/'other';owner.mkdir();other.mkdir()
    worker=tmp_path/'audio_worker.py';worker.write_text('import time;time.sleep(30)')
    original={'jobs':[{'output':str(owner/'done.json')},{'output':str(owner/'pending.json')}]}
    (owner/'job.json').write_text(json.dumps(original));(owner/'done.json').write_text('{"actual":"retained"}')
    (other/'job.json').write_text('{"jobs":[]}')
    owned=subprocess.Popen([sys.executable,str(worker),str(owner/'job.json')])
    foreign=subprocess.Popen([sys.executable,str(worker),str(other/'job.json')])
    try:
        cleanup_local_audio(owner)
        assert owned.wait(timeout=5)<0
        assert foreign.poll() is None
        assert json.loads((owner/'job.json').read_text())==original
        assert (owner/'done.json').read_text()=='{"actual":"retained"}'
        assert not (owner/'pending.json').exists()
    finally:
        for process in [owned,foreign]:
            if process.poll() is None:process.terminate()
            process.wait(timeout=5)
