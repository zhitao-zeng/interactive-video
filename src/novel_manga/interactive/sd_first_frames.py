"""Fresh API openings for Fast, followed by the normal SD production/review loop."""
from pathlib import Path
from types import SimpleNamespace
import copy
import os
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED

from novel_manga.util import read_json, atomic_write_json
from novel_manga.media.first_frames import (CompiledOpening, compile_opening, current_contract, require_current,
    review_problems, source_opening, source_assets_match, unchanged_opening_scale)


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
    from novel_manga.application.agents.episode import opening_source
    from novel_manga.application.agents.episode.first_frames import (build, check, review_candidate,
        opening_state, review_opening_sources)
    from novel_manga.story.voice_delivery import native_speech_selected
    from novel_manga.story.fields import opening_frame_error
    if read_json(novel/'profile.json',{}).get('sd_input_mode')!='first-frame':
        raise ValueError('Select SD first-frame mode before preparing openings')
    episode=novel/(novel.name+'_1');plan=read_json(episode/'clip_plan.json')
    runtime=root/'.runtime/sd-first-frames'/novel.name
    ctx=SimpleNamespace(novel_dir=novel,episode_dir=episode,run_dir=runtime,work_dir=runtime)
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
        if issue:=opening_frame_error(source_opening(ctx,clip)):
            raise ValueError(f"{clip['clip_id']}: creator opening_frame required: {issue}")
        compiled=compile_opening(ctx,clip,{'clip_id':clip['clip_id']},lane='sd20-fast',require_assets=False)
        row,contract,assets=compiled
        if not row['image_references']:raise ValueError(f"{clip['clip_id']}: opening requires registered source cards")
        identity={'row':row,'source_contract':contract,'source_assets':assets,
                  'references':[r.as_dict() for r in compiled.references]}
        pending.append((clip,compiled,identity))
    runtime.mkdir(parents=True,exist_ok=True)
    (runtime/'owner').write_text(f'Interactive Fast API first-frame preparation / {novel}\n')
    source_reviews=review_opening_sources(ctx,[compiled for _,compiled,_ in pending])
    blockers=opening_source.source_blockers(ctx,plan)
    if blockers:
        clips={clip['clip_id']:clip for clip in plan['clips']}
        for cid in blockers:
            opening=current_contract(ctx,clips[cid],[])['opening']
            review=source_reviews.get(cid) or opening_source.current_review(ctx,cid,opening)
            atomic_write_json(runtime/'input/first_frames'/cid/'source-preflight.json',review)
        raise ValueError(f"opening_source_conflict: {sorted(blockers)}")
    materialized=[]
    for clip,compiled,_ in pending:
        current=compile_opening(ctx,clip,compiled.row,lane='sd20-fast',require_assets=True)
        row,contract,assets=current
        identity={'row':row,'source_contract':contract,'source_assets':assets,
                  'references':[r.as_dict() for r in current.references]}
        materialized.append((clip,current,identity))
    pending=materialized
    def candidate_for(item):
        clip,compiled,identity=item
        row,contract,assets=compiled
        folder=novel/'first_frames'/episode.name/clip['clip_id'];folder.mkdir(parents=True,exist_ok=True)
        candidates=sorted(folder.glob('candidate_*'))
        def generation_input(value):
            value=copy.deepcopy(value)
            value.get('source_contract',{}).pop('character_facts',None)
            # This is derived from the authored prompt/style and review facts.
            # Additional review facts require rechecking retained pixels, not
            # another paid image solely because their compiled text changed.
            value.get('row',{}).pop('request_prompt',None)
            return value
        candidate=None
        generation_row,generation_contract=row,contract
        for directory in candidates:
            if read_json(directory/'retake-decision.json',{}).get('approved')is True:continue
            saved=read_json(directory/'sd-input.json',{})
            expected=copy.deepcopy(identity);saved_contract=saved.get('source_contract',{})
            if 'shot_scale'not in saved_contract.get('opening',{}) and unchanged_opening_scale(clip,contract):
                expected['source_contract']['opening'].pop('shot_scale',None)
            if 'character_facts'not in saved_contract and 'device_state'not in saved_contract.get('opening',{}):
                expected['source_contract']['opening'].pop('device_state',None)
            if 'references' not in saved:expected.pop('references',None)
            if not any('hash' in asset for asset in saved.get('source_assets',[])):
                for asset in expected['source_assets']:asset.pop('hash',None)
            left=generation_input(saved);right=generation_input(expected)
            saved_assets=left.pop('source_assets',[]);expected_assets=right.pop('source_assets',[])
            if left==right and source_assets_match(saved_assets,expected_assets):
                candidate=directory
                generation_row=saved['row'];generation_contract=saved['source_contract']
                break
        if candidate is None:
            if len(candidates)>=3:raise ValueError(f"{clip['clip_id']}: first-frame image candidate budget used")
            candidate=folder/f'candidate_{len(candidates)+1:02d}';candidate.mkdir()
            atomic_write_json(candidate/'sd-input.json',identity)
        generation_compiled=CompiledOpening(generation_row,generation_contract,assets,
            compiled.draft_frame,compiled.references)
        path=candidate/'opening.png'
        if not path.is_file():(builder or build)(ctx,generation_compiled if builder is None else generation_row,path)
        if not (candidate/'image-request.json').is_file():
            from novel_manga.application.profiles import frame_spec, load_profile
            atomic_write_json(candidate/'image-request.json',{
                'authored_prompt':generation_row['image_prompt'],'request_prompt':generation_row['request_prompt'],
                'image_references':list(generation_row['image_references']),'extra_references':[],
                'visible_characters':list(generation_row['visible_characters']),
                'references':[r.as_dict() for r in compiled.references],
                'ratio':frame_spec(load_profile(novel))['video_ratio'],
                'opening_state':opening_state(generation_compiled)})
        review,_,record=review_candidate(ctx,path,compiled,checker or check)
        if review.get('consistent') is not True or review_problems(review):
            error=ValueError(f"{clip['clip_id']}: first-frame review rejected; inspect {candidate}")
            error.frame_path=str(path.relative_to(novel))
            error.frame_update={'review':review,'review_record':str(record.relative_to(novel))}
            raise error
        return {'path':str(path.relative_to(novel)),'extra_references':[],
            'visible_characters':copy.deepcopy(row['visible_characters']),'source_contract':contract,
            'source_assets':assets,'references':[r.as_dict() for r in compiled.references],
            'prompt':row['image_prompt'],'request_prompt':row['request_prompt'],
            'image_sha256':read_json(record,{}).get('input',{}).get('candidate_sha256'),
            'review':review,'review_record':str(record.relative_to(novel)),'candidate':candidate.name}
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
