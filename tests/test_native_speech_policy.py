"""The chosen native-speech contract reaches words, audio inputs, packing and cache gates."""
import copy
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from novel_manga.application.agents.episode.requests import compose_request
from novel_manga.application.profiles import h3_stamp
from novel_manga.story.voice_delivery import native_speech_errors, require_native_speech, NATIVE_SPEECH_POLICY
from novel_manga.util import atomic_write_json


def case(offscreen=False):
    return {'clip_id':'clip_01','request_seconds':5,'shot_indexes':[1],
        'shot_timing':[{'seconds':5}], 'h3_speech_policy':NATIVE_SPEECH_POLICY,
        'references':[{'role':'character','name':'A','path':'actor.png'}],
        'dialogue_bindings':[{'stage':1,'speaker_name':'A','delivery_mode':'offscreen_dialogue'if offscreen else'visible_dialogue',
            'text':'先等等。','emotion':'Her voice drops briefly as she notices the step, then ends firmly.'}]}


@pytest.mark.parametrize('offscreen',[False,True])
def test_bound_performance_is_before_exact_words_and_no_audio_tags(offscreen):
    clip=case(offscreen);before=copy.deepcopy(clip)
    prompt=compose_request(clip,{'shots':['She stops. <DIALOGUE_1> She watches.'],'soundscape':'Soft water.'})
    cue=clip['dialogue_bindings'][0]['emotion']
    assert cue in prompt and prompt.index(cue)<prompt.index('<d>')<prompt.index('She watches.')
    assert prompt.count(cue)==1 and '<Audio 'not in prompt and 'audio reference'not in prompt
    assert not native_speech_errors(clip,prompt=prompt,audios=[])
    assert clip==before


@pytest.mark.parametrize('cue',['','低声，克制','None.'])
def test_missing_untranslated_performance_blocks_compilation(cue):
    clip=case();clip['dialogue_bindings'][0]['emotion']=cue
    with pytest.raises(ValueError,match='performance'):
        compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']})


def test_retained_voice_file_cannot_override_selected_no_reference_policy():
    clip=case();clip['references'].append({'role':'voice','name':'A','path':'old.wav'})
    with pytest.raises(ValueError,match='audio reference'):
        compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']})


def test_cue_after_dialogue_or_only_in_soundscape_does_not_pass():
    clip=case();prompt=compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']})
    cue=clip['dialogue_bindings'][0]['emotion'];wrong=prompt.replace(cue,'')+'\n'+cue
    assert any('before' in x for x in native_speech_errors(clip,prompt=wrong,audios=[]))


def test_speech_policy_changes_existing_request_identity():
    clip=case();legacy=copy.deepcopy(clip);legacy.pop('h3_speech_policy')
    assert h3_stamp(clip)!=h3_stamp(legacy)


def test_dropped_policy_blocks_before_any_cache_or_provider(tmp_path):
    from novel_manga.application.rendering.submission import generate_clip
    atomic_write_json(tmp_path/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    clip=case();clip.pop('h3_speech_policy')
    with pytest.raises(ValueError,match='speech policy'):
        generate_clip(NS(novel_dir=tmp_path,settings=NS(local_h3_base_url='http://h3.test'),options=NS(cache_only=True)),clip,1)


def test_actual_audio_list_blocks_even_if_prompt_is_clean_before_cache(tmp_path,monkeypatch):
    from novel_manga.application.rendering import submission
    clip=case();prompt=compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']});clip['prompt_h3']=prompt
    atomic_write_json(tmp_path/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    ctx=NS(novel_dir=tmp_path,settings=NS(local_h3_base_url='http://h3.test'),options=NS(cache_only=True),work=tmp_path/'book_1/work',state=NS(clip=lambda cid:NS()))
    monkeypatch.setattr(submission.generation,'build_request',lambda *a:({'prompt':prompt,'reference_audios':['retained.wav']},[],{},''))
    monkeypatch.setattr(submission.cache,'current_video',lambda *a:pytest.fail('must stop before accepting a cached conditioned take'))
    with pytest.raises(ValueError,match='audio reference'):
        submission.generate_clip(ctx,clip,1)


def test_packing_ignores_existing_voice_bank_only_for_selected_native_policy(tmp_path,monkeypatch):
    from support.split_episode import split_episode,explicitly_planned_shots
    from novel_manga.application.packing.context import context_for_plan
    from novel_manga.application.packing.service import compile_plan
    directory,script,saved=split_episode.__wrapped__(tmp_path,monkeypatch);script=explicitly_planned_shots(script)
    atomic_write_json(directory/'segments.json',[{'segment_id':'seg_1','text':'林凡说话。'}])
    ctx=context_for_plan(directory,directory.parent/'story_bible.json',saved)
    ctx['compiler_options']=replace(ctx['compiler_options'],voices={'林凡':'series_assets/voices/old.wav'})
    ordinary,_=compile_plan(script,ctx)
    assert any(r['role']=='voice'for c in ordinary['clips']for r in c.get('references',[]))
    ctx['profile']['h3_speech_policy']=NATIVE_SPEECH_POLICY
    native,_=compile_plan(script,ctx)
    assert all(c.get('h3_speech_policy')==NATIVE_SPEECH_POLICY for c in native['clips']if c['kind']=='video')
    assert not any(r['role']=='voice'for c in native['clips']for r in c.get('references',[]))
    assert ctx['compiler_options'].voices=={'林凡':'series_assets/voices/old.wav'}


def test_voice_builder_does_not_call_runner_when_native_policy_selected(tmp_path,monkeypatch):
    from novel_manga.interactive.authored import build_authored_voices
    atomic_write_json(tmp_path/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    monkeypatch.setattr('novel_manga.interactive.runtime.runner_for',lambda *a,**kw:pytest.fail('no paid voice sample under no-reference policy'))
    build_authored_voices(tmp_path,tmp_path)


def test_postmix_recording_keeps_its_own_voice_contract(tmp_path):
    from novel_manga.media.inner_voice import voice_clip
    from novel_manga.story.h3 import compose
    from novel_manga.story.voice_delivery import visual_request
    clip=case();clip.update(audio_delivery='postmix',first_frame={'path':'opening.png'},h3_input_mode='first-frame')
    clip['inner_voice']={'speaker':'A','text':'先等等。',
        'character_references':clip['references'],
        'voice_references':[{'role':'voice','name':'A','path':'thought.wav'}]}
    clip['dialogue_bindings'][0]['inner_monologue']=True
    recording=voice_clip(clip)
    assert not recording.get('h3_speech_policy') and not recording.get('first_frame')
    prompt=compose(recording,['<Subject 1> speaks quietly.'],[('',[])])
    assert '<Audio 1>'in prompt and '先等等'in prompt
    assert native_speech_errors(clip,prompt=compose(clip,['She watches silently.'],[('',[])]),audios=[])==[]
    assert visual_request(clip)['spoken_text']==''
    assert clip['h3_speech_policy']==NATIVE_SPEECH_POLICY and clip['first_frame']


def test_native_policy_builds_only_explicit_postmix_speakers(tmp_path,monkeypatch):
    from novel_manga.interactive import authored,runtime
    from novel_manga.util import read_json
    book=tmp_path/'book';episode=book/'book_1';episode.mkdir(parents=True)
    atomic_write_json(book/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY,'inner_voice_delivery':'postmix'})
    atomic_write_json(episode/'chapter_script.json',{'shots':[{'turns':[
        {'speaker_name':'A','text':'我想想。','delivery_mode':'offscreen_dialogue','inner_monologue':True},
        {'speaker_name':'B','text':'你好。','delivery_mode':'visible_dialogue'}]}]})
    atomic_write_json(book/'voice_specs.json',{name:{'prompt':'A natural voice.','sample_text':'你好。','picture':'actor.png'}for name in ('A','B')})
    calls=[]
    class Provider:
        before_video_submit=None
        def create_video(self,prompt,image,output,duration):
            self.before_video_submit();calls.append(output.name);output.write_bytes(b'fake video')
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**kw:NS(context=NS(provider=Provider())))
    monkeypatch.setattr(authored,'run',lambda command:Path(command[-1]).write_bytes(b'fake wav'))
    monkeypatch.setattr(authored,'pack_authored',lambda *a:{})
    authored.build_authored_voices(tmp_path,book)
    assert calls==['A-voice-source.mp4']
    assert set(read_json(book/'series_assets/voices/voices.json',{}))=={'A'}


def test_native_postmix_budget_counts_only_needed_speakers(tmp_path):
    from novel_manga.interactive.authored import prepare_authored
    from novel_manga.util import read_json
    source=Path(__file__).resolve().parents[1]/'projects/adventure/laughing-country'
    material=read_json(source/'material.json',{})
    material['profile'].update(h3_speech_policy=NATIVE_SPEECH_POLICY,inner_voice_delivery='postmix')
    material['reference_images']=[];material.pop('identity_image',None);material['reference_audios']=[]
    for shot in material['script']['shots']:
        for turn in shot.get('turns',[]):turn['inner_monologue']=False
    turn=next(t for s in material['script']['shots']for t in s.get('turns',[])if t.get('text'))
    turn.update(inner_monologue=True,delivery_mode='offscreen_dialogue')
    project=tmp_path/'material';project.mkdir()
    atomic_write_json(project/'material.json',material)
    (project/'source.md').write_bytes((source/'source.md').read_bytes())
    episode=prepare_authored(project,tmp_path/'edition')
    assert read_json(episode.parent/'production-budget.json',{})['voice_sources']==1


def test_first_frame_instructions_preserve_speaker_ids_but_not_audio_ids(tmp_path):
    from novel_manga.application.agents.episode.prompts import task_instructions
    atomic_write_json(tmp_path/'profile.json',{'h3_input_mode':'first-frame','h3_speech_policy':NATIVE_SPEECH_POLICY})
    text=task_instructions(NS(novel_dir=tmp_path,source_novel_dir=tmp_path))
    assert 'Preserve the ORIGINAL logical Subject IDs and Audio IDs'not in text
    assert 'no audio reference'in text.lower() and 'emotion'in text and 'before'in text


def test_new_first_frame_entry_cannot_infer_audio_policy_from_a_voice_file(tmp_path,monkeypatch):
    from novel_manga.interactive.first_frame_entry import select_mode
    book=tmp_path/'book';episode=book/'book_1';episode.mkdir(parents=True)
    atomic_write_json(book/'profile.json',{})
    atomic_write_json(episode/'authored_groups.json',[{'source_segment':'s','shot_indexes':[1]}])
    atomic_write_json(episode/'chapter_script.json',{'shots':[{'index':1,'duration_seconds':5}]})
    monkeypatch.setattr('novel_manga.interactive.authored.pack_authored',lambda *a:pytest.fail('select policy before repacking'))
    with pytest.raises(ValueError,match='speech policy'):
        select_mode(book,'first-frame')
    assert not (episode/'first-frame-mode.json').exists()


def test_duplicate_bound_performance_is_rejected():
    clip=case();cue=clip['dialogue_bindings'][0]['emotion']
    prompt=compose_request(clip,{'shots':[cue+' <DIALOGUE_1>']})
    assert any('duplicat'in error for error in native_speech_errors(clip,prompt=prompt))


def test_actual_native_request_and_provider_have_no_audio_condition(tmp_path):
    from novel_manga.media import generation
    from novel_manga.providers.local_h3 import LocalH3MediaProvider
    clip=case();Image.new('RGB',(8,12)).save(tmp_path/'actor.png');(tmp_path/'old.wav').write_bytes(b'retained')
    prompt=compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']});clip.update(prompt_h3=prompt,prompt_h3_of=h3_stamp(clip))
    ctx=NS(novel_dir=tmp_path,settings=NS(local_h3_base_url='http://h3.test'),feedback={},options=NS(voice_budget=None),state=NS(clip=lambda cid:NS(compliance=False,softened=False)),provider=None)
    request,refs,_,_=generation.build_request(ctx,clip,1)
    assert request['reference_audios']==[] and request['h3_speech_policy']==NATIVE_SPEECH_POLICY
    p=object.__new__(LocalH3MediaProvider);p.settings=NS(video_model='h3');p.ratio='9:16';p.short_edge=768
    payload=p._payload(request['prompt'],refs,tuple(map(Path,request['reference_audios'])),5)
    assert not any(row['type']=='audio'for row in payload['conditions'])
    assert (tmp_path/'old.wav').exists()


@pytest.mark.parametrize('audio_input',[[],['old.wav']])
def test_final_receipt_checks_selected_native_request_even_after_visual_pass(tmp_path,monkeypatch,audio_input):
    from novel_manga.interactive import delivery
    from novel_manga.application.review import final_cut
    book=tmp_path/'book';episode=book/'book_1';episode.mkdir(parents=True)
    clip=case();clip['kind']='video';clip['prompt_h3']=compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']})
    atomic_write_json(book/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    raw=episode/'work/clips/clip_01/attempt_01';raw.mkdir(parents=True);movie=raw/'clip.mp4';movie.write_bytes(b'video')
    atomic_write_json(raw/'request.json',{'prompt':clip['prompt_h3'],'reference_audios':audio_input})
    report={'clips':[{'clip_id':'clip_01','selected':{'video':str(movie)}}],
        'assembly':{'final_video':str(movie),'thin_passed':True},'quality_review':{'passed':True,'content_passed':True},
        'final_cut_review':{'complete':True,'passed':True,'issues':[],'errors':[],'movie':str(movie)}}
    destination=tmp_path/'delivery';destination.mkdir()
    atomic_write_json(destination/'delivery.json',{'final_video':str(movie),'export_context':{'same':True},'technical_passed':True})
    monkeypatch.setattr(delivery,'export_context',lambda *a:{'same':True});monkeypatch.setattr(final_cut,'current',lambda *a:True)
    got=delivery.finish_reviewed_delivery(NS(context=NS(episode_dir=episode)),report,destination)
    assert got['status']==('candidate'if audio_input else'accepted')
    assert got['content_passed']==(not audio_input)
    assert bool(got.get('speech_policy_errors'))==bool(audio_input)


def test_agent_finish_cannot_skip_audio_policy_by_reusing_a_passing_movie(tmp_path):
    from novel_manga.application.agents.episode.completion import EpisodeCompletion
    book=tmp_path/'book';episode=book/'book_1';episode.mkdir(parents=True)
    clip=case();clip['kind']='video';clip['prompt_h3']=compose_request(clip,{'shots':['She stops. <DIALOGUE_1>']})
    atomic_write_json(book/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY});atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    raw=episode/'work/clips/clip_01/attempt_01';raw.mkdir(parents=True)
    atomic_write_json(raw/'request.json',{'prompt':clip['prompt_h3'],'reference_audios':['old.wav']})
    atomic_write_json(episode/'thin_media_report.json',{'clips':[{'clip_id':'clip_01','selected':{'video':str(raw/'clip.mp4')}}],'status':'done','quality_review':{'passed':True}})
    ctx=NS(episode_dir=episode,run_dir=tmp_path/'run',work_dir=tmp_path/'work')
    got=EpisodeCompletion(ctx,None,None).finish({})
    assert got['ok']is False and got['error']=='native_speech_policy'and 'clip_01'in got['clips']


def test_two_turn_cues_can_share_a_sentence_without_false_duplicate():
    clip=case();clip['dialogue_bindings'][0]['emotion']='Her voice stays level.'
    clip['dialogue_bindings'].append({**clip['dialogue_bindings'][0],'text':'我再看看。',
        'emotion':'Her voice stays level. The final word softens.'})
    prompt=compose_request(clip,{'shots':['<DIALOGUE_1> She looks down. <DIALOGUE_2>']})
    assert native_speech_errors(clip,prompt=prompt)==[]


def test_pending_native_media_still_uses_normal_finish_readiness(tmp_path):
    from novel_manga.story.voice_delivery import episode_native_speech_errors
    book=tmp_path/'book';episode=book/'book_1';episode.mkdir(parents=True)
    clip=case();clip['kind']='video'
    atomic_write_json(book/'profile.json',{'h3_speech_policy':NATIVE_SPEECH_POLICY})
    atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    assert episode_native_speech_errors(episode,{'clips':[]})=={}
    errors=episode_native_speech_errors(episode,{'clips':[],'assembly':{'final_video':'old.mp4'}})
    assert 'clip_01'in errors and 'preserved request'in errors['clip_01'][0]



def test_task_instruction_block_has_one_policy_and_one_first_frame_contract(tmp_path):
    from novel_manga.application.agents.episode import prompts
    atomic_write_json(tmp_path/'profile.json', {
        'h3_input_mode':'first-frame', 'h3_speech_policy':NATIVE_SPEECH_POLICY})
    ctx = NS(novel_dir=tmp_path, source_novel_dir=tmp_path)
    text = prompts.task_instructions(ctx)
    assert text.count('TASK SPEECH POLICY:') == 1
    assert text.count('This edition uses OPTIONAL FIRST-FRAME MODE') == 1
    assert 'no Audio IDs are supplied' in text
