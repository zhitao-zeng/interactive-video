from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
import json

from clean_studio.compiler import script_hash
from clean_studio.domain import Dialogue, EpisodeSpec, Shot

_PATCHABLE_FIELDS = frozenset(
    {
        "scene_id",
        "source_segments",
        "location",
        "start_state",
        "action",
        "end_state",
        "camera",
        "shot_scale",
        "visible_characters",
        "opening_characters",
        "dialogue",
        "reference_subjects",
        "duration_seconds",
    }
)
_STRING_FIELDS = frozenset(
    {"scene_id", "location", "start_state", "action", "end_state", "camera", "shot_scale"}
)
_ID_SEQUENCE_FIELDS = frozenset(
    {"source_segments", "visible_characters", "opening_characters", "reference_subjects"}
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: object):
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _patch_value(field: str, value: object):
    if field in _STRING_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"patch field {field} requires a string")
        return value
    if field == "duration_seconds":
        if type(value) is not int:
            raise ValueError("patch field duration_seconds requires an integer")
        return value
    if field in _ID_SEQUENCE_FIELDS:
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError(f"patch field {field} requires an array of subject/segment IDs")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"patch field {field} requires nonempty string IDs")
        return list(value)
    if field == "dialogue":
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError("patch field dialogue requires an array")
        dialogue = []
        for line in value:
            if isinstance(line, Dialogue):
                line = asdict(line)
            if not isinstance(line, Mapping) or set(line) != {"speaker", "line"}:
                raise ValueError("dialogue entries require exactly speaker and line")
            if any(not isinstance(line[key], str) for key in ("speaker", "line")):
                raise ValueError("dialogue speaker and line must be strings")
            dialogue.append({"speaker": line["speaker"], "line": line["line"]})
        return dialogue
    raise ValueError(f"author-owned fields only; unsupported patch field: {field}")


def _domain_value(field: str, value: object):
    if field in _ID_SEQUENCE_FIELDS:
        return tuple(value)
    if field == "dialogue":
        return tuple(Dialogue(**line) for line in value)
    return value


@dataclass(frozen=True)
class PatchPlan:
    expected_script_hash: str
    input_hash: str
    affected_shot_ids: frozenset[str]
    patch_json: str
    grant_json: str

    @property
    def patch(self) -> dict:
        return json.loads(self.patch_json)

    @property
    def grant(self) -> dict:
        return json.loads(self.grant_json)

    @property
    def affected_ids(self) -> frozenset[str]:
        return self.affected_shot_ids


def check_patch(
    episode: EpisodeSpec,
    patch: Mapping[str, Mapping[str, object]],
    grant: Mapping[str, object],
) -> PatchPlan:
    if not isinstance(patch, Mapping) or not isinstance(grant, Mapping):
        raise ValueError("patch and grant must map shot IDs to field scopes")
    shots = {shot.shot_id: shot for shot in episode.shots}
    for shot_id in grant:
        if shot_id not in shots:
            raise ValueError(f"unknown shot in grant: {shot_id}")

    normalized_grant = {}
    for shot_id, raw_fields in grant.items():
        fields = {raw_fields} if isinstance(raw_fields, str) else raw_fields
        if isinstance(fields, (bytes, str)) or not isinstance(fields, (set, frozenset, list, tuple)):
            raise ValueError("grant fields must be a string or an array/set of strings")
        fields = set(fields)
        if any(not isinstance(field, str) for field in fields):
            raise ValueError("grant fields must be strings")
        unsupported = fields.difference(_PATCHABLE_FIELDS)
        if unsupported:
            raise ValueError(
                f"author-owned fields only; unsupported grant fields: {sorted(unsupported)}"
            )
        normalized_grant[shot_id] = sorted(fields)

    normalized_patch = {}
    affected = set()
    for shot_id, raw_changes in patch.items():
        if shot_id not in shots:
            raise ValueError(f"unknown shot in patch: {shot_id}")
        if shot_id not in normalized_grant:
            raise ValueError(f"patch shot is outside the grant: {shot_id}")
        if not isinstance(raw_changes, Mapping) or not raw_changes:
            raise ValueError("patch must map each shot ID to a nonempty field object")
        permitted = set(normalized_grant[shot_id])
        changes = {}
        for field, value in raw_changes.items():
            if field not in _PATCHABLE_FIELDS:
                raise ValueError(
                    f"author-owned fields only; unsupported patch field: {field}"
                )
            if field not in permitted:
                raise ValueError(f"patch field is outside the grant: {shot_id}.{field}")
            normalized = _patch_value(field, value)
            changes[field] = normalized
            if normalized != _json_value(getattr(shots[shot_id], field)):
                affected.add(shot_id)
        normalized_patch[shot_id] = changes
    if not affected:
        raise ValueError("patch has no changes (no-op revision)")

    expected = script_hash(episode)
    patch_json = _canonical(normalized_patch)
    grant_json = _canonical(normalized_grant)
    input_hash = script_input_hash(expected, normalized_patch, normalized_grant)
    return PatchPlan(
        expected_script_hash=expected,
        input_hash=input_hash,
        affected_shot_ids=frozenset(affected),
        patch_json=patch_json,
        grant_json=grant_json,
    )


def script_input_hash(expected_script_hash: str, patch: object, grant: object) -> str:
    from hashlib import sha256

    return sha256(
        _canonical(
            {
                "expected_script_hash": expected_script_hash,
                "patch": patch,
                "grant": grant,
            }
        ).encode("utf-8")
    ).hexdigest()


def apply_patch(
    episode: EpisodeSpec, plan: PatchPlan
) -> tuple[EpisodeSpec, set[str]]:
    if script_hash(episode) != plan.expected_script_hash:
        raise ValueError("expected_script_hash does not match current episode")
    checked = check_patch(episode, plan.patch, plan.grant)
    if checked.input_hash != plan.input_hash:
        raise ValueError("patch plan identity does not match its contents")

    replacements = {}
    for shot_id, changes in checked.patch.items():
        if shot_id not in checked.affected_shot_ids:
            continue
        old = next(shot for shot in episode.shots if shot.shot_id == shot_id)
        replacements[shot_id] = replace(
            old,
            **{
                field: _domain_value(field, value)
                for field, value in changes.items()
            },
        )
    revised = replace(
        episode,
        shots=tuple(
            replacements.get(shot.shot_id, shot) for shot in episode.shots
        ),
    )
    actual_affected = {
        old.shot_id
        for old, new in zip(episode.shots, revised.shots)
        if old != new
    }
    if actual_affected != set(checked.affected_shot_ids):
        raise ValueError("patch plan affected shot IDs do not match actual changes")
    return revised, actual_affected
