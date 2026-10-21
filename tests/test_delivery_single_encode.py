import json
import array
import math
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from support.delivery_cases import labelled_delivery
from novel_manga.interactive import delivery


def test_delivery_encodes_the_body_only_once(labelled_delivery, monkeypatch):
    case = labelled_delivery
    def probe(command, **kwargs):
        frames = '4705' if str(command[-1]).endswith('/episode01.mp4') else '4630'
        return json.dumps({'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
             'r_frame_rate': '25/1', 'nb_frames': frames},
            {'codec_type': 'audio', 'codec_name': 'aac'}]})
    monkeypatch.setattr(delivery.subprocess, 'check_output', probe)
    receipt = delivery.deliver(SimpleNamespace(context=case.ctx), case.report, case.destination)
    full = [c for c in case.commands if '-c:v' in c and not str(c[-1]).endswith('/interaction.mp4')]
    assert len(full) == 1
    assert 'ass=evidence.ass' in full[0][full[0].index('-filter_complex')+1]
    assert receipt['actual_body_seconds'] == 185.2 and receipt['technical_passed']


def test_vfr_duration_sets_cfr_body_frames_instead_of_source_frame_count():
    stream = {'nb_frames': '9790', 'r_frame_rate': '25/1', 'avg_frame_rate': '979000000/39344703',
              'duration_ts': 5036122, 'time_base': '1/12800'}
    assert delivery.cfr_body_frames(stream) == 9837


def test_cfr_body_keeps_its_exact_frames():
    assert delivery.cfr_body_frames({'nb_frames': '251', 'r_frame_rate': '25/1',
                                    'avg_frame_rate': '25/1', 'duration': '10.040000'}) == 251


def _run(*args):
    subprocess.run(['ffmpeg', '-y', '-v', 'error', *map(str, args)], check=True)


def _pcm(movie, start, seconds=.15):
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(movie),
        '-t', str(seconds), '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'])
    return array.array('h', data)


def _energy(samples, frequency):
    return abs(sum(value*complex(math.cos(2*math.pi*frequency*i/16000),
                               math.sin(2*math.pi*frequency*i/16000)) for i,value in enumerate(samples)))/len(samples)


def _frame(movie, frame, width=320, height=568):
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(movie),
        '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return Image.frombytes('RGB', (width, height), data)


@pytest.mark.parametrize('mode,publication,spoken,vfr', [
    ('ordinary', False, False, False),
    ('clean_mix', True, True, False),
    ('evidence', True, False, False),
    ('clean', False, False, True),
])
def test_real_final_graph_keeps_frames_audio_captions_cards_and_qc(tmp_path, monkeypatch, mode, publication, spoken, vfr):
    from novel_manga.config import DEFAULT_FONT_PATH
    from novel_manga.util import atomic_write_json
    if not shutil.which('ffmpeg') or not DEFAULT_FONT_PATH.is_file():
        pytest.skip('local ffmpeg and font required')
    episode = tmp_path/'book/book_1'; work = episode/'work'; work.mkdir(parents=True)
    ass = episode/'dialogue.ass'
    ass.write_text('''[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 568
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,0,0,90,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,CAPTION
''')
    joined = work/'joined.mp4'; assembled = episode/'assembled.mp4'
    mask = ('drawbox=x=0:y=0:w=100:h=60:color=0x303030:t=fill,'
            'drawbox=x=iw-100:y=ih-60:w=100:h=60:color=0x303030:t=fill,'
            'drawbox=x=0:y=ih-150:w=iw:h=90:color=0x303030:t=fill')
    if vfr: mask += ",select='not(eq(mod(n,5),2))'"
    _run('-f','lavfi','-i','testsrc2=s=320x568:r=25:d=10.04',
         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=10.24,volume=2',
         '-vf',mask,'-vsync','vfr' if vfr else 'cfr', '-c:v','libx264','-preset','ultrafast','-c:a','aac',joined)
    # The ordinary assembly already has captions; the clean mix uses its audio only.
    _run('-i',joined,'-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=10.24,volume=2',
         '-vf',f'ass={ass}', '-map','0:v','-map','1:a' if mode=='clean_mix' else '0:a',
         '-c:v','libx264','-preset','ultrafast','-c:a','aac',assembled)
    cover = episode/'cover.jpeg'; Image.new('RGB',(320,568),'gray').save(cover)
    atomic_write_json(episode/'authored_groups.json', [])
    shots = [{'index':1,'duration_seconds':4.04,'turns':[{'text':'synthetic speech marker'}] if spoken else []},
             {'index':2,'duration_seconds':6,'turns':[]}]
    clips = [{'clip_id':'clip_01','shot_indexes':[1],'request_seconds':4.04},
             {'clip_id':'clip_02','shot_indexes':[2],'request_seconds':6}]
    timeline = [{'unit_id':'clip_01','start':0,'end':4.04},{'unit_id':'clip_02','start':4.04,'end':10.04}]
    ctx = SimpleNamespace(episode_dir=episode,novel_dir=episode.parent,work=work,script={'shots':shots},
        clip_plan={'clips':clips},provider=SimpleNamespace(short_edge=768),profile={},
        settings=SimpleNamespace(width=320,height=568,fps=25,font_path=DEFAULT_FONT_PATH))
    report = {'assembly':{'final_video':str(assembled),'ass':str(ass),'cover':str(cover),
                         'timeline':timeline,'thin_passed':True},
              'quality_review':{'passed':True,'content_passed':True},'clips':[]}
    if mode=='clean_mix':report['assembly']['episode_music_mix']={'source':'synthetic'}
    policy = {'extra_visual_overlays':mode in ('ordinary','evidence'), 'packaging':{'watermark':{
        'text':'W','fontfile':str(DEFAULT_FONT_PATH),'fontsize':28,'fontcolor':'red','borderw':0,
        'bordercolor':'black','x':'w-text_w-8','y':'h-text_h-8'}}}
    atomic_write_json(episode/'delivery_policy.json',policy)
    interaction = {'seconds':3,'question':'?','options':[]}
    if mode=='evidence':interaction['ambience_shot_index']=2
    atomic_write_json(episode/'interaction.json',interaction)
    atomic_write_json(episode/'overlays.json',[{'index':2,'text':'EVIDENCE','placement':'panel'}])
    if publication:
        atomic_write_json(episode/'publication.json',{'world_card':{'after_clip_id':'clip_01','seconds':3,
            'series_title':'Series','episode':'01','title':'World','premise':'Rules'},'watermark':'PUB'})
    if mode=='evidence':
        atomic_write_json(episode/'evidence_surfaces.json',{'surfaces':[{'id':'synthetic'}]})
        monkeypatch.setattr('novel_manga.interactive.evidence.prepare_evidence',lambda *a:{
            'body':str(joined),'passed':True,'shot_spans':[{'index':2,'start':4.04,'end':10.04}]})
    monkeypatch.setattr(delivery,'_card',lambda path,*a:Image.new('RGB',(320,568),'blue').save(path))
    commands = []; original_run = subprocess.run
    def recording_run(command, **kwargs):
        commands.append(command)
        return original_run(command, **kwargs)
    monkeypatch.setattr(subprocess,'run',recording_run)
    destination=tmp_path/'delivery'
    receipt=delivery.deliver(SimpleNamespace(context=ctx),report,destination)
    movie=destination/'episode01.mp4'
    expected=401 if publication else 326
    streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(movie)]))
    video=next(s for s in streams['streams'] if s['codec_type']=='video')
    assert int(video['nb_frames'])==expected and video['avg_frame_rate']=='25/1'
    assert receipt['technical_passed'] and receipt['production_media_passed']
    assert receipt['actual_body_seconds']==(13.04 if publication else 10.04)
    assert receipt['interaction']['start']==receipt['actual_body_seconds']
    assert receipt['status']==('candidate' if mode=='evidence' else 'accepted')
    assert not (destination/'body.mp4').exists() and not (destination/'published-body.mp4').exists()
    full=[c for c in commands if '-c:v' in c and str(c[-1])!=str(destination/'interaction.mp4')]
    assert len(full)==1 and str(full[0][-1])==str(movie)
    samples=_pcm(movie,.5)
    frequency=880 if mode=='clean_mix' else 440
    assert _energy(samples,frequency)>10*_energy(samples,1320-frequency)
    if publication:
        samples=_pcm(movie,4.14,.1)
        rms=math.sqrt(sum(x*x for x in samples)/len(samples))
        assert rms<1 if spoken else rms>100
        shifted=(destination/'subtitles.ass').read_text()
        assert '0:00:03.00,0:00:04.04' in shifted
        assert '0:00:07.04,0:00:08.00' in shifted
    tail=_frame(movie,expected-75)
    assert all(r<20 and g<20 and b>200 for r,g,b in tail.crop((100,200,220,320)).getdata())
    last_body=_frame(movie,expected-76)
    assert sum(r<20 and g<20 and b>200 for r,g,b in last_body.crop((100,200,220,320)).getdata())<10000
    # Full-film watermark is present on the final choice card too.
    assert any(r>150 and r>g*2 and r>b*2 for r,g,b in tail.crop((250,508,320,568)).getdata())
    if publication:
        before=_frame(movie,25); world=_frame(movie,120)
        assert any(min(rgb)>100 for rgb in before.crop((15,18,75,45)).getdata())
        assert any(min(rgb)>100 for rgb in world.crop((15,18,75,45)).getdata())
        assert not any(min(rgb)>100 for rgb in tail.crop((15,18,75,45)).getdata())
    # The caption is visible on footage before the cut and absent over the world card.
    caption=_frame(movie,90)
    assert sum(min(rgb)>180 for rgb in caption.crop((50,440,270,490)).getdata())>50
    if publication:
        world=_frame(movie,120)
        assert sum(min(rgb)>180 for rgb in world.crop((50,440,270,490)).getdata())<10
    tail_pcm=_pcm(movie,receipt['interaction']['start']+.2)
    if mode=='evidence':
        assert _energy(tail_pcm,440)>100
        assert receipt['ambience_source']['start']==8.04
    else:
        assert max(map(abs,tail_pcm))<3
    if vfr:
        source_stream=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(joined)]))['streams'][0]
        assert int(source_stream['nb_frames'])<251
