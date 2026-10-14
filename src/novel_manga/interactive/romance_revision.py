"""Prepare a new H3 edition using single reference views and one authored beat per take."""
from pathlib import Path
from collections import Counter
import copy
import math
import re
import shutil
from PIL import Image

from novel_manga.interactive.romance import romance_material, pack_romance, compile_romance_h3
from novel_manga.application.profiles import h3_stamp
from novel_manga.util import read_json, atomic_write_json

EDITION='romance-h3-768-v2'


def single_take_material(package: Path) -> dict:
    material=romance_material(package)
    source=read_json(package/'routes/shared/storyboard.json',{})
    cuts=[c for s in source['episodes'][0]['segments'] for c in s['cuts']]
    shots=material['script']['shots'];groups=[]
    cast_ids={c['name']:c['id'] for c in material['cast']}
    seats={'C01':'northeast','C03':'northwest','C04':'southwest','C02':'southeast'}
    for shot,cut in zip(shots,cuts):
        turn=shot['turns'][0]
        words=re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]','',turn['text']) if turn['delivery_mode']!='silent_action' else ''
        seconds=max(4,math.ceil(max(float(cut['seconds']),len(words)/3.8+.6 if words else 0)))
        shot['original_duration_seconds']=float(cut['seconds'])
        shot['duration_seconds']=seconds
        shot['camera']='Static Shot'
        # Sequence-wide coverage notes remain provenance, not an instruction
        # to invent a reverse shot inside each individual generated take.
        shot['source_director_note']=cut.get('note','')
        source_quote='。'.join([cut['action'],cut.get('delivery','')])
        shot['source_quote']=source_quote
        material['segments'][shot['index']-1]['text']=source_quote
        frame=shot['authored_frame']
        frame=frame.replace('No rendered captions or text, no extra people or props.','')
        frame=frame.replace('Isolate this single face; other participants stay off-camera.','')
        frame=frame.replace('looking toward the conversation partner off-camera, with room for the eyeline.',
                            'with a natural eyeline beside the lens.')
        if len(shot['characters'])==1:
            actor=cast_ids[shot['characters'][0]];scene=shot['source_scene'];beat=shot['source_beat']
            seated=(scene==6 or scene==4 or actor=='C01' and (scene>1 or beat>=6)
                    or actor=='C03' and (scene>2 or scene==2 and beat>=8)
                    or actor=='C04' and (scene>3 or scene==3 and beat>=3)
                    or actor=='C02' and scene==5 and beat>=14)
            pose=f'seated on the {seats[actor]} chair' if seated else 'standing in the living room'
            frame=frame.replace('The subject is at the current position established by the preceding action,',
                                f'The subject is {pose},')
        if '固定摄像机' in turn['emotion']:
            frame=frame.replace('with a natural eyeline beside the lens.','looking at the fixed camera.')
        if shot['source_scene']==4:
            frame=frame.replace('gray crewneck with the overshirt removed off-screen','stone-gray crewneck')
            frame=frame.replace('The tall man takes off his charcoal overshirt, places it outside the frame and sits in the ivory interview chair, wearing only the stone-gray crewneck.',
                'The man, wearing a stone-gray crewneck, puts the loose charcoal garment from his hand beside the chair and sits in the ivory chair.')
            frame=frame.replace('addresses an unseen interviewer just beside the lens','looks just beside the lens')
            frame=frame.replace('looks toward the unseen interviewer','looks just beside the lens')
        # The selected authored framing and action still determine what is
        # shown; the backend performs that beat as one continuous camera take.
        frame+=' A single continuous locked camera take in the specified framing.'
        shot['authored_frame']=frame.strip();shot['motion_prompt']=shot['authored_frame']
        groups.append({'source_segment':shot['source_segment'],'shot_indexes':[shot['index']],'request_seconds':seconds})
    material['groups']=groups
    material['profile']['style']='romance-live-simple'
    material['profile']['native_h3_short_edge']=768
    material['script']['profile']=copy.deepcopy(material['profile'])
    return material


def selected_view(source: Path, target: Path, bounds, scope):
    """Read one supplied asset panel, preserving its original design."""
    with Image.open(source) as picture:
        w,h=picture.size
        box=tuple(round(v*(w if i%2==0 else h)) for i,v in enumerate(bounds))
        picture.crop(box).convert('RGB').save(target,quality=97)
    atomic_write_json(target.with_suffix('.jpeg.origin.json'),{'kind':'supplied-reference-view',
        'source':str(source),'source_rectangle':list(box),'reference_scope':scope})


def prepare_revision(root: Path) -> Path:
    novel=root/'outputs'/EDITION;episode=novel/(EDITION+'_1')
    if novel.exists():
        if (episode/'revision.json').is_file():return episode
        raise FileExistsError('Keep the existing edition; do not overwrite an incomplete preparation')
    package=root/'inputs/packages/romance/心动未剪辑-完整创作资产包-v2'
    material=single_take_material(package)
    episode.mkdir(parents=True)
    (novel/'owner').write_text('Codex / user-authorized full H3 768P regeneration / 2026-10-12\n')
    assets=novel/'series_assets'
    shutil.copytree(root/'outputs/romance-master/series_assets',assets)
    original=root/'outputs/romance-master/series_assets'
    portrait_widths=[.416,.338,.365,.400]
    for i,width in enumerate(portrait_widths,1):
        name=f'characters/character_{i:03d}/turnaround.jpeg'
        selected_view(original/name,assets/name,(.002,.002,width-.002,.998),'identity_portrait')
    for i in [1,2]:
        name=f'locations/location_{i:03d}/establishing.jpeg'
        selected_view(original/name,assets/name,(0,0,.783,.72),'setting_design')
    selected_view(original/'props/prop_001/turnaround.jpeg',assets/'props/prop_001/turnaround.jpeg',
                  (0,0,.50,1),'prop_design')
    selected_view(original/'props/prop_005/turnaround.jpeg',assets/'props/prop_005/turnaround.jpeg',
                  (0,0,.35,1),'prop_design')
    for i in range(4):
        selected_view(original/'props/prop_004/turnaround.jpeg',assets/f'props/prop_004/view_{i+1}.jpeg',
                      (i*.25,0,(i+1)*.25,1),'prop_design')
    # Use the supplied current-state interview image only in the interview.
    interview=assets/'characters/character_002/interview.jpeg'
    with Image.open(package/'images/周叙-前采一致性验证.png') as image:image.convert('RGB').save(interview,quality=97)
    atomic_write_json(interview.with_suffix('.jpeg.origin.json'),{'kind':'supplied-reference',
        'source':str(package/'images/周叙-前采一致性验证.png'),'reference_scope':'identity_portrait'})
    for name,key in [('profile','profile'),('story_bible','bible')]:atomic_write_json(novel/(name+'.json'),material[key])
    performance=read_json(root/'outputs/romance-master/performance_en.json',{})
    performance['轻松地介绍自己，对自己的说法有点得意']='The speaker introduces themself lightly, a little pleased with the wording.'
    atomic_write_json(novel/'performance_en.json',performance)
    for name,key in [('chapter_script','script'),('segments','segments'),('interaction','interaction'),('overlays','overlays'),('authored_groups','groups')]:
        atomic_write_json(episode/(name+'.json'),material[key])
    atomic_write_json(episode/'source_trace.json',{'original':str(package/'routes/shared/script.json'),
        'storyboard':str(package/'routes/shared/storyboard.json'),'cuts':[{'index':s['index'],'shot_id':s['shot_id'],
            'scene':s['source_scene'],'beat':s['source_beat']} for s in material['script']['shots']]})
    (episode/'source.txt').write_text('\n\n'.join(s['text'] for s in material['segments']))
    plan=pack_romance(episode)
    shots={s['index']:s for s in material['script']['shots']}
    cast_index={c['name']:i for i,c in enumerate(material['cast'],1)}
    for clip in plan['clips']:
        shot=shots[clip['shot_indexes'][0]]
        for ref in clip['references']:
            if ref['role']=='character' and ref['name']=='周叙' and shot['source_scene']==4:
                ref['path']='series_assets/characters/character_002/interview.jpeg';ref['view']='interview'
            if ref['role']=='prop' and ref['name']=='嘉宾行李箱':
                owner=shot['characters'][0] if shot['characters'] else '许知意'
                ref['path']=f'series_assets/props/prop_004/view_{cast_index[owner]}.jpeg'
    atomic_write_json(episode/'clip_plan.json',plan)
    plan=compile_romance_h3(episode)
    revision={'edition':EDITION,'source':'romance-master','native_short_edge':768,'shot_count':len(shots),
        'request_count':len(plan['clips']),'planned_body_seconds':sum(c['request_seconds'] for c in plan['clips']),
        'duration_counts':dict(Counter(c['request_seconds'] for c in plan['clips'])),
        'request_policy':'one authored beat per continuous take; cuts occur in assembly',
        'authorization':'User requested regenerate H3 after the 768P comparison; independent new edition, original histories retained'}
    atomic_write_json(episode/'revision.json',revision)
    return episode
