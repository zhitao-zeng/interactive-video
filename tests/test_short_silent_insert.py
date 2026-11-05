"""The provider's minimum must not lengthen an explicitly authored silent insert."""
from pathlib import Path
from types import SimpleNamespace as NS
import subprocess

import pytest

from novel_manga.config import Settings
from novel_manga.media.renderer import Renderer
from novel_manga.util import media_duration


def test_mux_short_insert_keeps_native_file_and_uses_authored_duration(tmp_path):
    original=tmp_path/'native.mp4'
    subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-f','lavfi','-i','color=c=blue:s=180x320:r=25:d=4',
        '-f','lavfi','-i','sine=frequency=240:sample_rate=48000:duration=4','-c:v','libx264',
        '-c:a','aac','-shortest',str(original)],check=True)
    before=original.read_bytes()
    renderer=Renderer(Settings(width=180,height=320,fps=25))
    _,duration=renderer.mux_visual_group(original,original,tmp_path/'insert.mp4',duration_limit=2)
    assert abs(duration-2)<.05
    assert original.read_bytes()==before and media_duration(original)>=4


@pytest.mark.parametrize('input_key',['h3_input_mode','sd_input_mode'])
@pytest.mark.parametrize('spoken,mode,expected',[(False,'first-frame',2),(True,'first-frame',None),(False,None,None)])
def test_only_silent_first_frame_floor_is_trimmed(tmp_path,monkeypatch,spoken,mode,expected,input_key):
    from novel_manga.media import postprocess
    clip={'clip_id':'clip_01','kind':'video','request_seconds':4,'shot_indexes':[1]}
    script={'shots':[{'index':1,'duration_seconds':2,'turns':[{'text':'保留台词'}]if spoken else[]}]}
    calls=[]
    def mux(video,audio,out,**kwargs):
        calls.append(kwargs)
        return out,kwargs.get('duration_limit',4)
    ctx=NS(work=tmp_path/'work',profile={input_key:mode},script=script,clip_plan={'clips':[clip]},
           renderer=NS(mux_visual_group=mux))
    monkeypatch.setattr(postprocess,'inner_voice_audio',lambda ctx,clip,wav:wav)
    monkeypatch.setattr(postprocess,'chat_segments',lambda *a:[])
    monkeypatch.setattr(postprocess.subtitles,'subtitle_events',lambda *a:[])
    parts=postprocess.story_segments(ctx,[{'clip_id':'clip_01','selected':{'video':str(tmp_path/'native.mp4')}}])
    assert calls==([{'duration_limit':2}]if expected else[{}])
    assert parts[0]['duration']==(expected or 4)


def test_real_packed_silent_insert_uses_source_duration(tmp_path,monkeypatch):
    from novel_manga.interactive.authored import prepare_authored,pack_authored
    from novel_manga.interactive.first_frame_entry import select_mode
    from novel_manga.util import atomic_write_json,read_json
    from novel_manga.media import postprocess
    project=Path(__file__).resolve().parents[1]/'projects/adventure/laughing-country'
    directory=prepare_authored(project,tmp_path/'edition');book=directory.parent
    script=read_json(directory/'chapter_script.json')
    script['shots'][0]['duration_seconds']=2
    script['shots'][0]['turns']=[]
    atomic_write_json(directory/'chapter_script.json',script)
    profile=read_json(book/'profile.json');profile['h3_speech_policy']='reference'
    atomic_write_json(book/'profile.json',profile)
    select_mode(book,'first-frame')
    clip=pack_authored(directory)['clips'][0]
    assert 'shots' not in clip and not clip['dialogue_bindings']
    calls=[]
    def mux(video,audio,out,**kwargs):
        calls.append(kwargs)
        return out,kwargs.get('duration_limit',4)
    ctx=NS(work=tmp_path/'work',profile={'h3_input_mode':'first-frame'},script=script,
           clip_plan={'clips':[clip]},renderer=NS(mux_visual_group=mux))
    monkeypatch.setattr(postprocess,'inner_voice_audio',lambda ctx,clip,wav:wav)
    monkeypatch.setattr(postprocess,'chat_segments',lambda *a:[])
    monkeypatch.setattr(postprocess.subtitles,'subtitle_events',lambda *a:[])
    parts=postprocess.story_segments(ctx,[{'clip_id':clip['clip_id'],'selected':{'video':str(tmp_path/'native.mp4')}}])
    assert calls==[{'duration_limit':2}] and parts[0]['duration']==2
