"""Six predeclared, fixed-seed 768P comparisons; no automatic artistic retakes."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import copy
import json
import os
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from novel_manga.interactive.runtime import configure
from novel_manga.config import Settings
from novel_manga.providers.local_h3 import LocalH3MediaProvider
from novel_manga.story.h3 import compose,request_issues
from novel_manga.media.analysis import analyse_clip
from novel_manga.util import atomic_write_json,read_json

OUT=ROOT/'outputs/romance-h3-768-ab'
WORDS=['周叙，建筑师。','以前觉得，事情做完再解释，也来得及。','现在不这么想了。']
SEEDS=[2101,3101]
LABELS={'A':'拼版人物图＋三镜头','B':'单人前采图＋三镜头','C':'单人前采图＋固定机位'}


def prompt(single):
    refs=[{'role':'character','name':'周叙','path':'character'},
          {'role':'location','name':'单人告白间','path':'room'},
          {'role':'voice','name':'周叙','path':'voice'}]
    clip={'clip_id':'probe','kind':'video','request_seconds':13,'references':refs,
          'render_family':'photo','h3_style_line':'Contemporary photorealistic live-action interview, natural adult skin texture, restrained warm-neutral grading. Reference images are design references, never a collage or multiple panels.'}
    state=('Only one <Subject 1> sits in the ivory chair in <Subject 2>, looking at the same silent interviewer just beside the lens. '
           'No other person or reflection appears. His charcoal overshirt has already been removed and remains out of frame; '
           'he wears his stone-gray crewneck, taupe trousers and silver watch on his left wrist. '
           'He keeps his hands still on his lap, his expression serious and calm. Neutral soft interview lighting stays constant. ')
    turns=[('周叙',line,False,'平静') for line in WORDS]
    if single:
        clip['shot_timing']=[{'stage':1,'seconds':13}]
        directions=[state+'A single uninterrupted, locked medium close-up; no cuts or zooms. '
                    'At about 0.5 seconds <DIALOGUE_1> In an even natural Mandarin voice. '
                    'At about 3 seconds <DIALOGUE_2> Reflective and unhurried. '
                    'At about 10 seconds <DIALOGUE_3> Quiet and serious. He stays silent between and after the supplied lines.']
        stages=[('',turns)]
    else:
        clip['shot_timing']=[{'stage':1,'seconds':3},{'stage':2,'seconds':7},{'stage':3,'seconds':3}]
        directions=[state+'A locked medium shot. At about 0.5 seconds <DIALOGUE_1> In an even natural Mandarin voice. He stays silent after the supplied line.',
                    state+'Cut to a locked medium close-up on the same side of the eyeline. <DIALOGUE_1> Reflective and unhurried. He stays silent after the supplied line.',
                    state+'Cut to a locked close-up on the same side of the eyeline. <DIALOGUE_1> Quiet and serious. He stays silent after the supplied line.']
        stages=[('',[turn]) for turn in turns]
    value=compose(clip,directions,stages,soundscape='Continuous quiet indoor room tone. The unseen interviewer remains silent. Only the three supplied Mandarin utterances are heard.',authored=True)
    value=value.replace('Identity and outfit design follow <Picture 1>.','Facial identity and hair follow <Picture 1>; clothing follows the explicitly described interview state.')
    value=value.replace('<Subject 1>: fully_preserved - the identity and outfit design of <Picture 1>;',
                        '<Subject 1>: partially_preserved - identity and hair from <Picture 1>, with the overshirt already removed as described;')
    clip['prompt_h3']=value
    issues=request_issues(clip)
    if issues:raise ValueError(issues)
    return value


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'owner').write_text('Codex / authorized six-sample H3 768P comparison / 2026-10-12\n')
    source=ROOT/'outputs/romance-master/series_assets'
    photo=ROOT/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2/images/周叙-前采一致性验证.png'
    groups={}
    for group in LABELS:
        groups[group]={'label':LABELS[group],'prompt':prompt(group=='C'),
                       'images':[str(source/'characters/character_002/turnaround.jpeg') if group=='A' else str(photo),
                                 str(source/'locations/location_002/establishing.jpeg')],
                       'audio':str(source/'voices/周叙.wav')}
    assert groups['A']['prompt']==groups['B']['prompt']
    manifest={'model':'minimax-h3-ref2va-turbo','short_edge':768,'seconds':13,'seeds':SEEDS,'words':WORDS,
              'groups':groups,'max_generations':6,'automatic_retakes':False,
              'reference_comparison_limit':'A/B compare supplied reference alternatives: layout, framing, visible clothing state and background differ. Not a pure collage-only causal test.',
              'resolution_comparison_limit':'All six samples are native 768P. Earlier 544P episode is historical context, not a matched-seed resolution control.'}
    path=OUT/'manifest.json'
    if path.exists() and read_json(path,{})!=manifest:raise ValueError('Existing experiment has different inputs; retain its record')
    atomic_write_json(path,manifest)
    for group,entry in groups.items():(OUT/f'{group}-prompt.txt').write_text(entry['prompt'])
    pool=copy.deepcopy(read_json(ROOT/'configs/h3_pool.json',{}))
    pool['resident']=[r for r in pool['resident'] if r['url']=='http://172.28.4.52:30022']
    if not pool['resident']:raise ValueError('Expected verified four-GPU endpoint is not configured')
    pool['resident'][0].update(slots=1,gpus=[1,2,4,5])
    pool['night_shift']={}
    atomic_write_json(OUT/'pool.json',pool)
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    configure(ROOT,'h3',project='romance')
    manifest=prepare()
    if args.prepare_only:print('Prepared 6 fixed-seed, 13-second native 768P samples');return
    settings=Settings.from_env(provider='phanrouter',output_root=OUT,admission_mode='preview')
    provider=LocalH3MediaProvider(settings,'pool:'+str(OUT/'pool.json'),ratio='16:9')
    if provider.sampling_parameters()['short_edge']!=768:raise ValueError('Native sampler must be 768P')
    ctx=SimpleNamespace(asr_python=shlex.split(os.environ.get('NOVEL_ASR_COMMAND',''))[0] if os.environ.get('NOVEL_ASR_COMMAND') else sys.executable,
                        asr_helper=ROOT/'scripts/thin_asr_segments.py',protected_terms=['周叙'],aliases={})
    results=[]
    try:
        for seed in SEEDS:
            for group,entry in manifest['groups'].items():
                name=f'{group}-seed{seed}';directory=OUT/name;directory.mkdir(exist_ok=True);video=directory/'clip.mp4'
                atomic_write_json(directory/'request.json',{'group':group,'seed':seed,'prompt':entry['prompt'],'references':entry['images'],
                                                          'reference_audio':entry['audio'],'seconds':13,'sampling':provider.sampling_parameters()})
                print(f'{name}: generating native 768P',flush=True)
                if not video.is_file():provider.create_video(entry['prompt'],None,video,13,
                    additional_images=tuple(Path(p) for p in entry['images']),reference_audios=(Path(entry['audio']),),seed=seed)
                receipt=read_json(video.with_suffix('.mp4.task.json'),{})
                if receipt.get('seed')!=seed or (receipt.get('h3_sampling') or {}).get('short_edge')!=768:
                    raise ValueError('Receipt does not match declared seed and native resolution')
                probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
                stream=next(s for s in probe['streams'] if s['codec_type']=='video')
                if min(stream['width'],stream['height'])!=768:raise ValueError('Downloaded video is not native 768P')
                subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True,capture_output=True)
                analysis=analyse_clip(ctx,{'clip_id':name,'spoken_text':''.join(WORDS)},video)
                row={'name':name,'group':group,'seed':seed,'video':str(video),'width':stream['width'],'height':stream['height'],
                     'duration':float(probe['format']['duration']),'speech':analysis,'receipt':receipt}
                results.append(row);atomic_write_json(OUT/'results.json',results)
                print(f'{name}: {stream["width"]}x{stream["height"]}, speech issues={analysis["issues"]}, CER={analysis["cer"]}',flush=True)
    finally:
        provider.h3_client.close();provider.client.close()


if __name__=='__main__':main()
