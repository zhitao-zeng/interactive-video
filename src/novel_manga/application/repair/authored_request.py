"""Recompile imported shot directions without rewriting their authored screenplay."""
from copy import deepcopy

from novel_manga.util import read_json, atomic_write_json
from novel_manga.application.profiles import h3_stamp
from novel_manga.application.rendering.h3 import convert
from novel_manga.application.preparation.request_check import request_consistency
from novel_manga.application.repair.agent import validate_prompt
from novel_manga.repair.proposal import RepairProposal

POLICY = 'authored-request-recompile-v6-conversation-convert'


def event_source(script, indexes):
    """The authored action is the event; cast/medium prose is reference metadata."""
    candidate=deepcopy(script)
    for shot in candidate.get('shots', []):
        if shot.get('index') in indexes and shot.get('authored_frame') and shot.get('source_quote'):
            shot['motion_prompt']=shot['source_quote']
    return candidate


def source_event_request(directory, draft, script):
    """Build a temporary Chinese request from the original events, preserving the adopted plan."""
    indexes=draft.get('shot_indexes', [])
    if not any(s.get('index') in indexes and s.get('authored_frame') for s in script.get('shots', [])):
        return
    if script.get('production_mode') == 'romance_conversation' and draft.get('prompt_compiler') == 'romance_conversation_v1':
        # Its Chinese request already contains the authored event and state,
        # rather than the legacy imported English appearance boilerplate.
        # Rebuilding it through novel stages both loses that structure and
        # mistakes source_stage metadata differences for changed utterances.
        selected = [s for s in script['shots'] if s.get('index') in indexes]
        if len(selected) != 1 or [s['index'] for s in selected] != indexes:
            raise ValueError('Conversation event stages do not match adopted order')
        fields = ('speaker_name', 'delivery_mode', 'text', 'emotion')
        expected = [tuple(t.get(k, '') for k in fields) for t in selected[0]['turns']
                    if t.get('delivery_mode') != 'silent_action']
        actual = [tuple(t.get(k, '') for k in fields) for t in draft.get('dialogue_bindings', [])]
        if expected != actual or any(t.get('stage') != 1 for t in draft.get('dialogue_bindings', [])):
            raise ValueError('Conversation request changed protected dialogue')
        return
    from dataclasses import replace
    from novel_manga.application.packing.context import load_context
    from novel_manga.application.packing.service import prepared_shots, clip_entry
    source=event_source(script,indexes)
    context=load_context(directory,directory.parent/'story_bible.json',None,None,None,None)
    context['compiler_options']=replace(context['compiler_options'],max_stages=6,camera_policy='authored')
    prepared=prepared_shots(source,directory,identity_data=context.get('identity_data'))
    selected=[s for s in prepared if s['index'] in indexes]
    if [s['index'] for s in selected]!=indexes:raise ValueError('Authored event stages do not match adopted order')
    packed={'kind':'video','location':draft['location'],'shots':selected,
            'seconds':draft['request_seconds'],'request_seconds':draft['request_seconds'],
            'shot_timing':deepcopy(draft['shot_timing'])}
    rebuilt=clip_entry(packed,draft['clip_id'],context)
    if ([(r['name'],r['role']) for r in rebuilt['references']]
            !=[(r['name'],r['role']) for r in draft['references']]):
        raise ValueError('Authored event rebuild changed reference ordering')
    if rebuilt['dialogue_bindings']!=draft['dialogue_bindings']:
        raise ValueError('Authored event rebuild changed protected dialogue')
    draft['prompt']=rebuilt['prompt']
    draft['action_bindings']=rebuilt['action_bindings']


def propose(directory, clip_id, issue):
    plan = read_json(directory/'clip_plan.json', {})
    original = next((c for c in plan.get('clips', []) if c['clip_id'] == clip_id), None)
    if not original or not original.get('authored_english_source') or not (directory/'authored_groups.json').is_file():
        return None
    script = read_json(directory/'chapter_script.json', {})
    notes = read_json(directory/'review_feedback.json', {})
    draft = deepcopy(original)
    draft.pop('prompt_h3_of', None)
    source_event_request(directory,draft,script)
    problems, conflicts = [], []
    instruction = (
        'Recompile from the supplied authored shots, source excerpts, native dialogue slots and reference images. '
        'The following is a diagnostic of a REJECTED GENERATED REQUEST OR TAKE, not an additional source constraint. '
        'Quoted faulty English is evidence of what to replace, not text to preserve. Resolve translation errors '
        '(including pronouns) using the original speaker, performance cue and reference identity. Preserve the '
        'authored action, dialogue and timing. Imported English frames may repeat cast-appearance boilerplate '
        'before the actual event. For stable visual identity and visible design, inherit the supplied character '
        'reference instead of repeating an incompatible appearance adjective from that boilerplate. This does '
        'not authorize changing a source-authored grooming, clothing, wearing or physical action, character '
        'identity, visible cast, or plot-significant design requirement. Report conflicts only if those '
        'authoritative inputs conflict, '
        'not merely because the rejected output contradicted them.\nRejected output diagnostic:\n' + issue)
    changed = convert(draft, note=instruction, episode_dir=directory, source_script=script,
                      problems=problems, source_conflicts=conflicts)
    if conflicts or problems or not changed:
        raise ValueError('authored request compilation unresolved: '+ '; '.join(conflicts or problems or ['no new request']))
    validate_prompt(original, draft['prompt_h3'])
    # The compiler's temporary director note is not a screenplay change. Only
    # its checked English result is adopted, under the original source stamp.
    candidate = deepcopy(plan)
    target = next(c for c in candidate['clips'] if c['clip_id'] == clip_id)
    target['prompt_h3'] = draft['prompt_h3']
    target['prompt_h3_of'] = h3_stamp(target, notes.get(clip_id, ''))
    target['authored_request_repair'] = {'policy': POLICY, 'issue': issue}
    check = request_consistency(target, directory.parent)
    evidence = {'clip_id': clip_id, 'policy': POLICY, 'candidate': target, 'check': check}
    atomic_write_json(directory/'work/request_checks'/f'{clip_id}.authored-recompile.json', evidence)
    if not check.get('consistent') or check.get('problems'):
        raise ValueError('authored request still conflicts: '+ '; '.join(check.get('problems') or ['unresolved check']))
    if target['prompt_h3'] == original['prompt_h3']:
        raise ValueError('authored request made no effective correction')
    proposal = RepairProposal(result={'changed': [clip_id], 'why': issue}, script=script,
                              plan=candidate, notes=notes, changes={clip_id: {'policy': POLICY, 'issue': issue}})
    return {'changed': [clip_id], 'proposal': proposal.payload}, [check]
