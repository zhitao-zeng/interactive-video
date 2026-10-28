from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time
from uuid import UUID
from zipfile import ZipFile

from clean_studio.agents.codex import CodexSession
from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    MusicCue,
    Review,
    Shot,
)
from clean_studio.frozen.llm import image_part
from clean_studio.source import FULL_SCRIPT, STRUCTURED_SCRIPT, validate_episode

SINGLE_CALL_SECONDS = 1200
CUMULATIVE_SECONDS = 5400
_AUTHOR_SHOT_FIELDS = (
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
)
_ROLE_ORDER = {"identity": 0, "wardrobe": 1, "location": 2, "style": 3}

_DIALOGUE_SCHEMA = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string"},
        "line": {"type": "string"},
    },
    "required": ["speaker", "line"],
    "additionalProperties": False,
}
_FIXED_OPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "target_world": {"type": "string"},
        "target_video": {"type": "string"},
        "title": {"type": "string"},
    },
    "required": ["label", "target_world", "target_video", "title"],
    "additionalProperties": False,
}
_MUSIC_CUE_SCHEMA = {
    "type": "object",
    "properties": {
        "start_seconds": {"type": "integer"},
        "end_seconds": {"type": "integer"},
        "mood": {"type": "string"},
        "soundscape": {"type": "string"},
    },
    "required": ["start_seconds", "end_seconds", "mood", "soundscape"],
    "additionalProperties": False,
}
_SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "shot_id": {"type": "string"},
        "scene_id": {"type": "string"},
        "source_segments": {"type": "array", "items": {"type": "string"}},
        "location": {"type": "string"},
        "start_state": {"type": "string"},
        "action": {"type": "string"},
        "end_state": {"type": "string"},
        "camera": {"type": "string"},
        "shot_scale": {"type": "string"},
        "visible_characters": {"type": "array", "items": {"type": "string"}},
        "opening_characters": {"type": "array", "items": {"type": "string"}},
        "dialogue": {"type": "array", "items": _DIALOGUE_SCHEMA},
        "reference_subjects": {"type": "array", "items": {"type": "string"}},
        "duration_seconds": {"type": "integer", "minimum": 4, "maximum": 15},
    },
    "required": [
        "shot_id",
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
    ],
    "additionalProperties": False,
}
EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "episode_id": {"type": "string"},
        "title": {"type": "string"},
        "target_seconds": {"type": "integer"},
        "style_id": {"type": "string"},
        "shots": {"type": "array", "items": _SHOT_SCHEMA},
        "fixed_question": {"type": "string"},
        "fixed_options": {"type": "array", "items": _FIXED_OPTION_SCHEMA},
        "music_cues": {"type": "array", "items": _MUSIC_CUE_SCHEMA},
    },
    "required": [
        "episode_id",
        "title",
        "target_seconds",
        "style_id",
        "shots",
        "fixed_question",
        "fixed_options",
        "music_cues",
    ],
    "additionalProperties": False,
}


class AuthorProtocolError(ValueError):
    pass


class AuthorBudgetExceeded(RuntimeError):
    pass


class AuthorRecoveryRequired(RuntimeError):
    pass


def _project_root() -> Path:
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[3]
    ).resolve()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_text(source) -> str:
    with ZipFile(Path(source.archive_path)) as archive:
        full_script = archive.read(FULL_SCRIPT).decode("utf-8")
        structured = archive.read(STRUCTURED_SCRIPT).decode("utf-8")
    return (
        f"ORIGINAL PACKAGE {FULL_SCRIPT}\n{full_script}\n"
        f"ORIGINAL PACKAGE {STRUCTURED_SCRIPT}\n{structured}"
    )


def _episode_data(episode):
    payload = asdict(episode)
    for shot in payload["shots"]:
        shot.pop("extra_fields", None)
    return payload


def _parse_episode(raw, source, references) -> EpisodeSpec:
    try:
        if not isinstance(raw, dict):
            raise AuthorProtocolError("author response must be a JSON object")
        if set(raw) != set(EPISODE_SCHEMA["required"]):
            raise AuthorProtocolError("author response does not match EpisodeSpec schema")
        shots = []
        for raw_shot in raw["shots"]:
            if not isinstance(raw_shot, dict) or set(raw_shot) != set(
                _SHOT_SCHEMA["required"]
            ):
                raise AuthorProtocolError("author shot does not match EpisodeSpec schema")
            shot = dict(raw_shot)
            shot["dialogue"] = tuple(
                Dialogue(**line) for line in shot["dialogue"]
            )
            shots.append(Shot(**shot))
        episode = EpisodeSpec(
            episode_id=raw["episode_id"],
            title=raw["title"],
            target_seconds=raw["target_seconds"],
            style_id=raw["style_id"],
            shots=tuple(shots),
            fixed_question=raw["fixed_question"],
            fixed_options=tuple(
                FixedOption(**option) for option in raw["fixed_options"]
            ),
            music_cues=tuple(MusicCue(**cue) for cue in raw["music_cues"]),
        )
        body = sum(shot.duration_seconds for shot in episode.shots)
        clamped = []
        for cue in episode.music_cues:
            end = min(cue.end_seconds, body)
            start = min(cue.start_seconds, max(0, end - 1))
            clamped.append(
                cue if (cue.start_seconds, cue.end_seconds) == (start, end)
                else replace(cue, start_seconds=start, end_seconds=end)
            )
        if len(clamped) != len(episode.music_cues) or any(
            a is not b for a, b in zip(clamped, episode.music_cues)
        ):
            episode = replace(episode, music_cues=tuple(clamped))
        validate_episode(episode, source, references)
        return episode
    except AuthorProtocolError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorProtocolError(f"invalid complete EpisodeSpec: {error}") from error


class Author:
    def __init__(
        self,
        source,
        references,
        client,
        *,
        clock=time.monotonic,
        prompt_path=None,
        image_max_side=1024,
        max_tokens=16000,
        store=None,
    ):
        self.source = source
        self.references = references
        self.client = client
        self.clock = clock
        self.prompt = Path(
            prompt_path or _project_root() / "prompts/author.md"
        ).read_text(encoding="utf-8")
        self.image_max_side = image_max_side
        self.max_tokens = max_tokens
        self.session = client.create_session("author")
        self._elapsed = 0.0
        self._current = None
        self.store = None
        self._episode_id = None
        self._fallback_started = False
        self._recovery_required = False
        self._recovery_error = None
        if store is not None:
            self.bind_store(store)

    @property
    def current(self):
        return self._current

    @property
    def elapsed_seconds(self):
        return self._elapsed

    def _session_data(self):
        work_dir = getattr(self.session, "work_dir", None)
        if work_dir is None:
            work_dir = getattr(self.session, "name", "")
        started = getattr(self.session, "started", self._fallback_started)
        return {"work_dir": str(work_dir), "started": bool(started)}

    def _author_rows(self):
        if self.store is None:
            return []
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            rows = connection.execute(
                "SELECT metadata FROM operations WHERE episode_id = ? AND kind = 'author' ORDER BY rowid",
                (episode_id,),
            ).fetchall()
        return [json.loads(row["metadata"]) for row in rows]

    def _pending_author_rows(self):
        if self.store is None:
            return []
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            rows = connection.execute(
                """
                SELECT id, metadata FROM operations
                WHERE episode_id = ? AND kind = 'author' AND status = 'pending'
                ORDER BY rowid
                """,
                (episode_id,),
            ).fetchall()
        return [(row["id"], json.loads(row["metadata"])) for row in rows]

    @staticmethod
    def _validated_pending_session(session):
        work_dir = session.get("work_dir") if isinstance(session, Mapping) else None
        if not isinstance(work_dir, str) or not work_dir.strip():
            raise AuthorRecoveryRequired(
                "pending author operation requires a valid work_dir/codex-session.json"
            )
        try:
            payload = json.loads(
                (Path(work_dir) / "codex-session.json").read_text(encoding="utf-8")
            )
            if not isinstance(payload, dict) or payload.get("backend") != "codex":
                raise ValueError("invalid Codex session backend")
            UUID(payload.get("session_id", ""))
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as error:
            raise AuthorRecoveryRequired(
                "pending author operation requires a valid work_dir/codex-session.json"
            ) from error
        return {"work_dir": work_dir, "started": True}

    @staticmethod
    def _pending_duration(metadata, now):
        try:
            started = float(metadata["started_wall_seconds"])
            elapsed = float(now) - started
        except (KeyError, TypeError, ValueError):
            return float(CUMULATIVE_SECONDS)
        if not math.isfinite(started) or not math.isfinite(elapsed) or elapsed <= 0:
            return float(CUMULATIVE_SECONDS)
        return elapsed

    def _recover_pending_calls(self):
        pending = self._pending_author_rows()
        now = time.time()
        for operation_id, row in pending:
            recovery_error = None
            try:
                session = self._validated_pending_session(row.get("session"))
            except AuthorRecoveryRequired as error:
                recovery_error = str(error)
                original = row.get("session")
                work_dir = original.get("work_dir") if isinstance(original, Mapping) else ""
                session = {"work_dir": str(work_dir or ""), "started": False}
            duration = self._pending_duration(row, now)
            metadata = {
                **row,
                "session": session,
                "duration_seconds": duration,
                "elapsed_total_seconds": (
                    float(row.get("elapsed_before_seconds") or 0.0) + duration
                ),
                "result": "failure",
                "error_type": "AuthorRecoveryRequired",
                "recovered_pending": True,
            }
            metadata.pop("recovery_error", None)
            if recovery_error is not None:
                metadata["recovery_error"] = recovery_error
            self.store.fail_operation(
                operation_id,
                "author_call_interrupted",
                metadata=metadata,
            )
        return bool(pending)

    def bind_store(self, store, episode_id=None):
        self.store = store
        state = store.load(episode_id)
        self._episode_id = state["episode_id"]
        recovered = self._recover_pending_calls()
        rows = self._author_rows()
        for row in reversed(rows):
            session = row.get("session") or {}
            if session.get("work_dir"):
                self.session = CodexSession(
                    work_dir=Path(session["work_dir"]),
                    started=bool(session.get("started")),
                )
                break
        self._elapsed = sum(
            float(row.get("duration_seconds") or 0.0) for row in rows
        )
        self._recovery_error = None
        if rows and rows[-1].get("recovered_pending") and rows[-1].get("recovery_error"):
            try:
                session = self._validated_pending_session(rows[-1].get("session"))
            except AuthorRecoveryRequired as error:
                self._recovery_error = str(error)
            else:
                self.session = CodexSession(
                    work_dir=Path(session["work_dir"]), started=True
                )
        self._recovery_required = self._recovery_required or recovered
        if state["spec"].shots:
            self._current = state["spec"]
        return self

    def check_recovery(self):
        required = self._recovery_required
        self._recovery_required = False
        if self._recovery_error is not None:
            raise AuthorRecoveryRequired(self._recovery_error)
        if required:
            raise AuthorRecoveryRequired(
                "pending author operation was recovered; resume the original session on the next launch"
            )

    def _begin_call(self, name):
        if self.store is None:
            return None
        rows = self._author_rows()
        sequence = len(rows) + 1
        current = _episode_data(self._current) if self._current is not None else None
        input_hash = sha256(
            _canonical(
                {
                    "episode_id": self._episode_id,
                    "sequence": sequence,
                    "name": name,
                    "current": current,
                }
            ).encode("utf-8")
        ).hexdigest()
        metadata = {
            "name": name,
            "sequence": sequence,
            "session": self._session_data(),
            "elapsed_before_seconds": self._elapsed,
            "started_wall_seconds": time.time(),
        }
        operation = self.store.begin_operation(
            f"author:{self._episode_id}:{sequence}",
            "author",
            input_hash,
            None,
            metadata=metadata,
        )
        return {"operation": operation, "metadata": metadata, "duration_seconds": 0.0}

    def _consume_elapsed(self, context, started):
        elapsed = max(0.0, self.clock() - started)
        self._elapsed += elapsed
        self._fallback_started = True
        if context is not None:
            context["duration_seconds"] = elapsed
        return elapsed

    def _persist_call(self, context, error=None):
        if context is None:
            return
        metadata = {
            **context["metadata"],
            "session": self._session_data(),
            "duration_seconds": context["duration_seconds"],
            "elapsed_total_seconds": self._elapsed,
        }
        operation = context["operation"]
        if error is None:
            self.store.complete_operation(
                operation.id,
                metadata={**metadata, "result": "success"},
            )
        else:
            self.store.fail_operation(
                operation.id,
                "author_call_failed",
                metadata={
                    **metadata,
                    "result": "failure",
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:600],
                },
            )

    def _host_rejections(self):
        if self.store is None:
            return []
        messages = []
        for row in self._author_rows():
            if row.get("result") != "failure":
                continue
            message = str(row.get("error_message") or "").strip()
            if message and message not in messages:
                messages.append(message)
        return messages[-3:]

    def _start_parts(self):
        subjects = {
            subject.subject_id: subject for subject in self.references.subjects
        }
        catalog = [
            {
                "subject_id": subject.subject_id,
                "name": subject.name,
                "subject_type": subject.subject_type,
                "profile": subject.profile,
                "bound_reference_images": sum(
                    1
                    for reference in self.references.references
                    if reference.subject_id == subject.subject_id
                ),
            }
            for subject in subjects.values()
        ]
        parts = [{"type": "text", "text": self.prompt}]
        rejections = self._host_rejections()
        if rejections:
            parts.append(
                {
                    "type": "text",
                    "text": (
                        "HOST REJECTIONS OF PREVIOUS COMPLETE DRAFTS "
                        "(the host refused these outputs; fix them and do not repeat):\n"
                        + "\n".join(f"- {message}" for message in rejections)
                    ),
                }
            )
        segment_index = [
            {
                "segment_id": segment.segment_id,
                "scene_id": segment.scene_id,
                "action": segment.action,
                **(
                    {"speaker": segment.speaker, "line": segment.line}
                    if segment.speaker is not None
                    else {}
                ),
            }
            for scene in self.source.scenes
            for segment in scene.segments
        ]
        parts.extend(
            [
                {"type": "text", "text": source_text(self.source)},
                {
                    "type": "text",
                    "text": (
                        "SOURCE SEGMENTS (host-assigned ids; shots[].source_segments"
                        " must use these exact ids, cover each one exactly once, and"
                        " keep source order across shots; shot.scene_id must equal the"
                        " segment scene_id; for every segment with speaker/line the"
                        " shot's dialogue must reproduce that exact speaker/line pair"
                        " verbatim, in segment order, with no extra lines):\n"
                        + _canonical(segment_index)
                    ),
                },
                {
                    "type": "text",
                    "text": "REGISTERED SUBJECTS\n" + _canonical(catalog),
                },
            ]
        )
        ordered = sorted(
            self.references.references,
            key=lambda reference: (
                _ROLE_ORDER.get(reference.role, 99),
                reference.order,
                reference.subject_id,
                reference.path,
            ),
        )
        for reference in ordered:
            if reference.subject_id in subjects:
                label = f"REFERENCE {reference.role} subject={reference.subject_id}"
            else:
                label = (
                    f"REFERENCE {reference.role} style_id={reference.subject_id}"
                    " (style attachment, not a subject_id; never list it in"
                    " visible_characters or reference_subjects)"
                )
            parts.append(
                {
                    "type": "text",
                    "text": (
                        f"{label} scope={reference.scope} sha256={reference.sha256}"
                    ),
                }
            )
            parts.append(image_part(Path(reference.source_path), self.image_max_side))
        return parts

    def _call(self, parts, name):
        self.check_recovery()
        remaining = CUMULATIVE_SECONDS - self._elapsed
        if remaining <= 0:
            raise AuthorBudgetExceeded("author cumulative 5400 second budget exhausted")
        timeout = min(SINGLE_CALL_SECONDS, remaining)
        context = self._begin_call(name)
        started = self.clock()
        try:
            result = self.client.ask_json(
                parts,
                EPISODE_SCHEMA,
                name=name,
                max_tokens=self.max_tokens,
                timeout=timeout,
                session=self.session,
            )
        except Exception as error:
            self._consume_elapsed(context, started)
            self._persist_call(context, error)
            raise
        elapsed = self._consume_elapsed(context, started)
        if elapsed > SINGLE_CALL_SECONDS:
            error = AuthorBudgetExceeded("author single 1200 second budget exhausted")
            self._persist_call(context, error)
            raise error
        if self._elapsed > CUMULATIVE_SECONDS:
            error = AuthorBudgetExceeded("author cumulative 5400 second budget exhausted")
            self._persist_call(context, error)
            raise error
        return result, context

    def start(self) -> EpisodeSpec:
        if self._current is not None:
            raise AuthorProtocolError("start cannot replace an authored episode; use revise")
        raw, context = self._call(self._start_parts(), "clean_author_start")
        try:
            episode = _parse_episode(raw, self.source, self.references)
        except Exception as error:
            self._persist_call(context, error)
            raise
        self._persist_call(context)
        self._current = episode
        return episode

    def _confirmed_findings(self, findings):
        if isinstance(findings, Review):
            findings = findings.findings
        elif isinstance(findings, Mapping):
            findings = (findings,)
        try:
            normalized = tuple(deepcopy(finding) for finding in findings)
        except TypeError as error:
            raise ValueError("findings must be confirmed contradictions") from error
        if not normalized:
            raise ValueError("at least one confirmed contradiction is required")
        for finding in normalized:
            if (
                not isinstance(finding, Mapping)
                or finding.get("status") != "confirmed"
                or finding.get("category") != "contradiction"
            ):
                raise ValueError("Author.revise accepts only confirmed contradiction findings")
        return normalized

    def _ground_findings(self, findings):
        from clean_studio.agents.review import ReviewSources, ground_finding

        sources = ReviewSources(source=self.source, references=self.references)
        for finding in findings:
            try:
                ground_finding(finding, self._current, sources)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Author.revise findings must be grounded in the current EpisodeSpec"
                ) from error
        return findings

    def _allowed_fields(self, findings, allowed_fields):
        if allowed_fields is None:
            derived = {}
            for finding in findings:
                tokens = finding["field_path"].strip("/").split("/")
                if (
                    len(tokens) >= 3
                    and tokens[0] == "shots"
                    and tokens[1].isdigit()
                    and tokens[2] in _AUTHOR_SHOT_FIELDS
                    and tokens[2] != "source_segments"
                ):
                    index = int(tokens[1])
                    if self._current is None or index >= len(self._current.shots):
                        raise ValueError("finding field_path is outside the episode")
                    shot_id = self._current.shots[index].shot_id
                    derived.setdefault(shot_id, set()).add(tokens[2])
            allowed_fields = derived
        if not isinstance(allowed_fields, Mapping) or not allowed_fields:
            raise ValueError("allowed_fields must identify revisable shot fields")
        shot_ids = {shot.shot_id for shot in self._current.shots}
        normalized = {}
        for shot_id, fields in allowed_fields.items():
            if shot_id not in shot_ids:
                raise ValueError(f"unknown revision shot: {shot_id}")
            fields = {fields} if isinstance(fields, str) else set(fields)
            unsupported = fields.difference(_AUTHOR_SHOT_FIELDS)
            if unsupported or not fields:
                raise ValueError(f"unsupported revision fields: {sorted(unsupported)}")
            normalized[shot_id] = tuple(sorted(fields))
        return normalized

    def _validate_revision(self, revised, allowed_fields):
        previous = self._current
        old_episode = {**asdict(previous), "shots": None}
        new_episode = {**asdict(revised), "shots": None}
        if old_episode != new_episode:
            raise AuthorProtocolError("revision scope only permits shot fields")
        if tuple(shot.shot_id for shot in previous.shots) != tuple(
            shot.shot_id for shot in revised.shots
        ):
            raise AuthorProtocolError("revision scope must preserve shot IDs and order")
        changed = 0
        for old_shot, new_shot in zip(previous.shots, revised.shots):
            permitted = set(allowed_fields.get(old_shot.shot_id, ()))
            for field in _AUTHOR_SHOT_FIELDS:
                if getattr(old_shot, field) == getattr(new_shot, field):
                    continue
                if field not in permitted:
                    raise AuthorProtocolError(
                        f"revision scope violation: {old_shot.shot_id}.{field}"
                    )
                changed += 1
        if not changed:
            raise AuthorProtocolError("revision made no allowed changes")

    def revise(self, findings, allowed_fields=None) -> EpisodeSpec:
        """allowed_fields is the host grant; findings are evidence, not authorization.

        An omitted grant is derived by the host only after every finding has been
        grounded again in the current episode. A reply that edits fields outside
        the grant is a protocol slip, not a content verdict: the author gets a
        bounded retry that names the exact violation.
        """
        if self._current is None:
            raise AuthorProtocolError("Author.start must succeed before revise")
        confirmed = self._ground_findings(self._confirmed_findings(findings))
        allowed = self._allowed_fields(confirmed, allowed_fields)
        rejected = None
        for _attempt in range(3):
            payload = {
                "current_episode": _episode_data(self._current),
                "confirmed_findings": confirmed,
                "allowed_fields": allowed,
                "instruction": (
                    "allowed_fields is the host grant; findings are evidence only. "
                    "Revise only the granted shot fields and return the complete EpisodeSpec. "
                    "Preserve every other value and all original source obligations. "
                    "A continuity finding about a shot's opening state is fixed in that "
                    "opening field; later action fields cannot repair it."
                ),
            }
            if rejected is not None:
                payload["rejected_previous_attempt"] = rejected
            parts = [{"type": "text", "text": self.prompt + "\n" + _canonical(payload)}]
            raw, context = self._call(parts, "clean_author_revise")
            try:
                revised = _parse_episode(raw, self.source, self.references)
                self._validate_revision(revised, allowed)
            except Exception as error:
                self._persist_call(context, error)
                rejected = {
                    "error": f"{type(error).__name__}: {error}",
                    "allowed_fields": allowed,
                }
                continue
            self._persist_call(context)
            self._current = revised
            return revised
        raise AuthorProtocolError(
            f"revision protocol failed after 3 attempts: {rejected['error']}"
        )
