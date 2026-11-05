"""Episode room tone is additive; reuse verified assets and preserve native takes."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy
import json
import subprocess
import wave
import math
import struct

import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.repair.ambience import apply_assembly


def wave_tone(path,hz,amplitude,seconds=4):
    with wave.open(str(path),'wb')as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'))
        w.writeframes(b''.join(struct.pack('<h',int(32767*amplitude*math.sin(2*math.pi*hz*i/16000)))for i in range(int(seconds*16000))))


def test_episode_bed_keeps_video_packets_and_foreground_audio(tmp_path):
    episode=tmp_path/'book_1';episode.mkdir()
    foreground=episode/'foreground.wav';bed=episode/'room.wav'
    wave_tone(foreground,1000,.15);wave_tone(bed,300,.02)
    source=episode/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=4','-i',str(foreground),'-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True)
    entry={'id':'room','path':str(bed),'review':{'speech_free':True,'method':'synthetic fixture without speech'}}
    atomic_write_json(episode/'delivery_policy.json',{'episode_ambience':{'asset':entry,'gain_db':-3}})
    before={'status':'assembled','assembly':{'final_video':str(source),'timeline':[]}}
    result=apply_assembly(episode,copy.deepcopy(before))
    output=Path(result['assembly']['final_video']);assert output!=source and output.is_file()
    def video(path):return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-c','copy','-f','h264','-'])
    assert video(source)==video(output)
    def strength(path,hz):
        data=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'])
        samples=struct.unpack('<'+'f'*(len(data)//4),data)
        return abs(sum(x*complex(math.cos(2*math.pi*hz*i/16000),math.sin(2*math.pi*hz*i/16000))for i,x in enumerate(samples)))/len(samples)
    assert strength(output,1000)>.8*strength(source,1000) # vocal performance wasn't replaced
    assert strength(output,300)>4*strength(source,300)
    stamp=output.stat().st_mtime_ns
    again=apply_assembly(episode,result)
    assert again['assembly']['final_video']==str(output)and output.stat().st_mtime_ns==stamp
    assert again['assembly']['episode_ambience_mix']['video_reencoded']is False


def test_mix_tool_uses_registered_audio_and_preserves_generation_counts(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode.audio_mix import AudioMixOperations
    episode=tmp_path/'book_1';episode.mkdir();run=tmp_path/'run';work=run/'sandbox';(work/'output').mkdir(parents=True)
    ctx=NS(episode_dir=episode,novel_dir=tmp_path,run_dir=run,work_dir=work)
    bed=episode/'room.wav';wave_tone(bed,300,.02)
    asset={'id':'room','path':str(bed),'review':{'speech_free':True,'method':'source-reviewed speech-free fixture'}}
    atomic_write_json(episode/'delivery_policy.json',{'interaction_ambience':asset})
    native=episode/'native.mp4';native.touch()
    atomic_write_json(episode/'thin_media_report.json',{'status':'assembled','assembly':{'final_video':str(native)}})
    (run/'source.txt').write_text('同一间池室，背景是水声和空房间底声。')
    plan={'candidate_id':'room','gain_db':-6,'source_quote':'背景是水声和空房间底声','reason':'Keep the room acoustic bed between spoken lines.'}
    atomic_write_json(work/'output/audio-mix.json',plan)
    seen=[]
    monkeypatch.setattr('novel_manga.application.repair.ambience.apply_assembly',lambda *a,**k:seen.append(read_json(episode/'delivery_policy.json'))or a[1])
    monkeypatch.setattr('novel_manga.application.review.execution.save_result',lambda d,r,*a,**k:r)
    media=NS(runner=lambda:NS(context=NS()))
    result=AudioMixOperations(ctx,media,source_checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})
    assert result['ok']and result['assembly_changed']
    assert seen[0]['episode_ambience']['gain_db']==-6
    assert not (episode/'repair_history/history.json').exists()
    plan['candidate_id']='not-registered';atomic_write_json(work/'output/audio-mix.json',plan)
    with pytest.raises(ValueError,match='registered'):AudioMixOperations(ctx,media).prepare({})
    plan['candidate_id']='room';atomic_write_json(work/'output/audio-mix.json',plan)
    records=read_json(run/'audio-mix-candidates.json')
    records[0].pop('source_review_policy',None)
    records[0]['source_review']={'consistent':True,'problems':['Synthetic, listening not performed.']}
    atomic_write_json(run/'audio-mix-candidates.json',records)
    checks=[]
    def clarified(*a):
        checks.append(True)
        return {'consistent':True,'problems':[],'limitations':['Synthetic, listening not performed.']}
    tool=AudioMixOperations(ctx,media,source_checker=clarified)
    assert tool.prepare({})['ok']
    assert tool.prepare({})['ok'] and len(checks)==1
    records=read_json(run/'audio-mix-candidates.json')
    assert records[0]['source_review_history'][0]['review']['problems']==['Synthetic, listening not performed.']


def test_music_and_ambience_use_the_same_policy_persistence_helper(tmp_path,monkeypatch):
    from novel_manga.application.agents.episode import postproduction
    from novel_manga.application.agents.episode.audio_mix import AudioMixOperations
    from novel_manga.application.agents.episode.music import MusicOperations

    episode=tmp_path/'book_1';episode.mkdir();run=tmp_path/'run';work=run/'sandbox';(work/'output').mkdir(parents=True)
    ctx=NS(project_root=tmp_path,episode_dir=episode,novel_dir=tmp_path,run_dir=run,work_dir=work)
    bed=episode/'room.wav';wave_tone(bed,300,.02)
    asset={'id':'room','path':str(bed),'review':{'speech_free':True,'method':'source-reviewed speech-free fixture'}}
    atomic_write_json(episode/'delivery_policy.json',{'interaction_ambience':asset})
    native=episode/'native.mp4';native.touch()
    atomic_write_json(episode/'thin_media_report.json',{'status':'assembled','assembly':{'final_video':str(native)}})
    (run/'source.txt').write_text('同一间池室，背景是水声和空房间底声。')
    atomic_write_json(work/'output/audio-mix.json',{'candidate_id':'room','gain_db':-6,
        'source_quote':'背景是水声和空房间底声','reason':'Keep the room acoustic bed between spoken lines.'})
    media=NS(runner=lambda:NS(context=NS()))
    calls=[]
    def persist(*args,**kwargs):
        calls.append((args,kwargs))
        report=read_json(episode/'thin_media_report.json')
        return report,(report.get('assembly')or{}).get('final_video')
    monkeypatch.setattr(postproduction,'persist_audio_policy',persist,raising=False)
    monkeypatch.setattr('novel_manga.application.repair.ambience.apply_assembly',lambda _d,report,**_k:report)
    monkeypatch.setattr('novel_manga.application.review.execution.save_result',lambda _d,report,*_a,**_k:report)

    music={'asset':{'id':'BGM-009','review':{'status':'accepted'}},'gain_db':-18,
        'source_review':{'consistent':True,'problems':[]}}
    assert MusicOperations(ctx,media).publish(music)['ok']
    assert AudioMixOperations(ctx,media,source_checker=lambda *a:{'consistent':True,'problems':[]}).prepare({})['ok']
    assert len(calls)==2
    assert all(kwargs['context'] is ctx for _,kwargs in calls)
    assert [kwargs['policy_key'] for _,kwargs in calls]==['episode_music','episode_ambience']


@pytest.mark.parametrize('clip_bed',[False,True])
def test_published_mix_reuses_pre_bed_source_for_replay_and_gain(tmp_path,monkeypatch,clip_bed):
    from novel_manga.application.repair.delivery import publish_if_ready
    episode=tmp_path/'book_1';episode.mkdir()
    foreground=episode/'foreground.wav';bed=episode/'room.wav'
    wave_tone(foreground,1000,.15);wave_tone(bed,300,.02)
    source=episode/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=4','-i',str(foreground),'-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True)
    entry={'id':'room','path':str(bed),'review':{'speech_free':True,'method':'synthetic fixture'}}
    policy={'episode_ambience':{'asset':entry,'gain_db':-3}}
    if clip_bed:policy['clip_ambience']={'c1':entry}
    atomic_write_json(episode/'delivery_policy.json',policy)
    atomic_write_json(episode/'clip_plan.json',{'clips':[{'clip_id':'c1','kind':'video','shot_indexes':[1]}]})
    atomic_write_json(episode/'chapter_script.json',{'shots':[{'index':1,'turns':[{'delivery_mode':'silent_action'}]}]})
    report=apply_assembly(episode,{'assembly':{'final_video':str(source),'timeline':[{'unit_id':'c1','start':1,'end':2}]}})
    original=copy.deepcopy(report['assembly']['episode_ambience_mix'])
    atomic_write_json(episode/'thin_media_report.json',report)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'done')
    monkeypatch.setattr('novel_manga.review.contracts.current_review',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.repair.delivery.precise_review_blockers',lambda *a:[])
    assert publish_if_ready(episode,{}, {})
    published=read_json(episode/'thin_media_report.json')
    again=apply_assembly(episode,copy.deepcopy(published))
    assert again['assembly']['final_video']==published['assembly']['final_video']
    assert again['assembly']['episode_ambience_mix']['source']==original['source']
    policy['episode_ambience']['gain_db']=-9;atomic_write_json(episode/'delivery_policy.json',policy)
    revised=apply_assembly(episode,published)
    assert revised['assembly']['episode_ambience_mix']['source']==original['source']
    assert revised['assembly']['episode_ambience_mix']['gain_db']==-9


def test_persisted_bed_keeps_missing_assembly_available_for_repair(tmp_path):
    bed=tmp_path/'room.wav';wave_tone(bed,300,.02)
    entry={'id':'room','path':str(bed),'review':{'speech_free':True,'method':'synthetic fixture'}}
    atomic_write_json(tmp_path/'delivery_policy.json',{'episode_ambience':{'asset':entry,'gain_db':-6}})
    report={'status':'clips_failed','assembly':None}
    assert apply_assembly(tmp_path,report)==report
