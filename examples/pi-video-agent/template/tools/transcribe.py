"""Optional SenseVoice batch transcription; use the Python in CAPABILITIES.json.

<asr.python> tools/transcribe.py media/*.mp4 --output-dir inspection/asr-v1
Token timestamps are estimates, not a forced alignment or a quality verdict.
"""
import argparse
import json
from pathlib import Path
import subprocess
import wave


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('media',nargs='+')
    parser.add_argument('--output-dir',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    config=json.loads((root/'CAPABILITIES.json').read_text())['asr']
    model=Path(config['model_dir'])
    import numpy as np
    import sherpa_onnx
    rec=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model/'model.int8.onnx'),
        tokens=str(model/'tokens.txt'),num_threads=2,use_itn=True,language='zh')
    folder=Path(args.output_dir);folder.mkdir(parents=True,exist_ok=True)
    for i,name in enumerate(args.media):
        source=Path(name).resolve();stat=source.stat();output=folder/f'{i:03d}-{source.stem}.json'
        identity={'source':str(source),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
        if output.exists():
            result=json.loads(output.read_text())
            if result.get('input')!=identity:raise ValueError('Changed media: choose a new evidence directory.')
        else:
            wav=output.with_suffix('.wav')
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-vn','-ac','1','-ar','16000',
                            '-c:a','pcm_s16le','-y',str(wav)],check=True)
            with wave.open(str(wav))as f:
                samples=np.frombuffer(f.readframes(f.getnframes()),dtype=np.int16).astype(np.float32)/32768
            stream=rec.create_stream();stream.accept_waveform(16000,samples);rec.decode_stream(stream)
            result={'input':identity,'text':stream.result.text,'tokens':list(stream.result.tokens or []),
                    'timestamps':list(stream.result.timestamps or []),'duration':len(samples)/16000,
                    'scope':'ASR observations with estimated token times; not proof of pronunciation, lip sync or acceptance.'}
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'source':str(source),'text':result['text'],'evidence':str(output)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
