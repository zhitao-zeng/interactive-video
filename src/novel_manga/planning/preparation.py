"""Pre-render audit schemas, original-source and authored-field grounding rules."""
from __future__ import annotations

from novel_manga.media.evidence_backends import normalize_text

POLICY = 'h3-book-preparation-v5-source-wear-bindings'
TERMINAL = {'ready', 'needs_source', 'needs_replan', 'needs_repair', 'error', 'existing_video', 'production_owned'}


def source_segment_text(segment_id, segments):
    """The program, rather than the reviewer, supplies literal source evidence."""
    return next((s['text'] for s in segments if s.get('segment_id') == segment_id), '')

def needs_full_replan(issues):
    return any(i.get('stage') == 0 for i in issues)


AUTHORED_REVIEW_POLICY = 'authored-creative-review-v18-opening-continuity-omission'
CREATIVE_TIMES = {'t=0', 'during', 't=end', 'whole_shot'}
CREATIVE_RELATIONS = {'contradiction', 'underspecified', 'compatible'}
CREATIVE_EVIDENCE_ORIGINS = ('original_text', 'authored_field')


def audit_schema(*, creative=False):
    from novel_manga.llm.client import obj
    properties = {'kind': {'type': 'string', 'enum': ['speaker', 'action', 'missing_event', 'location', 'identity']},
                  'stage': {'type': 'integer', 'minimum': 0}, 'source_quote': {'type': 'string', 'enum': ['']},
                  'source_segment': {'type': 'string'},
                  'problem': {'type': 'string'}, 'correction': {'type': 'string'}}
    if not creative:
        return obj({'source_readable': {'type': 'boolean'}, 'source_problem': {'type': 'string'},
                    'issues': {'type': 'array', 'maxItems': 12, 'items': obj(properties)}})
    required = list(properties) + ['evidence_origin']
    properties.update({
        'evidence_origin': {'type': 'string', 'enum': list(CREATIVE_EVIDENCE_ORIGINS)},
        'basis': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'string'}},
        'observed': {'type': 'string', 'enum': ['']}, 'expected': {'type': 'string', 'enum': ['']},
        'observed_time': {'type': 'string', 'enum': sorted(CREATIVE_TIMES)},
        'expected_time': {'type': 'string', 'enum': sorted(CREATIVE_TIMES)},
        'relation': {'type': 'string', 'enum': sorted(CREATIVE_RELATIONS)}})
    authored = {**properties,
                'kind': {'type': 'string', 'enum': [*properties['kind']['enum'], 'continuity_omission']},
                'evidence_origin': {'type': 'string', 'enum': ['authored_field']},
                'source_segment': {'type': 'string', 'enum': ['']},
                'observed': {'type': 'string', 'enum': ['']},
                'expected': {'type': 'string', 'enum': ['']}}
    original = {**properties,
                'evidence_origin': {'type': 'string', 'enum': ['original_text']},
                'source_segment': {'type': 'string', 'minLength': 1}}
    # Strict structured endpoints require every property to be present. Missing
    # optional field evidence is represented by null, then removed by grounding.
    for key in set(properties) - set(required):
        original[key] = {'anyOf': [properties[key], {'type': 'null'}]}
    return obj({'source_readable': {'type': 'boolean'}, 'source_problem': {'type': 'string'},
                'issues': {'type': 'array',
                           'items': {'anyOf': [obj(authored), obj(original)]}}})


def grounded_issues(answer, script, segments):
    """An invented citation or stage cannot authorize a rewrite or a clean pass."""
    passage = normalize_text('\n'.join(s['text'] for s in segments))
    indexes = {s.get('index', i) for i, s in enumerate(script.get('shots', []), 1)}
    for issue in answer['issues']:
        if not issue['source_quote'].strip():
            issue['source_quote'] = source_segment_text(issue.get('source_segment'), segments)
        quote = normalize_text(issue['source_quote'])
        if len(quote) < 6 or quote not in passage:
            raise ValueError('text audit supplied an ungrounded source quotation')
        if issue['stage'] not in indexes and not (issue['stage'] == 0 and issue['kind'] == 'missing_event'):
            raise ValueError('text audit supplied an unknown stage')
    return answer['issues']


def _creative_path_parts(path):
    import re
    if not isinstance(path, str) or not re.fullmatch(
            r'[^.[\]]+(?:\[\d+\])?(?:\.[^.[\]]+(?:\[\d+\])?)*', path):
        raise ValueError('creative finding has an invalid evidence path: '+str(path))
    parts = []
    for component in path.split('.'):
        match = re.fullmatch(r'([^[\]]+)((?:\[\d+\])*)', component)
        parts.append(match[1])
        parts.extend(int(index) for index in re.findall(r'\[(\d+)\]', match[2]))
    return parts


def _creative_evidence(episode, path):
    value = episode
    for part in _creative_path_parts(path):
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                raise ValueError('creative finding evidence path does not exist: '+path)
            value = value[part]
        else:
            if not isinstance(value, dict) or part not in value:
                raise ValueError('creative finding evidence path does not exist: '+path)
            value = value[part]
    return value


def _contains_literal(value, quote):
    if isinstance(value, str):
        return quote in value
    if isinstance(value, list):
        return any(_contains_literal(row, quote) for row in value)
    if isinstance(value, dict):
        return any(_contains_literal(row, quote) for row in value.values())
    return False


def _creative_required_time(path):
    parts = _creative_path_parts(path)
    if len(parts) < 4 or parts[:2] != ['chapter_script', 'shots'] or not isinstance(parts[2], int):
        return None
    field = parts[3]
    if field in {'start_state', 'opening_frame'}:
        return 't=0'
    if field == 'storyboard' and len(parts) > 4 and parts[4] == 'frame':
        return 't=0'
    if field in {'event', 'action_beats'}:
        return 'during'
    if field == 'storyboard' and len(parts) > 4 and parts[4] == 'action_beats':
        return 'during'
    if field == 'end_state':
        return 't=end'
    return None


def ground_creative_finding(issue: dict, episode: dict) -> None:
    """Bind episode text fields and time/stage; original-source citations are separate."""
    if not isinstance(issue, dict):
        raise ValueError('creative finding must be an object')
    if issue.get('evidence_origin') not in CREATIVE_EVIDENCE_ORIGINS:
        raise ValueError('creative finding has an invalid evidence_origin')
    basis = issue.get('basis')
    if not isinstance(basis, list) or len(basis) != 2:
        raise ValueError('creative finding requires two evidence paths')
    # The model sees an {"episode": ...} request envelope. Both its rooted
    # paths and the documented relative paths refer to the same source object.
    basis = issue['basis'] = [path.removeprefix('episode.') if isinstance(path, str) else path
                             for path in basis]
    values = [_creative_evidence(episode, path) for path in basis]
    for path, value in zip(basis, values):
        if not isinstance(value, str):
            raise ValueError('creative finding evidence path must address a leaf text field: '+path)
    if any(not value.strip() for value in values):
        raise ValueError('creative finding evidence path must address nonempty leaf text')
    if 'relation' in issue and issue['relation'] not in CREATIVE_RELATIONS:
        raise ValueError('creative finding has an invalid relation')
    if issue.get('observed_time') not in CREATIVE_TIMES or issue.get('expected_time') not in CREATIVE_TIMES:
        raise ValueError('creative finding has an invalid evidence time')
    for path, key in zip(basis, ('observed_time', 'expected_time')):
        required = _creative_required_time(path)
        if required is not None and issue[key] != required:
            raise ValueError('creative finding evidence time does not match evidence path: '+path)
    shots = (episode.get('chapter_script') or {}).get('shots') or []
    stage = issue.get('stage')
    indexes = {shot.get('index', i) for i, shot in enumerate(shots, 1)}
    if type(stage) is not int or stage not in indexes:
        raise ValueError('creative finding has an unknown stage')
    import re
    stage_paths = [path for path in basis if isinstance(path, str)
                   and re.match(r'^chapter_script\.shots\[(\d+)\](?:\.|$)', path)]
    if not any(shots[int(re.match(r'^chapter_script\.shots\[(\d+)\]', path)[1])].get('index') == stage
               for path in stage_paths):
        raise ValueError('creative finding stage does not match its evidence path')
    if issue.get('kind') == 'continuity_omission':
        if issue.get('evidence_origin') != 'authored_field' or issue.get('relation') != 'underspecified':
            raise ValueError('continuity omission requires underspecified authored-field evidence')
        current, previous = (_creative_path_parts(path) for path in basis)
        current_opening = (len(current) == 4 and current[3] == 'start_state'
                           or len(current) == 5 and current[3:] == ['opening_frame', 'image_prompt'])
        previous_state = len(previous) == 4 and previous[3] in {'start_state', 'end_state'}
        if (not current_opening or not previous_state
                or current[:2] != ['chapter_script', 'shots'] or previous[:2] != ['chapter_script', 'shots']
                or not isinstance(current[2], int) or not isinstance(previous[2], int)
                or current[2] == 0 or previous[2] != current[2] - 1):
            raise ValueError('continuity omission requires current opening and immediately preceding shot state')
        before, after = shots[previous[2]], shots[current[2]]
        if not before.get('scene_id') or before.get('scene_id') != after.get('scene_id'):
            raise ValueError('continuity omission requires two shots in the same scene')
    issue['observed'],issue['expected']=values
