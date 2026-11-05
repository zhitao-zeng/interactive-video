"""The existing scoped input-repair argument contract."""
from __future__ import annotations
import copy
from dataclasses import dataclass
import hashlib
import json
from .arguments import RepairArgumentError, validate_resume
from novel_manga.story.fields import ACTION_KEYS
from novel_manga.story.field_validation import action_strings, wears_values, patch_turn_values, delivery_issue
from .input_records import _phase_groups
from novel_manga.application.identity.phases import LOOK_FIELDS

SHOT_NAMES = {'in_frame', 'extras', 'listeners'}
SHOT_FIELDS = {'location', 'light', 'camera', 'shot_scale', 'scene_id', 'scene_time', 'scene_transition',
               'wears', 'props', 'scene_objects', 'visual_prompt', 'motion_prompt',
               'start_state', 'event', 'end_state', 'sfx', 'actions', 'speaker_map', 'turn_texts', 'turns',
               'opening_frame', 'device_state', 'audio_plan', 'storyboard', *SHOT_NAMES}


@dataclass(frozen=True)
class RepairGrant:
    operation: str
    shot_indexes: tuple
    shot_fields: frozenset
    evidence: dict

    def __post_init__(self):
        object.__setattr__(self,'shot_indexes',tuple(self.shot_indexes))
        object.__setattr__(self,'shot_fields',frozenset(self.shot_fields))

    def as_dict(self):
        return {'operation':self.operation,'shot_indexes':list(self.shot_indexes),
                'shot_fields':sorted(self.shot_fields),'evidence':copy.deepcopy(self.evidence)}


def evidence_fingerprint(value):
    payload=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def operation_grant(operation,clip,evidence=None):
    evidence=copy.deepcopy(evidence or {})
    evidence.setdefault('fingerprint',evidence_fingerprint(evidence))
    indexes=clip.get('shot_indexes') or []
    return RepairGrant(operation,tuple(indexes[:1]),frozenset(),evidence)


def opening_grant(clip,findings,evidence=None):
    selected=[f for f in findings if f.get('origin')=='source' and (
        f.get('relation')=='contradiction' or
        (f.get('kind')=='continuity_clarification' and f.get('relation')=='underspecified'
         and f.get('continuity_verified') is True))]
    if not selected:return None
    fields=set();basis=set()
    for finding in selected:
        if finding.get('kind')=='continuity_clarification':
            # The author may clarify this opening and its corresponding source
            # start; the historical shot remains outside this first-shot grant.
            fields.update({'opening_frame','start_state'})
        for value in finding.get('basis') or []:
            parts=value.split('.')
            if parts and parts[0]=='opening':parts=parts[1:]
            if parts and parts[0]=='opening':parts=parts[1:]
            basis.add(value)
            if not parts:continue
            if parts[0]=='opening_frame' and len(parts)>1:fields.add('opening_frame')
            elif parts[0]=='storyboard' and len(parts)>1 and parts[1]=='frame':fields.add('storyboard')
            elif len(parts)==1 and parts[0] in {'camera','shot_scale','device_state'}:fields.add(parts[0])
    if not fields:
        raise RepairArgumentError('opening findings have no minimally authorized source field mapping: '+sorted(basis).__repr__())
    evidence=copy.deepcopy(evidence or {})
    evidence.update(origin='source',basis=sorted(basis))
    evidence.setdefault('fingerprint',evidence_fingerprint(evidence))
    indexes=clip.get('shot_indexes') or []
    return RepairGrant('input',tuple(indexes[:1]),frozenset(fields),evidence)


def source_patch_fields(row):
    containers={'shots','phases','locations','characters','music_plan'}
    return set(row)&(containers|SHOT_FIELDS)


def validate_grant(row,grant):
    if grant is None:return
    patches=row.get('shots') or []
    if grant.operation!='input':
        if source_patch_fields(row):
            raise RepairArgumentError(f'{grant.operation} does not accept source patch fields')
        return
    frame_storyboard_grant=('storyboard' in grant.shot_fields and any(
        isinstance(value,str) and value.split('.')[-2:]==['storyboard','frame']
        for value in (grant.evidence or {}).get('basis') or []))
    for patch in patches:
        index=patch.get('index')
        if index not in grant.shot_indexes:
            raise RepairArgumentError(f'input shot index {index} is outside the authorized grant: {list(grant.shot_indexes)}')
        extra=set(patch)-{'index'}-set(grant.shot_fields)
        if extra:
            raise RepairArgumentError(f'input fields outside the authorized grant: {sorted(extra)}')
        if frame_storyboard_grant and 'storyboard' in patch:
            siblings=set(patch['storyboard'])-{'frame'}
            if siblings:
                raise RepairArgumentError('storyboard.frame grant accepts only the frame member; '
                                          'omit sibling fields: '+sorted(siblings).__repr__())
    if set(row)&{'phases','locations','characters','music_plan'}:
        raise RepairArgumentError('top-level source patches are outside the authorized opening grant')


def expand_shared_scope(row, plan):
    scope = row.get('clip_ids')
    if scope is None:
        return row
    addresses = {i for c in plan['clips'] if c['clip_id'] in scope for i in c.get('shot_indexes', [])}
    touched = {p['index'] for p in row.get('shots') or []} & addresses
    shared = [c['clip_id'] for c in plan['clips'] if touched.intersection(c.get('shot_indexes', []))]
    return {**row, 'clip_ids': list(dict.fromkeys([*scope, *shared]))}


def normalize_arguments(row):
    """Reject malformed interface values before receipts, models or asset work."""
    validate_resume(row)
    allowed = {'clip_id','action','reason','source_quote','clip_ids','shots','phases','locations','characters','resume','music_plan'}
    unknown = set(row)-allowed
    if 'input' in unknown:
        raise RepairArgumentError("unsupported 'input' wrapper; put speaker_map and other source patch fields in "
                                  "shots:[{index: <current shot index>, ...}] with no nested input object")
    if unknown:
        raise RepairArgumentError(f'unsupported input fields: {sorted(unknown)}; '
                                  'use shots, characters, phases.characters and locations for adopted input repairs')
    row = copy.deepcopy(row)
    if 'music_plan' in row and not isinstance(row['music_plan'],dict):
        raise RepairArgumentError('music_plan must be an authored postmix plan object')
    scope = row.get('clip_ids')
    if scope is not None and (not isinstance(scope, list) or not scope
            or any(not isinstance(key, str) or not key.strip() for key in scope)):
        raise RepairArgumentError('clip_ids must be a nonempty list of clip ID strings, including the target clip')
    patches = row.get('shots')
    if patches is not None and not isinstance(patches, list):
        raise RepairArgumentError('shots must be a list of patch objects with index and supported source fields')
    for number, patch in enumerate(patches or []):
        path = f'shots[{number}]'
        if not isinstance(patch, dict) or type(patch.get('index')) is not int:
            raise RepairArgumentError(f'{path} must be an object with an integer index')
        if extra := set(patch)-{'index'}-SHOT_FIELDS:
            raise RepairArgumentError(f'{path} unsupported fields {sorted(extra)}; allowed: {sorted(SHOT_FIELDS)}')
        for field, value in patch.items():
            if field=='audio_plan':
                if (not isinstance(value,dict) or not value
                        or set(value)-{'music_cue','entry','withdrawal'}
                        or any(v is not None and not isinstance(v,str) for v in value.values())):
                    raise RepairArgumentError(f'{path}.audio_plan supports postmix music_cue/entry/withdrawal text or null only; native sound and voice fields are not changed here')
            elif field=='opening_frame':
                from novel_manga.story.fields import opening_frame_error
                if issue:=opening_frame_error(value):raise RepairArgumentError(issue)
            elif field=='storyboard':
                if not isinstance(value,dict) or not isinstance(value.get('frame'),str):
                    raise RepairArgumentError(f'{path}.storyboard must be a complete object with text frame')
            elif field == 'turns':
                from novel_manga.planning.constants import DELIVERY_MODES
                if not isinstance(value,list):
                    raise RepairArgumentError(f'{path}.turns must be an ordered list of source-correct turns')
                for turn in value:
                    if not patch_turn_values(turn, DELIVERY_MODES):
                        raise RepairArgumentError(f'{path}.turns needs speaker_name, delivery_mode and nonempty text')
                    if issue := delivery_issue(turn):
                        raise RepairArgumentError(issue)
            elif field == 'turn_texts':
                if not isinstance(value, list) or not value or any(not isinstance(t, str) or not t.strip() for t in value):
                    raise RepairArgumentError(f'{path}.turn_texts must list each shortened complete spoken turn in original order')
            elif field == 'speaker_map':
                if not isinstance(value, dict) or not value or any(
                        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                        for k, v in value.items()):
                    raise RepairArgumentError(f'{path}.speaker_map must map existing speech owners to source-correct names')
            elif field == 'actions':
                if not isinstance(value, list) or any(not action_strings(v) or set(v) != set(ACTION_KEYS) for v in value):
                    raise RepairArgumentError(f'{path}.actions must contain actor/action/target text objects')
            elif field in SHOT_NAMES:
                if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                    raise RepairArgumentError(f'{path}.{field} must be a list of nonempty names')
            elif field == 'wears':
                if value is not None and not wears_values(value):
                    raise RepairArgumentError(f'{path}.wears must map character names to worn prop names, '
                        'for example {"Character":"Armor"}; use {} or null to clear. '
                        'Put ordinary clothing in phases.characters.<name>.base_costume and body traits in appearance.')
            elif field in {'props','scene_objects'}:
                if value is not None and (not isinstance(value,list) or any(not isinstance(v,str) for v in value)):
                    raise RepairArgumentError(f'{path}.{field} must be a list of names; use [] or null to clear')
            elif field != 'index' and not isinstance(value, str):
                raise RepairArgumentError(f'{path}.{field} must be text; use an empty string to clear it')
    phases = row.get('phases')
    if phases is not None:
        if not isinstance(phases,dict) or set(phases)-{'characters'} or not isinstance(phases.get('characters',{}),dict):
            raise RepairArgumentError('phases must contain a characters object; put scene descriptions in locations')
        row['phases'] = _phase_groups(row)
        for name, entries in row['phases'].get('characters',{}).items():
            if not isinstance(entries,list) or len(entries)!=1 or not isinstance(entries[0],dict):
                raise RepairArgumentError(f'phases.characters.{name} must be one phase object or a one-item list of phase objects')
            phase = entries[0]
            if extra := set(phase)-set(LOOK_FIELDS)-{'from','to','asset_id','label','wears','closed','reuse_asset_id'}:
                raise RepairArgumentError(f'phases.characters.{name} unsupported appearance fields: {sorted(extra)}')
            if 'reuse_asset_id' in phase:
                if (not isinstance(phase['reuse_asset_id'], str) or not phase['reuse_asset_id'].strip()
                        or set(phase)-{'reuse_asset_id','from','to'}):
                    raise RepairArgumentError(f'phases.characters.{name}.reuse_asset_id must select an existing '
                        'state without new appearance/clothing fields; reuse its saved design or describe a new state')
            if phase.get('wears') is not None and not isinstance(phase['wears'],str):
                raise RepairArgumentError(f'phases.characters.{name}.wears must be a registered worn prop name')
    locations = row.get('locations')
    if locations is not None and (not isinstance(locations,dict) or any(
            not isinstance(name,str) or not (isinstance(value,str) or
                isinstance(value,dict) and set(value)=={'reuse_asset_id'} and
                isinstance(value['reuse_asset_id'],str) and value['reuse_asset_id'].strip())
            for name,value in locations.items())):
        raise RepairArgumentError('locations must map selected location names to empty-setting descriptions')
    if 'characters' in row:
        from novel_manga.models.bible import Character
        characters = row['characters']
        if not isinstance(characters, dict) or not characters:
            raise RepairArgumentError('characters must map missing source actors to appearance/wardrobe designs')
        for name, design in characters.items():
            if (not isinstance(name, str) or not name.strip() or not isinstance(design, dict)
                    or set(design)-{'gender', 'age', 'appearance', 'wardrobe', 'visual_archetype'}):
                raise RepairArgumentError('characters must describe named source actors using gender, age, appearance, wardrobe, visual_archetype')
            try:
                Character(name=name, **design)
            except ValueError as error:
                raise RepairArgumentError(f'characters.{name}: {error}') from error
        if set(characters) & set((row.get('phases') or {}).get('characters', {})):
            raise RepairArgumentError('request a missing character with its intended design, or an existing phase, not both')
    return row
