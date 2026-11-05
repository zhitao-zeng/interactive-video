"""Fresh API openings for Fast, followed by the normal SD production/review loop."""
from pathlib import Path
from types import SimpleNamespace
import copy
import os
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED

from novel_manga.util import read_json, atomic_write_json
from novel_manga.media.first_frames import current_contract, source_asset_state, require_current, review_problems, unchanged_opening_scale


def select_mode(novel, mode):
    if mode is None:return
    if mode!='first-frame':raise ValueError('Unsupported SD input mode')
    profile=read_json(novel/'profile.json',{})
    if profile.get('sd_input_mode')==mode:return
    episode=novel/(novel.name+'_1')
    from novel_manga.application.repair.history import generation_counts
    if (any(generation_counts(episode).values()) or any((episode/'work').rglob('*.task.json'))
            or any((episode/'work').rglob('*.mp4'))):
        raise ValueError('SD first-frame mode requires a new unrendered edition; retain existing takes')
    profile['sd_input_mode']=mode
    atomic_write_json(novel/'profile.json',profile)


def prepare(root, novel, *, builder=None, checker=None):
    from novel_manga.application.agents.episode.first_frames import (build, check, source_opening,
        review_candidate, request_prompt, opening_state)
    from novel_manga.story.voice_delivery import native_speech_selected
    from novel_manga.story.fields import opening_frame_error
    if read_json(novel/'profile.json',{}).get('sd_input_mode')!='first-frame':
        raise ValueError('Select SD first-frame mode before preparing openings')
    episode=novel/(novel.name+'_1');plan=read_json(episode/'clip_plan.json')
    runtime=root/'.runtime/sd-first-frames'/novel.name
    ctx=SimpleNamespace(novel_dir=novel,episode_dir=episode,run_dir=runtime)
    pending=[]
    # Check the complete source before paying for any images.
    for clip in plan['clips']:
        if clip.get('kind')!='video':continue
        if not 4<=clip['request_seconds']<=15:
            raise ValueError(f"{clip['clip_id']}: Fast first-frame groups require 4–15 seconds")
        if not native_speech_selected(clip):
            raise ValueError('Fast first-frame mode requires the selected native speech policy')
        if clip.get('first_frame',{}).get('extra_references'):
            raise ValueError('Fast first-frame video sends only its complete opening image')
        try:
            require_current(ctx,clip)
            if (clip.get('first_frame')or{}).get('review_record'):continue
        except (ValueError,OSError):pass
        opening=source_opening(ctx,clip)
        if issue:=opening_frame_error(opening):
            raise ValueError(f"{clip['clip_id']}: creator opening_frame required: {issue}")
        refs=[r for r in clip.get('references',[]) if r.get('role') in {'character','location','prop'}]
        subjects=opening.get('reference_subjects')
        if subjects:
            if set(subjects)-{r['name'] for r in refs}:
                raise ValueError(f"{clip['clip_id']}: opening reference_subjects must name registered source cards")
            refs=[r for r in refs if r['name'] in subjects]
        paths=[r['path'] for r in refs]
        if not paths:raise ValueError(f"{clip['clip_id']}: opening requires registered source cards")
        contract=current_contract(ctx,clip,paths,include_style=True)
        row={'image_prompt':opening['image_prompt'],'visible_characters':opening['visible_characters'],
             'image_references':paths,'extra_references':[],
             'request_prompt':request_prompt(ctx,{'image_prompt':opening['image_prompt']},contract)}
        identity={'row':row,'source_contract':contract,'source_assets':source_asset_state(ctx,paths)}
        pending.append((clip,row,contract,identity))
    runtime.mkdir(parents=True,exist_ok=True)
    (runtime/'owner').write_text(f'Interactive Fast API first-frame preparation / {novel}\n')
    def candidate_for(item):
        clip,row,contract,identity=item
        folder=novel/'first_frames'/episode.name/clip['clip_id'];folder.mkdir(parents=True,exist_ok=True)
        candidates=sorted(folder.glob('candidate_*'))
        def generation_input(value):
            value=copy.deepcopy(value)
            value.get('source_contract',{}).pop('character_facts',None)
            return value
        candidate=None
        for directory in candidates:
            saved=read_json(directory/'sd-input.json',{})
            expected=copy.deepcopy(identity);saved_contract=saved.get('source_contract',{})
            if 'shot_scale'not in saved_contract.get('opening',{}) and unchanged_opening_scale(clip,contract):
                expected['source_contract']['opening'].pop('shot_scale',None)
            if 'character_facts'not in saved_contract and 'device_state'not in saved_contract.get('opening',{}):
                expected['source_contract']['opening'].pop('device_state',None)
            if generation_input(saved)==generation_input(expected):candidate=directory;break
        if candidate is None:
            if len(candidates)>=3:raise ValueError(f"{clip['clip_id']}: first-frame image candidate budget used")
            candidate=folder/f'candidate_{len(candidates)+1:02d}';candidate.mkdir()
            atomic_write_json(candidate/'sd-input.json',identity)
        path=candidate/'opening.png'
        if not path.is_file():(builder or build)(ctx,row,path)
        if not (candidate/'image-request.json').is_file():
            from novel_manga.application.profiles import frame_spec, load_profile
            atomic_write_json(candidate/'image-request.json',{
                'authored_prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
                'image_references':list(row['image_references']),'extra_references':[],
                'visible_characters':list(row['visible_characters']),
                'ratio':frame_spec(load_profile(novel))['video_ratio'],
                'opening_state':opening_state(row,contract)})
        review,_,record=review_candidate(ctx,path,row,contract,identity['source_assets'],checker or check)
        if review.get('consistent') is not True or review_problems(review):
            error=ValueError(f"{clip['clip_id']}: first-frame review rejected; inspect {candidate}")
            error.frame_path=str(path.relative_to(novel))
            error.frame_update={'review':review,'review_record':str(record.relative_to(novel))}
            raise error
        return {'path':str(path.relative_to(novel)),'extra_references':[],
            'visible_characters':copy.deepcopy(row['visible_characters']),'source_contract':contract,
            'source_assets':identity['source_assets'],'prompt':row['image_prompt'],
            'request_prompt':row['request_prompt'],'review':review,'review_record':str(record.relative_to(novel)),
            'candidate':candidate.name}
    from novel_manga.media.resources import acquire_inflight_slot,release_inflight_slot
    workers=max(1,min(8,int(read_json(novel/'profile.json',{}).get('inflight',1))))
    shared=os.environ.get('NOVEL_INFLIGHT_DIR')
    pool=Path(shared).parent/'first-frame-shared-inflight'if shared else novel/'.inflight-first-frames'
    def complete(item):
        handle=acquire_inflight_slot(novel,workers,pool_directory=pool)
        try:return candidate_for(item)
        finally:release_inflight_slot(handle)
    remaining=iter(pending);errors=[]
    with ThreadPoolExecutor(max_workers=workers)as executor:
        running={}
        def dispatch():
            while not errors and len(running)<workers:
                item=next(remaining,None)
                if item is None:break
                running[executor.submit(complete,item)]=item
        dispatch()
        while running:
            done,_=wait(running,return_when=FIRST_COMPLETED)
            for future in done:
                item=running.pop(future);clip=item[0]
                try:frame=future.result()
                except Exception as error:
                    if (getattr(error,'frame_update',None) and
                            (clip.get('first_frame')or{}).get('path')==error.frame_path):
                        clip['first_frame'].update(error.frame_update)
                        atomic_write_json(episode/'clip_plan.json',plan)
                    errors.append(error)
                else:
                    clip['first_frame']=frame
                    atomic_write_json(episode/'clip_plan.json',plan)
            dispatch()
    if errors:raise errors[0]
    for clip in plan['clips']:
        if clip.get('kind')=='video':
            require_current(ctx,clip)
            if clip['first_frame'].get('extra_references'):
                raise ValueError('Fast first-frame video sends only its complete opening image')
    return plan
