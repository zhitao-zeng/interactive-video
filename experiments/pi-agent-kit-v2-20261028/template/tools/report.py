"""Measure delivery facts; the Agent supplies its review, this script does not grade art.

python tools/report.py output/film.mp4 --review output/self-review.md
"""
import argparse
import json
from pathlib import Path
import subprocess
from media import WORK, file, probe


def report(movie,review,output='output/delivery.json'):
    movie,review=file(movie),file(review)
    if not review.is_file():raise FileNotFoundError('Write the actual self-review before delivery.')
    metadata=probe(movie);streams={s['codec_type']:s for s in metadata['streams']}
    if 'video'not in streams or'audio'not in streams:raise ValueError('Final movie must contain picture and audio.')
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(movie),'-f','null','-'],check=True)
    video,audio=streams['video'],streams['audio']
    budget=json.loads((WORK/'run-budget.json').read_text())if(WORK/'run-budget.json').exists()else{}
    data={'movie':str(movie),'self_review':str(review),'technical_decode_passed':True,
          'duration':float(metadata['format']['duration']),
          'video':{k:video.get(k)for k in ['codec_name','width','height','r_frame_rate','start_time','duration']},
          'audio':{k:audio.get(k)for k in ['codec_name','sample_rate','channels','start_time','duration']},
          'stream_duration_difference_seconds':round(abs(float(video['duration'])-float(audio['duration'])),6),
          'agent_requests_at_measurement':budget.get('provider_requests'),'request_limit':budget.get('request_limit'),
          'note':'Technical facts and Agent self-review only. Track-duration difference does not establish lip sync; no human review claimed.'}
    target=file(output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('movie')
    parser.add_argument('--review',default='output/self-review.md');parser.add_argument('--output',default='output/delivery.json')
    args=parser.parse_args();print(json.dumps(report(args.movie,args.review,args.output),ensure_ascii=False))
