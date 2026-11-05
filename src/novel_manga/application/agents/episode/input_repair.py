"""Source-backed input repairs through existing cards, compilation and publication."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from novel_manga.application.identity.phases import LOOK_FIELDS, chapter_of
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError, UnsupportedInput, CompileConflict, validate_resume

from .arguments import service_interruption

from .input_records import (INPUT_LIMIT, SOURCE_REVIEW_POLICY, _phase_groups, effects,
                           remaining_input_attempts, input_receipt, source_review_recoverable,
                           latest_input_records)
from .input_records import asset_configuration_recoverable, posture_protocol_recoverable, first_frame_recoverable, asset_image_recoverable

from .input_contracts import (normalize_arguments, expand_shared_scope, SHOT_NAMES, SHOT_FIELDS,
                              opening_grant, validate_grant, evidence_fingerprint)
from .input_compilation import compile_input
from .input_candidate import prepare_candidate, resolve_input_scope


def current_repair_grant(ctx,row,plan):
    from .first_frames import current_frame_grant
    target=next(c for c in plan['clips'] if c['clip_id']==row['clip_id'])
    from .input_contracts import RepairGrant
    saved=current_frame_grant(ctx,target)
    if not saved:return None
    return RepairGrant(saved['operation'],saved.get('shot_indexes') or [],
                       frozenset(saved.get('shot_fields') or []),saved.get('evidence') or {})






def repair_status(ctx):
    states = {}
    records = [(p, read_json(p, {})) for p in sorted(
        (ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json'), key=lambda p: p.stat().st_mtime)]
    for path, record in records:
        cid = record.get('clip_id')
        if not cid or record.get('operation')=='upstream_asset':
            continue
        row = {key: record.get(key) for key in ('status', 'step', 'error', 'error_kind', 'resume_count', 'revision_count', 'changed')}
        if record.get('service_interruption'):
            row['service_interruption'] = record['service_interruption']
            row['next_action'] = ('Stop this batch: the shared service rejected the request. No content revision, '
                                  'take or input attempt was spent. Resume the same saved candidates once the '
                                  'service or credential is fixed.')
        if record.get('error_details'):row['error_details']=record['error_details']
        if record.get('decision') and (record.get('error_kind') == 'service_interrupted'
                or source_review_recoverable(record) or record.get('status') in {'checking', 'published'} or (
                record.get('status') == 'incomplete' and int(record.get('resume_count', 0)) < 1)):
            row['resume_action'] = {'clip_id': cid, 'action': 'input', 'resume': True,
                                    'reason': 'Continue the saved input repair using its existing assets'}
        if (not record.get('published') and record.get('status') in {'rejected', 'incomplete'}
                and int(record.get('revision_count', len(record.get('previous_decisions', [])))) < 1):
            row['revision_available'] = True
            row['next_action'] = 'Correct the rejected ownership/source fields once; do not repeat the same proposal or draw assets for an unsupported state.'
        decision = record.get('decision') or {}
        if not decision and not record.get('published') and record.get('status') in {'checking','incomplete'}:
            row['revision_available'] = int(record.get('revision_count',len(record.get('previous_decisions',[])))) < 1
            row['next_action'] = ('The saved input has no complete decision to resume. Submit one full source-backed '
                                  'input proposal using the existing revision allowance.' if row['revision_available'] else
                                  'The saved decision is missing and the revision allowance is used; stop this input.')
        if (record.get('status') in {'checking','incomplete'} and decision and not record.get('published')
                and record.get('step') != 'draft_assets'):
            try:
                normalize_arguments(decision)
            except RepairArgumentError as error:
                row.pop('resume_action',None)
                row['argument_error'] = str(error)
                row['revision_available'] = int(record.get('revision_count',len(record.get('previous_decisions',[])))) < 1
                row['next_action'] = ('Correct these arguments once using the existing revision allowance; do not resume the same malformed request.'
                                      if row['revision_available'] else 'The saved arguments are invalid and the revision allowance is used; stop this unresolved input.')
        states[cid] = row
    for cid, remaining in remaining_input_attempts(ctx, states, records=records).items():
        row = states[cid]
        row['remaining_attempts'] = remaining
        if row.get('error_kind') == 'service_interrupted':
            row['resume_action'] = {'clip_id': cid, 'action': 'input', 'resume': True,
                                    'reason': 'Resume the same candidate after the shared service is available.'}
            row['next_action'] = ('The shared service interrupted this candidate. Resume it unchanged after the '
                                  'service is available; no content revision, take, or input attempt was spent.')
            continue
        if row.get('status') == 'tool_error':
            row['next_action'] = ('This historical tool_error is not counted as an input attempt. '
                                  'Use the current allowances, not the old blocked message.')
            pending = [r for _, r in records if r.get('clip_id') == cid
                       and r.get('status') in {'checking','incomplete','published','rejected'}]
            if pending and not pending[-1].get('published'):
                previous = pending[-1]
                row['revision_available'] = int(previous.get('revision_count',len(previous.get('previous_decisions',[])))) < 1
            if remaining > 0 or row.get('revision_available'):
                row['next_action'] += ' Submit a complete source-backed input proposal; the host will reuse the existing allowance or its one revision.'
        record=next(r for _,r in reversed(records) if r.get('clip_id')==cid)
        if asset_configuration_recoverable(ctx,record):
            row['resume_action']={'clip_id':cid,'action':'input','resume':True,
                'reason':'Continue the unchanged saved candidate after the image configuration fix'}
            row['next_action']='The image configuration failure was fixed. Resume this exact candidate once; preserve source, completed assets and all input/video counts.'
        if posture_protocol_recoverable(record):
            row['resume_action'] = {'clip_id': cid, 'action': 'input', 'resume': True,
                'reason': 'Continue the unchanged saved candidate after the posture protocol correction'}
            row['next_action'] = 'Resume this candidate once with schema-owned stage/person keys; reuse completed assets and preserve input/video counts.'
        if first_frame_recoverable(ctx,record):
            row['resume_action']={'clip_id':cid,'action':'input','resume':True,
                'reason':'Continue the same source-checked correction into fresh first-frame preparation.'}
            row['next_action']='Resume this unchanged candidate once after the host ordering fix; all image/video limits and earlier resume counts remain.'
        if asset_image_recoverable(record):
            row['resume_action']={'clip_id':cid,'action':'input','resume':True,
                'reason':'Continue source-approved asset image repair within the new three-picture bound; text and video counts are unchanged.'}
            row['next_action']='Resume the saved source/design at the picture stage; failed old picture is attempt one, at most three pictures total.'
    from .input_records import input_operations
    for cid, row in states.items():
        operations = input_operations(ctx, cid, records=records)
        if row.get('argument_error'):
            operations['resume'] = {'allowed': False, 'reason': row['argument_error']}
        row['operations'] = operations
        row['revision_available'] = operations['revise']['allowed']
        if not operations['resume']['allowed']:
            row.pop('resume_action', None)
        if not any(operation['allowed'] for operation in operations.values()):
            row['next_action'] = 'No legal input route remains for this candidate; use an offered upstream operation or finish unresolved. Numeric balance does not renew a spent revision.'
    return states


from novel_manga.application.repair.source_support import source_check


def _source_review_shot_context(before_shots, after_shots, affected_indexes):
    """Approved before-state, host diff and native siblings for source review.

    ``before_shots`` is the script the current plan was approved from;
    ``after_shots`` is the candidate.  Review sees only the actual changes and
    the source-shot siblings they were split from, so untouched dialogue in a
    sibling cannot be read as this repair deleting it.
    """
    old = ({s.get('index', i): s for i, s in enumerate(before_shots, 1)}
           if isinstance(before_shots, (list, tuple)) else dict(before_shots))
    new = ({s.get('index', i): s for i, s in enumerate(after_shots, 1)}
           if isinstance(after_shots, (list, tuple)) else dict(after_shots))

    def changed_fields(left, right):
        fields = []
        for key in sorted((set(left) | set(right)) - {'index'}):
            if key == 'turns':
                lturns, rturns = left.get(key) or [], right.get(key) or []
                for n in range(max(len(lturns), len(rturns))):
                    a = lturns[n] if n < len(lturns) else {}
                    b = rturns[n] if n < len(rturns) else {}
                    for turn_key in sorted(set(a) | set(b)):
                        if a.get(turn_key) != b.get(turn_key):
                            fields.append(f'turns[{n}].{turn_key}')
            elif left.get(key) != right.get(key):
                fields.append(key)
        return fields

    groups = {}
    for index, shot in old.items():
        if shot.get('source_shot') is not None and shot.get('source_kind') is not None:
            groups.setdefault((shot['source_shot'], shot['source_kind']), []).append(index)
    review = []
    for index in sorted(affected_indexes):
        before, after = old.get(index, {}), new.get(index, {})
        key = (before.get('source_shot'), before.get('source_kind'))
        sibling_indexes = groups.get(key, [index]) if all(v is not None for v in key) else [index]
        review.append({
            **copy.deepcopy(after),
            'index': index,
            'before': copy.deepcopy(before),
            'after': copy.deepcopy(after),
            'changed_fields': changed_fields(before, after),
            'reason': '',
            'source_group': [
                {'index': sibling, 'source_shot': old[sibling].get('source_shot'),
                 'source_kind': old[sibling].get('source_kind'),
                 'shot': copy.deepcopy(new.get(sibling, {}))}
                for sibling in sorted(sibling_indexes)
            ],
        })
    return review


from .input_assets import local_phases


def build_phases(ctx, names):
    from novel_manga.application.agents.episode.session import SKILL_SOURCE
    script = SKILL_SOURCE.parents[1]/'scripts/build_phase_cards.py'
    log = ctx.run_dir/'input-assets.log'
    if not names:
        return
    def build(name):
        # Each character keeps its own primary -> companion dependencies. One
        # slow card must not consume the entire other characters' time window.
        with log.open('a') as stream:
            result = subprocess.run([sys.executable, str(script), '--novel-dir', str(ctx.novel_dir),
                '--only', name, '--chapter', str(chapter_of(ctx.episode_dir)),
                '--expressions'], stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f'image helper exited {result.returncode}')
    failures = {}
    with ThreadPoolExecutor(max_workers=min(3, len(names))) as pool:
        pending = {pool.submit(build, name): name for name in sorted(names)}
        for future in as_completed(pending):
            try:
                future.result()
            except (OSError, ValueError) as error:
                failures[pending[future]] = str(error)
    if failures:
        detail = '; '.join(f'{name}: {reason}' for name, reason in sorted(failures.items()))
        raise ValueError(f'input phase images failed ({detail}); completed images retained; inspect {log.name}')


def copy_registered_assets(ctx, names):
    """Copy existing alternative scenes/props; do not redraw the book's library."""
    from .inputs import _catalog
    refs = []
    manifest = read_json(ctx.source_novel_dir/'series_assets/manifest.json', {})
    for group, role in (('characters', 'character'), ('locations', 'location'), ('props', 'prop')):
        for row in manifest.get(group, []):
            name = str(row.get('name') or '')
            if name not in names and name.split('：', 1)[0] not in names:
                continue
            for field in ('primary_image', 'secondary_image'):
                if row.get(field):
                    refs.append({'role': role, 'name': name.split('：', 1)[0] if role == 'location' else name,
                                 'asset_id': row['asset_id'], 'path': row[field]})
    _catalog(ctx, refs)


def refresh_inputs(ctx, plan, script):
    from .inputs import _catalog, _references
    refs, missing, _ = _catalog(ctx, list(_references(plan)))
    if missing:
        raise ValueError('repaired references missing: ' + ', '.join(str(r['path']) for r in missing))
    current = read_json(ctx.run_dir/'catalog.json', [])
    keyed = {(r['path'], r['role'], r['name']): r for r in current}
    keyed.update({(r['path'], r['role'], r['name']): r for r in refs})
    catalog = list(keyed.values())
    atomic_write_json(ctx.run_dir/'catalog.json', catalog)
    atomic_write_json(ctx.work_dir/'input/catalog.json', {'assets': catalog})
    atomic_write_json(ctx.work_dir/'input/clip_plan.json', plan)
    atomic_write_json(ctx.work_dir/'input/chapter_script.json', script)
    shutil.copy2(ctx.novel_dir/'story_bible.json', ctx.work_dir/'input/story_bible.json')
    phases = ctx.novel_dir/'series_assets/phases.json'
    if phases.is_file():
        shutil.copy2(phases, ctx.work_dir/'input/phases.json')


def repair(ctx, row, *, check_only=False, request_receipt=None, grant=None):
    from novel_manga.application.repair.publication import publish_candidate

    directory = ctx.episode_dir
    validate_resume(row)
    if row.get('resume') is True:
        latest = latest_input_records(directory).get(row['clip_id'])
        saved = latest[1] if latest else {}
        if (not saved.get('decision') or not (saved.get('status') in {'checking', 'incomplete', 'published'}
                                             or saved.get('error_kind') == 'service_interrupted'
                                             or source_review_recoverable(saved)
                                             or asset_image_recoverable(saved))):
            raise ValueError('no incomplete input repair is available to resume')
        row = {**saved['decision'], 'reason': row.get('reason') or saved.get('reason') or 'Resume saved input repair'}
    row = normalize_arguments(row)
    plan = read_json(directory/'clip_plan.json')
    script = read_json(directory/'chapter_script.json')
    row = expand_shared_scope(row, plan)
    cid = row['clip_id']
    target = next(c for c in plan['clips'] if c['clip_id'] == cid)
    scope = row.get('clip_ids')
    if scope is not None and (cid not in scope
            or any(key not in {c['clip_id'] for c in plan['clips']} for key in scope)):
        raise RepairArgumentError('clip_ids must include the target and only current episode clips')
    grant=grant or current_repair_grant(ctx,row,plan)
    validate_grant(row,grant)
    source = (ctx.run_dir/'source.txt').read_text(encoding='utf-8')
    quote = str(row.get('source_quote') or '').strip()
    if not quote or quote not in source:
        raise ValueError('input repair requires a verbatim source_quote from input/source.txt')
    previous_inputs = sorted((directory/'work/input_repairs').glob(f'{cid}-*.json'),
                             key=lambda p: p.stat().st_mtime)
    matching = next(((path, record) for path in reversed(previous_inputs)
                     if (record := read_json(path, {})).get('decision')
                     and (effects(record['decision']) == effects(row)
                          or (record.get('submitted_decision') and effects(record['submitted_decision']) == effects(row)))), None)
    scope_info = resolve_input_scope(ctx,row,plan,script,target)
    reuse_fingerprint=evidence_fingerprint({'policy':SOURCE_REVIEW_POLICY,'source':source,
        'quote':quote,'decision':effects(row),'script':script})
    def recheck_reused_source(receipt,record,*,compiled):
        if compiled:
            review_shots=shots;review_patches=patches;review_phases=phases
            review_locations=locations;review_characters=characters;after_script=candidate_script
        else:
            review_shots=script['shots'];review_patches=row.get('shots') or []
            review_phases=(row.get('phases') or {}).get('characters',{})
            review_locations=row.get('locations') or {};review_characters=row.get('characters') or {};after_script=script
        scoped_shots=_source_review_shot_context(script['shots'],review_shots,set(scope_info.source_review_indexes))
        options={}
        if row.get('reason'):options['review_reason']=row['reason']
        if review_characters:options['characters']=review_characters
        if 'music_plan' in row:
            options['music_plans']={'before':script.get('music_plan'),'after':after_script.get('music_plan')}
        if record.get('reuse_source_fingerprint')==reuse_fingerprint and record.get('reuse_source_review'):
            review=record['reuse_source_review']
        else:
            try:
                review=source_check(source,quote,review_patches,review_phases,review_locations,scoped_shots,**options)
            except UnsupportedInput as error:
                review={'consistent':False,'problems':[str(error)]}
        record['reuse_source_fingerprint']=reuse_fingerprint
        record['reuse_source_review']=review
        atomic_write_json(receipt,record)
        if review.get('consistent') is False or review.get('problems'):
            return {'ok':False,'error':'source_rejected','changed':[],'accepted':[],
                    'input_repaired':[],'input_reused':[cid],'reused':True,
                    'blocked':{cid:'; '.join(review.get('problems') or ['current source review rejected the candidate'])}}
        return None
    if matching and matching[1].get('published'):
        if check_only:return {'valid':True,'resume_published':True}
        receipt,record=matching
        if failure:=recheck_reused_source(receipt,record,compiled=False):return failure
        refresh_inputs(ctx,plan,script)
        changed=record.get('changed',[]) if record.get('status')!='prepared' else []
        record.update(status='prepared',step='complete')
        record.pop('error',None);record.pop('error_kind',None)
        atomic_write_json(receipt,record)
        return {'changed':changed,'accepted':[],'input_repaired':[cid],'blocked':{},'reused':True}
    addresses = set(scope_info.allowed_indexes)
    prepared = prepare_candidate(ctx, row, plan, script, target, addresses, scope, matching,
                                 scope_info=scope_info)
    case = prepared.compilation
    row = case.row
    candidate_script = case.candidate_script
    shots = case.shots
    patches = case.patches
    targets = case.targets
    counts = case.counts
    phases = case.phases
    locations = case.locations
    characters = case.characters
    submitted_row = prepared.submitted_row
    chapter = prepared.chapter
    bible = prepared.bible
    base_ids = prepared.base_ids
    reused = prepared.reused
    reused_locations = prepared.reused_locations
    if check_only:
        input_receipt(ctx,cid,row,check_only=True)
        return {'valid':True,'clip_ids':sorted(targets),'allowed_shot_indexes':sorted(addresses)}
    if request_receipt is None:
        receipt, record = input_receipt(ctx, cid, row)
    else:
        receipt, record = request_receipt
        if (effects(record.get('decision') or {}) != effects(row)
                or (record.get('source') or {}).get('consistent') is not True):
            raise ValueError('request correction must continue the exact source-approved input')
    if row != submitted_row:
        record['submitted_decision'] = submitted_row
    if record.get('status') in {'prepared', 'checked_existing_wearing'}:
        if failure:=recheck_reused_source(receipt,record,compiled=True):return failure
        refresh_inputs(ctx, plan, script)
        return {'changed': [], 'accepted': [], 'input_repaired': [cid], 'blocked': {}, 'reused': True}
    if record.get('published'):
        if failure:=recheck_reused_source(receipt,record,compiled=True):return failure
        refresh_inputs(ctx, plan, script)
        record.update(status='prepared', step='complete')
        atomic_write_json(receipt, record)
        return {'changed': record.get('changed', []), 'accepted': [], 'input_repaired': [cid], 'blocked': {}, 'reused':True}
    phase_path = None
    old_phases = None
    bible_path = ctx.novel_dir/'story_bible.json'
    old_bible = bible_path.read_bytes()
    metadata_paths = [ctx.novel_dir/'entity/entities.json', ctx.work_dir/'input/story_bible.json']
    old_metadata = {path: path.read_bytes() if path.exists() else None for path in metadata_paths} if characters else {}
    published = False
    stage = 'source'
    def progress(step):
        nonlocal stage
        stage = step
        record.update(status='checking', step=step)
        record.pop('error', None)
        atomic_write_json(receipt, record)
    try:
        if not record.get('source'):
            record['source_review_policy'] = SOURCE_REVIEW_POLICY
            atomic_write_json(receipt, record)
            scoped_shots = _source_review_shot_context(
                script['shots'],shots,set(scope_info.source_review_indexes))
            review_options = {}
            if row.get('reason'):
                review_options['review_reason'] = row['reason']
            if characters:
                review_options['characters'] = characters
            if 'music_plan' in row:
                review_options['music_plans']={'before':script.get('music_plan'),
                                              'after':candidate_script['music_plan']}
            record['source'] = source_check(source, quote, patches, phases, locations, scoped_shots,
                                            **review_options)
            atomic_write_json(receipt, record)
        if characters:
            from .draft_assets import _asset
            from .input_assets import register_metadata
            progress('character_assets')
            assets = [_asset(ctx, 'character', name, design) for name, design in characters.items()]
            record['assets'] = [a['id'] for a in assets]
            atomic_write_json(receipt, record)
            register_metadata(ctx, assets)
            bible = read_json(bible_path)
        phase_path = local_phases(ctx) if phases or locations else None
        old_phases = phase_path.read_bytes() if phase_path and phase_path.exists() else None
        if phase_path:
            progress('assets')
            ledger = read_json(phase_path, {'policy': 'phase-cards-v1', 'characters': {}})
            ledger.setdefault('policy', 'phase-cards-v1')
            ledger.setdefault('characters', {})
            suffix = receipt.stem + (f"-resume{record['asset_revision']}" if record.get('asset_revision') else '')
            for name, entries in phases.items():
                phase = {**entries[0], 'from': chapter, 'to': chapter,
                         'asset_id': reused.get(name) or f'{base_ids[name]}-agent-{directory.name}-{suffix}'}
                ledger['characters'][name] = [phase, *[p for p in ledger['characters'].get(name, [])
                                                       if p.get('asset_id') != phase['asset_id']]]
            if locations:
                ledger.setdefault('locations', {})
                bible.setdefault('locations', [])
                for name, description in locations.items():
                    names_in_bible = [text.split('：', 1)[0].strip() for text in bible['locations']]
                    if name not in names_in_bible:
                        bible['locations'].append(name+'：'+description)
                        names_in_bible.append(name)
                    base = f'location_{names_in_bible.index(name)+1:03d}'
                    phase = {'from': chapter, 'to': chapter, 'label': name, 'description': description,
                             'base_asset': base, 'asset_id': f'{base}-agent-{directory.name}-{suffix}'}
                    if name in reused_locations:
                        phase={**reused_locations[name],'from':chapter,'to':chapter}
                    ledger['locations'][name] = [phase, *[p for p in ledger['locations'].get(name, [])
                                                         if p.get('asset_id') != phase['asset_id']]]
                atomic_write_json(bible_path, bible)
            atomic_write_json(phase_path, ledger)
            if phases:
                copy_registered_assets(ctx, set(phases) | {p.get('wears') for es in phases.values() for p in es})
                build_phases(ctx, set(phases)-set(reused))
            if locations:
                from .input_assets import build_locations
                pending_locations=set(locations)-set(reused_locations)
                if pending_locations:build_locations(ctx, pending_locations)
        copy_registered_assets(ctx, {str(shots[i].get('location') or '') for i in addresses})
        compiled = compile_input(case, receipt, record, progress)
        if compiled.proposal is None:
            published = True
            return {'changed': [], 'accepted': [], 'input_repaired': [cid], 'blocked': {}}
        proposal, candidate, changed = compiled.proposal, compiled.plan, compiled.changed
        progress('publish')
        publish_candidate(directory, proposal, 'episode_agent_input', changed)
        published = True
        record.update(status='published', published=True, changed=changed)
        atomic_write_json(receipt, record)
        repair_history = history.load(directory)
        repair_history['trials'][-1]['managed'] = True
        history.save(directory, repair_history)
        progress('refresh')
        refresh_inputs(ctx, candidate, candidate_script)
        record.update(status='prepared', step='complete')
        atomic_write_json(receipt, record)
        return {'changed': changed, 'accepted': [], 'input_repaired': [cid], 'blocked': {}}
    except Exception as error:
        from novel_manga.application.assets.candidates import AssetImageRejected
        if isinstance(error,CompileConflict):record['error_details']=error.details
        if isinstance(error, UnsupportedInput) and not isinstance(error, AssetImageRejected) and hasattr(error, 'review'):
            record['source_review'] = error.review
        outage = service_interruption(error)
        kind = ('asset_image_rejected' if isinstance(error, AssetImageRejected) else
                'source_rejected' if isinstance(error, UnsupportedInput) else
                'tool_error' if isinstance(error, (KeyError, AttributeError, TypeError)) else
                'service_interrupted' if outage or isinstance(error, (OSError, TimeoutError, subprocess.TimeoutExpired)) else
                'input_validation')
        image_outage = (kind == 'service_interrupted' and stage in {'character_assets', 'assets'}
                        and (record.get('source') or {}).get('consistent') is True)
        record.update(status='rejected' if kind in {'source_rejected', 'asset_image_rejected'} else
                      'tool_error' if kind in {'tool_error', 'service_interrupted'} and not image_outage else 'incomplete',
                      step=stage, error=f'{type(error).__name__}: {error}', error_kind=kind)
        if isinstance(error, AssetImageRejected):
            record['asset_image_review']={'exhausted':error.exhausted,'asset_id':error.asset_id,
                                          'review':error.image_review}
        if kind == 'service_interrupted':
            # This is the shared service, not this candidate: keep the saved
            # decision and every count for an unchanged continuation.
            record['service_interruption'] = outage or {'http_status': None, 'detail': str(error)[:500]}
        atomic_write_json(receipt, record)
        raise
    finally:
        if phase_path and not published:
            if old_phases is None:
                phase_path.unlink(missing_ok=True)
            else:
                phase_path.write_bytes(old_phases)
        if not published and (locations or characters):
            bible_path.write_bytes(old_bible)
        if not published:
            for path, content in old_metadata.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
