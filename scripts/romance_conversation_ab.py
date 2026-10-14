"""Authorized 26-second old-stage vs continuous-exchange H3 comparison.

Both arms use the same words, references, seeds and native 544p. No SD calls,
no artistic retries. Production build_request/submit are the send path.
"""
from pathlib import Path
from dataclasses import replace
import argparse,copy,json,os,shutil,sys,functools
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from novel_manga.util import read_json,atomic_write_json
from novel_manga.interactive.runtime import configure,runner_for
from novel_manga.interactive.romance import pack_romance
from novel_manga.media.generation import build_request,submit
from novel_manga.providers.local_h3 import LocalH3MediaProvider
from novel_manga.story.h3 import subject_lines

OUT=ROOT/'outputs/romance-conversation-h3'
SOURCE=Path('/mnt/disk1/zengzhitao/interactive-video-studio/outputs/romance-h3-768-v2')
NAMES=['许知意','陆屿','宋乔']
STATE=('同一海边客厅，四把扶手椅围绕一张低茶几；北墙门与边柜，东墙海景窗。'
 '许知意坐东北椅、陆屿坐西北椅、宋乔坐西南椅，东南椅始终空。'
 '三人全程已坐定，没有进出或换座。知意奶油箱、陆屿橄榄灰箱、宋乔焦糖箱，从西向东停北墙边柜右侧，三只都保留。')
CAMERA='同一个东南方向固定三人中远景，能看清三人、四椅及北墙三箱；不推拉、不反打、不逐句切镜'
EN=('A single locked medium-wide three-person composition viewed from the southeast of the low coffee table. '
 'The east sea window stays on the right and the north door and credenza stay in the background. '
 '<Subject 1> sits on NE, <Subject 2> on NW, <Subject 3> on SW; SE stays empty. '
 'All three adults, all four chairs and the three parked suitcases remain visible throughout. '
 'One cream suitcase belonging to <Subject 1>, one gray-olive suitcase belonging to <Subject 2>, '
 'and one caramel suitcase belonging to <Subject 3> stand in that order along the north wall to the right of the credenza. '
 'The suitcases are separate props, never seats. No arrivals, departures, seat changes or luggage movement. '
 'Use the supplied room design, natural late-afternoon light and original outfits. '
 '<Subject 1> quietly listens and looks toward the person currently speaking, without speaking or exaggerated reactions. ')
WORDS=[ [('宋乔','你那书店还卖咖啡？'),('陆屿','嗯。杯子老磕坏，正想换一批。'),('宋乔','那你得让我去看看。')],
 [('陆屿','明天？'),('宋乔','行啊。你几点开门？'),('陆屿','九点。你八点来吧，我先开。'),('宋乔','好，八点。')]]
EVENTS=['宋乔接着咖啡杯的话题问陆屿，陆屿解释换杯子的原因，宋乔提出去店里看看。知意自然听两人接话。',
        '接上前段去书店的提议，陆屿问明天是否方便；宋乔询问开门时间，两人约定八点。知意仍在原位听。']

def prepare():
 if (OUT/'manifest.json').is_file():return read_json(OUT/'manifest.json',{})
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'owner').write_text('Codex / authorized H3 26s conversation A/B / 2026-10-13 / four takes only\n')
 configure(ROOT,'h3',project='romance')
 prototype=read_json(ROOT/'projects/romance/skill-v1/chapter_script.json',{})
 base=copy.deepcopy(prototype['shots'][21]);manifest={'arms':{},'native_short_edge':544,'export_short_edge':480,'seed':2101,'max_submissions':4,'seconds_per_arm':26,
 'comparison':'Same authored exchange/state/assets/seed/durations; A legacy stage compiler, B continuous conversation compiler. This tests structure plus provider-specific prompt representation, not a single-word effect.',
 'source_adaptation':'Original coffee-cup conversation and next-morning 08:00 agreement; fixed seated three-person test. Shop breakage/09:00 opening are authored trial details, not claims in the original package.'}
 for arm in ['A','B']:
  novel=OUT/arm;episode=novel/(arm+'_1');episode.mkdir(parents=True)
  shutil.copytree(SOURCE/'series_assets',novel/'series_assets')
  for name in ['story_bible.json','profile.json','performance_en.json']:shutil.copy2(SOURCE/name,novel/name)
  profile=read_json(novel/'profile.json',{});profile.update(resolution='480p',style='romance-live',native_h3_short_edge=544);atomic_write_json(novel/'profile.json',profile)
  perf=read_json(novel/'performance_en.json',{});perf['面对谈话对象顺势接话，不朝镜头表演']='Respond to the partner conversationally, without delivering to the camera.';atomic_write_json(novel/'performance_en.json',perf)
  shots=[];groups=[]
  for g,words in enumerate(WORDS):
   turns=[{'speaker_name':n,'delivery_mode':'visible_dialogue','text':t,'emotion':'面对谈话对象顺势接话，不朝镜头表演'} for n,t in words]
   indices=[];parts=[turns] if arm=='B' else [[t] for t in turns]
   for partno,part in enumerate(parts):
    shot=copy.deepcopy(base);index=len(shots)+1;indices.append(index)
    shot.update(index=index,origin_index=index,shot_id=f'CONV-{g+1}-{partno+1}',label=f'CONV-{g+1}-{partno+1}',segment_id=f'seg_{index}',
     characters=list(NAMES),in_frame=list(NAMES),listeners=['许知意'],props=['嘉宾行李箱'],location='海边小屋客厅',scene_id='scene_conversation',source_scene=3,source_beat=4,
     source_segment=f'CONV-{g+1}',source_quote=EVENTS[g]+' 对白：'+' '.join(t['text'] for t in part),visual_prompt=STATE,motion_prompt=EVENTS[g],end_state=STATE,camera=CAMERA,
     shot_scale='中远景',duration_seconds=13/len(parts),sfx='低室内底噪，保留对白间停顿。',turns=part,
     authored_frame=EN+' A continuous uncut take. '+ ' '.join(f'<DIALOGUE_{i}>' for i in range(1,len(part)+1))+' End with the same three seated people and the same three parked suitcases.')
    if arm=='A':shot['authored_frame']=shot['authored_frame'].replace(' A continuous uncut take. ',' Continue the specified fixed composition for this stage. ')
    shots.append(shot)
   groups.append({'source_segment':f'CONV-{g+1}','shot_indexes':indices,'request_seconds':13})
  script=copy.deepcopy(prototype);script.update(shots=shots,profile=profile,video_title='三人同室咖啡邀约短样',summary='三人已坐定，宋乔和陆屿由杯子话题约定明早八点，知意静默在场。')
  if arm=='B':script['production_mode']='romance_conversation'
  atomic_write_json(episode/'chapter_script.json',script);atomic_write_json(episode/'authored_groups.json',groups)
  atomic_write_json(episode/'segments.json',[{'segment_id':s['segment_id'],'text':s['source_quote']} for s in shots]);(episode/'source.txt').write_text('\n'.join(s['source_quote'] for s in shots))
  plan=pack_romance(episode)
  for clip in plan['clips']:
   _,subjects=subject_lines(clip);assert {n:subjects[n] for n in NAMES}==dict(zip(NAMES,[1,2,3]))
   assert {r['name'] for r in clip['references'] if r['role']=='character'}==set(NAMES)
   assert any(r['name']=='嘉宾行李箱' for r in clip['references'])
   assert all((novel/r['path']).is_file() for r in clip['references'])
  manifest['arms'][arm]={'novel':str(novel),'episode':str(episode),'prompt_compiler':plan['clips'][0].get('prompt_compiler','legacy')}
 atomic_write_json(OUT/'manifest.json',manifest);return manifest

def run():
 manifest=prepare();configure(ROOT,'h3',project='romance')
 pool=read_json(ROOT/'configs/h3_pool.json',{});pool['resident']=[r for r in pool['resident'] if r['url']=='http://172.28.4.52:30022'];pool['night_shift']={};atomic_write_json(OUT/'pool.json',pool)
 for arm,info in manifest['arms'].items():
  novel=Path(info['novel']);runner=runner_for(ROOT,novel,'h3');ctx=runner.context
  os.environ['NOVEL_H3_SHORT_EDGE']='544'
  provider=LocalH3MediaProvider(ctx.settings,'pool:'+str(OUT/'pool.json'),ratio='16:9')
  provider.create_video=functools.partial(provider.create_video,seed=manifest['seed'])
  ctx.provider=provider
  try:
   for clip in ctx.clip_plan['clips']:
    d=OUT/'takes'/arm/clip['clip_id'];d.mkdir(parents=True,exist_ok=True)
    request,refs,_,_=build_request(ctx,clip,1);request['fixed_seed']=manifest['seed'];atomic_write_json(d/'request.json',request)
    # Verify the actual SD route locally, without creating an SD provider/task.
    local=ctx.settings;ctx.settings=replace(local,local_h3_base_url='')
    try:sd,_,_,_=build_request(ctx,clip,1)
    finally:ctx.settings=local
    atomic_write_json(d/'sd-request-offline.json',sd)
    output=d/'clip.mp4';print(arm,clip['clip_id'],'generating 544P via production submit',flush=True)
    if not output.is_file():submit(ctx,clip,request,output,refs)
    receipt=read_json(output.with_suffix('.mp4.task.json'),{});assert receipt['seed']==2101 and receipt['h3_sampling']['short_edge']==544
    print(arm,clip['clip_id'],'complete',flush=True)
  finally:provider.h3_client.close();provider.client.close()

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
 if args.prepare_only:print(json.dumps(prepare(),ensure_ascii=False,indent=2))
 else:run()
