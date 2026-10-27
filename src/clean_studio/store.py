from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Iterator, Mapping
from uuid import uuid4

from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    Finding,
    FixedOption,
    MusicCue,
    Operation,
    Review,
    Shot,
    export_bundle_identity,
)
from clean_studio.review_contract import validate_final_review

_GENERATION_QUOTAS = {"image": 3, "video": 3}
_INPUT_QUOTA = 2
_MEDIA_KINDS = {"image", "video", "assembly"}
_ARTIFACT_KINDS = {"image": "frame", "video": "video", "assembly": "export"}
_VALID_VERDICTS = {"accepted", "rejected"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    frozen_config TEXT NOT NULL,
    current_script TEXT NOT NULL,
    stage TEXT NOT NULL,
    current_script_hash TEXT NOT NULL,
    compile_hash TEXT,
    current_script_artifact_id TEXT,
    current_export_artifact_id TEXT,
    blocked TEXT,
    next_action TEXT,
    launches INTEGER NOT NULL,
    recovery_validation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shots (
    episode_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_compile_hash TEXT,
    frame_request_hash TEXT,
    video_request_hash TEXT,
    current_frame_artifact_id TEXT,
    current_video_artifact_id TEXT,
    PRIMARY KEY (episode_id, shot_id),
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    shot_id TEXT,
    status TEXT NOT NULL,
    external_task_id TEXT,
    receipt_path TEXT,
    verdict TEXT,
    findings TEXT NOT NULL,
    metadata TEXT NOT NULL,
    affected_shots TEXT NOT NULL,
    error TEXT,
    UNIQUE (episode_id, idempotency_key),
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    shot_id TEXT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE (operation_id, kind),
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id),
    FOREIGN KEY (operation_id) REFERENCES operations(id)
);
"""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value_hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_review_kind(kind: str) -> bool:
    return kind == "review" or kind.endswith("_review")


def validate_frozen_config(value, path="config") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"configuration keys must be strings at {path}")
            lower = key.lower()
            field_path = f"{path}.{key}"
            if lower.endswith(("_env", "_key_var")):
                if not isinstance(item, str) or re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*", item
                ) is None:
                    raise ValueError(
                        f"environment variable name required at {field_path}"
                    )
                continue
            normalized = re.sub(r"[^a-z0-9]", "", lower)
            suspicious = (
                normalized in {
                    "key",
                    "keys",
                    "apikey",
                    "apikeys",
                    "authorization",
                    "token",
                    "secret",
                    "password",
                    "credential",
                    "credentials",
                }
                or lower.endswith(
                    (
                        "_key",
                        "_token",
                        "_secret",
                        "_password",
                        "_authorization",
                        "_credential",
                        "_credentials",
                    )
                )
                or normalized.startswith(("api", "st"))
                and normalized.endswith(("key", "token", "secret"))
                or normalized.endswith(
                    (
                        "accesstoken",
                        "refreshtoken",
                        "sessiontoken",
                        "privatekey",
                        "clientsecret",
                    )
                )
            )
            if suspicious:
                raise ValueError(
                    f"secret-like configuration field is not allowed at {field_path}"
                )
            validate_frozen_config(item, field_path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_frozen_config(item, f"{path}[{index}]")


def _episode_from_json(payload: str) -> EpisodeSpec:
    raw = json.loads(payload)
    return EpisodeSpec(
        episode_id=raw["episode_id"],
        title=raw["title"],
        target_seconds=raw["target_seconds"],
        style_id=raw["style_id"],
        shots=tuple(
            Shot(**{**shot, "dialogue": tuple(Dialogue(**line) for line in shot["dialogue"])})
            for shot in raw["shots"]
        ),
        fixed_question=raw["fixed_question"],
        fixed_options=tuple(FixedOption(**option) for option in raw["fixed_options"]),
        music_cues=tuple(MusicCue(**cue) for cue in raw["music_cues"]),
    )


def _operation(row: sqlite3.Row) -> Operation:
    return Operation(
        id=row["id"],
        idempotency_key=row["idempotency_key"],
        kind=row["kind"],
        input_hash=row["input_hash"],
        shot_id=row["shot_id"],
        status=row["status"],
        external_task_id=row["external_task_id"],
        receipt_path=row["receipt_path"],
    )


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact and receipt paths must be relative")
    return value


def _artifact_hash(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("artifact SHA-256 must be 64 lowercase hex characters")
    return value


def _validated_export_bundle(metadata, video_sha256):
    if not isinstance(metadata, Mapping):
        raise ValueError("export artifact metadata must be an object")
    bundle = metadata.get("export_bundle")
    if not isinstance(bundle, Mapping):
        raise ValueError("assembly requires a complete export bundle")
    identity = bundle.get("bundle_hash")
    if not isinstance(identity, str):
        raise ValueError("export bundle identity must be a SHA-256")
    _artifact_hash(identity)
    if identity != export_bundle_identity(bundle):
        raise ValueError("export bundle identity does not match its canonical contents")
    if bundle.get("video_sha256") != video_sha256:
        raise ValueError("export bundle video SHA-256 differs from the artifact")
    return dict(bundle)


class Store:
    def __init__(self, path: str | Path, *, _readonly: bool = False) -> None:
        self.path = Path(path)
        self._episode_id: str | None = None
        self._readonly = _readonly
        if _readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as connection:
            connection.executescript(_SCHEMA)

    @classmethod
    def open_readonly(cls, path: str | Path) -> "Store":
        return cls(path, _readonly=True)

    @contextmanager
    def _db(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        if self._readonly and not readonly:
            raise RuntimeError("read-only store cannot be mutated")
        if readonly or self._readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True
            )
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _resolve_episode_id(self, connection: sqlite3.Connection) -> str:
        if self._episode_id is not None:
            return self._episode_id
        rows = connection.execute("SELECT episode_id FROM episodes").fetchall()
        if len(rows) != 1:
            raise ValueError("an episode must be selected")
        self._episode_id = rows[0]["episode_id"]
        return self._episode_id

    def _get_operation(self, connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown operation: {operation_id}")
        return row

    def _existing_operation(
        self,
        connection: sqlite3.Connection,
        episode_id: str,
        idempotency_key: str,
        kind: str,
        input_hash: str,
        shot_id: str | None,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM operations WHERE episode_id = ? AND idempotency_key = ?",
            (episode_id, idempotency_key),
        ).fetchone()
        if row is not None and (
            row["kind"] != kind
            or row["input_hash"] != input_hash
            or row["shot_id"] != shot_id
        ):
            raise ValueError("idempotency key is bound to different operation input")
        return row

    def _insert_operation(
        self,
        connection: sqlite3.Connection,
        episode_id: str,
        idempotency_key: str,
        kind: str,
        input_hash: str,
        shot_id: str | None,
        *,
        status: str = "pending",
        metadata: Mapping[str, object] | None = None,
        affected_shots: tuple[str, ...] = (),
    ) -> sqlite3.Row:
        operation_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO operations (
                id, episode_id, idempotency_key, kind, input_hash, shot_id,
                status, findings, metadata, affected_shots
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)
            """,
            (
                operation_id,
                episode_id,
                idempotency_key,
                kind,
                input_hash,
                shot_id,
                status,
                _canonical_json(dict(metadata or {})),
                _canonical_json(affected_shots),
            ),
        )
        return self._get_operation(connection, operation_id)

    def _input_usage(self, connection: sqlite3.Connection, episode_id: str, shot_id: str) -> int:
        rows = connection.execute(
            """
            SELECT affected_shots FROM operations
            WHERE episode_id = ? AND kind = 'input' AND status != 'not_accepted'
            """,
            (episode_id,),
        ).fetchall()
        return sum(shot_id in json.loads(row["affected_shots"]) for row in rows)

    def _invalidate_episode(self, connection: sqlite3.Connection, episode_id: str) -> None:
        connection.execute(
            """
            UPDATE episodes
            SET compile_hash = NULL, current_export_artifact_id = NULL
            WHERE episode_id = ?
            """,
            (episode_id,),
        )

    def _publish_target(self, connection: sqlite3.Connection, episode_id: str) -> str | None:
        row = connection.execute(
            "SELECT frozen_config FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return json.loads(row["frozen_config"]).get("publish_target")

    def create_episode(
        self, spec: EpisodeSpec, frozen_config: Mapping[str, object]
    ) -> dict:
        validate_frozen_config(frozen_config)
        script = _canonical_json(asdict(spec))
        config = _canonical_json(dict(frozen_config))
        with self._db() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO episodes (
                        episode_id, frozen_config, current_script, stage,
                        current_script_hash, launches, recovery_validation
                    ) VALUES (?, ?, ?, 'AUTHORING', ?, 1, 0)
                    """,
                    (spec.episode_id, config, script, _value_hash(asdict(spec))),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"episode already exists: {spec.episode_id}") from error
            connection.executemany(
                "INSERT INTO shots (episode_id, shot_id, source_hash) VALUES (?, ?, ?)",
                [
                    (spec.episode_id, shot.shot_id, _value_hash(asdict(shot)))
                    for shot in spec.shots
                ],
            )
        self._episode_id = spec.episode_id
        return self.load()

    def load(self, episode_id: str | None = None) -> dict:
        with self._db(readonly=True) as connection:
            if episode_id is not None:
                self._episode_id = episode_id
            selected_id = self._resolve_episode_id(connection)
            episode = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (selected_id,)
            ).fetchone()
            if episode is None:
                self._episode_id = None
                raise KeyError(f"unknown episode: {selected_id}")
            shots = connection.execute(
                "SELECT * FROM shots WHERE episode_id = ? ORDER BY shot_id",
                (selected_id,),
            ).fetchall()
            artifacts = connection.execute(
                "SELECT * FROM artifacts WHERE episode_id = ? ORDER BY operation_id, kind",
                (selected_id,),
            ).fetchall()
            return {
                "episode_id": selected_id,
                "spec": _episode_from_json(episode["current_script"]),
                "frozen_config": json.loads(episode["frozen_config"]),
                "stage": episode["stage"],
                "status": self._projection(connection, episode),
                "current_script_hash": episode["current_script_hash"],
                "compile_hash": episode["compile_hash"],
                "current_script_artifact_id": episode["current_script_artifact_id"],
                "current_export_artifact_id": episode["current_export_artifact_id"],
                "blocked": episode["blocked"],
                "next_action": episode["next_action"],
                "launches": episode["launches"],
                "recovery_validation": bool(episode["recovery_validation"]),
                "shots": [dict(shot) for shot in shots],
                "artifacts": [
                    {**dict(artifact), "metadata": json.loads(artifact["metadata"])}
                    for artifact in artifacts
                ],
            }

    def _latest_valid_review(
        self,
        connection: sqlite3.Connection,
        episode_id: str,
        content_hash: str,
        kind: str | None,
    ) -> sqlite3.Row | None:
        if kind is not None and not _is_review_kind(kind):
            raise ValueError(f"not a review kind: {kind}")
        rows = connection.execute(
            """
            SELECT operations.* FROM operations
            JOIN artifacts
              ON artifacts.operation_id = operations.id
             AND artifacts.kind = 'receipt'
             AND artifacts.path = operations.receipt_path
            WHERE operations.episode_id = ?
              AND operations.input_hash = ?
              AND operations.status = 'completed'
              AND operations.verdict IN ('accepted', 'rejected')
            ORDER BY operations.rowid DESC
            """,
            (episode_id, content_hash),
        ).fetchall()
        for row in rows:
            if kind is not None and row["kind"] != kind:
                continue
            if kind is None and not _is_review_kind(row["kind"]):
                continue
            return row
        return None

    def _projection(self, connection: sqlite3.Connection, episode: sqlite3.Row) -> str:
        if episode["blocked"] is not None:
            return "blocked"
        export_id = episode["current_export_artifact_id"]
        if export_id is None:
            return "in_progress"
        export = connection.execute(
            "SELECT sha256, metadata FROM artifacts WHERE id = ? AND kind = 'export'",
            (export_id,),
        ).fetchone()
        if export is None:
            raise RuntimeError("current export artifact is missing")
        try:
            bundle = _validated_export_bundle(
                json.loads(export["metadata"]), export["sha256"]
            )
        except (ValueError, TypeError, KeyError):
            return "candidate"
        bundle_hash = bundle["bundle_hash"]
        for review_kind in ("final_visual_review", "final_audio_review"):
            review = self._latest_valid_review(
                connection, episode["episode_id"], bundle_hash, review_kind
            )
            if review is None:
                return "candidate"
            try:
                artifact = connection.execute(
                    """
                    SELECT sha256 FROM artifacts
                    WHERE operation_id = ? AND kind = 'receipt' AND path = ?
                    """,
                    (review["id"], review["receipt_path"]),
                ).fetchone()
                if artifact is None:
                    return "candidate"
                receipt, _ = self._read_receipt(
                    review["receipt_path"], artifact["sha256"]
                )
                self._validate_receipt(review, receipt, review["verdict"])
                decision = validate_final_review(review_kind, receipt, bundle)
            except (ValueError, TypeError, KeyError, OSError, IndexError):
                return "candidate"
            if not decision.accepted:
                return "candidate"
        upload = connection.execute(
            """
            SELECT operations.*, artifacts.sha256 AS receipt_sha256
            FROM operations
            JOIN artifacts
              ON artifacts.operation_id = operations.id
             AND artifacts.kind = 'receipt'
             AND artifacts.path = operations.receipt_path
            WHERE operations.episode_id = ?
              AND operations.kind = 'upload'
              AND operations.input_hash = ?
              AND operations.status = 'completed'
            ORDER BY operations.rowid DESC
            LIMIT 1
            """,
            (episode["episode_id"], bundle_hash),
        ).fetchone()
        if upload is not None:
            try:
                receipt, _ = self._read_receipt(
                    upload["receipt_path"], upload["receipt_sha256"]
                )
                self._validate_receipt(upload, receipt, None)
            except (ValueError, TypeError, KeyError, OSError):
                return "accepted"
            target = self._publish_target(connection, episode["episode_id"])
            if (
                receipt.get("input_hash") == bundle_hash
                and receipt.get("bundle_hash") == bundle_hash
                and receipt.get("target") == target
                and receipt.get("verified") is True
                and receipt.get("remote_sha256") == bundle["video_sha256"]
                and receipt.get("cover_sha256") == bundle["cover_sha256"]
                and receipt.get("private") is True
                and receipt.get("visibility") == "private"
                and isinstance(receipt.get("remote_object"), str)
                and receipt["remote_object"]
                and isinstance(receipt.get("cover_object"), str)
                and receipt["cover_object"]
            ):
                return "complete"
        return "accepted"

    def advance(
        self,
        stage: str,
        *,
        blocked: str | None = None,
        next_action: str | None = None,
        new_launch: bool = False,
    ) -> dict:
        if not stage:
            raise ValueError("stage must be non-empty")
        if blocked is not None and not next_action:
            raise ValueError("blocked state requires a legal next_action")
        if blocked is None and next_action is not None:
            raise ValueError("next_action requires a blocked state")
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            episode = connection.execute(
                "SELECT launches, recovery_validation FROM episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            launches = episode["launches"]
            recovery_validation = bool(episode["recovery_validation"])
            if new_launch:
                if launches >= 3:
                    raise ValueError("episode launch quota exhausted")
                launches += 1
                recovery_validation = True
            connection.execute(
                """
                UPDATE episodes
                SET stage = ?, blocked = ?, next_action = ?,
                    launches = ?, recovery_validation = ?
                WHERE episode_id = ?
                """,
                (stage, blocked, next_action, launches, int(recovery_validation), episode_id),
            )
        return self.load()

    def begin_operation(
        self,
        idempotency_key: str,
        kind: str,
        input_hash: str,
        shot_id: str | None,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> Operation:
        if kind == "input":
            raise ValueError("input operations must use apply_script_revision")
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            existing = self._existing_operation(
                connection, episode_id, idempotency_key, kind, input_hash, shot_id
            )
            if existing is not None:
                return _operation(existing)
            if shot_id is not None:
                shot = connection.execute(
                    "SELECT 1 FROM shots WHERE episode_id = ? AND shot_id = ?",
                    (episode_id, shot_id),
                ).fetchone()
                if shot is None:
                    raise ValueError(f"unknown shot: {shot_id}")
            if kind in _GENERATION_QUOTAS:
                if shot_id is None:
                    raise ValueError(f"{kind} operations require a shot_id")
                used = connection.execute(
                    """
                    SELECT COUNT(*) FROM operations
                    WHERE episode_id = ? AND shot_id = ? AND kind = ?
                      AND status != 'not_accepted'
                    """,
                    (episode_id, shot_id, kind),
                ).fetchone()[0]
                if used >= _GENERATION_QUOTAS[kind]:
                    raise ValueError(f"{kind} quota exhausted: {_GENERATION_QUOTAS[kind]}")
            return _operation(
                self._insert_operation(
                    connection,
                    episode_id,
                    idempotency_key,
                    kind,
                    input_hash,
                    shot_id,
                    metadata=metadata,
                )
            )

    def compile_shot(
        self,
        shot_id: str,
        *,
        expected_source_hash: str,
        source_compile_hash: str,
    ) -> dict:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            shot = connection.execute(
                "SELECT * FROM shots WHERE episode_id = ? AND shot_id = ?",
                (episode_id, shot_id),
            ).fetchone()
            if shot is None:
                raise ValueError(f"unknown shot: {shot_id}")
            if shot["source_hash"] != expected_source_hash:
                raise ValueError("expected_source_hash does not match current shot")
            if shot["source_compile_hash"] != source_compile_hash:
                updated = connection.execute(
                    """
                    UPDATE shots
                    SET source_compile_hash = ?, frame_request_hash = NULL,
                        video_request_hash = NULL, current_frame_artifact_id = NULL,
                        current_video_artifact_id = NULL
                    WHERE episode_id = ? AND shot_id = ? AND source_hash = ?
                    """,
                    (source_compile_hash, episode_id, shot_id, expected_source_hash),
                )
                if updated.rowcount != 1:
                    raise ValueError("expected_source_hash changed during shot compilation")
                self._invalidate_episode(connection, episode_id)
        return self.load()

    def set_frame_request(
        self,
        shot_id: str,
        *,
        expected_source_compile_hash: str,
        frame_request_hash: str,
    ) -> dict:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            shot = connection.execute(
                "SELECT * FROM shots WHERE episode_id = ? AND shot_id = ?",
                (episode_id, shot_id),
            ).fetchone()
            if shot is None or shot["source_compile_hash"] != expected_source_compile_hash:
                raise ValueError("expected_source_compile_hash does not match current shot")
            if shot["frame_request_hash"] != frame_request_hash:
                updated = connection.execute(
                    """
                    UPDATE shots
                    SET frame_request_hash = ?, video_request_hash = NULL,
                        current_frame_artifact_id = NULL, current_video_artifact_id = NULL
                    WHERE episode_id = ? AND shot_id = ? AND source_compile_hash = ?
                    """,
                    (frame_request_hash, episode_id, shot_id, expected_source_compile_hash),
                )
                if updated.rowcount != 1:
                    raise ValueError("source compilation changed during frame request update")
                self._invalidate_episode(connection, episode_id)
        return self.load()

    def set_video_request(
        self,
        shot_id: str,
        *,
        expected_frame_request_hash: str,
        expected_frame_hash: str,
        video_request_hash: str,
    ) -> dict:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            shot = connection.execute(
                "SELECT * FROM shots WHERE episode_id = ? AND shot_id = ?",
                (episode_id, shot_id),
            ).fetchone()
            if shot is None or shot["frame_request_hash"] != expected_frame_request_hash:
                raise ValueError("expected_frame_request_hash does not match current shot")
            frame = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE id = ? AND kind = 'frame' AND sha256 = ? AND input_hash = ?
                """,
                (
                    shot["current_frame_artifact_id"],
                    expected_frame_hash,
                    expected_frame_request_hash,
                ),
            ).fetchone()
            if frame is None:
                raise ValueError("expected_frame_hash does not match the current accepted frame")
            if shot["video_request_hash"] != video_request_hash:
                updated = connection.execute(
                    """
                    UPDATE shots
                    SET video_request_hash = ?, current_video_artifact_id = NULL
                    WHERE episode_id = ? AND shot_id = ?
                      AND frame_request_hash = ? AND current_frame_artifact_id = ?
                    """,
                    (
                        video_request_hash,
                        episode_id,
                        shot_id,
                        expected_frame_request_hash,
                        frame["id"],
                    ),
                )
                if updated.rowcount != 1:
                    raise ValueError("accepted frame changed during video request update")
                self._invalidate_episode(connection, episode_id)
        return self.load()

    def compile_episode(self, *, expected_script_hash: str, compile_hash: str) -> dict:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            episode = connection.execute(
                "SELECT current_script_hash, compile_hash FROM episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            if episode["current_script_hash"] != expected_script_hash:
                raise ValueError("expected_script_hash does not match current script")
            if episode["compile_hash"] != compile_hash:
                updated = connection.execute(
                    """
                    UPDATE episodes SET compile_hash = ?, current_export_artifact_id = NULL
                    WHERE episode_id = ? AND current_script_hash = ?
                    """,
                    (compile_hash, episode_id, expected_script_hash),
                )
                if updated.rowcount != 1:
                    raise ValueError("current script changed during episode compilation")
        return self.load()

    def apply_script_revision(
        self,
        idempotency_key: str,
        input_hash: str,
        spec: EpisodeSpec,
        *,
        expected_script_hash: str,
        plan: object,
    ) -> Operation:
        if plan is None:
            raise ValueError("a patch plan is required for script revision")
        if plan.input_hash != input_hash:
            raise ValueError("plan input_hash does not match revision input_hash")
        spec_dict = asdict(spec)
        script = _canonical_json(spec_dict)
        script_hash = _value_hash(spec_dict)
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self._resolve_episode_id(connection)
            existing = self._existing_operation(
                connection, episode_id, idempotency_key, "input", input_hash, None
            )
            if existing is not None:
                if (
                    existing["status"] != "completed"
                    or json.loads(existing["metadata"]).get("script_hash") != script_hash
                ):
                    raise ValueError("idempotency key is bound to a different script revision")
                return _operation(existing)
            episode = connection.execute(
                "SELECT current_script, current_script_hash FROM episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            if episode["current_script_hash"] != expected_script_hash:
                raise ValueError("expected_script_hash does not match current script")
            current_spec = _episode_from_json(episode["current_script"])
            if plan.expected_script_hash != expected_script_hash:
                raise ValueError("plan expected_script_hash does not match revision expectation")
            if plan.input_hash != input_hash:
                raise ValueError("plan input_hash does not match revision input_hash")
            from clean_studio.agents.repair import apply_patch

            planned_spec, planned_affected = apply_patch(current_spec, plan)
            if spec != planned_spec:
                raise ValueError("submitted spec does not match plan-applied episode")
            current_dict = asdict(current_spec)
            current_episode_fields = {**current_dict, "shots": None}
            revised_episode_fields = {**spec_dict, "shots": None}
            if (
                spec.episode_id != episode_id
                or current_episode_fields != revised_episode_fields
            ):
                raise ValueError("script revision only supports shot fields")
            if tuple(shot.shot_id for shot in spec.shots) != tuple(
                shot.shot_id for shot in current_spec.shots
            ):
                raise ValueError("script revision must preserve shot IDs and order")
            current_shots = {shot.shot_id: shot for shot in current_spec.shots}
            revised_shots = {shot.shot_id: shot for shot in spec.shots}
            affected = tuple(
                shot_id
                for shot_id in current_shots
                if asdict(current_shots[shot_id]) != asdict(revised_shots[shot_id])
            )
            if set(affected) != planned_affected or planned_affected != set(
                plan.affected_shot_ids
            ):
                raise ValueError("affected shot IDs do not match the applied patch plan")
            if not affected:
                raise ValueError("script revision has no changes")
            for affected_id in affected:
                if self._input_usage(connection, episode_id, affected_id) >= _INPUT_QUOTA:
                    raise ValueError(f"shot {affected_id} input quota exhausted: {_INPUT_QUOTA}")
            operation = self._insert_operation(
                connection,
                episode_id,
                idempotency_key,
                "input",
                input_hash,
                None,
                status="completed",
                metadata={"script_hash": script_hash},
                affected_shots=affected,
            )
            updated = connection.execute(
                """
                UPDATE episodes
                SET current_script = ?, current_script_hash = ?, compile_hash = NULL,
                    current_script_artifact_id = NULL, current_export_artifact_id = NULL
                WHERE episode_id = ? AND current_script_hash = ?
                """,
                (script, script_hash, episode_id, expected_script_hash),
            )
            if updated.rowcount != 1:
                raise ValueError("expected_script_hash changed during script revision")
            for affected_id in affected:
                connection.execute(
                    """
                    UPDATE shots
                    SET source_hash = ?, source_compile_hash = NULL,
                        frame_request_hash = NULL, video_request_hash = NULL,
                        current_frame_artifact_id = NULL, current_video_artifact_id = NULL
                    WHERE episode_id = ? AND shot_id = ?
                    """,
                    (
                        _value_hash(asdict(revised_shots[affected_id])),
                        episode_id,
                        affected_id,
                    ),
                )
            return _operation(operation)

    def mark_submitting(self, operation_id: str) -> Operation:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = self._get_operation(connection, operation_id)
            if operation["external_task_id"] is not None:
                return _operation(operation)
            if operation["status"] == "submitting":
                return _operation(operation)
            if operation["status"] != "pending":
                raise ValueError(
                    f"cannot start submission in {operation['status']} state"
                )
            connection.execute(
                """
                UPDATE operations
                SET status = 'submitting', error = NULL
                WHERE id = ?
                """,
                (operation_id,),
            )
            return _operation(self._get_operation(connection, operation_id))

    def mark_submitted(self, operation_id: str, external_task_id: str) -> Operation:
        if not external_task_id:
            raise ValueError("external task ID must be non-empty")
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = self._get_operation(connection, operation_id)
            if operation["external_task_id"] is not None:
                if operation["external_task_id"] != external_task_id:
                    raise ValueError("operation is bound to a different external task ID")
                return _operation(operation)
            if operation["status"] not in {"pending", "submitting", "submission_uncertain"}:
                raise ValueError(f"cannot submit operation in {operation['status']} state")
            connection.execute(
                """
                UPDATE operations
                SET status = 'submitted', external_task_id = ?, error = NULL
                WHERE id = ?
                """,
                (external_task_id, operation_id),
            )
            return _operation(self._get_operation(connection, operation_id))

    def fail_operation(
        self,
        operation_id: str,
        reason: str,
        *,
        not_accepted: bool = False,
        metadata: Mapping[str, object] | None = None,
    ) -> Operation:
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = self._get_operation(connection, operation_id)
            if (
                not_accepted
                and operation["status"] == "not_accepted"
                and operation["error"] == reason
            ):
                return _operation(operation)
            if operation["status"] == "completed":
                raise ValueError("completed operation cannot fail")
            if reason == "download_failed" and operation["external_task_id"] is None:
                raise ValueError("download_failed requires an external task ID")
            if not_accepted and (
                operation["kind"] not in {"image", "video"}
                or operation["status"] not in {"pending", "submitting", "submission_uncertain"}
                or operation["external_task_id"] is not None
            ):
                raise ValueError(
                    "not_accepted requires a reconciled pending image/video submission"
                )
            status = (
                "not_accepted"
                if not_accepted
                else "submission_uncertain"
                if reason == "submission_uncertain"
                else "failed"
            )
            if operation["status"] == "not_accepted" and status != "not_accepted":
                raise ValueError("not_accepted operation is final")
            merged_metadata = {
                **json.loads(operation["metadata"]),
                **dict(metadata or {}),
            }
            metadata_json = _canonical_json(merged_metadata)
            if operation["status"] == status and operation["error"] == reason:
                if metadata_json != operation["metadata"]:
                    connection.execute(
                        "UPDATE operations SET metadata = ? WHERE id = ?",
                        (metadata_json, operation_id),
                    )
                return _operation(self._get_operation(connection, operation_id))
            connection.execute(
                "UPDATE operations SET status = ?, error = ?, metadata = ? WHERE id = ?",
                (status, reason, metadata_json, operation_id),
            )
            return _operation(self._get_operation(connection, operation_id))

    def _read_receipt(self, path: str, expected_sha256: str) -> tuple[dict, str]:
        data = (self.path.parent / path).read_bytes()
        actual_sha256 = sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("receipt SHA-256 does not match actual receipt file")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("receipt must be a UTF-8 JSON object") from error
        if not isinstance(payload, dict):
            raise ValueError("receipt must be a JSON object")
        return payload, actual_sha256

    def _validate_receipt(
        self,
        operation: sqlite3.Row,
        receipt: Mapping[str, object],
        verdict: str | None,
    ) -> None:
        if not isinstance(receipt.get("receipt_id"), str) or not receipt["receipt_id"]:
            raise ValueError("receipt identity must be non-empty")
        expected = {
            "operation_id": operation["id"],
            "kind": operation["kind"],
            "input_hash": operation["input_hash"],
            "verdict": verdict,
        }
        for field, value in expected.items():
            if field not in receipt or receipt[field] != value:
                raise ValueError(f"receipt {field} does not match operation {field}")

    def complete_operation(
        self,
        operation_id: str,
        *,
        path: str | None = None,
        sha256: str | None = None,
        receipt_path: str | None = None,
        receipt_sha256: str | None = None,
        expected_current_hash: str | None = None,
        metadata: Mapping[str, object] | None = None,
        verdict: str | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> Operation:
        if verdict is not None and verdict not in _VALID_VERDICTS:
            raise ValueError("verdict must be accepted or rejected")
        if any(not isinstance(finding, dict) for finding in findings):
            raise TypeError("findings must be structured objects")
        if (path is None) != (sha256 is None):
            raise ValueError("artifact path and SHA-256 are required together")
        if (receipt_path is None) != (receipt_sha256 is None):
            raise ValueError("receipt path and receipt SHA-256 are required together")
        if path is not None:
            _relative_path(path)
            _artifact_hash(sha256)
        if receipt_path is not None:
            _relative_path(receipt_path)
            _artifact_hash(receipt_sha256)
        metadata_json = _canonical_json(dict(metadata or {}))
        findings_json = _canonical_json(deepcopy(tuple(findings)))

        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = self._get_operation(connection, operation_id)
            review_kind = _is_review_kind(operation["kind"])
            if operation["kind"] == "input":
                raise ValueError("input operations must use apply_script_revision")
            if verdict is not None and not review_kind:
                raise ValueError("only a review kind operation may write a verdict")
            if review_kind and verdict is None:
                raise ValueError("review completion requires a verdict")
            if (review_kind or operation["kind"] in _MEDIA_KINDS | {"upload"}) and receipt_path is None:
                raise ValueError("operation completion requires a receipt file")
            if operation["kind"] in _MEDIA_KINDS:
                if path is None:
                    raise ValueError("media operation requires a primary artifact")
                if expected_current_hash is None:
                    raise ValueError("media operation requires expected_current_hash")
                if expected_current_hash != operation["input_hash"]:
                    raise ValueError("expected_current_hash must equal operation input_hash")
            receipt = None
            actual_receipt_hash = None
            if receipt_path is not None:
                receipt, actual_receipt_hash = self._read_receipt(
                    receipt_path, receipt_sha256
                )
                self._validate_receipt(operation, receipt, verdict)
                if operation["kind"] == "upload" and receipt.get("target") != self._publish_target(
                    connection, operation["episode_id"]
                ):
                    raise ValueError("upload target does not match frozen publish_target")
            if operation["kind"] == "assembly":
                merged_metadata = dict(metadata or {})
                metadata_bundle = merged_metadata.get("export_bundle")
                receipt_bundle = receipt.get("export_bundle") if receipt is not None else None
                if metadata_bundle is not None and receipt_bundle is not None and metadata_bundle != receipt_bundle:
                    raise ValueError("assembly metadata and receipt export bundles differ")
                bundle = metadata_bundle if metadata_bundle is not None else receipt_bundle
                merged_metadata["export_bundle"] = _validated_export_bundle(
                    {"export_bundle": bundle}, sha256
                )
                metadata_json = _canonical_json(merged_metadata)
            if operation["status"] == "completed":
                self._check_completion_retry(
                    connection,
                    operation,
                    path=path,
                    sha256=sha256,
                    receipt_path=receipt_path,
                    receipt=receipt,
                    receipt_hash=actual_receipt_hash,
                    metadata_json=metadata_json,
                    verdict=verdict,
                    findings_json=findings_json,
                )
                return _operation(operation)
            if operation["status"] == "not_accepted":
                raise ValueError("not_accepted operation cannot complete")
            if receipt is not None:
                bound_id = connection.execute(
                    "SELECT operation_id FROM artifacts WHERE id = ?",
                    (receipt["receipt_id"],),
                ).fetchone()
                if bound_id is not None:
                    raise ValueError("receipt identity is already bound to an operation/hash")
                bound_path = connection.execute(
                    "SELECT operation_id FROM artifacts WHERE kind = 'receipt' AND path = ?",
                    (receipt_path,),
                ).fetchone()
                if bound_path is not None:
                    raise ValueError("receipt path is already bound to an operation/hash")

            artifact_kind = _ARTIFACT_KINDS.get(operation["kind"], operation["kind"])
            if path is not None:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        id, episode_id, operation_id, shot_id, kind, path,
                        sha256, input_hash, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"], operation["episode_id"], operation["id"],
                        operation["shot_id"], artifact_kind, path, sha256,
                        operation["input_hash"], metadata_json,
                    ),
                )
            if receipt is not None:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        id, episode_id, operation_id, shot_id, kind, path,
                        sha256, input_hash, metadata
                    ) VALUES (?, ?, ?, ?, 'receipt', ?, ?, ?, ?)
                    """,
                    (
                        receipt["receipt_id"], operation["episode_id"], operation["id"],
                        operation["shot_id"], receipt_path, actual_receipt_hash,
                        operation["input_hash"], _canonical_json(receipt),
                    ),
                )
            connection.execute(
                """
                UPDATE operations
                SET status = 'completed', receipt_path = ?, verdict = ?,
                    findings = ?, metadata = ?, error = NULL
                WHERE id = ?
                """,
                (receipt_path, verdict, findings_json, metadata_json, operation_id),
            )
            if operation["kind"] == "image":
                updated = connection.execute(
                    """
                    UPDATE shots
                    SET current_frame_artifact_id = ?, video_request_hash = NULL,
                        current_video_artifact_id = NULL
                    WHERE episode_id = ? AND shot_id = ? AND frame_request_hash = ?
                    """,
                    (
                        operation["id"], operation["episode_id"], operation["shot_id"],
                        expected_current_hash,
                    ),
                )
                if updated.rowcount == 1:
                    self._invalidate_episode(connection, operation["episode_id"])
            elif operation["kind"] == "video":
                updated = connection.execute(
                    """
                    UPDATE shots SET current_video_artifact_id = ?
                    WHERE episode_id = ? AND shot_id = ? AND video_request_hash = ?
                    """,
                    (
                        operation["id"], operation["episode_id"], operation["shot_id"],
                        expected_current_hash,
                    ),
                )
                if updated.rowcount == 1:
                    self._invalidate_episode(connection, operation["episode_id"])
            elif operation["kind"] == "assembly":
                connection.execute(
                    """
                    UPDATE episodes SET current_export_artifact_id = ?
                    WHERE episode_id = ? AND compile_hash = ?
                    """,
                    (operation["id"], operation["episode_id"], expected_current_hash),
                )
            return _operation(self._get_operation(connection, operation_id))

    def _check_completion_retry(
        self,
        connection: sqlite3.Connection,
        operation: sqlite3.Row,
        *,
        path: str | None,
        sha256: str | None,
        receipt_path: str | None,
        receipt: dict | None,
        receipt_hash: str | None,
        metadata_json: str,
        verdict: str | None,
        findings_json: str,
    ) -> None:
        artifacts = connection.execute(
            "SELECT * FROM artifacts WHERE operation_id = ?", (operation["id"],)
        ).fetchall()
        primary = [artifact for artifact in artifacts if artifact["kind"] != "receipt"]
        stored_receipt = next(
            (artifact for artifact in artifacts if artifact["kind"] == "receipt"), None
        )
        matches = (
            operation["receipt_path"] == receipt_path
            and operation["verdict"] == verdict
            and operation["findings"] == findings_json
            and operation["metadata"] == metadata_json
            and len(primary) == (1 if path is not None else 0)
            and (stored_receipt is not None) == (receipt_path is not None)
        )
        if path is not None and primary:
            matches = matches and primary[0]["path"] == path and primary[0]["sha256"] == sha256
        if receipt_path is not None and stored_receipt is not None and receipt is not None:
            matches = (
                matches
                and stored_receipt["id"] == receipt["receipt_id"]
                and stored_receipt["path"] == receipt_path
                and stored_receipt["sha256"] == receipt_hash
                and stored_receipt["metadata"] == _canonical_json(receipt)
            )
        if not matches:
            raise ValueError("idempotency conflict on completed operation")

    def usage(self, shot_id: str, kind: str | None = None) -> dict[str, int] | int:
        if kind is not None and kind not in {"image", "video", "input"}:
            raise ValueError(f"unknown quota kind: {kind}")
        with self._db(readonly=True) as connection:
            episode_id = self._resolve_episode_id(connection)
            counts = {"image": 0, "video": 0, "input": 0}
            rows = connection.execute(
                """
                SELECT kind, COUNT(*) AS count FROM operations
                WHERE episode_id = ? AND shot_id = ? AND kind IN ('image', 'video')
                  AND status != 'not_accepted'
                GROUP BY kind
                """,
                (episode_id, shot_id),
            ).fetchall()
            counts.update({row["kind"]: row["count"] for row in rows})
            counts["input"] = self._input_usage(connection, episode_id, shot_id)
        return counts[kind] if kind is not None else counts

    def refresh_frame_review(self, operation_id: str, review: Mapping[str, object]) -> None:
        """Mirror a re-review verdict onto an existing frame artifact.

        The re-review's own operation/receipt already exists; this only updates
        the artifact metadata so later readers see the current-policy verdict."""
        with self._db() as connection:
            row = connection.execute(
                "SELECT metadata FROM artifacts WHERE operation_id = ? AND kind = 'frame'",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"no frame artifact for operation {operation_id}")
            metadata = json.loads(row["metadata"] or "{}")
            metadata["review"] = dict(review)
            connection.execute(
                "UPDATE artifacts SET metadata = ? WHERE operation_id = ? AND kind = 'frame'",
                (_canonical_json(metadata), operation_id),
            )
            connection.execute(
                "UPDATE operations SET metadata = ? WHERE id = ?",
                (_canonical_json(metadata), operation_id),
            )

    def find_valid_review(self, content_hash: str, kind: str | None = None) -> Review | None:
        with self._db(readonly=True) as connection:
            episode_id = self._resolve_episode_id(connection)
            row = self._latest_valid_review(connection, episode_id, content_hash, kind)
            if row is None:
                return None
            metadata = json.loads(row["metadata"] or "{}")
            return Review(
                review_id=row["id"],
                content_hash=row["input_hash"],
                verdict=row["verdict"],
                findings=tuple(json.loads(row["findings"])),
                unresolved=tuple(metadata.get("unresolved_findings") or ()),
            )
