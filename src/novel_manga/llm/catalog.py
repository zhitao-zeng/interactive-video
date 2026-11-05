"""Explicit resource facts over existing presets; never store credential values."""
from __future__ import annotations
import copy
import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parents[3] / 'configs/model-resources.json'


@lru_cache(maxsize=1)
def catalogue():
    return json.loads(PATH.read_text())


def resource(name):
    return copy.deepcopy(catalogue()['resources'].get(name, {}))


def identify(model, base_url=None):
    base = str(base_url or '').rstrip('/').removesuffix('/v1')
    for name, row in catalogue()['resources'].items():
        if row.get('model') != model:
            continue
        if base and str(row.get('base_url', '')).rstrip('/').removesuffix('/v1') != base:
            continue
        return name, copy.deepcopy(row)
    return None, {}


def choose(candidates, capability, *, explicit=None, rows=None):
    """Prefer qualified available no-per-call-fee resources; never override a task choice."""
    rows = catalogue()['resources'] if rows is None else rows
    if explicit is not None:
        return explicit
    eligible = [(i, name) for i, name in enumerate(candidates)
                if rows.get(name, {}).get('available') is True
                and capability in rows.get(name, {}).get('qualified', [])]
    no_fee = {'free', 'subscription', 'local'}
    eligible.sort(key=lambda item: (rows[item[1]].get('billing') not in no_fee, item[0]))
    return eligible[0][1] if eligible else None


def review_defaults(preferred):
    """Keep known image capability separate from text and opt-in native-video review."""
    row = resource(preferred)
    if not row:
        return preferred, preferred  # Existing custom QA presets keep their configured contract.
    capabilities = row.get('qualified', [])
    vision = preferred if 'vision' in capabilities else choose(catalogue()['candidates']['vision'], 'vision')
    video = preferred if 'video' in capabilities else ''
    return vision, video


def quota_group(name, environment=None):
    row = resource(name)
    group = row.get('quota_group')
    members = catalogue()['quota_groups'].get(group, {}).get('key_vars', [])
    if environment and members:
        var = row.get('key_var')
        own, canonical = environment.get(var), environment.get(members[0])
        if own and canonical and own != canonical:
            return f'{group}--{var.lower()}'
    return group


def select_agent(config, *, explicit=False, resumed=False):
    if explicit or resumed or config.get('_agent_config_explicit'):
        return copy.deepcopy(config)
    current, _ = identify(config.get('model'), config.get('base_url'))
    # A caller's custom route is already a choice, even without a public preset.
    if current != 'phanthy-ds41':
        return copy.deepcopy(config)
    selected = choose(catalogue()['candidates']['agent'], 'agent')
    if not selected:
        return copy.deepcopy(config)
    row = resource(selected)
    result = {**config, **{k: row[k] for k in ('model', 'base_url', 'key_var') if k in row},
              'resource_id': selected, 'resource_billing': row['billing']}
    fallback = result.get('refusal_fallback')
    saved_login = row.get('base_url') == 'codex://local'
    if saved_login or not fallback or fallback.get('endpoint') == 'phanthy-luna':
        # Persist disabled fallback so resume cannot restore the old template's API alias.
        result['refusal_fallback'] = None
    if saved_login:
        result.update(backend='codex', key_var='H3_PROMPT_NO_KEY', provider='codex-saved-login',
                      codex_parallel=2, codex_slots_reserved_for_tools=1,
                      codex_inflight_dir='/mnt/disk1/zengzhitao/tmp/model-inflight/codex-saved-login')
    return result


def inventory(environment=None):
    """Missing balances remain unknown; equality is evaluated in memory, without hashes."""
    environment = environment or {}
    rows = catalogue()['resources']
    variables = []
    for row in rows.values():
        var = row.get('key_var')
        if var and var not in variables and environment.get(var):
            variables.append(var)
    groups = []
    for var in variables:
        group = next((g for g in groups if environment[g[0]] == environment[var]), None)
        if group is None:
            groups.append([var])
        else:
            group.append(var)
    visible = copy.deepcopy(rows)
    for name, row in visible.items():
        row['effective_quota_group'] = quota_group(name, environment)
        row['configured_available'] = row.pop('available', False)
    return {'resources': visible, 'quota_groups': copy.deepcopy(catalogue()['quota_groups']),
            'same_credentials': groups, 'role_modes': dict(catalogue()['role_modes']),
            'account_balance': 'unknown unless an authenticated account query exposes it',
            'live_health': 'not queried by this read-only report; configured availability is not a live balance'}
