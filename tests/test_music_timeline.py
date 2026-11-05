import copy
import math
from pathlib import Path
import struct
import subprocess
import wave

import pytest

from novel_manga.application.music_timeline import resolve,prepare_bed
from novel_manga.application.repair.ambience import apply_assembly
from novel_manga.application.music_selection import episode_music_errors
from novel_manga.util import atomic_write_json,read_json


def tone(path,hz,seconds=8):
    with wave.open(str(path),'wb')as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'))
        w.writeframes(b''.join(struct.pack('<h',int(4000*math.sin(2*math.pi*hz*i/16000)))for i in range(seconds*16000)))


def anchor(shot,edge='start',offset=0):return {'shot':shot,'edge':edge,'offset_seconds':offset}


@pytest.fixture
def episode(tmp_path):
    directory=tmp_path/'book_1';directory.mkdir()
    atomic_write_json(directory/'chapter_script.json',{'shots':[{'index':1,'duration_seconds':3},{'index':2,'duration_seconds':3}]})
    atomic_write_json(directory/'clip_plan.json',{'clips':[{'clip_id':'a','shot_indexes':[1],'request_seconds':3},
        {'clip_id':'b','shot_indexes':[2],'request_seconds':3}]})
    audio=directory/'music.wav';tone(audio,300)
    asset={'id':'BGM-009','path':str(audio),'selected_seconds':8,'review':{'status':'accepted','reason':'fixture'}}
    policy={'cues':[{'id':'cue1','asset':asset,'start':anchor(1),'end':anchor(2,'end'),
        'gain_db':-12,'fade_in_seconds':.2,'fade_out_seconds':.2,'loop':False}],
        'silences':[{'start':anchor(2),'end':anchor(2,'start',1),'reason':'Listen to the source sound.'}]}
    timeline=[{'unit_id':'a','start':0,'end':3},{'unit_id':'b','start':3,'end':6}]
    return directory,policy,timeline


def test_story_anchors_follow_actual_cut_timing_including_silence(episode):
    directory,policy,timeline=episode
    before=resolve(directory,policy,timeline)
    assert before['cues'][0]['end']==6 and before['silences']==[{'start':3.,'end':4.}]
    timeline[0]['end']=4;timeline[1].update(start=4,end=7)
    after=resolve(directory,policy,timeline)
    assert after['cues'][0]['end']==7 and after['silences']==[{'start':4.,'end':5.}]
    assert policy['silences'][0]['start']==anchor(2)


def test_source_authored_silence_is_kept_and_planned_music_cannot_be_omitted(episode):
    directory,policy,timeline=episode
    script=read_json(directory/'chapter_script.json');script['music_plan']={
        'cues':[{'id':'intent'}],'silences':copy.deepcopy(policy['silences'])}
    atomic_write_json(directory/'chapter_script.json',script)
    policy['silences']=[]
    assert resolve(directory,policy,timeline)['silences']==[{'start':3.,'end':4.}]
    assert episode_music_errors(directory,{'assembly':{}})


def test_h3_story_music_entry_reaches_persistent_agent_without_first_frames(tmp_path,monkeypatch):
    from novel_manga.interactive import production,first_frame_entry
    root=tmp_path/'studio';book=root/'outputs/suspense-test';directory=book/'suspense-test_1';directory.mkdir(parents=True)
    atomic_write_json(directory/'clip_plan.json',{'clips':[]})
    atomic_write_json(directory/'chapter_script.json',{'shots':[],'music_plan':{'cues':[{'id':'cue1'}]}})
    atomic_write_json(book/'profile.json',{})
    seen=[]
    monkeypatch.setattr(production,'runner_for',lambda *a,**k:object())
    monkeypatch.setattr(first_frame_entry,'run',lambda *a,**k:seen.append(k)or{'status':'agent-routed'})
    assert production.run_episode(root,'h3',project='suspense',edition=book.name)['status']=='agent-routed'
    assert seen and seen[0]['resume']is False


def test_bad_anchor_unknown_shot_and_fade_cannot_mix(episode):
    directory,policy,timeline=episode
    policy['cues'][0]['start']=anchor(99)
    with pytest.raises(ValueError,match='shot'):resolve(directory,policy,timeline)
    policy['cues'][0]['start']=anchor(1);policy['cues'][0]['fade_out_seconds']=20
    with pytest.raises(ValueError,match='fade'):resolve(directory,policy,timeline)


def test_external_story_music_is_not_also_requested_from_the_video_model(tmp_path,monkeypatch):
    from support.split_episode import split_episode
    from novel_manga.application.packing.context import context_for_plan
    from novel_manga.application.packing.service import compile_plan
    from novel_manga.story.h3 import compose
    episode,script,saved=split_episode.__wrapped__(tmp_path,monkeypatch)
    script['music_plan']={'cues':[{'id':'story-cue','reason':'music will be mixed after video'}]}
    for shot in script['shots']:shot['audio_plan']={'music_cue':'A restrained suspense pad.'}
    context=context_for_plan(episode,episode.parent/'story_bible.json',saved)
    plan,_=compile_plan(script,context)
    clip=next(c for c in plan['clips']if c['kind']=='video')
    assert clip['music_delivery']=='postmix'and not clip.get('music_cues')
    assert clip['external_music_cues']
    count=len(clip['shot_timing'])
    prompt=compose(clip,['A person observes.']*count,[('',[])]*count)
    assert prompt.endswith('non_diegetic_music:\nNone.')
    with pytest.raises(ValueError,match='postmix'):
        compose(clip,['A person observes.']*count,[('',[])]*count,music='A suspense pad.')


def test_music_bus_leaves_real_gap_and_native_audio_remains_while_video_is_copied(episode):
    directory,policy,timeline=episode;foreground=directory/'foreground.wav';tone(foreground,1000)
    video=directory/'native.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=8',
        '-i',str(foreground),'-t','8','-c:v','libx264','-c:a','aac',str(video)],check=True)
    report={'assembly':{'final_video':str(video),'timeline':timeline}}
    atomic_write_json(directory/'delivery_policy.json',{'episode_music':policy})
    mixed=apply_assembly(directory,copy.deepcopy(report));output=Path(mixed['assembly']['final_video'])
    assert output!=video and episode_music_errors(directory,mixed)==[]
    def signal(path,start,seconds,hz):
        data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t',str(seconds),
            '-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'])
        values=struct.unpack('<'+'f'*(len(data)//4),data)
        return abs(sum(x*complex(math.cos(2*math.pi*hz*i/16000),math.sin(2*math.pi*hz*i/16000))for i,x in enumerate(values)))/len(values)
    assert signal(output,1,1,300)>10*signal(output,3.2,.5,300)
    assert signal(output,4.2,.5,300)>10*signal(output,3.2,.5,300)
    assert signal(output,7,.5,300)<signal(output,1,1,300)/10 # no restart in an unscored tail/card
    assert signal(output,3.2,.5,1000)==pytest.approx(signal(video,3.2,.5,1000),rel=.05)
    assert signal(output,1,1,300)>.005
    mix=mixed['assembly']['episode_music_mix']
    assert mix['inputs']['processing']=='music-ebur128-reference--5-v1'
    assert mix['inputs']['music_timeline']['cues'][0]['target_lufs']==-17
    graph=mix['command'][mix['command'].index('-filter_complex')+1]
    assert 'amix=inputs=2:duration=first:normalize=0' in graph
    elementary=lambda p:subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:v:0','-c','copy','-f','h264','-'])
    assert elementary(output)==elementary(video)
    again=apply_assembly(directory,copy.deepcopy(mixed))
    assert again['assembly']['final_video']==str(output)
    policy['silences'][0]['end']=anchor(2,'start',2)
    atomic_write_json(directory/'delivery_policy.json',{'episode_music':policy})
    assert episode_music_errors(directory,mixed)
    revised=apply_assembly(directory,mixed)
    assert revised['assembly']['final_video']!=str(output)
    assert signal(Path(revised['assembly']['final_video']),4.2,.5,300)<signal(output,4.2,.5,300)/10
    assert output.exists()and video.exists()


def test_explicit_source_offset_can_cross_a_cut_without_changing_silence(episode):
    directory,policy,timeline=episode
    source={'start':anchor(1),'end':anchor(1,'end',1.5)}
    script=read_json(directory/'chapter_script.json');script['music_plan']={'silences':[source]}
    atomic_write_json(directory/'chapter_script.json',script)
    original=copy.deepcopy(script)
    resolved=resolve(directory,policy,timeline)
    assert {'start':0.,'end':4.5} in resolved['silences']
    timeline[0]['end']=4;timeline[1].update(start=4,end=7)
    assert {'start':0.,'end':5.5} in resolve(directory,policy,timeline)['silences']
    assert read_json(directory/'chapter_script.json')==original


@pytest.mark.parametrize('point',[anchor(1,'start',-.1),anchor(2,'end',.1)])
def test_explicit_offset_cannot_leave_actual_episode(episode,point):
    directory,policy,timeline=episode
    policy['silences']=[{'start':anchor(1),'end':point}]
    with pytest.raises(ValueError,match='leaves the episode'):
        resolve(directory,policy,timeline)


def test_authored_end_card_silence_is_silent_without_extending_music_bus(episode):
    directory,policy,timeline=episode
    atomic_write_json(directory/'interaction.json',{'seconds':3})
    tail={'id':'tail','start':anchor(2,'end'),'end':anchor(2,'end',2)}
    script=read_json(directory/'chapter_script.json');script['music_plan']={'silences':[tail]}
    atomic_write_json(directory/'chapter_script.json',script)
    result=resolve(directory,policy,timeline)
    assert result['duration']==6 and result['silences']==[{'start':3.,'end':4.}]
    assert read_json(directory/'chapter_script.json')==script
    policy['cues'][0]['end']=anchor(2,'end',2)
    with pytest.raises(ValueError,match='cue cue1.*leaves the episode'):resolve(directory,policy,timeline)


def test_silence_can_cross_into_declared_card_but_not_beyond_it(episode):
    directory,policy,timeline=episode
    atomic_write_json(directory/'interaction.json',{'seconds':3})
    policy['silences']=[{'id':'tail','start':anchor(2,'end',-1),'end':anchor(2,'end',2)}]
    assert resolve(directory,policy,timeline)['silences']==[{'start':5.,'end':6.}]
    policy['silences'][0]['end']=anchor(2,'end',4)
    with pytest.raises(ValueError,match='silence tail.*leaves the episode'):resolve(directory,policy,timeline)


@pytest.mark.parametrize('ambience',['shot','independent'])
def test_card_audio_source_is_not_assumed_silent_for_tail_music_acceptance(episode,ambience):
    directory,policy,timeline=episode
    atomic_write_json(directory/'interaction.json',{'seconds':3,**({'ambience_shot_index':2}if ambience=='shot'else{})})
    if ambience=='independent':atomic_write_json(directory/'delivery_policy.json',{'interaction_ambience':{'id':'existing'}})
    policy['silences']=[{'id':'tail','start':anchor(2,'end'),'end':anchor(2,'end',2)}]
    with pytest.raises(ValueError,match='silence tail.*leaves the episode'):resolve(directory,policy,timeline)


def test_music_bed_cache_tracks_source_bytes_without_duplicate_encoding(episode,monkeypatch):
    import os
    from novel_manga.application import music_timeline

    directory,policy,timeline=episode;report={'assembly':{'timeline':timeline}}
    encodes=[];original_run=music_timeline.run
    monkeypatch.setattr(music_timeline,'run',lambda command:encodes.append(command)or original_run(command))
    first=prepare_bed(directory,report,policy)
    assert prepare_bed(directory,report,policy)==first and len(encodes)==1
    source=Path(policy['cues'][0]['asset']['path']);before=source.stat()
    tone(source,900)
    os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
    changed=prepare_bed(directory,report,policy)
    assert changed['asset']['path']!=first['asset']['path']and len(encodes)==2
    assert changed['music_timeline']['cues'][0]['source_sha256']!=first['music_timeline']['cues'][0]['source_sha256']
    assert prepare_bed(directory,report,policy)==changed and len(encodes)==2
