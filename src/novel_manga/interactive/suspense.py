"""Bind the approved suspense pilot to the existing reviewed production flow."""
from pathlib import Path
from dataclasses import replace

from PIL import Image, ImageDraw, ImageFont
from novel_manga.util import atomic_write_json, read_json, run

REVISION = 'projects/suspense/revisions/2026-10-13-investigation'
SHEN = '沈序'
XU = '徐程'
LOCATIONS = [
    '公寓走廊：正对同一面墙时801左、802右，相邻两户之间共墙；棕色木门、米灰墙、灰砖地、小型白色摄像头。',
    '801室内：空置普通公寓，右侧共墙小检查标记；木桌左侧玻璃杯与不锈钢保温杯，中间黑手机连白充电线，右侧维修纸页，桌下工具包。',
    '802室内：徐程居住的普通公寓，米灰墙、木柜、灰布椅；朝室内看左侧与801共墙，调查时徐程始终站在自家门内。',
]
PROPS = [
    ('门锁打印记录', 'log.png', '一张白色A4打印纸，记录01:52，徐程（临时），密码被使用；不显示实际密码。'),
    ('维修清点单', 'maintenance.png', '普通米白A4维修资料，标题维修清点单，不增加日期、签名或新调查结论。'),
    ('黑本笔记页', 'notebook.png', '黑色笔记本中同一页的事实、证词、假设三栏；假设徐程进入带问号。'),
    ('松脱装订条', 'attachment.png', '一张独立米色窄纸条，017／附件04；不是沈序的旧卡。'),
    ('沈序旧案卡', 'old-card.png', '沈序黑本中的旧米色纸卡，只有编号017；无案情照片和人物姓名。'),
    ('017对照材料', 'comparison.png', '两张分开的纸：旧卡017与窄装订条017／附件04，形状不同。'),
]
PROP_BY_SHOT = {3:0, 4:0, 12:1, 14:2, 15:0, 19:0, 20:2, 24:3, 26:4, 27:5, 32:0}
VOICE_SPECS = {
    SHEN: {
        'prompt': 'A clear calm adult Chinese male medium-low voice, restrained and thoughtful, conversational Mandarin at about 190 Chinese characters per minute. Natural pauses, never a theatrical announcer.',
        'text': '先把记录看清楚，再核对谁知道密码。听到的和看到的要分开记，不能急着下结论。',
    },
    XU: {
        'prompt': 'An ordinary adult Chinese male mid-range voice, slightly breathy and tired, natural Mandarin at a conversational pace, subtly tense but not threatening. Distinctly lighter and less resonant than a deep narrator voice.',
        'text': '我住在旁边，平时回来就关门休息。那天夜里听到一点动静，我停下来听了一会儿。',
    },
}


def suspense_material(root: Path) -> dict:
    board = read_json(root / REVISION / 'storyboard.review.json', {})
    profile = {'style':'suspense-live', 'frame':'9:16', 'tier':'quality', 'genre':'generic',
               'resolution':'480p', 'expressions':False, 'two_views':'off', 'episode_shape':'fixed',
               'speech_gate':'enforce', 'props':False}
    bible = {'novel_title':board['title'], 'genre':'当代悬疑调查互动短剧',
             'visual_style':'现代真人电影摄影，自然皮肤、普通公寓、克制表演',
             'palette':'钢灰、炭黑、米白、旧木棕；低饱和自然调色', 'style_fingerprint':'suspense-live-v1',
             'characters':[
                 {'name':SHEN,'role':'主角','gender':'男','age':'28',
                  'appearance':'中国成年男性，清瘦、短黑侧分发、无眼镜无胡须，以原包身份基准为准。',
                  'wardrobe':'炭灰哑光及大腿中部羊毛外套，黑色圆领内搭，深色长裤、黑靴，无警徽无武器。',
                  'voice_profile_id':'shenxu-v1'},
                 {'name':XU,'role':'租客','gender':'男','age':'31',
                  'appearance':'中国成年男性，柔和偏圆椭圆脸，微乱短黑发，自然刘海，无胡须，略疲惫；普通租客。',
                  'wardrobe':'无字米灰圆领卫衣、深灰休闲裤、深色室内拖鞋；手持普通深色壳手机。',
                  'voice_profile_id':'xucheng-v1'},
             ], 'locations':LOCATIONS,
             'props':[{'name':n,'appearance':d,'category':'其他'} for n,_,d in PROPS],
             'continuity_rules':[
                 '801和802在走廊同侧相邻，共墙；不能变成走廊两侧对门。',
                 '徐程调查时留在802门内，沈序在走廊与801移动。',
                 '只有801充电手机与徐程手持手机两部；沈序拿门锁打印纸。',
                 'S24以前装订条朝下不露017，S26取旧卡对照，S29收回旧卡，装订条留在801。',
                 '三下敲击只在开场陈述画面发生，不把它解释成超自然或确定的维修行为。',
                 '门锁记录仅证明密码被使用，不证明徐程进入；相同017编号不等于已确认同案。',
             ]}
    shots, segments, overlays, groups = [], [], [], []
    xu_shots = {2,4,7,9,16,18,31}
    corridor_shots = {1,3,14,15,17,19,20,21,22,29,30,32}
    detail_shots = {1,3,8,11,12,20,22,24,26,27,30,32}
    for i, authored in enumerate(board['shots'], 1):
        location = '802室内' if i in xu_shots else '公寓走廊' if i in corridor_shots else '801室内'
        visible = [XU] if i in xu_shots else [] if i in {1,22,27,30} else [SHEN]
        if i in {4,14,15}: visible = [SHEN,XU]
        action = authored['action']
        # A shoulder/hand view still needs its owner's identity, without requesting a face.
        frame = authored['start_state'] + '。' + authored['scale'] + '；' + action
        if i in detail_shots and visible:
            frame += '。人物只露本镜需要的手或衣袖，不添加脸部或全身。'
        if i in {4,15}:
            frame += '。反打只有所指定主说话者脸部，另一人只肩缘，不转成同框双人正脸。'
        if i == 29:
            frame = frame.replace('对面门内', '相邻802门内')
        seconds = max(4, authored['duration_seconds'])
        if authored['dialogue']:
            mode = 'visible_dialogue' if authored['on_camera_dialogue'] else 'offscreen_dialogue'
            turn = {'speaker_name':authored['speaker'], 'delivery_mode':mode,
                    'text':authored['dialogue'], 'emotion':('成年男性中低音，清楚克制，约190字每分钟，自然停顿' if authored['speaker']==SHEN else '成年男性中音，略疲惫，紧张但不阴险，自然说话')}
            if mode == 'offscreen_dialogue':
                frame += '。台词由画外的' + authored['speaker'] + '发声，本镜可见人物闭嘴不代说台词。'
        else:
            turn = {'speaker_name':'','delivery_mode':'silent_action','text':action,'emotion':''}
        # Readable wording is added after generation; scene objects keep their specified shapes.
        if i in PROP_BY_SHOT: frame += '。精确文字在后期证据层呈现。'
        shot = {'index':i,'origin_index':i,'shot_id':authored['id'],'label':authored['id'],
                'segment_id':f'seg_{i}', 'source_quote':action + authored['dialogue'],
                'source_shots':authored['source_shots'], 'location':location,'scene_id':location,
                'scene_time':'徐程陈述的凌晨两点' if i <= 2 else '次日调查，连续时间',
                'characters':visible, 'in_frame':visible, 'extras':[], 'listeners':[], 'wears':{},
                'props':[PROPS[PROP_BY_SHOT[i]][0]] if i in PROP_BY_SHOT else [],
                'visual_prompt':frame,'motion_prompt':action,'start_state':authored['start_state'],
                'end_state':authored['end_state'],'camera':authored['camera'].replace('对面门内','相邻802门内'),
                'light':'普通公寓实景光，真实肤色；凌晨仅普通室内灯，调查时柔和日光和楼道顶灯。',
                'shot_scale':authored['scale'],'duration_seconds':seconds,
                'authored_duration_seconds':authored['duration_seconds'],
                'sfx':authored['sound_events']+'；自然连续的公寓底声，无配乐。',
                'avoid':'多余人物、重复人物、凶手式阴笑、夸张蒸汽、鬼影、字幕、水印、未安排的敲墙声',
                'turns':[turn]}
        if i == 1: shot['avoid'] = shot['avoid'].replace('、未安排的敲墙声','')
        if i in {4,14,15}:
            shot['listeners'] = [name for name in visible if name != authored['speaker']]
        shots.append(shot)
        segments.append({'segment_id':f'seg_{i}','text':shot['source_quote']})
        if authored['on_screen_text']:
            overlays.append({'index':i,'text':'\n'.join(authored['on_screen_text'])})
        if i == 1: overlays.append({'index':1,'text':'凌晨 02:00｜徐程的陈述\n801 空房 · 相邻 802 租客'})
        # Only group compatible adjacent shots. Same-sex dialogue never shares one request.
        speaker = authored['speaker'] if authored['dialogue'] else ''
        if (groups and groups[-1]['location'] == location and len(groups[-1]['shot_indexes']) < 3
                and groups[-1]['request_seconds'] + seconds <= 15
                and len({p for j in groups[-1]['shot_indexes'] for p in shots[j-1]['props']} | set(shot['props'])) <= 1
                and (not speaker or not groups[-1]['speaker'] or groups[-1]['speaker'] == speaker)
                and i not in {2,3,24,27,29}):
            group = groups[-1]
            group['shot_indexes'].append(i);group['request_seconds'] += seconds
            group['speaker'] = group['speaker'] or speaker
        else:
            groups.append({'source_segment':authored['id'], 'shot_indexes':[i],
                           'request_seconds':seconds,'location':location,'speaker':speaker})
    interaction = {'id':'suspense-ep01-end','series_title':board['title'],
                   'question':board['interaction']['question'],'seconds':3,'ambience_shot_index':18,
                   'options':[{'id':o['id'],'label':o['text'],'next':'第二集'+o['id']+'入口'}
                              for o in board['interaction']['choices']],
                   'selected':None,'theme':{'background':'#24282B','foreground':'#EEE8DC','secondary':'#A9B2B5'}}
    return {'profile':profile,'bible':bible,'groups':groups,'segments':segments,'overlays':overlays,
            'interaction':interaction,'voice_specs':VOICE_SPECS,
            'script':{'video_title':board['title'],'source_title':'凌晨两点谁进过空房 第一集',
                      'episode_index':1,'profile':profile,'shots':shots,'skipped_segments':[],
                      'hook':'空房的门锁记录上出现相邻租客的名字。',
                      'summary':'核对记录的证据边界，发现017编号对应，提出三条未获选的调查入口。'}}


def evidence_artwork(destination: Path, font: Path) -> None:
    """Typeset the authored paper props; no extra case facts or generated lettering."""
    destination.mkdir(parents=True,exist_ok=True)
    text = {
        'log.png':['门锁使用记录','01:52','徐程（临时）','密码被使用'],
        'maintenance.png':['维修清点单'],
        'notebook.png':['事实：01:52，密码被使用','证词：徐程称未出门','假设：徐程进入？'],
        'attachment.png':['017／附件04'], 'old-card.png':['017'],
    }
    for filename, lines in text.items():
        size = (1200,500) if filename == 'attachment.png' else (1000,700) if filename == 'old-card.png' else (1200,1600)
        image = Image.new('RGB',size,'#ECE5D6');draw=ImageDraw.Draw(image)
        face=ImageFont.truetype(str(font),54 if filename=='notebook.png' else 76)
        for n,line in enumerate(lines):draw.text((70,100+n*170),line,font=face,fill='#282A2B')
        image.save(destination/filename)
    pair=Image.new('RGB',(1500,1000),'#76695B')
    with Image.open(destination/'old-card.png') as card:pair.paste(card.resize((650,455)),(55,160))
    with Image.open(destination/'attachment.png') as strip:pair.paste(strip.resize((650,271)),(785,290))
    pair.save(destination/'comparison.png')


def prepare_suspense(root: Path) -> Path:
    material=suspense_material(root);novel=root/'outputs/suspense-master';episode=novel/'suspense-master_1'
    if (episode/'thin_media_report.json').exists():raise FileExistsError('Keep existing suspense production history')
    episode.mkdir(parents=True,exist_ok=True)
    (novel/'owner').write_text('Codex suspense 01a133e5 / SD2.5 480p full trial / authorized 2026-10-13\n')
    for name in ['profile','bible','voice_specs']:
        atomic_write_json(novel/('story_bible.json' if name=='bible' else name+'.json'),material[name])
    for name,key in [('chapter_script','script'),('segments','segments'),('interaction','interaction'),
                     ('overlays','overlays'),('suspense_groups','groups')]:
        atomic_write_json(episode/(name+'.json'),material[key])
    package=root/'inputs/packages/suspense'
    imports=[(package/'角色资产/沈序-身份基准-v1.0.png','characters/character_001/turnaround.jpeg','identity_portrait'),
             (root/'assets/suspense/xucheng-reference.png','characters/character_002/turnaround.jpeg','character_design_sheet')]
    imports += [(root/'assets/suspense'/name, f'locations/location_{i:03d}/establishing.jpeg','setting_design')
                for i,name in enumerate(['corridor.png','room801.png','room802.png'],1)]
    from novel_manga.config import Settings
    evidence_artwork(root/'assets/suspense/evidence',Settings.from_env().font_path)
    imports += [(root/'assets/suspense/evidence'/filename,f'props/prop_{i:03d}/turnaround.jpeg','prop_design')
                for i,(_,filename,_) in enumerate(PROPS,1)]
    for source,relative,scope in imports:
        target=novel/'series_assets'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            with Image.open(source) as image:image.convert('RGB').save(target,quality=97)
            atomic_write_json(target.with_suffix('.jpeg.origin.json'),{'source':str(source),'reference_scope':scope,
                              'kind':'supplied-reference' if package in source.parents else 'authored-reference'})
    atomic_write_json(novel/'imported_assets.json',{'assets':[{'source':str(s),'path':'series_assets/'+t,'scope':c} for s,t,c in imports]})
    atomic_write_json(episode/'source_trace.json',{'original':str(package/'试播集/EP01-凌晨两点谁进过空房.md'),
                      'authored':str(root/REVISION/'storyboard.review.json'),
                      'shots':[{'shot_id':s['shot_id'],'source_shots':s['source_shots'],
                                'authored_seconds':s['authored_duration_seconds'],'request_budget_seconds':s['duration_seconds']}
                               for s in material['script']['shots']],
                      'timing_note':'Two 3-second reaction shots request the backend minimum of 4 seconds. Authored body 177s; production budget 179s. No dialogue changed or sped up.'})
    (episode/'source.txt').write_text('\n\n'.join(s['text'] for s in material['segments']))
    return episode


def pack_suspense(directory: Path) -> dict:
    from novel_manga.application.packing.context import load_context,POLICY
    from novel_manga.application.packing.service import prepared_shots,clip_entry
    from novel_manga.application.preparation.readiness import plan_issues
    from novel_manga.story.compilation import plan_totals
    script=read_json(directory/'chapter_script.json',{})
    context=load_context(directory,directory.parent/'story_bible.json')
    context['compiler_options']=replace(context['compiler_options'],max_clip_seconds=15,max_stages=3,camera_policy='authored')
    shots=prepared_shots(script,directory,identity_data=context.get('identity_data'));clips=[]
    for n,group in enumerate(read_json(directory/'suspense_groups.json',[]),1):
        selected=[shots[i-1] for i in group['shot_indexes']]
        packed={'kind':'video','location':selected[0]['location'],'shots':selected,
                'seconds':group['request_seconds'],'request_seconds':group['request_seconds'],
                'shot_timing':[{'seconds':s['duration_seconds']} for s in selected]}
        clip=clip_entry(packed,f'clip_{n:02d}',context)
        clip['prompt']=clip['prompt'].replace('中国国漫短剧片段','现代真人悬疑短剧片段')
        clothes = [f'{c.name}：{c.wardrobe}' for c in context['bible'].characters if c.name in clip['cast']]
        if clothes:
            clip['prompt'] += '\n【固定衣装】' + '；'.join(clothes) + '；只呈现本镜构图内可见部位，不为展示全套衣装改变景别。'
        clip['prompt'] += '\n【证据呈现】道具参考中的印刷字样只作为排版参考，准确时间、标签、编号与选择文字由后期证据层按本镜时段呈现。'
        clip['prompt_chars']=len(clip['prompt']);clips.append(clip)
    plan={'policy':POLICY,'limits':{'max_clip_seconds':15,'max_stages':3,'soft_cut_seconds':15,'camera_policy':'authored'},
          'totals':plan_totals(clips,shots,context),'clips':clips}
    issues=plan_issues(plan,script)
    if issues:raise ValueError(f'Suspense plan is not ready: {issues}')
    atomic_write_json(directory/'clip_plan.json',plan)
    return plan


def build_suspense_voices(root: Path) -> None:
    from novel_manga.interactive.runtime import runner_for
    from novel_manga.providers.base import ImageResult
    novel=root/'outputs/suspense-master';runner=runner_for(root,novel,'sd25')
    directory=novel/'series_assets/voices';directory.mkdir(parents=True,exist_ok=True)
    bank={}
    for i,(name,spec) in enumerate(VOICE_SPECS.items(),1):
        video=directory/(name+'-voice-source.mp4');wav=directory/(name+'.wav')
        if not wav.exists():
            if not video.exists():
                prompt=('Clean live-action solo casting recording. Only one adult man appears, matching the person in the reference image. '
                        'The reference may show multiple views of this ONE man; render one single medium close-up, no panels. '
                        'Static neutral studio background. '+spec['prompt']+' No music, no other voices. '
                        'Begin immediately and speak this whole phrase in Mandarin, then rest quietly: '+spec['text'])
                runner.context.provider.create_video(prompt,ImageResult(path=novel/f'series_assets/characters/character_{i:03d}/turnaround.jpeg'),video,14)
            run(['ffmpeg','-y','-v','error','-i',str(video),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
        bank[name]={'path':str(wav.relative_to(novel)),'source':str(video.relative_to(novel)),
                    'text':spec['text'],'origin':'generated-reference','voice_quality':'reference established; episode speech remains gated'}
    atomic_write_json(directory/'voices.json',bank)
    pack_suspense(novel/(novel.name+'_1'))
