"""Final speech policy follows the selected take's saved provider, preserving every grammar's guard."""
from types import SimpleNamespace
import copy
import pytest
from novel_manga.application.agents.episode.requests import compose_request
from novel_manga.story.voice_delivery import episode_native_speech_errors,NATIVE_SPEECH_POLICY
from novel_manga.util import atomic_write_json
from support.native_speech import case

def saved(tmp_path,model,*,h3=False,profile=None):
    book=tmp_path/'book';ep=book/'book_1';ep.mkdir(parents=True)
    clip=case();clip['kind']='video';good_h3=compose_request(clip,{'shots':['She pauses. <DIALOGUE_1>']});clip['prompt_h3']=good_h3
    atomic_write_json(book/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY,**(profile or{})})
    atomic_write_json(ep/'clip_plan.json',{'clips':[clip]})
    video=ep/'work/clips/clip_01/attempt_01/clip.mp4';video.parent.mkdir(parents=True);video.write_bytes(b'preserved native take')
    cue=clip['dialogue_bindings'][0]['emotion'];request={'prompt':good_h3 if h3 else f'A：先等等。（{cue}）','reference_audios':[]}
    atomic_write_json(video.parent/'request.json',request)
    atomic_write_json(video.with_suffix('.mp4.task.json'),{'model':model,'kind':'video','task_id':'saved-task'})
    media={'clips':[{'clip_id':'clip_01','selected':{'video':str(video)}}]}
    return ep,clip,video,request,media

@pytest.mark.parametrize('model',['sd2.0-fast','sd2.0-mini','sd2.5','sd2.0'])
def test_actual_sd_provider_ignores_unused_h3_metadata(tmp_path,model):
    ep,clip,video,request,media=saved(tmp_path,model,profile={'h3_input_mode':'first-frame'})
    frozen={p:p.read_bytes()for p in[ep/'clip_plan.json',video,video.parent/'request.json',video.with_suffix('.mp4.task.json')]}
    assert episode_native_speech_errors(ep,media)=={}
    assert all(p.read_bytes()==data for p,data in frozen.items())

@pytest.mark.parametrize('defect',['missing_performance','changed_words','audio_reference'])
def test_sd_actual_words_performance_and_no_audio_guard_still_blocks(tmp_path,defect):
    ep,clip,video,request,media=saved(tmp_path,'sd2.0-fast')
    if defect=='missing_performance':request['prompt']='A：先等等。'
    elif defect=='changed_words':request['prompt']=request['prompt'].replace('先等等。','我走了。')
    else:request['reference_audios']=['old.wav']
    atomic_write_json(video.parent/'request.json',request)
    assert episode_native_speech_errors(ep,media)['clip_01']

@pytest.mark.parametrize('model',['minimax-h3-ref2va-turbo','MiniMax-H3'])
def test_actual_h3_provider_keeps_strict_h3_rules_even_after_sd_runtime(tmp_path,model):
    ep,clip,video,request,media=saved(tmp_path,model,h3=True,profile={'sd_input_mode':'first-frame'})
    assert episode_native_speech_errors(ep,media)=={}
    cue=clip['dialogue_bindings'][0]['emotion'];request['prompt']=request['prompt'].replace(cue,'')+'\n'+cue
    atomic_write_json(video.parent/'request.json',request)
    assert any('before'in e for e in episode_native_speech_errors(ep,media)['clip_01'])

def test_unknown_provider_keeps_previous_strict_fallback(tmp_path):
    ep,clip,video,request,media=saved(tmp_path,'unknown-provider')
    assert any('before'in e for e in episode_native_speech_errors(ep,media)['clip_01'])

def test_fix_does_not_bypass_normal_review_readiness(tmp_path):
    from novel_manga.application.agents.episode.completion import EpisodeCompletion
    ep,clip,video,request,media=saved(tmp_path,'sd2.0-fast')
    atomic_write_json(ep/'thin_media_report.json',media)
    evidence=SimpleNamespace(unreviewed_clips=lambda *a:['clip_01'])
    result=EpisodeCompletion(SimpleNamespace(episode_dir=ep,run_dir=tmp_path/'run',work_dir=tmp_path/'work'),None,evidence).finish({})
    assert result['ok']is False and result['error']=='missing_review'
