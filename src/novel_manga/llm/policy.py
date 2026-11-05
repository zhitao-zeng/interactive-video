"""Task reasoning is independent of provider choice; old sessions keep their policy."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from .catalog import catalogue, identify

POLICY = 'task-reasoning-v1'


def role_for(name):
    if name in {'episode_first_frame','character_card','location_card','card_look','lookalike','repair_reference_view'}:
        return 'review'
    if name in {'episode_source_draft', 'chapter_parts', 'chapter_summary', 'volume_summary'}:
        return 'author'
    if 'translation' in name or name == 'h3prompt':
        return 'translation'
    if any(word in name for word in ('review', 'verify', 'judge', 'audit', 'vision', 'picture')):
        return 'review'
    if name in {'clip_repair','modrepair','repair_source_appearance'} or 'repair_cause' in name or name == 'upstream_asset_diagnosis':
        return 'repair'
    return 'check'


def modes(model, role, *, base_url=None, requested=None):
    _, row = identify(model, base_url)
    requested = requested or catalogue()['role_modes'].get(role, 'low')
    if requested not in {'off', 'low', 'medium', 'high'}:
        raise ValueError(f'unsupported reasoning mode: {requested}')
    # Luna and the older free aliases have no verified off switch. Report the
    # supported minimum rather than falsely claiming disabled reasoning.
    switch = row.get('thinking_switch', False) and 'luna' not in model.lower()
    effective = 'low' if requested == 'off' and not switch else requested
    return requested, effective, switch


def agent_policy(config, role):
    result = copy.deepcopy(config)
    model = result.get('model', '')
    _, row = identify(model, result.get('base_url') if result.get('backend') != 'codex' else 'codex://local')
    if not row and 'luna' not in model.lower():
        return result
    requested, effective, switch = modes(model, role, base_url=row.get('base_url'),
                                          requested=(result.get('task_reasoning') or {}).get(role))
    body = dict(result.get('extra_body') or {})
    # These are provider controls, not portable task policy. Rebuild them after
    # any DS -> Luna switch instead of inheriting the disabled flag.
    body.pop('thinking', None)
    body.pop('reasoning_effort', None)
    result.pop('max_thinking_tokens', None)
    effort = 'low' if effective == 'off' else effective
    body['output_config'] = {**(body.get('output_config') or {}), 'effort': effort}
    if effective == 'off' and switch:
        body['thinking'] = {'type': 'disabled'}
        result['max_thinking_tokens'] = 0
    result.update(effort=effort, extra_body=body,
                  reasoning_policy={'policy': POLICY, 'role': role, 'requested': requested,
                                    'effective': effective, 'provider_enforcement': 'not guaranteed'})
    return result


def json_policy(model, role):
    _, supported = identify(model)
    if not supported and 'luna' not in model.lower():
        return {}  # Preserve existing controls for explicit, unregistered providers.
    _, effective, switch = modes(model, role)
    result = {'reasoning_effort': 'low' if effective == 'off' else effective}
    if switch:
        result['thinking'] = {'type': 'disabled' if effective == 'off' else 'enabled'}
    return result


def chat_options(model, role):
    controls = json_policy(model, role)
    effort = controls.pop('reasoning_effort', None)
    return {**({'reasoning_effort':effort} if effort else {}), 'extra_body': controls}


def session_role(work, config, expected_output=None):
    if config.get('task_role'):
        return config['task_role']
    try:
        manifest = json.loads((Path(work)/'input/manifest.json').read_text())
    except (OSError, ValueError):
        manifest = {}
    phase = manifest.get('phase') or manifest.get('agent_phase')
    if phase in {'creation_only', 'source_draft'}:
        return 'author'
    if phase == 'script_preparation':
        return 'translation'
    if phase in {'clip_repair', 'input_repair'} or expected_output in {'repair.json', 'decision.json'}:
        return 'repair'
    if phase in {'model_json', 'media_review'}:
        return 'review' if phase == 'media_review' else 'json'
    return 'agent'


def session_policy(config, work, *, resume=False, role=None):
    path = Path(work)/'task-reasoning.json'
    if resume and not path.is_file():
        return copy.deepcopy(config)  # Existing production sessions have no stamp.
    if path.is_file():
        saved = json.loads(path.read_text())
        role = saved['role']
        config = {**config, 'task_reasoning': {**(config.get('task_reasoning') or {}),
                                             role: saved['requested']}}
    result = agent_policy(config, role or session_role(work, config))
    stamp = result.get('reasoning_policy')
    if stamp and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stamp, ensure_ascii=False, indent=2)+'\n')
    return result
