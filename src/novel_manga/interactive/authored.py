"""Import an adopted travel screenplay using the existing production contracts."""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import shutil

from PIL import Image
from novel_manga.util import atomic_write_json, read_json, run


def _required_voice_specs(profile,script,specs):
    from novel_manga.story.voice_delivery import native_speech_selected
    if not native_speech_selected(profile):return specs
    speakers={t['speaker_name']for s in script.get('shots',[])for t in s.get('turns',[])
              if profile.get('inner_voice_delivery')=='postmix'and t.get('inner_monologue')and t.get('text')
              and t.get('delivery_mode')in {'visible_dialogue','offscreen_dialogue'}}
    return {name:spec for name,spec in specs.items()if name in speakers}


def prepare_authored(project: Path, output: Path, *, family: str = 'adventure') -> Path:
    if output.exists():
        raise FileExistsError('Keep the existing episode, assets and all generation history')
    material=read_json(project/'material.json',{})
    from novel_manga.interactive.intake import PROJECTS
    if family not in PROJECTS:raise ValueError('Unknown interactive project')
    voice_specs=_required_voice_specs(material['profile'],material['script'],material['voice_specs'])
    script=copy.deepcopy(material['script'])
    if material['profile'].get('h3_speech_policy'):
        script.setdefault('profile',{})['h3_speech_policy']=material['profile']['h3_speech_policy']
    shots=script['shots']
    indexes=[i for g in material['groups'] for i in g['shot_indexes']]
    if indexes != list(range(1,len(shots)+1)):
        raise ValueError('Authored groups must cover every beat once, in order')
    episode=output/(output.name+'_1')
    episode.mkdir(parents=True)
    (output/'owner').write_text(f'Interactive studio / project={family} / imported screenplay / {project.resolve()}\n')
    for name,key in [('profile','profile'),('story_bible','bible'),('voice_specs','voice_specs')]:
        atomic_write_json(output/(name+'.json'),material[key])
    for name in ('performance_en.json','style.json'):
        if (project/name).is_file():
            shutil.copy2(project/name,output/name)
    segments=[{'segment_id':s['segment_id'],'text':s['source_quote']} for s in shots]
    for name,value in [('chapter_script',script),('segments',segments),('authored_groups',material['groups']),
                       ('interaction',material['interaction']),('overlays',material['overlays']),
                       ('evidence_surfaces',material.get('evidence_surfaces',[]))]:
        atomic_write_json(episode/(name+'.json'),value)
    for name in ('publication', 'delivery_policy'):
        if name in material:
            atomic_write_json(episode/(name+'.json'), material[name])
    atomic_write_json(episode/'source_trace.json',{'original':str(project/'source.md'),
        'archive':material.get('source_archive'),'shots':[{'index':s['index'],'shot_id':s['shot_id'],
        'original_shot':s.get('source_shot'),'source_kind':s.get('source_kind'),
        'turns':s.get('turns',[])} for s in shots]})
    shutil.copy2(project/'source.md',episode/'authored_source.md')
    (episode/'source.txt').write_text('\n\n'.join(s['text'] for s in segments))
    references=material.get('reference_images',[])
    if material.get('identity_image'):
        references=[*references, {'source':material['identity_image'],
            'path':'series_assets/characters/character_001/turnaround.jpeg'}]
    for reference in references:
        identity=Path(reference['source'])
        if not identity.is_absolute():identity=project/identity
        card=output/reference['path']
        card.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(identity) as image:image.convert('RGB').save(card,quality=97)
        atomic_write_json(card.with_suffix('.jpeg.origin.json'),{'kind':'supplied-reference',
            'source':str(identity),'reference_scope':reference.get('scope','supplied-reference'),'redrawn':False})
    supplied_voices={}
    for reference in material.get('reference_audios',[]):
        source=Path(reference['source'])
        if not source.is_absolute():source=project/source
        voice=output/reference['path']
        voice.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,voice)
        supplied_voices[reference['name']]={
            'path':str(voice.relative_to(output)),'source':str(source),
            'text':reference.get('text',''),'origin':reference.get('origin','supplied-reference'),
            'voice_quality':'Supplied timbre reference; episode speech checked separately.'}
    if supplied_voices:
        atomic_write_json(output/'series_assets/voices/voices.json',supplied_voices)
    atomic_write_json(output/'production-budget.json',{'source_seconds':material.get('source_seconds'),
        'planned_body_seconds':sum(s['duration_seconds'] for s in shots),'end_card_seconds':3,
        'voice_sources':len(voice_specs),'video_units':len(material['groups']),
        'max_generated_takes_per_unit':3,'runner_max_attempts':2,'sd_video_enabled':False,
        'policy':'Keep old editions and every request/task/take; resume the same edition without resetting limits'})
    from novel_manga.interactive.intake import finalize
    finalize(episode,family,documents=[project/'source.md',project/'material.json'])
    return episode


def pack_authored(directory: Path) -> dict:
    from novel_manga.application.packing.context import load_context, POLICY
    from novel_manga.application.packing.service import prepared_shots, clip_entry
    from novel_manga.application.preparation.readiness import plan_issues
    from novel_manga.story.compilation import plan_totals
    script=read_json(directory/'chapter_script.json',{})
    context=load_context(directory,directory.parent/'story_bible.json',None,None,None,None)
    context['compiler_options']=replace(context['compiler_options'],max_stages=6,camera_policy='authored')
    shots=prepared_shots(script,directory,identity_data=context.get('identity_data'))
    clips=[]
    first_frame=read_json(directory.parent/'profile.json',{}).get('h3_input_mode')=='first-frame'
    for number,group in enumerate(read_json(directory/'authored_groups.json',[]),1):
        selected=[shots[i-1] for i in group['shot_indexes']]
        seconds=sum(s['duration_seconds'] for s in selected)
        if not (0 < seconds <= 15 if first_frame else 4 <= seconds <= 15) or len(selected)>6:
            raise ValueError('Travel generation group exceeds the existing duration/stage budget')
        packed={'kind':'video','location':selected[0]['location'],'shots':selected,
            'seconds':seconds,'request_seconds':max(4,seconds)if first_frame else seconds,
            'shot_timing':[{'seconds':s['duration_seconds']} for s in selected]}
        clip=clip_entry(packed,f'clip_{number:02d}',context)
        if first_frame:clip['request_seconds']=max(4,seconds)
        clip['source_authored_segment']=group['source_segment']
        if first_frame:clip['first_frame_opening']=[copy.deepcopy(selected[0])]
        clips.append(clip)
    plan={'policy':POLICY,'limits':{'max_clip_seconds':15,'max_stages':6,'camera_policy':'authored'},
        'totals':plan_totals(clips,shots,context),'clips':clips}
    issues=plan_issues(plan,script)
    if issues:raise ValueError(f'Adopted travel plan needs correction: {issues}')
    atomic_write_json(directory/'clip_plan.json',plan)
    return plan


def build_authored_voices(root: Path, novel: Path, *, family: str = 'adventure') -> None:
    episode=novel/(novel.name+'_1')
    specs=_required_voice_specs(read_json(novel/'profile.json',{}),read_json(episode/'chapter_script.json',{}),
                                read_json(novel/'voice_specs.json',{}))
    if not specs:return
    from novel_manga.interactive.runtime import runner_for
    from novel_manga.providers.base import ImageResult
    runner=runner_for(root,novel,'h3',project=family)
    directory=novel/'series_assets/voices';directory.mkdir(parents=True,exist_ok=True)
    bank=read_json(directory/'voices.json',{})
    for name,spec in specs.items():
        wav=directory/(name+'.wav');video=directory/(name+'-voice-source.mp4')
        if wav.is_file() and name in bank:continue
        if name not in bank:
            bank[name]={'path':str(wav.relative_to(novel)),'source':str(video.relative_to(novel)),
                'text':spec['sample_text'],'origin':'generated-reference','sample_requests':0,
                'voice_quality':'not independently listened to; episode speech checked separately'}
        prompt=spec['prompt']+' No music and no other voices. Begin immediately and say this phrase once: <d>[Chinese] '+spec['sample_text']+'</d>'
        reference=novel/spec['picture']
        if not video.is_file():
            # The existing provider hook runs only before a new submission. A
            # cached task's polling/download does not consume another take.
            provider=runner.context.provider
            original_hook=getattr(provider,'before_video_submit',None)
            task_path=video.with_suffix('.mp4.task.json')
            def before_submit():
                if original_hook is not None:original_hook()
                if bank[name]['sample_requests']>=2:raise RuntimeError(f'Voice sample limit reached: {name}')
                previous=read_json(task_path,{})
                if previous:
                    atomic_write_json(directory/(name+f'-submission-{bank[name]["sample_requests"]}.task.json'),previous)
                bank[name]['sample_requests']+=1
                bank[name]['status']='requested'
                atomic_write_json(directory/'voices.json',bank)
                atomic_write_json(directory/(name+f'-sample-request-{bank[name]["sample_requests"]}.json'),
                    {'prompt':prompt,'seconds':14,'picture':str(reference.relative_to(novel)),
                     'request_number':bank[name]['sample_requests']})
            provider.before_video_submit=before_submit
            try:provider.create_video(prompt,ImageResult(path=reference),video,14)
            finally:
                provider.before_video_submit=original_hook
                receipt=read_json(task_path,{})
                if receipt:
                    bank[name]['task_id']=receipt.get('task_id')
                    atomic_write_json(directory/(name+f'-submission-{bank[name]["sample_requests"]}.task.json'),receipt)
                atomic_write_json(directory/'voices.json',bank)
        run(['ffmpeg','-y','-v','error','-i',str(video),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
        bank[name]['status']='prepared'
        atomic_write_json(directory/'voices.json',bank)
    episode=novel/(novel.name+'_1')
    if any(s.get('authored_frame') for s in read_json(episode/'chapter_script.json',{}).get('shots',[])):
        from novel_manga.interactive.romance import pack_romance
        pack_romance(episode)
    else:
        pack_authored(episode)
