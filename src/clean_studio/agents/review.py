from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from clean_studio.agents.authoring import source_text
from clean_studio.domain import Review
from clean_studio.source import validate_episode

_POLICY_VERSION = "clean-source-review-v3"
_TIME_VALUES = ["t=0", "during", "end", "whole_shot", "whole_episode"]
_COMMON_FIELDS = (
    "id",
    "category",
    "evidence_origin",
    "field_path",
    "quote",
    "time",
    "message",
)
_FINDING_FIELDS = {
    *_COMMON_FIELDS,
    "source_segment",
    "relation",
    "status",
}
_RAW_FINDING_FIELDS = _FINDING_FIELDS - {"status"}
_RELATION_FIELDS = {
    "type",
    "other_field_path",
    "other_quote",
    "other_time",
    "source_segment",
}
_RELATION_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "type": {"type": "string"},
        "other_field_path": {"type": ["string", "null"]},
        "other_quote": {"type": ["string", "null"]},
        "other_time": {"type": ["string", "null"], "enum": [*_TIME_VALUES, None]},
        "source_segment": {"type": ["string", "null"]},
    },
    "required": [
        "type",
        "other_field_path",
        "other_quote",
        "other_time",
        "source_segment",
    ],
    "additionalProperties": False,
}
_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "category": {"type": "string", "const": "contradiction"},
        "evidence_origin": {
            "type": "string",
            "enum": ["original_text", "authored_field"],
        },
        "field_path": {"type": "string"},
        "quote": {"type": "string"},
        "time": {"type": "string", "enum": _TIME_VALUES},
        "source_segment": {"type": ["string", "null"]},
        "relation": _RELATION_SCHEMA,
        "message": {"type": "string"},
    },
    "required": [*_COMMON_FIELDS, "source_segment", "relation"],
    "additionalProperties": False,
}
_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
    },
    "required": ["findings"],
    "additionalProperties": False,
}
_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "retract_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["retract_ids"],
    "additionalProperties": False,
}


class ReviewProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewSources:
    source: object
    references: object


def _project_root() -> Path:
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[3]
    ).resolve()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _copy_review(review: Review) -> Review:
    return Review(
        review_id=review.review_id,
        content_hash=review.content_hash,
        verdict=review.verdict,
        findings=deepcopy(review.findings),
        unresolved=deepcopy(review.unresolved),
    )


def _at_path(episode, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("field_path must be an absolute JSON pointer")
    current = asdict(episode)
    try:
        for raw_token in path.strip("/").split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, (list, tuple)):
                if not token.isdigit():
                    raise KeyError(token)
                current = current[int(token)]
            else:
                current = current[token]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError(f"field_path does not exist: {path}") from error
    return current


def _segment(sources, segment_id):
    for segment in sources.source.segments:
        if segment.segment_id == segment_id:
            return segment
    raise ValueError(f"source_segment does not exist: {segment_id}")


def _require_verbatim(value, quote, label):
    if not isinstance(quote, str) or not quote:
        raise ValueError(f"{label} quote must be a nonempty verbatim substring")
    if isinstance(value, (list, tuple)):
        if not any(isinstance(item, str) and quote in item for item in value):
            raise ValueError(f"{label} quote is not verbatim present")
        return
    if not isinstance(value, str) or quote not in value:
        raise ValueError(f"{label} quote is not verbatim present")


_ORIGINAL_SEGMENT_PATH = re.compile(r"^/shots/\d+/source_segments/\d+$")
_TEMPORAL_FIELD_PATH = re.compile(r"^/shots/\d+/(start_state|action|end_state)$")
_SHOT_VALUE_PATH = re.compile(r"^/shots/\d+/.+$")
_TEMPORAL_TIMES = {"start_state": "t=0", "action": "during", "end_state": "end"}


def _expected_time(path):
    temporal = _TEMPORAL_FIELD_PATH.fullmatch(path)
    if temporal is not None:
        return _TEMPORAL_TIMES[temporal.group(1)]
    if _SHOT_VALUE_PATH.fullmatch(path):
        return "whole_shot"
    return "whole_episode"


def _validate_time(path, value, label):
    expected = _expected_time(path)
    if value != expected:
        raise ValueError(f"{label} must be {expected} for {path}")


def _ground_relation(relation, episode, sources):
    if not isinstance(relation, Mapping):
        raise ValueError("relation must explicitly describe the compared evidence")
    relation = {key: value for key, value in relation.items() if value is not None}
    if set(relation).difference(_RELATION_FIELDS):
        raise ValueError("relation contains unsupported fields")
    if not isinstance(relation.get("type"), str) or not relation["type"].strip():
        raise ValueError("relation.type must be explicit")
    if relation.get("source_segment") is not None:
        _segment(sources, relation["source_segment"])
    other_fields = {"other_field_path", "other_quote", "other_time"}
    present = other_fields.intersection(relation)
    if present and present != other_fields:
        raise ValueError("relation other field path, quote and time are required together")
    if relation["type"] == "repeated_action" and present != other_fields:
        raise ValueError("repeated_action relation requires both field evidence")
    if present:
        other_value = _at_path(episode, relation["other_field_path"])
        _require_verbatim(other_value, relation["other_quote"], "relation")
        _validate_time(
            relation["other_field_path"], relation["other_time"], "relation.other_time"
        )


def ground_finding(finding, episode, sources) -> None:
    if not isinstance(finding, Mapping):
        raise ValueError("finding must be an object")
    finding = {key: value for key, value in finding.items() if value is not None}
    unsupported = set(finding).difference(_FINDING_FIELDS)
    if unsupported:
        raise ValueError(f"finding contains unsupported fields: {sorted(unsupported)}")
    for field in _COMMON_FIELDS:
        if field not in finding:
            raise ValueError(f"finding.{field} is required")
        if not isinstance(finding[field], str) or not finding[field].strip():
            raise ValueError(f"finding.{field} must be nonempty")
    if finding["category"] != "contradiction":
        raise ValueError("final findings must be contradictions")
    if finding.get("status", "candidate") not in {
        "candidate",
        "confirmed",
        "retracted",
    }:
        raise ValueError("finding.status is invalid")
    origin = finding["evidence_origin"]
    if origin not in {"original_text", "authored_field"}:
        raise ValueError("evidence_origin is invalid")
    value = _at_path(episode, finding["field_path"])
    if origin == "original_text":
        if finding["time"] not in {"during", "whole_shot"}:
            raise ValueError("finding.time must be during or whole_shot for original_text")
        if _ORIGINAL_SEGMENT_PATH.fullmatch(finding["field_path"]) is None:
            raise ValueError(
                "original_text field_path must be /shots/{n}/source_segments/{m}"
            )
        segment_id = finding.get("source_segment")
        if not isinstance(segment_id, str) or not segment_id:
            raise ValueError("original_text finding.source_segment is required")
        segment = _segment(sources, segment_id)
        if value != segment_id:
            raise ValueError("original_text source_segment must match field_path")
        originals = [
            text for text in (segment.action, segment.line, segment.delivery) if text
        ]
        if not any(
            isinstance(finding["quote"], str)
            and finding["quote"]
            and finding["quote"] in text
            for text in originals
        ):
            raise ValueError("original_text quote is not verbatim present")
    else:
        _validate_time(finding["field_path"], finding["time"], "finding.time")
        _require_verbatim(value, finding["quote"], "authored_field")
        if "relation" not in finding:
            raise ValueError("authored_field relation is required")
    if "relation" in finding:
        _ground_relation(finding["relation"], episode, sources)


def _source_projection(source):
    projected = asdict(source)
    projected.pop("archive_path", None)
    return projected


def _references_projection(references):
    projected = asdict(references)
    projected.pop("source_path", None)
    projected.pop("recorded_date", None)
    for reference in projected["references"]:
        reference.pop("path", None)
        reference.pop("source_path", None)
        reference.pop("recorded_date", None)
    return projected


class Reviewer:
    def __init__(
        self,
        client,
        *,
        store=None,
        prompt_path=None,
        max_tokens=16000,
        timeout=1200,
    ):
        self.client = client
        self.store = store
        self.prompt = Path(
            prompt_path or _project_root() / "prompts/source-review.md"
        ).read_text(encoding="utf-8")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._cache = {}
        self.last_dropped = []

    def _fingerprint(self, episode, sources):
        value = {
            "content": asdict(episode),
            "source": _source_projection(sources.source),
            "references": _references_projection(sources.references),
            "policy": {"version": _POLICY_VERSION, "prompt": self.prompt},
        }
        return sha256(_canonical(value).encode("utf-8")).hexdigest()

    def _initial_parts(self, episode, sources):
        return [
            {"type": "text", "text": self.prompt},
            {"type": "text", "text": source_text(sources.source)},
            {
                "type": "text",
                "text": "AUTHORED EPISODE\n" + _canonical(asdict(episode)),
            },
        ]

    def _ask(self, parts, schema, name):
        return self.client.ask_json(
            parts,
            schema,
            name=name,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )

    def _candidates(self, raw, episode, sources):
        if not isinstance(raw, dict) or set(raw) != {"findings"}:
            raise ReviewProtocolError("review response must contain only findings")
        if not isinstance(raw["findings"], list):
            raise ReviewProtocolError("review findings must be an array")
        candidates = []
        identities = set()
        dropped = []
        for raw_finding in raw["findings"]:
            if not isinstance(raw_finding, dict):
                raise ReviewProtocolError("review finding must be an object")
            if set(raw_finding).difference(_RAW_FINDING_FIELDS):
                raise ReviewProtocolError("review finding contains unsupported fields")
            try:
                ground_finding(raw_finding, episode, sources)
            except ValueError as error:
                dropped.append(
                    {
                        "id": raw_finding.get("id"),
                        "field_path": raw_finding.get("field_path"),
                        "error": str(error),
                    }
                )
                continue
            if raw_finding["id"] in identities:
                raise ReviewProtocolError("review finding IDs must be unique")
            identities.add(raw_finding["id"])
            candidates.append(deepcopy(raw_finding))
        if dropped and not candidates:
            raise ReviewProtocolError(
                f"all review findings were ungrounded: {dropped[0]['error']}"
            )
        self.last_dropped = dropped
        return candidates

    def _retracted_ids(self, raw, candidates):
        if not isinstance(raw, dict) or set(raw) != {"retract_ids"}:
            raise ReviewProtocolError("confirmation must contain only retract_ids")
        retracted = raw["retract_ids"]
        if not isinstance(retracted, list) or any(
            not isinstance(item, str) or not item for item in retracted
        ):
            raise ReviewProtocolError("retract_ids must be nonempty strings")
        if len(retracted) != len(set(retracted)):
            raise ReviewProtocolError("retract_ids must be unique")
        known = {finding["id"] for finding in candidates}
        if not set(retracted).issubset(known):
            raise ReviewProtocolError("retract_ids contain an unknown finding")
        return set(retracted)

    def _cached(self, fingerprint):
        if self.store is not None:
            cached = self.store.find_valid_review(fingerprint, "source_review")
        else:
            cached = self._cache.get(fingerprint)
        return _copy_review(cached) if cached is not None else None

    def _validate_cached(self, cached, fingerprint, episode, sources):
        if cached.content_hash != fingerprint:
            raise ReviewProtocolError("cached review content_hash does not match")
        if cached.verdict == "accepted":
            if cached.findings:
                raise ReviewProtocolError("cached accepted review must have no findings")
            if cached.unresolved:
                raise ReviewProtocolError(
                    "cached accepted review must have no unresolved evidence items"
                )
            return
        if cached.verdict != "rejected" or not cached.findings:
            raise ReviewProtocolError("cached rejected review must have findings")
        for finding in cached.findings:
            try:
                if (
                    not isinstance(finding, Mapping)
                    or finding.get("status") != "confirmed"
                    or finding.get("category") != "contradiction"
                ):
                    raise ValueError("cached findings must be confirmed contradictions")
                ground_finding(finding, episode, sources)
            except (TypeError, ValueError) as error:
                raise ReviewProtocolError(f"invalid cached review: {error}") from error

    def _persist(self, operation, review, fingerprint, retracted):
        receipt = {
            "receipt_id": f"receipt-{operation.id}",
            "operation_id": operation.id,
            "kind": operation.kind,
            "input_hash": fingerprint,
            "verdict": review.verdict,
            "findings": review.findings,
            "retracted_ids": sorted(retracted),
            "policy_version": _POLICY_VERSION,
        }
        data = _canonical(receipt).encode("utf-8")
        relative = f"receipts/{operation.id}.json"
        path = self.store.path.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.store.complete_operation(
            operation.id,
            verdict=review.verdict,
            findings=review.findings,
            receipt_path=relative,
            receipt_sha256=sha256(data).hexdigest(),
            metadata={
                "policy_version": _POLICY_VERSION,
                "retracted_ids": sorted(retracted),
                "dropped_ungrounded_findings": list(self.last_dropped),
                "unresolved_findings": list(review.unresolved),
            },
        )

    def review(self, episode, sources) -> Review:
        validate_episode(episode, sources.source, sources.references)
        fingerprint = self._fingerprint(episode, sources)
        cached = self._cached(fingerprint)
        if cached is not None:
            self._validate_cached(cached, fingerprint, episode, sources)
            return _copy_review(cached)
        operation = None
        if self.store is not None:
            operation = self.store.begin_operation(
                f"source-review-{fingerprint}",
                "source_review",
                fingerprint,
                None,
            )
            if operation.status == "completed":
                cached = self._cached(fingerprint)
                if cached is None:
                    raise ReviewProtocolError(
                        "completed source review operation has no valid cached verdict"
                    )
                self._validate_cached(cached, fingerprint, episode, sources)
                return _copy_review(cached)
        retracted = set()
        try:
            parts = self._initial_parts(episode, sources)
            raw = self._ask(parts, _REVIEW_SCHEMA, "clean_source_review")
            candidates = self._candidates(raw, episode, sources)
            if candidates:
                confirmation_parts = [
                    *parts,
                    {
                        "type": "text",
                        "text": (
                            "SECOND CONFIRMATION: independently recheck each candidate. "
                            "Retract only demonstrated false positives by ID; do not add, "
                            "rewrite, or reclassify findings.\nCANDIDATES\n"
                            + _canonical(candidates)
                        ),
                    },
                ]
                raw_confirmation = self._ask(
                    confirmation_parts,
                    _CONFIRM_SCHEMA,
                    "clean_source_confirm",
                )
                retracted = self._retracted_ids(raw_confirmation, candidates)
        except Exception as error:
            if operation is not None:
                reason = (
                    "invalid_review_result"
                    if isinstance(error, ValueError)
                    else "review_service_error"
                )
                self.store.fail_operation(operation.id, reason)
            raise
        findings = tuple(
            {**finding, "status": "confirmed"}
            for finding in candidates
            if finding["id"] not in retracted
        )
        unresolved = tuple(self.last_dropped)
        review = Review(
            review_id=(
                operation.id if operation is not None else f"source-review-{fingerprint}"
            ),
            content_hash=fingerprint,
            verdict="rejected" if findings or unresolved else "accepted",
            findings=findings,
            unresolved=unresolved,
        )
        if operation is not None:
            self._persist(operation, review, fingerprint, retracted)
        else:
            self._cache[fingerprint] = _copy_review(review)
        return _copy_review(review)
