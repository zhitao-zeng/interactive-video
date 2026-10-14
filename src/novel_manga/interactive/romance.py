"""Import the supplied, authored romance episode without regenerating its screenplay."""
from pathlib import Path
import copy
import json
import math
from dataclasses import replace
from PIL import Image
from novel_manga.util import atomic_write_json, read_json

SIZES={'wide':'全景','medium':'中景','close':'特写','detail':'特写','extreme_close':'大特写'}
SOUNDS={
    '低室内底噪，保留对白间停顿。':'Quiet interior room tone, with natural pauses between utterances.',
    '安静室内底噪，轻呼吸；不添加敲门声。':'Quiet interview-room tone and natural breathing. No knocking.',
    '纸卡轻摩擦，低室内底噪。':'Gentle paper rustling synchronized with card handling, over quiet room tone.',
    '脚步、衣料或箱轮声随实际动作出现。':'Footsteps, cloth rustling and luggage wheels only when the corresponding action occurs.'}


def romance_material(package: Path) -> dict:
    board=read_json(package/'routes/shared/storyboard.json',{})
    episode=read_json(package/'routes/shared/script.json',{})['episodes'][0]
    cast=read_json(package/'心动未剪辑-cast.json',{})['characters']
    art=read_json(package/'心动未剪辑-art.json',{})
    auxiliary=[{'id':'A01','name':'嘉宾行李箱','file':'四人行李箱-sheet.png','appearance':'嘉宾行李箱款式参考；当前镜头只出现动作指定嘉宾的箱子，知意用奶油色款，不能因参考图有多款就全部摆进画面。'},
               {'id':'A02','name':'玻璃水杯','file':'玻璃水杯-sheet.png','appearance':'朴素透明玻璃饮水杯，按本镜动作决定数量和位置。'},
               {'id':'A03','name':'黑色中性笔','file':'黑色中性笔-sheet.png','appearance':'无品牌的黑色中性笔，按邀请卡书写动作出现。'}]
    asset_lookup={p['id']:p['name'] for p in [*art['props'],*auxiliary]}
    names={c['id']:c['name'] for c in cast}
    voices={c['name']:{'gender':c['persona']['gender'],'prompt':c['voice']['prompt'],'source_lines':[]} for c in cast}
    voices['节目解说']={'gender':'女','prompt':'A mature clear female narrator, neutral warm tone, measured Mandarin, precise diction. Distinct from the young participants.','source_lines':[]}
    shots=[];groups=[];segments=[];overlays=[]
    for authored in board['episodes'][0]['segments']:
        scene=episode['scenes'][authored['sceneIndex']-1]
        location=next(s['name'] for s in art['scenes'] if s['id']==scene['sceneId'])
        current=[];heard={}
        def finish():
            nonlocal current,heard
            if not current:return
            requested=max(4,math.ceil(round(sum(shots[i-1]['duration_seconds'] for i in current),3)))
            if requested>15:raise ValueError('Authored generation group exceeds 15 seconds')
            # Backend requests are whole seconds. Put the small rounding remainder in the
            # final authored beat; do not speed-change footage or invent a new event.
            remainder=requested-sum(shots[i-1]['duration_seconds'] for i in current)
            shots[current[-1]-1]['duration_seconds']=round(shots[current[-1]-1]['duration_seconds']+remainder,3)
            groups.append({'source_segment':authored['id'],'shot_indexes':list(current),'request_seconds':requested})
            current=[];heard={}
        for cut_no,cut in enumerate(authored['cuts'],1):
            start,end=cut['beats']
            if start!=end:raise ValueError('This authored package requires one source beat per cut')
            beat=scene['flow'][start-1]
            who='';mode='silent_action';words=beat.get('action','')
            if 'line' in beat:
                who=names.get(beat['speaker'],'节目解说')
                if beat['speaker']=='VO':
                    who=next((name for name in names.values() if name in beat.get('delivery','')), '节目解说')
                mode='offscreen_dialogue' if beat['speaker']=='VO' else 'visible_dialogue'
                words=beat['line']
                gender=voices[who]['gender']
                if heard.get(gender) and who not in heard[gender]:finish()
                heard.setdefault(gender,set()).add(who)
                voices[who]['source_lines'].append(words)
            index=len(shots)+1;sid=f'seg_{index}';shot_id=f'{authored["id"]}-{cut_no:02d}'
            visible=[names[c] for c in cut['characters']]
            source='。'.join([cut['action'],cut.get('delivery',''),cut.get('note','')])
            frame=cut['frame'].replace('film still','film footage').replace(
                'woman in ivory sits northeast, wavy-haired man northwest, bob-haired woman southwest, tall man southeast. Preserve geography and only occupied seats at this point.',
                'the four seats are northeast, northwest, southwest and southeast. Only the people explicitly present in this shot occupy the seats; this is a seating geography reference, not an instruction to add all participants.')
            shot={'index':index,'origin_index':index,'shot_id':shot_id,'label':shot_id,'segment_id':sid,
                  'source_quote':source[:500],'location':location,'scene_id':f'scene_{authored["sceneIndex"]}',
                  'scene_time':scene['lighting'],'characters':visible,'in_frame':visible,'extras':[],'listeners':[],
                  'wears':{},'props':list(dict.fromkeys([*[asset_lookup[pid] for pid in cut['props']],
                       *[p['name'] for p in auxiliary if any(r['path'].endswith(p['file']) for r in cut['references'])]])),
                  'visual_prompt':f'{location}，{scene["lighting"]}，{SIZES.get(cut["size"],cut["size"])}；可见人物：'+('、'.join(visible) or '无'),
                  'motion_prompt':frame,'end_state':'','camera':cut['camera'],'light':scene['lighting'],
                  'shot_scale':SIZES.get(cut['size'],cut['size']),'duration_seconds':float(cut['seconds']),
                  'sfx':cut.get('sound',''),'avoid':'未安排的人物、重复人物、字幕、水印、品牌标志',
                  'turns':[{'speaker_name':who,'delivery_mode':mode,'text':words,'emotion':cut.get('delivery','自然')}],
                  'authored_frame':frame,'source_scene':authored['sceneIndex'],'source_beat':start,
                  'source_segment':authored['id'],'source_references':cut['references']}
            if who and mode=='visible_dialogue' and who not in visible:
                raise ValueError(f'{shot_id}: visible speaker is absent from authored cast')
            shots.append(shot);current.append(index);segments.append({'segment_id':sid,'text':source})
            identity_titles={(1,1):'许知意｜自由插画师',(2,2):'陆屿｜书店主理人',
                             (3,2):'宋乔｜陶艺工作室主理人',(4,2):'周叙｜建筑师'}
            if (authored['sceneIndex'],start) in identity_titles:
                overlays.append({'index':index,'text':identity_titles[(authored['sceneIndex'],start)]})
            if (authored['sceneIndex'],start)==(5,6):overlays.append({'index':index,'text':'许知意 × 周叙｜两年前分手'})
            if (authored['sceneIndex'],start)==(6,4):overlays.append({'index':index,'text':'周叙的邀请｜今晚22:00 · 露台'})
            if (authored['sceneIndex'],start)==(6,9):overlays.append({'index':index,'text':'陆屿的邀请｜今晚22:00 · 厨房'})
        finish()
    profile={'style':'romance-live','frame':'16:9','tier':'quality','genre':'generic','resolution':'720p',
             'expressions':False,'two_views':'off','episode_shape':'fixed','speech_gate':'enforce','props':False}
    bible={'novel_title':'心动未剪辑','genre':'当代互动恋爱综艺','visual_style':'真人影视写实、现代海边小屋，自然皮肤与克制表演',
           'palette':'暖白、木棕、深青、鼠尾草绿、低饱和铜','style_fingerprint':'romance-live-v1',
           'characters':[{'name':c['name'],'role':'主角','gender':c['persona']['gender'],'age':c['persona']['ageRange'],
                          'appearance':c['persona']['appearance'],'wardrobe':c['continuity']['wardrobe'],
                          'face_anchors':c['continuity']['anchors'],'voice_profile_id':c['id']+'-voice-v1'} for c in cast],
           'locations':[s['name']+'：'+'；'.join(a['desc'] for a in s['anchors']) for s in art['scenes'] if s['id'] in ['S01','S04']],
           'props':[*[{'name':p['name'],'appearance':'；'.join(a['desc'] for a in p['anchors']),'category':'其他'} for p in art['props']],
                    *[{'name':p['name'],'appearance':p['appearance'],'category':'其他'} for p in auxiliary]],
           'continuity_rules':['四位嘉宾身份固定，同一夜晚衣装不变。','周叙在前采脱去外套，入住镜头恢复原外套。',
                               '未揭晓前不显示前任身份条。','邀请卡与其他剧情文字后期叠加。','三条选择均未获选，第一集不确立恋爱关系。']}
    bible['characters'].append({'name':'节目解说','role':'画外解说','gender':'女','age':'成年','appearance':'仅有画外声音，不上镜','wardrobe':'不适用'})
    return {'profile':profile,'bible':bible,'cast':cast,'art':art,'auxiliary':auxiliary,'voice_specs':voices,'groups':groups,'segments':segments,
            'script':{'video_title':'初次见面，有人例外','source_title':'心动未剪辑 第一集','episode_index':1,'profile':profile,
                      'hook':episode['hook'],'summary':episode['cliff'],'shots':shots,'skipped_segments':[]},
            'interaction':{'id':'E01-shared-END','series_title':'心动未剪辑','question':'今晚十点，知意先做什么？','seconds':3,
                           'theme':{'background':'#F1ECE0','foreground':'#244B4C','secondary':'#9B7453'},
                           'options':[{'id':'A','label':'去露台，问清过去','next':'第二集A线'},{'id':'B','label':'去厨房，认识新人','next':'第二集B线'},
                                      {'id':'C','label':'今晚独处，两张退回','next':'第二集C线'}],'selected':None},'overlays':overlays}


def prepare_romance(root: Path) -> Path:
    package=root/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2'
    material=romance_material(package);novel=root/'outputs/romance-master';episode=novel/'romance-master_1'
    if (episode/'thin_media_report.json').exists():raise FileExistsError('Keep the adopted romance episode and its generation history')
    episode.mkdir(parents=True,exist_ok=True)
    (novel/'owner').write_text('Codex / romance EP01 shared / authorized 2026-10-12\n')
    for name,value in [('profile',material['profile']),('story_bible',material['bible']),('voice_specs',material['voice_specs'])]:
        atomic_write_json(novel/(name+'.json'),value)
    for name,key in [('chapter_script','script'),('segments','segments'),('interaction','interaction'),('overlays','overlays'),('authored_groups','groups')]:
        atomic_write_json(episode/(name+'.json'),material[key])
    atomic_write_json(episode/'source_trace.json',{'original':str(package/'routes/shared/script.json'),
                      'storyboard':str(package/'routes/shared/storyboard.json'),'cuts':[{'index':s['index'],'shot_id':s['shot_id'],
                      'scene':s['source_scene'],'beat':s['source_beat']} for s in material['script']['shots']]})
    (episode/'source.txt').write_text('\n\n'.join(s['text'] for s in material['segments']))
    imports=[]
    for i,c in enumerate(material['cast'],1):imports.append((c['continuity']['masterImage'],f'characters/character_{i:03d}/turnaround.jpeg','character_design_sheet'))
    for i,filename in enumerate(['海边小屋客厅-sheet.png','单人告白间-sheet.png'],1):imports.append(('images/'+filename,f'locations/location_{i:03d}/establishing.jpeg','location_design_sheet'))
    for i,p in enumerate(material['art']['props'],1):
        source='images/'+p['name']+'-sheet.png'
        if (package/source).exists():imports.append((source,f'props/prop_{i:03d}/turnaround.jpeg','prop_design_sheet'))
    for i,p in enumerate(material['auxiliary'],len(material['art']['props'])+1):
        imports.append(('images/'+p['file'],f'props/prop_{i:03d}/turnaround.jpeg','prop_design_sheet'))
    for source,target,scope in imports:
        path=novel/'series_assets'/target;path.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(package/source) as img:img.convert('RGB').save(path,quality=97)
        atomic_write_json(path.with_suffix(path.suffix+'.origin.json'),{'kind':'supplied-reference','source':str(package/source),'reference_scope':scope})
    atomic_write_json(novel/'imported_assets.json',{'assets':[{'source':s,'path':'series_assets/'+t,'scope':scope} for s,t,scope in imports]})
    return episode


def pack_romance(directory: Path) -> dict:
    if read_json(directory/'chapter_script.json', {}).get('production_mode') == 'romance_conversation':
        from novel_manga.interactive.conversation import pack_conversation
        return pack_conversation(directory)
    from novel_manga.application.packing.context import load_context,POLICY
    from novel_manga.application.packing.service import prepared_shots,clip_entry
    from novel_manga.story.compilation import plan_totals
    from novel_manga.application.preparation.readiness import plan_issues
    script=read_json(directory/'chapter_script.json',{})
    context=load_context(directory,directory.parent/'story_bible.json',None,None,None,None)
    context['compiler_options']=replace(context['compiler_options'],max_stages=6,camera_policy='authored')
    shots=prepared_shots(script,directory,identity_data=context.get('identity_data'))
    clips=[]
    for n,g in enumerate(read_json(directory/'authored_groups.json',[]),1):
        selected=[shots[i-1] for i in g['shot_indexes']]
        packed={'kind':'video','location':selected[0]['location'],'shots':selected,'seconds':g['request_seconds'],
                'request_seconds':g['request_seconds'],'shot_timing':[{'seconds':s['duration_seconds']} for s in selected]}
        clip=clip_entry(packed,f'clip_{n:02d}',context)
        clip['prompt']=clip['prompt'].replace('中国国漫短剧片段','现代真人恋综片段')
        clip['prompt_chars']=len(clip['prompt']);clip['source_authored_segment']=g['source_segment'];clips.append(clip)
    plan={'policy':POLICY,'limits':{'max_clip_seconds':15,'max_stages':6,'soft_cut_seconds':15,'camera_policy':'authored'},
          'totals':plan_totals(clips,shots,context),'clips':clips}
    issues=plan_issues(plan,script)
    if issues:raise ValueError(f'Imported romance plan needs correction: {issues}')
    atomic_write_json(directory/'clip_plan.json',plan)
    return compile_romance_h3(directory)


def compile_romance_h3(directory: Path) -> dict:
    """Use the supplied English shot directions; no new script/translation model call."""
    from novel_manga.story.h3 import compose,stages_of,subject_lines,request_issues
    from novel_manga.application.profiles import h3_stamp
    plan=read_json(directory/'clip_plan.json',{});script=read_json(directory/'chapter_script.json',{})
    performance=read_json(directory.parent/'performance_en.json',{})
    shots={s['index']:s for s in script['shots']}
    for clip in plan['clips']:
        _,subjects=subject_lines(clip);directions=[];sounds=[]
        for stage,index in enumerate(clip['shot_indexes'],1):
            shot=shots[index]
            names=[f'<Subject {subjects[name]}>' for name in shot['characters'] if name in subjects]
            text=('Only '+', '.join(names)+' are visible in this shot. ' if names else 'No person is visible in this shot. ')+shot['authored_frame']
            turn=shot['turns'][0]
            cue=performance.get(turn['emotion'],'')
            if performance and not cue:raise ValueError(f'Missing translated performance cue: {turn["emotion"]}')
            for name,romanized in [('许知意','Xu Zhiyi'),('周叙','Zhou Xu'),('陆屿','Lu Yu'),('宋乔','Song Qiao')]:
                if name in subjects:cue=cue.replace(romanized,f'<Subject {subjects[name]}>')
            if cue:text+=' Performance: '+cue
            if turn['delivery_mode']=='offscreen_dialogue':
                if shot['source_scene']==6:
                    text+=' This is an in-room programme announcement heard by all guests; no guest speaks this line.'
                else:
                    text+=' This is prerecorded audience-only voiceover, unheard by the people in the scene; no visible lip movement for this line.'
            if turn['delivery_mode']!='silent_action':
                text=text.replace('Preserve exact identity','<DIALOGUE_1> Preserve exact identity',1)
                if '<DIALOGUE_1>' not in text:text+=' <DIALOGUE_1>'
            directions.append(text)
            sounds.append(f'Shot {stage}: '+SOUNDS[shot['sfx']])
        clip['prompt_h3']=compose(clip,directions,stages_of(clip['prompt']),soundscape=' '.join(sounds)+' No background music.',authored=True)
        problems=request_issues(clip)
        if problems:raise ValueError(f'{clip["clip_id"]}: {problems}')
        clip['prompt_h3_of']=h3_stamp(clip)
        clip['authored_english_source']='routes/shared/storyboard.json'
    atomic_write_json(directory/'clip_plan.json',plan)
    return plan


def build_romance_voices(root: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from novel_manga.interactive.runtime import runner_for
    from novel_manga.providers.base import ImageResult
    from novel_manga.util import run
    novel=root/'outputs/romance-master';runner=runner_for(root,novel,'h3')
    specs=read_json(novel/'voice_specs.json',{});directory=novel/'series_assets/voices';directory.mkdir(parents=True,exist_ok=True)
    cast={c.name:i for i,c in enumerate(runner.context.bible.characters,1) if c.name!='节目解说'}
    def voice(item):
        name,spec=item;wav=directory/(name+'.wav');video=directory/(name+'-voice-source.mp4')
        text=''
        for line in spec['source_lines']:
            if len(text)>=36:break
            text+=line
        if not wav.is_file():
            is_guest=name in cast
            picture=(novel/f'series_assets/characters/character_{cast[name]:03d}/turnaround.jpeg' if is_guest
                     else novel/'series_assets/locations/location_002/establishing.jpeg')
            prompt=('A clean solo Mandarin voice recording. '+spec['prompt']+' '
                    'The reference sheet shows different views of one person, not multiple people. '
                    'Show a single coherent medium close-up of that one person against a neutral studio backdrop, ignoring the sheet layout. '
                    if is_guest else 'Use <Picture 1> only as the design reference for an empty room, with no people visible. A clean solo off-screen female narration. '+spec['prompt']+' ')
            prompt+='No music, no other voices. Begin speaking immediately and use the full phrase naturally: <d>[Chinese] '+text+'</d>'
            if not video.is_file():runner.context.provider.create_video(prompt,ImageResult(path=picture),video,14)
            run(['ffmpeg','-y','-v','error','-i',str(video),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
        return name,{'path':str(wav.relative_to(novel)),'source':str(video.relative_to(novel)),'text':text,
                    'origin':'generated-reference','voice_quality':'episode speech is checked separately'}
    with ThreadPoolExecutor(2) as pool:bank=dict(pool.map(voice,specs.items()))
    atomic_write_json(directory/'voices.json',bank)
    pack_romance(novel/(novel.name+'_1'))
