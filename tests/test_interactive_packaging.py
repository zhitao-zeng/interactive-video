from novel_manga.interactive.packaging import shift_subtitles, world_card_boundary
import array
import math
import shutil
import subprocess
import pytest


def test_world_card_follows_actual_first_clip_boundary():
    publication={'world_card':{'after_clip_id':'clip_01','seconds':3}}
    assert world_card_boundary(publication,[{'unit_id':'clip_01','end':4.041}])==(4.04,3)


def test_subtitles_move_after_card_and_do_not_show_over_card(tmp_path):
    source=tmp_path/'source.ass';target=tmp_path/'final.ass'
    source.write_text('[Events]\n'
        'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,before\n'
        'Dialogue: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,cross\n'
        'Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,after\n')
    shift_subtitles(source,target,4,3)
    result=target.read_text()
    assert '0:00:01.00,0:00:03.00' in result
    assert '0:00:03.00,0:00:04.00' in result
    assert '0:00:07.00,0:00:08.00' in result
    assert '0:00:07.00,0:00:09.00' in result


@pytest.mark.parametrize('has_speech', [False, True])
def test_world_card_does_not_replay_spoken_tail_but_keeps_wordless_ambience(tmp_path, has_speech):
    from novel_manga.config import Settings
    from novel_manga.interactive.packaging import prepare_publication,publication_graph
    font = Settings.from_env().font_path
    if not shutil.which('ffmpeg') or not font.is_file():
        pytest.skip('ffmpeg and a local font are required for the audio splice check')
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=gray:s=160x284:r=25:d=2',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                    '-c:v','libx264','-c:a','aac',str(source)],check=True)
    publication={'world_card':{'after_clip_id':'clip_01','seconds':3,'series_title':'旅行',
                               'episode':'01','title':'世界','premise':'规则'}}
    prepared=prepare_publication(publication,[{'unit_id':'clip_01','end':1}],tmp_path,font,160,284,
                                 boundary_has_speech=has_speech)
    output=tmp_path/'published.mp4'
    graph=('[0:v]setsar=1[body_video];[0:a]aresample=48000,aformat=channel_layouts=stereo[body_audio];'
           +publication_graph(prepared,1,2))
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(source),'-loop','1','-framerate','25',
                    '-i',str(tmp_path/'world-introduction.jpeg'),'-loop','1','-framerate','25',
                    '-i',str(tmp_path/'series-watermark.png'),'-filter_complex',graph,
                    '-map','[published_video]','-map','[published_audio]',
                    '-c:v','libx264','-c:a','aac',str(output)],check=True)
    # Sample inside the inserted card. AAC boundary bleed is excluded.
    pcm=subprocess.check_output(['ffmpeg','-v','error','-ss','1.1','-t','0.1','-i',str(output),
                                 '-vn','-ac','1','-ar','16000','-f','s16le','-'])
    samples=array.array('h',pcm)
    rms=math.sqrt(sum(x*x for x in samples)/len(samples))
    assert rms < 1 if has_speech else rms > 100


def test_approved_labels_split_around_inserted_world_card():
    from novel_manga.interactive.packaging import shift_events
    events=[{'text':'before','start':1,'end':3},{'text':'cross','start':3,'end':5},
            {'text':'after','start':4,'end':6}]
    result=shift_events(events,4,3)
    assert result==[{'text':'before','start':1,'end':3},{'text':'cross','start':3,'end':4},
                    {'text':'cross','start':7,'end':8},{'text':'after','start':7,'end':9}]
    assert events[1]['end']==5
