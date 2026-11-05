"""Eureka sensor adapted from the parallel kit. No automatic judgment or device selection."""
import json
import os
from pathlib import Path
import sys
import time

job = json.loads(Path(sys.argv[1]).read_text())
config = job['runtime']
device = config.get('device', 'cpu')
if device.startswith('cuda'):
    os.environ['CUDA_VISIBLE_DEVICES'] = config['cuda_visible_devices']
else:
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    for name in ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS']:
        os.environ[name] = str(config.get('cpu_threads', 8))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
sys.path.insert(0, config['audio_source_root'])
import torch
if not device.startswith('cuda'):
    torch.set_num_threads(int(config.get('cpu_threads', 8)))
import torchaudio
import soundfile

def load(path, *args, **kwargs):
    data, sr = soundfile.read(path, dtype='float32', always_2d=True)
    return torch.from_numpy(data.T.copy()), sr

torchaudio.load = load
from eureka_infer.api import EurekaAudio
model = EurekaAudio(config['audio_model_dir'], device=device)
for row in job['jobs']:
    started = time.monotonic()
    response = model.generate([{'role': 'user', 'content': [
        {'type': 'audio_url', 'audio_url': {'url': row['audio']}},
        {'type': 'text', 'text': row.get('question') or '只依据实际音频转录听清的中文，描述说话人数、音乐环境声、可懂度、爆音和异常断音。听不清就说明，不推测剧情。'}]}],
        max_new_tokens=config.get('max_new_tokens', 256), do_sample=False)
    Path(row['output']).write_text(json.dumps({'model': 'Eureka-Audio-Instruct', 'device': device,
        'audio': row['audio'], 'source_window': row['source_window'],
        'inference_seconds': round(time.monotonic() - started, 3), 'response': response}, ensure_ascii=False, indent=2) + '\n')
