from PIL import Image,ImageFont
from novel_manga.interactive.delivery import evidence_events


def test_overlay_follows_observed_cut_instead_of_authored_ratio():
    script={'shots':[{'index':1,'duration_seconds':6},{'index':2,'duration_seconds':6}]}
    plan={'clips':[{'clip_id':'clip_01','shot_indexes':[1,2]}]}
    timeline=[{'unit_id':'clip_01','start':0,'end':12}]
    events=evidence_events([{'index':2,'text':'证据'}],script,plan,timeline,
                           shot_spans=[{'index':2,'start':8,'end':12}])
    assert events==[{'text':'证据','start':8,'end':12}]
    assert evidence_events([{'index':2,'text':'证据'}],script,plan,timeline,shot_spans=[])==[]


def test_surface_preserves_outside_pixels_and_occluding_hand():
    from novel_manga.interactive.evidence import paste_surface
    frame=Image.new('RGB',(200,200),(80,90,100))
    layer=Image.new('RGBA',(100,50),(230,200,130,255))
    region={'quad':[[100,100],[900,200],[850,900],[150,800]],
            'occlusions':[[[400,400],[600,400],[600,600],[400,600]]]}
    result=paste_surface(frame,layer,region)
    assert result.getpixel((0,0))==frame.getpixel((0,0))
    assert result.getpixel((50,90))!=frame.getpixel((50,90))
    assert result.getpixel((100,100))==frame.getpixel((100,100))


def test_geometry_never_interpolates_across_a_camera_cut():
    from novel_manga.interactive.evidence import regions_at
    old={'cue_id':'sign','quad':[[10,10],[100,10],[100,100],[10,100]],'occlusions':[]}
    new={'cue_id':'phone','quad':[[700,700],[900,700],[900,900],[700,900]],'occlusions':[]}
    observed={'duration':3,'cuts':[1.5],'frames':[{'time':.1,'regions':[old]},{'time':1.4,'regions':[old]}, {'time':1.6,'regions':[new]},{'time':2.8,'regions':[new]}]}
    assert [r['cue_id'] for r in regions_at(observed,1.4)]==['sign']
    assert [r['cue_id'] for r in regions_at(observed,1.6)]==['phone']


def test_missing_surface_is_a_delivery_failure():
    from novel_manga.interactive.evidence import evidence_complete
    cues=[{'id':'sign'},{'id':'phone'}]
    assert not evidence_complete(cues,{'sign':1.5})
    assert evidence_complete(cues,{'sign':1.5,'phone':2.5})


def test_choice_card_renders_full_reason(tmp_path,monkeypatch):
    from novel_manga.interactive import delivery
    from novel_manga.config import DEFAULT_FONT_PATH
    font=DEFAULT_FONT_PATH
    rendered=[]
    original=delivery.ImageDraw.ImageDraw.text
    def record(self,xy,text,*args,**kwargs):
        rendered.append(text);return original(self,xy,text,*args,**kwargs)
    monkeypatch.setattr(delivery.ImageDraw.ImageDraw,'text',record)
    interaction={'question':'明天先查哪边？','options':[
        {'id':'A','label':'沿脚印找新路','reason':'查现场脚印去向与可通性'},
        {'id':'B','label':'在地面查站房','reason':'查旧入口的位置资料与记录'}]}
    delivery._card(tmp_path/'card.jpeg',interaction, font,1080,1920)
    assert all(option['reason'] in rendered for option in interaction['options'])


def test_composite_preserves_frame_count_and_audio(tmp_path):
    import json,subprocess
    from novel_manga.config import DEFAULT_FONT_PATH
    from novel_manga.interactive.evidence import composite_evidence
    source=tmp_path/'source.mp4';output=tmp_path/'out.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=gray:s=64x96:r=25',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1',
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
    region={'cue_id':'sign','quad':[[100,100],[900,100],[900,900],[100,900]],'occlusions':[]}
    observed={'duration':1,'cuts':[],'frames':[{'time':.1,'regions':[region]},{'time':.9,'regions':[region]}]}
    result,coverage=composite_evidence(source,output,[{'start':0,'end':1,'observed':observed}],
                        [{'id':'sign','kind':'sign','text':'青溪村'}],DEFAULT_FONT_PATH)
    assert result==output and .75<=coverage['sign']<=.85
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
    assert next(s for s in info['streams'] if s['codec_type']=='video')['nb_frames']=='25'
    assert next(s for s in info['streams'] if s['codec_type']=='audio')['codec_name']=='aac'
    def samples(path):return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vn','-f','s16le','pipe:1'])
    assert samples(source)==samples(output)


def test_unfolding_and_closing_are_not_extrapolated():
    from novel_manga.interactive.evidence import regions_at,observed_shot_spans
    region={'cue_id':'map','quad':[[10,10],[900,10],[900,900],[10,900]],'occlusions':[],'anchors':[]}
    observed={'duration':5,'cuts':[],'frames':[
        {'time':1,'shot_index':None,'regions':[]},
        {'time':2,'shot_index':8,'regions':[region]},
        {'time':3,'shot_index':8,'regions':[region]},
        {'time':4,'shot_index':None,'regions':[]}]}
    assert regions_at(observed,1.9)==[]
    assert regions_at(observed,2.5)
    assert regions_at(observed,3.1)==[]
    assert observed_shot_spans(observed,10)==[{'index':8,'start':12,'end':13}]


def test_moving_hand_is_conservatively_masked_and_untrackable_jump_is_skipped():
    from novel_manga.interactive.evidence import regions_at
    def region(x):return {'cue_id':'nodes','quad':[[0,0],[1000,0],[1000,1000],[0,1000]],'anchors':[],
        'occlusions':[[[x,400],[x+250,400],[x+250,650],[x,650]]]}
    observed={'duration':2,'cuts':[],'frames':[
        {'time':0,'regions':[region(100)]},{'time':1,'regions':[region(250)]}]}
    middle=regions_at(observed,.5)[0]
    assert max(p[0] for p in middle['occlusions'][0])>=500
    observed['frames'][1]['regions']=[region(700)]
    assert regions_at(observed,.5)==[]


def test_station_nodes_use_observed_pointing_anchors():
    from novel_manga.interactive.evidence import surface_image
    from novel_manga.config import DEFAULT_FONT_PATH
    import pytest
    cue={'id':'nodes','kind':'nodes','text':'谷底站\n山腰入口'}
    region={'quad':[[0,0],[1000,0],[1000,1000],[0,1000]],
            'anchors':[{'label':'谷底站','point':[150,250]}, {'label':'山腰入口','point':[800,700]}]}
    image=surface_image(cue,DEFAULT_FONT_PATH,region)
    assert image.getpixel((120,125))==(31,37,29,255)
    assert image.getpixel((640,350))==(31,37,29,255)
    with pytest.raises(ValueError):surface_image(cue,DEFAULT_FONT_PATH,{**region,'anchors':[]})
