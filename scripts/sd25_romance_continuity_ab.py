"""Authorized 26s A/B: frozen old prompts vs state repair + Blender reference.

Two source clips per arm, one submission each. No artistic retries or changes
while generating. Existing production media and histories remain read-only.
"""
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
import argparse
import copy
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from novel_manga.util import read_json, atomic_write_json
from novel_manga.interactive.runtime import configure
from novel_manga.interactive.continuity import repair_room_shots
from novel_manga.models.directing import HandoffState
from novel_manga.models.bible import StoryBible
from novel_manga.application.packing.context import context_for_plan
from novel_manga.application.packing.service import prepared_shots,clip_entry
from novel_manga.repair.proposal import RepairProposal
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.base import ImageResult
from novel_manga.config import Settings
from novel_manga.media.analysis import analyse_clip

SOURCE=Path('/mnt/disk1/zengzhitao/interactive-video-studio')
OUT=ROOT/'outputs/romance-sd25-continuity-ab'
CLIPS=['clip_05','clip_06']
LAYOUT=read_json(ROOT/'projects/romance/reference-layout.json',{})
SET=('同一海边小屋客厅，约6×5米；北墙西侧是唯一入户门，东墙三格海景窗。'
     '始终恰好四把象牙白软包橡木扶手椅和一张胡桃木椭圆矮茶几，不能新增椅子。'
     '固定座位：许知意东北、陆屿西北、宋乔西南；东南椅此时无人，周叙尚未到场。'
     '茶几是膝前矮桌，座面约48厘米、桌面约45厘米；数字为本次空间设计假设。'
     '反打只改变机位，不镜像房间、不移动椅子；人物不入镜时仍在原座位。')


def source_edits(script):
    """This sample's states are grounded in source scenes 1/2/3, not guessed."""
    by_index={s['index']:s for s in script['shots']}
    assert by_index[19]['source_scene']==3 and by_index[19]['source_beat']==1
    assert by_index[21]['turns'][0]['text']=='许知意招呼她坐下，宋乔坐在陆屿对面。'
    parked='知意奶油色、陆屿橄榄绿箱已停北墙边柜右侧，从西向东排列，始终保留'
    people={'许知意':'坐在东北椅，朝向桌心','陆屿':'坐在西北椅，朝向桌心'}
    entering=HandoffState(physical={**people,'宋乔':'站在北墙西侧门口，拉着焦糖色箱',
                                   '嘉宾行李箱':parked})
    waiting=HandoffState(physical={**people,'宋乔':'站在西南空椅旁，尚未落座',
        '嘉宾行李箱':'三只箱子已停北墙边柜右侧，奶油色、橄榄绿、焦糖色从西向东排列，不移动'})
    seated=HandoffState(physical={**people,'宋乔':'坐在西南椅，面向西北椅的陆屿',
                                  '嘉宾行李箱':waiting.physical['嘉宾行李箱']})
    edits={}
    for index in range(19,28):
        shot=by_index[index];turn=shot['turns'][0]
        start=entering if index==19 else waiting if index in [20,21] else seated
        end=waiting if index in [19,20] else seated
        event=turn['text'] if turn['delivery_mode']=='silent_action' else turn['speaker_name']+'面对谈话对象自然说话，'+turn['emotion']
        edits[index]={'start':start,'end':end,'event':event,
            'visible':['宋乔','许知意','陆屿'] if index==19 else shot['in_frame'],
            'props':['嘉宾行李箱']}
    return edits


def prepare():
    if (OUT/'manifest.json').is_file():return read_json(OUT/'manifest.json',{})
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'owner').write_text('Codex / user-authorized 26s SD2.5 structure+Blender A/B / 2026-10-13 / at most four video submissions\n')
    source_episode=SOURCE/'outputs/romance-sd25/romance-sd25_1'
    source_novel=source_episode.parent
    old=read_json(source_episode/'chapter_script.json',{})
    old_plan=read_json(source_episode/'clip_plan.json',{})
    candidate=repair_room_shots(old,source_edits(old))
    episode=OUT/'romance-sd25-continuity-ab_1';episode.mkdir(exist_ok=True)
    shutil.copytree(source_novel/'series_assets',OUT/'series_assets',dirs_exist_ok=True)
    for name in ['story_bible.json','profile.json','visual_grammar.json','performance_en.json']:
        if (source_novel/name).is_file():shutil.copy2(source_novel/name,OUT/name)
    for name in ['segments.json','source_trace.json']:
        if (source_episode/name).is_file():shutil.copy2(source_episode/name,episode/name)
    bible=read_json(OUT/'story_bible.json',{})
    for prop in bible['props']:
        if prop['name']=='嘉宾行李箱':
            prop['appearance']=('四款箱子的外观设计参考：知意奶油色、陆屿橄榄绿、宋乔焦糖色、周叙炭灰。'
                '按已到场嘉宾保留其箱子，本段三只；未到场周叙的箱子不出现。'
                '停放后的箱子持续留在北墙边柜右侧，不能因为当前无人操作而移走。')
    atomic_write_json(OUT/'story_bible.json',bible)
    atomic_write_json(episode/'chapter_script.json',candidate)
    ctx=context_for_plan(episode,OUT/'story_bible.json',old_plan)
    ctx['compiler_options']=replace(ctx['compiler_options'],camera_policy='authored',max_stages=6)
    geometry_prompt=copy.deepcopy(ctx.get('grammar') or {})
    geometry_prompt['composition_space']=SET
    new_shots=prepared_shots(candidate,episode,identity_data=ctx.get('identity_data'))
    old_current_shots=prepared_shots(old,episode,identity_data=ctx.get('identity_data'))
    current_baseline=[];new_clips=[];groups={};audit=[]
    for c in old_plan['clips']:
        if c['clip_id'] not in CLIPS:continue
        def compile_group(shots, context):
            selected=[shots[i-1] for i in c['shot_indexes']]
            packed={'kind':'video','location':c['location'],'shots':selected,
                'seconds':c['request_seconds'],'request_seconds':c['request_seconds'],
                'shot_timing':[{'seconds':s['duration_seconds']} for s in selected]}
            return clip_entry(packed,c['clip_id'],context,override={})
        old_bible=StoryBible.model_validate_json((source_novel/'story_bible.json').read_text())
        current=compile_group(old_current_shots,{**ctx,'bible':old_bible});current_baseline.append(current)
        compiled=compile_group(new_shots,{**ctx,'grammar':geometry_prompt})
        compiled['prompt']=compiled['prompt'].replace('中国国漫短剧片段','现代真人恋综片段')
        spatial=OUT/'series_assets/spatial'/c['clip_id']/'layout.png'
        images=[r for r in compiled['references'] if r['role']!='voice']
        tag=f'@图片{len(images)+1}'
        compiled['references'].append({'tag':tag,'role':'spatial_reference','name':'客厅空间白模','path':str(spatial.relative_to(OUT))})
        binding=(f'【空间依据】{tag}是Blender灰白几何预演，只采用四椅、矮桌、人物坐站位置、门窗与箱子停放位置；'
                 '不用灰白材质、圆头人偶或人偶面貌。角色外观只用角色参考。'
                 '此参考不是成片第一帧，不能播放白模画面；各阶段按原分镜切景别。'
                 '镜外人物仍在室内；实际构图能看到其座位时，保留对应人物，不能画成空椅。')
        prompt=compiled['prompt'].splitlines();at=next(i for i,s in enumerate(prompt) if s.startswith('【场景】'))
        prompt.insert(at+1,binding);compiled['prompt']='\n'.join(prompt);compiled['prompt_chars']=len(compiled['prompt'])
        new_clips.append(compiled)
        req=read_json(source_episode/'work/clips'/c['clip_id']/'attempt_01/request.json',{})
        def owned_image(path):
            return str(OUT/Path(path).relative_to(source_novel))
        groups['A-'+c['clip_id']]={'arm':'A','clip_id':c['clip_id'],'seconds':req['duration'],
            'prompt':req['prompt'],'images':[owned_image(p) for p in req['references']],
            'audio':req.get('reference_audios',[]),'spoken_text':c['spoken_text']}
        # Same exact trimmed voice files as A; both female/male speakers match.
        groups['B-'+c['clip_id']]={'arm':'B','clip_id':c['clip_id'],'seconds':req['duration'],
            'prompt':compiled['prompt'],'images':[str(OUT/r['path']) for r in compiled['references'] if r['role']!='voice'],
            'audio':req.get('reference_audios',[]),'spoken_text':c['spoken_text']}
        audit.append({'clip_id':c['clip_id'],'source_field_changes':[s['index'] for s in candidate['shots'] if s['index'] in c['shot_indexes']],
            'old_request_chars':len(req['prompt']),'old_source_compiled_current_chars':len(current['prompt']),
            'candidate_compiled_chars':len(compiled['prompt']),'compiler_drift':'English prose now preserves word boundaries; A uses the frozen historical request.',
            'geometry_binding':'Experimental final-reference binding added to the normally compiled source repair; not a production first-frame adapter.'})
        for k in ['shot_indexes','request_seconds','lines','dialogue_bindings']:
            if compiled[k]!=c[k]:raise ValueError(f'Protected source contract changed: {c["clip_id"]}/{k}')
    replacements={c['clip_id']:c for c in new_clips}
    candidate_plan={**copy.deepcopy(old_plan),'clips':[
        replacements.get(c['clip_id'],copy.deepcopy(c)) for c in old_plan['clips']]}
    proposal=RepairProposal({'changed':CLIPS,'why':'Restore source-backed occupants, seats and parked luggage; separate action from photographic prose.'},
        script=candidate,plan=candidate_plan,changes={'source_shots':list(range(19,28))},
        notes={'spatial_binding':'explicit experimental reference_image, not first_frame',
               'bible_input_repair':'Owned story_bible props/嘉宾行李箱: retain parked arrived-guest luggage, replacing the old current-action-only rule.'})
    atomic_write_json(OUT/'repair-proposal.json',proposal.as_result(include_proposal=True))
    atomic_write_json(OUT/'old-source-current-compiler.json',{'clips':current_baseline})
    atomic_write_json(episode/'clip_plan.json',candidate_plan)
    heights={c['name']:c['continuity']['heightCm']/100 for c in read_json(ROOT/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2/心动未剪辑-cast.json',{})['characters']}
    seated_people={'许知意':{'xy':LAYOUT['chairs']['NE']['xy'],'pose':'seated','height':heights['许知意'],'facing':-1},
        '陆屿':{'xy':LAYOUT['chairs']['NW']['xy'],'pose':'seated','height':heights['陆屿'],'facing':-1}}
    snapshots={'clip_05':{'people':{**seated_people,'宋乔':{'xy':[1.15,4.45],'pose':'standing','height':heights['宋乔'],'facing':-1}},
        'luggage':{'许知意':LAYOUT['luggage_xy']['许知意'],'陆屿':LAYOUT['luggage_xy']['陆屿'],'宋乔':[.88,4.40]}},
        'clip_06':{'people':{**seated_people,'宋乔':{'xy':LAYOUT['chairs']['SW']['xy'],'pose':'seated','height':heights['宋乔'],'facing':1}},
        'luggage':LAYOUT['luggage_xy']},'room-master':{'people':{},'luggage':{}}}
    manifest={'model':'sd2.5','ratio':'16:9','resolution':'720p','source':str(source_episode),'seconds_per_arm':26,
        'groups':groups,'layout':LAYOUT,'blockout_snapshots':snapshots,'audit':audit,'max_video_submissions':4,
        'artistic_retakes':False,'seed':'Current provider adapter does not expose seed control; independent samples. Not a causal single-variable or seed-matched experiment.',
        'comparison_scope':'A frozen old prompts/assets; B source states plus extra occupant/luggage binding and grayscale Blender reference. Evaluate the whole repair package, not Blender in isolation.'}
    atomic_write_json(OUT/'manifest.json',manifest)
    for name,entry in groups.items():
        (OUT/(name+'-prompt.txt')).write_text(entry['prompt']+'\n')
    print('Prepared: 26 seconds per arm, two clips each, max four video submissions',flush=True)
    return manifest


def generate(public_base):
    configure(ROOT,'sd25',project='romance')
    os.environ['PHANROUTER_ASSET_PUBLIC_BASE']=public_base
    os.environ['NOVEL_ASSET_PUBLISH_DIR']=str(OUT/'published')
    settings=Settings.from_env(provider='phanrouter',output_root=OUT,admission_mode='preview')
    manifest=read_json(OUT/'manifest.json',{})
    for cid in CLIPS:
        source=OUT/'blockout'/f'{cid}.png';target=OUT/'series_assets/spatial'/cid/'layout.png'
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copy2(source,target)
    # All newly introduced references must be registered before the run starts.
    provider=PhanRouterMediaProvider(settings,ratio='16:9',resolution='720p')
    try:
        unique=dict.fromkeys(path for entry in manifest['groups'].values() for path in entry['images'])
        for p in unique:provider._restore_image_url(ImageResult(path=Path(p)))
    finally:provider.client.close()
    print(f'Asset library ready: {len(unique)} distinct images, existing references reused',flush=True)
    lock=threading.Lock();stop=threading.Event()
    results=[]
    def run_sample(item):
        name,entry=item;folder=OUT/name;folder.mkdir(exist_ok=True);video=folder/'clip.mp4'
        provider=PhanRouterMediaProvider(settings,ratio='16:9',resolution='720p')
        def before_submit():
            with lock:
                counts=read_json(OUT/'submissions.json',{})
                if stop.is_set():raise RuntimeError('A quota or infrastructure stop is active')
                if counts.get(name,0)>=1:raise RuntimeError('This sample already spent its one-submission budget')
                if sum(counts.values())>=4:raise RuntimeError('Experiment four-submission cap reached')
                counts[name]=1;atomic_write_json(OUT/'submissions.json',counts)
        provider.before_video_submit=before_submit
        atomic_write_json(folder/'request.json',entry)
        try:
            if not video.is_file():
                print(name+': submit/poll 720P '+str(entry['seconds'])+'s',flush=True)
                provider.create_video(entry['prompt'],None,video,entry['seconds'],
                    additional_images=tuple(Path(p) for p in entry['images']),reference_audios=tuple(Path(p) for p in entry['audio']))
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
            stream=next(s for s in probe['streams'] if s['codec_type']=='video')
            subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],capture_output=True,check=True)
            asr_python=shlex.split(os.environ.get('NOVEL_ASR_COMMAND',''))[0] if os.environ.get('NOVEL_ASR_COMMAND') else sys.executable
            ctx=SimpleNamespace(asr_python=asr_python,asr_helper=ROOT/'scripts/thin_asr_segments.py',protected_terms=['许知意','陆屿','宋乔'],aliases={})
            speech=analyse_clip(ctx,{'clip_id':name,'spoken_text':entry['spoken_text']},video)
            row={'name':name,'video':str(video),'duration':float(probe['format']['duration']),
                'width':stream['width'],'height':stream['height'],'speech':speech,'status':'generated'}
            atomic_write_json(folder/'result.json',row)
            print(name+': completed, '+str(row['duration'])+'s, speech issues='+str(speech['issues']),flush=True)
            return row
        except Exception as error:
            message=re.sub(r'https?://\S+','[redacted URL]',str(error))[:900]
            if any(s in message.lower() for s in ['quota','credit','402']):stop.set()
            row={'name':name,'status':'failed','error_type':type(error).__name__,'message':message}
            atomic_write_json(folder/'result.json',row);print(name+': failed '+message,flush=True)
            return row
        finally:provider.client.close()
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(run_sample,item) for item in manifest['groups'].items()]
        for future in as_completed(futures):
            results.append(future.result());atomic_write_json(OUT/'results.json',results)
    print('All four samples finished; no artistic retries.',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--generate',action='store_true');parser.add_argument('--public-base');args=parser.parse_args()
    configure(ROOT,'sd25',project='romance')
    prepare()
    if args.generate:
        if not args.public_base:raise ValueError('Public base for the two new Blender assets is required')
        generate(args.public_base)


if __name__=='__main__':main()
