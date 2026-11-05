import json
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from novel_manga.application.agents.episode.postproduction import assembly_inputs, authorize_cutaway
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


def _video(path, color, frequency, duration=4, fps=25):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        f'color=c={color}:s=160x284:r={fps}:d={duration}', '-f', 'lavfi', '-i',
        f'sine=frequency={frequency}:sample_rate=48000:duration={duration}', '-shortest', '-t', str(duration),
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k',
        '-movflags', '+faststart', str(path)], check=True, capture_output=True)
    return path


def _audio(path):
    return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
        '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', 'pipe:1'])


def _frame_rgb(path, second):
    raw = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(second),
        '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    middle = (284//2*160 + 80)*3
    return tuple(raw[middle:middle+3])


def _frame_rgb_index(path, index):
    raw = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
        '-vf', f'select=eq(n\\,{index})', '-frames:v', '1', '-f', 'rawvideo',
        '-pix_fmt', 'rgb24', 'pipe:1'])
    middle = (284//2*160 + 80)*3
    return tuple(raw[middle:middle+3])


def test_24fps_target_is_ceil_normalized_before_exact_cutaway_and_resume(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode import postproduction

    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    source = _video(ctx.episode_dir/'work/clips/clip_34/source.mp4', 'blue', 660,
                    duration=277/24, fps=24)
    target = _video(ctx.episode_dir/'work/clips/clip_42/target.mp4', 'red', 440,
                    duration=243/24, fps=24)
    source_probe,target_probe=postproduction._probe(source),postproduction._probe(target)
    assert source_probe['video']['nb_frames']=='277' and postproduction._body_frames(source_probe)==289
    assert target_probe['video']['nb_frames']=='243' and postproduction._body_frames(target_probe)==254
    clips=plan['clips'][:2]
    clips[0]['clip_id']='clip_34';clips[1]['clip_id']='clip_42';plan['clips']=clips
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    selected=[{'clip_id':'clip_34','selected':{'video':str(source),'passed':True,'chunks':[]}},
              {'clip_id':'clip_42','selected':{'video':str(target),'passed':True,'chunks':[]}}]
    report={'status':'clips_ready','clips':selected,'failed_clips':[],'gate_failed_clips':[]}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *_a,**_k:True)
    monkeypatch.setattr('novel_manga.application.repair.ambience.apply_assembly',lambda _d,r,**_k:r)
    decision={'clip_id':'clip_42','action':'cutaway','source_clip_id':'clip_34',
        'source_start_seconds':3.6,'source_end_seconds':4.8,'target_clip_id':'clip_42',
        'target_start_seconds':1.12,'target_end_seconds':2.32,'reason':'restore the approved insert'}
    authorize_cutaway(ctx.episode_dir,decision)

    attempt1=ctx.episode_dir/'work/postproduction/binding-001/attempts/attempt-001'
    attempt1.mkdir(parents=True)
    bad_partial=attempt1/'target-picture-cutaway.partial.mp4'
    old_graph=(
        '[0:v]setpts=PTS-STARTPTS,fps=25,split=2[before_in][after_in];'
        '[before_in]trim=start_frame=0:end_frame=28,setpts=PTS-STARTPTS,scale=160:284:force_original_aspect_ratio=decrease,'
        'pad=160:284:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1,format=yuv420p[before];'
        '[1:v]setpts=PTS-STARTPTS,fps=25,trim=start_frame=90:end_frame=120,setpts=PTS-STARTPTS,'
        'scale=160:284:force_original_aspect_ratio=decrease,pad=160:284:(ow-iw)/2:(oh-ih)/2,'
        'fps=25,setsar=1,format=yuv420p[insert];'
        '[after_in]trim=start_frame=58:end_frame=254,setpts=PTS-STARTPTS,scale=160:284:force_original_aspect_ratio=decrease,'
        'pad=160:284:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1,format=yuv420p[after];'
        '[before][insert][after]concat=n=3:v=1:a=0[v]')
    subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(target),'-i',str(source),
        '-filter_complex',old_graph,'-map','[v]','-map','0:a:0','-c:v','libx264','-preset','ultrafast',
        '-pix_fmt','yuv420p','-c:a','copy',str(bad_partial)],check=True,capture_output=True)
    assert postproduction._probe(bad_partial)['video']['nb_frames']=='253'
    bad_bytes=bad_partial.read_bytes()
    rows,inserts=assembly_inputs(SimpleNamespace(episode_dir=ctx.episode_dir,clip_plan=plan),report)
    derived=Path(rows[1]['selected']['video'])
    probe=postproduction._probe(derived)
    assert postproduction._video_frames(probe)==254
    assert probe['video']['r_frame_rate']=='25/1'
    assert probe['video']['nb_frames']=='254'
    assert _audio(target)==_audio(derived)
    assert _frame_rgb_index(derived,27)[0]>_frame_rgb_index(derived,27)[2]
    assert _frame_rgb_index(derived,28)[2]>_frame_rgb_index(derived,28)[0]
    assert _frame_rgb_index(derived,57)[2]>_frame_rgb_index(derived,57)[0]
    assert _frame_rgb_index(derived,58)[0]>_frame_rgb_index(derived,58)[2]
    assert inserts[0]['frame_count']==30
    assert bad_partial.read_bytes()==bad_bytes
    assert (attempt1/'partial-verification-error.json').is_file()

    monkeypatch.setattr(postproduction,'_derive',lambda *_a: (_ for _ in ()).throw(AssertionError('derived again')))
    resumed,_=assembly_inputs(SimpleNamespace(episode_dir=ctx.episode_dir,clip_plan=plan),report)
    assert Path(resumed[1]['selected']['video'])==derived
    assert bad_partial.read_bytes()==bad_bytes


def test_cutaway_is_a_picture_only_derived_clip_consumed_by_normal_assembly(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch)
    source = _video(ctx.episode_dir/'work/clips/clip_01/source.mp4', 'blue', 660, duration=6, fps=24)
    target = _video(ctx.episode_dir/'work/clips/clip_02/target.mp4', 'red', 440, fps=24)
    clips = plan['clips'][:2]
    clips[0]['clip_id'] = 'clip_34'
    clips[1]['clip_id'] = 'clip_42'
    plan['clips'] = clips
    atomic_write_json(ctx.episode_dir/'clip_plan.json', plan)
    selected = [
        {'clip_id':'clip_34','selected':{'video':str(source),'passed':True,'chunks':[]}},
        {'clip_id':'clip_42','selected':{'video':str(target),'passed':True,'chunks':[]}},
    ]
    report = {'status':'clips_ready','clips':selected,'failed_clips':[],'gate_failed_clips':[]}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json', report)
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches', lambda *_a,**_k: True)
    monkeypatch.setattr('novel_manga.application.repair.ambience.apply_assembly', lambda _d,r,**_k:r)

    decision = {'clip_id':'clip_42','action':'cutaway','source_clip_id':'clip_34',
        'source_start_seconds':3.6,'source_end_seconds':4.8,'target_clip_id':'clip_42',
        'target_start_seconds':1.12,'target_end_seconds':2.32,'reason':'Restore the supported B visual at the authored target.'}
    authorize_cutaway(ctx.episode_dir, decision)

    captured = {}
    assembly_context = SimpleNamespace(episode_dir=ctx.episode_dir, clip_plan=plan)
    captured['rows'], captured['inserts'] = assembly_inputs(assembly_context, {'clips':selected})
    derived = Path(captured['rows'][1]['selected']['video'])
    assert derived.is_file() and derived != target
    assert captured['rows'][1]['selected']['native_audio_path'] == str(target.parent/'native.wav')
    info = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=r_frame_rate','-of','json',str(derived)],text=True))
    assert info['streams'][0]['r_frame_rate'] == '25/1'
    assert _audio(target) == _audio(derived)
    assert _frame_rgb(derived, 1.5)[2] > _frame_rgb(derived, 1.5)[0]
    assert _frame_rgb(derived, 0.5)[0] > _frame_rgb(derived, 0.5)[2]
    assert _frame_rgb(derived, 2.8)[0] > _frame_rgb(derived, 2.8)[2]
    # The persisted native take and all video generation counts stay unchanged.
    assert read_json(ctx.episode_dir/'thin_media_report.json')['clips'][1]['selected']['video'] == str(target)
    assert captured['inserts'][0]['source']['clip_id'] == 'clip_34'
    assert captured['inserts'][0]['target']['clip_id'] == 'clip_42'
    attempt = derived.parent
    (attempt/'receipt.json').unlink()
    partial = attempt/'target-picture-cutaway.partial.mp4'
    derived.rename(partial)
    recovered_rows, recovered_trace = assembly_inputs(assembly_context, {'clips':selected})
    assert Path(recovered_rows[1]['selected']['video']).is_file()
    assert read_json(attempt/'receipt.json')['recovered_partial_output'] is True
    assert (attempt/'target-picture-cutaway.mp4').is_file()
    assert not partial.exists()
