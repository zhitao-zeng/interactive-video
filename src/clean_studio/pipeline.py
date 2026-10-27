from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path

from clean_studio.acceptance import accept_export, review_audio, review_visual
from clean_studio.agents.repair import PatchPlan, apply_patch, check_patch
from clean_studio.agents.review import ReviewSources
from clean_studio.assembly import ExportArtifact, TechnicalReport, assemble
from clean_studio.compiler import (
    Compilation,
    CompiledShot,
    TranslationReceipt,
    compile_episode,
    script_hash,
)
from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    MusicCue,
    Reference,
    Shot,
    export_bundle_metadata,
)
from clean_studio.frozen.provider import SubmissionUncertain
from clean_studio.media import MediaOperations
from clean_studio.providers.music import (
    AcceptedMusic,
    AudioEvidence,
    MusicAudition,
    MusicCandidate,
    adopt_music,
    search_music,
    validate_music_audition,
)
from clean_studio.source import build_reference_manifest, load_source
from clean_studio.store import Store, validate_frozen_config

STAGES = (
    "AUTHORING",
    "SOURCE_REVIEW",
    "COMPILING",
    "FIRST_FRAMES",
    "VIDEOS",
    "MUSIC",
    "ASSEMBLY",
    "FINAL_REVIEW",
    "PUBLISHING",
    "COMPLETE",
)

_TEMPORARY_ATTEMPTS = 3
_REVISION_FIELDS = (
    "scene_id",
    "source_segments",
    "location",
    "start_state",
    "action",
    "end_state",
    "camera",
    "shot_scale",
    "visible_characters",
    "dialogue",
    "reference_subjects",
    "duration_seconds",
)


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: asdict(item) if is_dataclass(item) else item,
    )


def _json(value):
    return json.loads(_canonical(value))


def _hash(value) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_scope_syntax(stop_after, only_shot):
    if stop_after is not None and stop_after not in STAGES[:-1]:
        raise ValueError(f"unknown stop_after stage: {stop_after}")
    if only_shot is not None and (
        not isinstance(only_shot, str) or not only_shot.strip()
    ):
        raise ValueError("only_shot must be a nonempty shot ID")


def _validate_recovery_scope(state, stop_after, only_shot):
    effective_stage = state["stage"]
    if (
        only_shot is None
        and effective_stage == "PUBLISHING"
        and state["current_export_artifact_id"] is None
    ):
        effective_stage = "FINAL_REVIEW"
    if stop_after is not None and STAGES.index(stop_after) < STAGES.index(effective_stage):
        raise ValueError("stop_after cannot be earlier than the current stage")
    if only_shot is not None and state["stage"] == "COMPLETE":
        raise ValueError("only_shot scoped validation cannot reuse a COMPLETE edition")
    if only_shot is not None and state["spec"].shots and all(
        shot.shot_id != only_shot for shot in state["spec"].shots
    ):
        raise ValueError(f"unknown shot for only_shot: {only_shot}")


def _file_hash(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _project_root() -> Path:
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[2]
    ).resolve()


def _code_identity() -> str:
    root = _project_root()
    files = sorted((root / "src/clean_studio").rglob("*.py"))
    prompts = root / "prompts"
    if prompts.is_dir():
        files.extend(sorted(path for path in prompts.rglob("*") if path.is_file()))
    digest = sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _public_config(value):
    validate_frozen_config(value)
    return _json(value)


_RELOCATABLE_CONFIG_PATHS = {
    "database_path",
    "store_path",
    "publication_dir",
    "search_root",
    "work_dir",
    "source_archive",
    "source_zip",
    "material_dir",
    "style_dir",
    "input_config",
}


def _execution_identity(value):
    if isinstance(value, Mapping):
        return {
            str(key): _execution_identity(item)
            for key, item in value.items()
            if str(key).lower() not in _RELOCATABLE_CONFIG_PATHS
        }
    if isinstance(value, (list, tuple)):
        return [_execution_identity(item) for item in value]
    return value


def _resolve(root: Path, value) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_config(config) -> tuple[dict, Path | None]:
    if isinstance(config, Mapping):
        return dict(config), None
    path = Path(config).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("produce config must be a JSON object")
    return payload, path


def _database_path(config: Mapping[str, object], root: Path, episode_id: str) -> Path:
    configured = config.get("database_path") or config.get("store_path")
    if configured:
        return _resolve(root, configured)
    return root / ".runtime" / episode_id / "studio.sqlite3"


def _frozen_config(config, source, references) -> dict:
    public = _public_config(config)
    identity = {
        "execution": _execution_identity(public),
        "models": _execution_identity(public.get("models", public.get("model"))),
        "input": {
            "input_id": public.get("input_id") or getattr(source, "material_id", None),
            "source_sha256": getattr(source, "archive_sha256", None),
            "reference_manifest_sha256": sha256(references.to_bytes()).hexdigest(),
        },
        "channel": public.get("channel") or public.get("video_channel") or "h3-first-frame",
        "publish_target": public.get("publish_target"),
    }
    return {
        **public,
        "publish_target": public.get("publish_target"),
        "_frozen_identity": identity,
        "_code_hash": _code_identity(),
    }


def _assert_frozen(stored, current) -> None:
    old = stored.get("_frozen_identity")
    new = current.get("_frozen_identity")
    if old != new:
        changed = sorted(
            key for key in set(old or {}) | set(new or {}) if (old or {}).get(key) != (new or {}).get(key)
        )
        raise ValueError(
            "frozen model/input/channel/publish target cannot change during recovery: "
            + ", ".join(changed)
        )


def _shot_from_dict(raw) -> Shot:
    values = dict(raw)
    values["dialogue"] = tuple(Dialogue(**line) for line in values["dialogue"])
    return Shot(**values)


def _compilation_from_dict(raw) -> Compilation:
    shots = []
    for value in raw["shots"]:
        receipt = dict(value["translation_receipt"])
        receipt["translation"] = dict(receipt["translation"])
        shots.append(
            CompiledShot(
                source_shot=_shot_from_dict(value["source_shot"]),
                style_id=value["style_id"],
                opening=value["opening"],
                h3_prompt=value["h3_prompt"],
                references=tuple(Reference(**row) for row in value["references"]),
                source_hash=value["source_hash"],
                compile_hash=value["compile_hash"],
                translation_cache_key=value["translation_cache_key"],
                translation_config_hash=value["translation_config_hash"],
                translation_receipt=TranslationReceipt(**receipt),
                frame_request_hash=value["frame_request_hash"],
                compiler_version=value["compiler_version"],
            )
        )
    return Compilation(
        episode_id=raw["episode_id"],
        shots=tuple(shots),
        compile_hash=raw["compile_hash"],
        source_script_hash=raw["source_script_hash"],
        compiler_version=raw["compiler_version"],
    )


def _technical_from_dict(raw):
    if raw is None:
        return None
    return TechnicalReport(**dict(raw))


def _export_from_metadata(metadata) -> ExportArtifact:
    raw = dict(metadata["export"])
    raw.pop("bundle_hash", None)
    technical = _technical_from_dict(raw.pop("technical", None))
    for field in ("shot_ids", "source_videos", "subtitles", "mixed_music", "fixed_options"):
        raw[field] = tuple(raw[field])
    export = ExportArtifact(**raw, technical=technical)
    bundle = metadata.get("export_bundle") or {}
    if bundle.get("bundle_hash") != export.bundle_hash:
        raise ValueError("stored export bundle identity is invalid")
    return export


def _music_from_dict(raw) -> AcceptedMusic:
    candidate = dict(raw["candidate"])
    candidate["cue"] = MusicCue(**candidate["cue"])
    candidate["audio_evidence"] = AudioEvidence(**candidate["audio_evidence"])
    candidate["limitations"] = tuple(candidate["limitations"])
    audition = dict(raw["audition"])
    audition["cue"] = MusicCue(**audition["cue"])
    audition["limitations"] = tuple(audition["limitations"])
    audition["listened_windows"] = tuple(audition["listened_windows"])
    return AcceptedMusic(
        candidate=MusicCandidate(**candidate),
        audition=MusicAudition(**audition),
        limitations=tuple(raw["limitations"]),
        source_start_seconds=raw["source_start_seconds"],
        source_seconds=raw["source_seconds"],
        gain_db=raw["gain_db"],
        fade_in_seconds=raw["fade_in_seconds"],
        fade_out_seconds=raw["fade_out_seconds"],
        loop=raw["loop"],
        final_audio_accepted=raw["final_audio_accepted"],
    )


@dataclass(frozen=True)
class StageOutcome:
    status: str
    reason: str | None = None
    next_action: str | None = None

    @classmethod
    def completed(cls):
        return cls("completed")

    @classmethod
    def blocked(cls, reason, next_action):
        return cls("blocked", reason, next_action)


@dataclass(frozen=True)
class RunResult:
    episode_id: str
    stage: str
    status: str
    stages: tuple[str, ...]
    blocked: str | None
    next_action: str | None
    launches: int
    recovery_validation: bool
    validation_scope: Mapping[str, object]

    @property
    def complete(self) -> bool:
        return (
            self.stage == "COMPLETE"
            and self.status == "complete"
            and not any(self.validation_scope.values())
        )

    def to_dict(self) -> dict:
        return {**_json(asdict(self)), "complete": self.complete}

    def __getitem__(self, key):
        return self.to_dict()[key]


class _StageBlock(Exception):
    def __init__(self, reason, next_action):
        self.reason = reason
        self.next_action = next_action
        super().__init__(reason)


class Pipeline:
    def __init__(
        self,
        store: Store | None,
        services,
        *,
        store_path: str | Path | None = None,
        frozen_config: Mapping[str, object] | None = None,
        source=None,
        references=None,
    ) -> None:
        if store is None and store_path is None:
            raise ValueError("Pipeline requires a Store or store_path")
        self.store = store
        self.services = services
        self.store_path = Path(store_path or store.path).resolve()
        self.frozen_config = dict(frozen_config or (store.load()["frozen_config"] if store else {}))
        self.source = source if source is not None else self._service("source")
        self.references = references if references is not None else self._service("references")
        self.episode_id = store.load()["episode_id"] if store else None
        self._stop_after = None
        self._only_shot = None
        self._history = []
        self._launch_evidence = None
        self._scoped_accepted = None

    def _service(self, name, default=None):
        if isinstance(self.services, Mapping):
            return self.services.get(name, default)
        return getattr(self.services, name, default)

    def _selected_shots(self):
        spec = self.store.load()["spec"]
        if self._only_shot is None:
            return spec.shots
        selected = next((shot for shot in spec.shots if shot.shot_id == self._only_shot), None)
        if selected is None:
            raise ValueError(f"unknown shot for only_shot: {self._only_shot}")
        return (selected,)

    def _record_validation(self) -> None:
        if self.store is None:
            return
        scope = {"stop_after": self._stop_after, "only_shot": self._only_shot}
        state = self.store.load()
        operation = self.store.begin_operation(
            f"validation:{state['launches']}:{_hash(scope)}",
            "validation",
            _hash(scope),
            None,
        )
        if operation.status != "completed":
            self.store.complete_operation(
                operation.id,
                metadata={
                    "scope": scope,
                    "recovery_validation": state["recovery_validation"],
                    "launches": state["launches"],
                },
            )

    def _record_launch(self) -> None:
        if self._launch_evidence is None:
            return
        evidence = self._launch_evidence
        self._launch_evidence = None
        state = self.store.load()
        operation = self.store.begin_operation(
            f"launch:{state['launches']}", "launch", evidence["current_code"], None
        )
        if operation.status != "completed":
            self.store.complete_operation(
                operation.id,
                metadata={
                    "launches": state["launches"],
                    "code": evidence["current_code"],
                    "recovery_validation": state["recovery_validation"],
                },
            )
        if evidence["previous_code"] != evidence["current_code"]:
            recovery = self.store.begin_operation(
                f"recovery:{state['launches']}",
                "recovery",
                evidence["current_code"],
                None,
            )
            if recovery.status != "completed":
                self.store.complete_operation(
                    recovery.id,
                    metadata={
                        "previous_code": evidence["previous_code"],
                        "current_code": evidence["current_code"],
                        "launches": state["launches"],
                    },
                )

    def run(self, episode_id, *, stop_after=None, only_shot=None) -> RunResult:
        _validate_scope_syntax(stop_after, only_shot)
        self.episode_id = episode_id
        self._stop_after = stop_after
        self._only_shot = only_shot
        self._history = []
        if self.store is not None:
            state = self.store.load(episode_id)
            _validate_recovery_scope(state, stop_after, only_shot)
            if (
                self._only_shot is None
                and state["stage"] == "PUBLISHING"
                and state["current_export_artifact_id"] is None
            ):
                state = self.store.advance("FINAL_REVIEW")
            self._record_validation()
        return self.advance()

    def advance(self) -> RunResult:
        attempts = 0
        while True:
            if self.store is None:
                stage = "AUTHORING"
                state = {"blocked": None}
            else:
                state = self.store.load()
                stage = state["stage"]
            if not self._history:
                self._history.append(stage)
            if state["blocked"] is not None:
                return self._result()
            if stage == "COMPLETE":
                return self._result()
            try:
                author = self._bind_author()
                if hasattr(author, "check_recovery"):
                    author.check_recovery()
                outcome = self._handler(stage)()
                attempts = 0
            except _StageBlock as error:
                outcome = StageOutcome.blocked(error.reason, error.next_action)
            except Exception as error:
                if self.store is None:
                    raise
                if _temporary_failure(error) and attempts < _TEMPORARY_ATTEMPTS - 1:
                    attempts += 1
                    continue
                next_action = (
                    "reconcile_submission"
                    if isinstance(error, SubmissionUncertain)
                    or self._has_submission_uncertain()
                    else f"resume {stage}"
                )
                outcome = StageOutcome.blocked(
                    f"{type(error).__name__}: {error}", next_action
                )
            if outcome.status == "blocked":
                self.store.advance(
                    stage,
                    blocked=outcome.reason or "stage blocked",
                    next_action=outcome.next_action or f"resume {stage}",
                )
                return self._result()
            if outcome.status != "completed":
                raise RuntimeError(f"invalid stage handler outcome: {outcome.status}")
            if self._stop_after == stage:
                return self._result()
            next_stage = STAGES[STAGES.index(stage) + 1]
            if next_stage == "COMPLETE" and self._only_shot is not None:
                return self._result()
            self.store.advance(next_stage)
            self._history.append(next_stage)

    @property
    def _scoped(self) -> bool:
        return self._stop_after is not None or self._only_shot is not None

    def _result(self) -> RunResult:
        state = self.store.load()
        return RunResult(
            episode_id=state["episode_id"],
            stage=state["stage"],
            status=state["status"],
            stages=tuple(self._history),
            blocked=state["blocked"],
            next_action=state["next_action"],
            launches=state["launches"],
            recovery_validation=state["recovery_validation"],
            validation_scope={"stop_after": self._stop_after, "only_shot": self._only_shot},
        )

    def _handler(self, stage):
        return {
            "AUTHORING": self._authoring,
            "SOURCE_REVIEW": self._source_review_stage,
            "COMPILING": self._compiling,
            "FIRST_FRAMES": self._first_frames,
            "VIDEOS": self._videos,
            "MUSIC": self._music_stage,
            "ASSEMBLY": self._assembly_stage,
            "FINAL_REVIEW": self._final_review,
            "PUBLISHING": self._publishing,
        }[stage]

    def _has_submission_uncertain(self):
        if self.store is None:
            return False
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            row = connection.execute(
                """
                SELECT 1 FROM operations
                WHERE episode_id = ? AND kind IN ('image', 'video')
                  AND status = 'submission_uncertain'
                LIMIT 1
                """,
                (episode_id,),
            ).fetchone()
        return row is not None

    def _bind_author(self):
        author = self._service("author")
        if (
            author is not None
            and self.store is not None
            and hasattr(author, "bind_store")
            and getattr(author, "store", None) is not self.store
        ):
            author.bind_store(self.store, self.episode_id)
        return author

    def _authoring(self):
        if self.store is not None and self.store.load()["spec"].shots:
            return StageOutcome.completed()
        author = self._bind_author()
        if author is None:
            raise ValueError("an authoring Agent is required for a new episode")
        spec = author.start()
        if not isinstance(spec, EpisodeSpec):
            raise TypeError("Author.start must return EpisodeSpec")
        if self.episode_id is not None and spec.episode_id != self.episode_id:
            raise ValueError("authored episode_id differs from the configured edition")
        if self.store is None:
            self.store = Store(self.store_path)
            self.store.create_episode(spec, self.frozen_config)
        else:
            self._persist_initial_authoring(spec)
        self.episode_id = spec.episode_id
        self._record_launch()
        self._record_validation()
        if self._only_shot is not None:
            self._selected_shots()
        return StageOutcome.completed()

    def _persist_initial_authoring(self, spec):
        state = self.store.load()
        previous_hash = state["current_script_hash"]
        script_hash_value = script_hash(spec)
        with self.store._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            episode_id = self.store._resolve_episode_id(connection)
            shot_count = connection.execute(
                "SELECT COUNT(*) FROM shots WHERE episode_id = ?", (episode_id,)
            ).fetchone()[0]
            if shot_count:
                raise ValueError("initial authoring cannot replace an existing shot plan")
            updated = connection.execute(
                """
                UPDATE episodes
                SET current_script = ?, current_script_hash = ?, compile_hash = NULL,
                    current_script_artifact_id = NULL, current_export_artifact_id = NULL
                WHERE episode_id = ? AND current_script_hash = ? AND stage = 'AUTHORING'
                """,
                (
                    _canonical(asdict(spec)),
                    script_hash_value,
                    episode_id,
                    previous_hash,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("initial authoring state changed before persistence")
            connection.executemany(
                "INSERT INTO shots (episode_id, shot_id, source_hash) VALUES (?, ?, ?)",
                [
                    (episode_id, shot.shot_id, _hash(asdict(shot)))
                    for shot in spec.shots
                ],
            )

    def _reviewer(self):
        reviewer = self._service("reviewer")
        if reviewer is None:
            factory = self._service("reviewer_factory")
            reviewer = factory(self.store) if factory is not None else None
        if reviewer is None:
            raise ValueError("an independent source reviewer is required")
        if hasattr(reviewer, "store") and reviewer.store is None:
            reviewer.store = self.store
        return reviewer

    def _review_source(self):
        spec = self.store.load()["spec"]
        review = self._reviewer().review(
            spec, ReviewSources(source=self.source, references=self.references)
        )
        if review.verdict not in {"accepted", "rejected"}:
            raise ValueError("source reviewer returned no valid verdict")
        return review

    def _source_review_stage(self):
        review = self._review_source()
        if review.verdict == "accepted":
            return StageOutcome.completed()
        if not review.findings:
            raise ValueError("rejected source review requires confirmed content findings")
        self._repair_content(review.findings)
        self._compile()
        revised = self._review_source()
        if revised.verdict == "accepted":
            return StageOutcome.completed()
        return StageOutcome.blocked(
            "source review still has confirmed content findings after typed apply",
            "resume SOURCE_REVIEW",
        )

    def _grant_from_findings(self, findings):
        spec = self.store.load()["spec"]
        grant = {}
        for finding in findings:
            path = finding.get("field_path") if isinstance(finding, Mapping) else None
            if isinstance(path, str):
                tokens = path.strip("/").split("/")
                if len(tokens) >= 3 and tokens[0] == "shots" and tokens[1].isdigit():
                    index = int(tokens[1])
                    field = tokens[2]
                    if index < len(spec.shots) and field in _REVISION_FIELDS:
                        grant.setdefault(spec.shots[index].shot_id, set()).add(field)
            shot_ids = []
            if isinstance(finding, Mapping):
                shot_ids = finding.get("clip_ids") or ([finding["shot_id"]] if finding.get("shot_id") else [])
            for shot_id in shot_ids:
                if any(shot.shot_id == shot_id for shot in spec.shots):
                    grant.setdefault(shot_id, set()).update(_REVISION_FIELDS)
        return {shot_id: sorted(fields) for shot_id, fields in grant.items()}

    def _full_spec_patch(self, revised):
        current = self.store.load()["spec"]
        old_shots = {shot.shot_id: shot for shot in current.shots}
        patch = {}
        for shot in revised.shots:
            old = old_shots.get(shot.shot_id)
            if old is None:
                continue
            changes = {}
            for field in _REVISION_FIELDS:
                before = _json(getattr(old, field))
                after = _json(getattr(shot, field))
                if before != after:
                    changes[field] = after
            if changes:
                patch[shot.shot_id] = changes
        return patch

    def _repair_content(self, findings, *, prefer_author=True):
        author = self._bind_author()
        current = self.store.load()["spec"]
        host_grant = self._grant_from_findings(findings)
        if not host_grant:
            raise ValueError("content findings do not identify a legal repair scope")
        revised = None
        result = None
        if prefer_author and author is not None and getattr(author, "current", None) == current and hasattr(author, "revise"):
            revised = author.revise(findings)
        else:
            agent = self._service("repair_agent")
            if agent is None:
                raise ValueError("content findings require an exception repair Agent")
            result = agent.repair(current, findings, grant=host_grant)
        if revised is None and isinstance(result, EpisodeSpec):
            revised = result
        if revised is not None:
            patch = self._full_spec_patch(revised)
        elif isinstance(result, PatchPlan):
            patch = result.patch
        else:
            if isinstance(result, Mapping) and result.get("ok") is False:
                raise _StageBlock(
                    "exception repair was not applied",
                    f"resume {self.store.load()['stage']}",
                )
            patch = result.get("patch") if isinstance(result, Mapping) else result
        plan = check_patch(current, patch, host_grant)
        revised, affected = apply_patch(current, plan)
        if not affected:
            raise ValueError("content repair applied no affected shots")
        operation = self.store.apply_script_revision(
            f"input:{plan.input_hash}",
            plan.input_hash,
            revised,
            expected_script_hash=script_hash(current),
            plan=plan,
        )
        if operation.status != "completed":
            raise ValueError("typed repair apply did not complete")
        return revised

    def _stored_compilation(self, *, current_only=False):
        state = self.store.load()
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            rows = connection.execute(
                "SELECT input_hash, metadata FROM operations WHERE episode_id = ? AND kind = 'compilation' AND status = 'completed' ORDER BY rowid DESC",
                (episode_id,),
            ).fetchall()
        for row in rows:
            if current_only and row["input_hash"] != state["current_script_hash"]:
                continue
            metadata = json.loads(row["metadata"])
            compilation = _compilation_from_dict(metadata["compilation"])
            if not current_only or compilation.compile_hash == state["compile_hash"]:
                return compilation
        return None

    def _compile(self):
        state = self.store.load()
        existing = self._stored_compilation(current_only=True)
        if existing is not None:
            return existing
        previous = self._stored_compilation()
        translation_config = self._service(
            "translation_config", self.frozen_config.get("translation_config")
        )
        compilation = compile_episode(
            state["spec"],
            self.references,
            self._service("translator"),
            previous=previous,
            translation_reviewer=self._service("translation_reviewer"),
            translation_config=translation_config,
            prompt_path=self._service("h3_translation_prompt"),
        )
        self.store.compile_episode(
            expected_script_hash=state["current_script_hash"],
            compile_hash=compilation.compile_hash,
        )
        operation = self.store.begin_operation(
            f"compilation:{compilation.compile_hash}",
            "compilation",
            compilation.source_script_hash,
            None,
        )
        if operation.status != "completed":
            self.store.complete_operation(
                operation.id, metadata={"compilation": _json(asdict(compilation))}
            )
        return compilation

    def _compilation(self):
        state = self.store.load()
        compilation = self._stored_compilation()
        if compilation is None or compilation.source_script_hash != state["current_script_hash"]:
            raise ValueError("current episode has no compilation")
        if state["compile_hash"] != compilation.compile_hash:
            self.store.compile_episode(
                expected_script_hash=state["current_script_hash"],
                compile_hash=compilation.compile_hash,
            )
        return compilation

    def _compiling(self):
        self._compile()
        return StageOutcome.completed()

    def _media(self):
        return MediaOperations(
            self.store,
            self._compilation(),
            image_provider=self._service("image_provider"),
            h3_provider=self._service("h3_provider"),
            frame_review_client=self._service("frame_review_client"),
            asr_provider=self._service("asr_provider"),
            video_reviewer=self._service("video_reviewer"),
            probe=self._service("video_probe"),
            submission_reconciler=self._service("submission_reconciler"),
            poll_interval=self._service("poll_interval", 5.0),
            poll_timeout=self._service("poll_timeout", 3600.0),
            sleeper=self._service("sleeper"),
            clock=self._service("clock"),
        )

    def _first_frames(self):
        media = self._media()
        for shot in self._selected_shots():
            media.ensure_first_frame(shot.shot_id)
        return StageOutcome.completed()

    def _videos(self):
        media = self._media()
        for shot in self._selected_shots():
            media.ensure_video(shot.shot_id)
        return StageOutcome.completed()

    def _stage_spec(self):
        spec = self.store.load()["spec"]
        if self._only_shot is None:
            return spec
        selected = self._selected_shots()[0]
        shot_start = 0
        for shot in spec.shots:
            if shot.shot_id == selected.shot_id:
                break
            shot_start += shot.duration_seconds
        shot_end = shot_start + selected.duration_seconds
        cues = []
        for cue in spec.music_cues:
            start = max(cue.start_seconds, shot_start) - shot_start
            end = min(cue.end_seconds, shot_end) - shot_start
            if start < end:
                cues.append(MusicCue(start, end, cue.mood, cue.soundscape))
        return replace(
            spec,
            target_seconds=selected.duration_seconds + 3,
            shots=(selected,),
            music_cues=tuple(cues),
        )

    def _music_input(self):
        spec = self._stage_spec()
        return _hash({
            "episode_id": spec.episode_id,
            "only_shot": self._only_shot,
            "music_cues": [asdict(cue) for cue in spec.music_cues],
        })

    def _stored_music(self):
        input_hash = self._music_input()
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            row = connection.execute(
                "SELECT metadata FROM operations WHERE episode_id = ? AND kind = 'music' AND input_hash = ? AND status = 'completed' ORDER BY rowid DESC LIMIT 1",
                (episode_id, input_hash),
            ).fetchone()
        if row is None:
            return None
        return tuple(_music_from_dict(value) for value in json.loads(row["metadata"])["accepted_music"])

    def _select_music(self, cue, candidates):
        agent = self._service("music_agent")
        if agent is None:
            raise ValueError("music content selection requires an exception Agent")
        selector = agent.select if hasattr(agent, "select") else agent
        selected = selector(candidates, cue)
        if isinstance(selected, AcceptedMusic):
            if selected.cue != cue or selected.candidate not in candidates:
                raise ValueError("music Agent accepted a different cue or candidate")
            if not selected.limitations or selected.final_audio_accepted is not False:
                raise ValueError("accepted music lacks explicit limitations and final-audio boundary")
            validate_music_audition(
                selected.candidate,
                selected.audition,
                source_start_seconds=selected.source_start_seconds,
                source_seconds=selected.source_seconds,
            )
            return selected
        if not isinstance(selected, Mapping):
            raise ValueError("music Agent did not adopt an actually auditioned candidate")
        candidate = next(
            (row for row in candidates if row.candidate_id == selected.get("candidate_id")),
            None,
        )
        if candidate is None:
            raise ValueError("music Agent selected an unknown candidate")
        options = {
            key: selected[key]
            for key in (
                "source_start_seconds",
                "source_seconds",
                "gain_db",
                "fade_in_seconds",
                "fade_out_seconds",
                "loop",
            )
            if key in selected
        }
        judgment = selected.get("judgment", selected)
        return adopt_music(candidate, judgment, **options)

    def _music(self):
        stored = self._stored_music()
        if stored is not None:
            return stored
        accepted = []
        for cue in self._stage_spec().music_cues:
            candidates = search_music(cue, provider=self._service("music_provider"))
            if not candidates:
                raise _StageBlock("music search returned no candidates", "resume MUSIC")
            accepted.append(self._select_music(cue, candidates))
        input_hash = self._music_input()
        operation = self.store.begin_operation(
            f"music:{input_hash}", "music", input_hash, None
        )
        if operation.status != "completed":
            self.store.complete_operation(
                operation.id,
                metadata={"accepted_music": [_json(asdict(row)) for row in accepted]},
            )
        return tuple(accepted)

    def _music_stage(self):
        self._music()
        return StageOutcome.completed()

    def _current_export(self):
        state = self.store.load()
        artifact_id = state["current_export_artifact_id"]
        artifact = next(
            (
                row for row in state["artifacts"]
                if row["id"] == artifact_id and row["kind"] == "export"
            ),
            None,
        )
        return _export_from_metadata(artifact["metadata"]) if artifact is not None else None

    def _scoped_export(self):
        state = self.store.load()
        candidates = [
            row for row in state["artifacts"]
            if row["kind"] == "validation_assembly"
            and row["metadata"].get("scope", {}).get("only_shot") == self._only_shot
        ]
        if not candidates:
            return None
        return _export_from_metadata(candidates[-1]["metadata"])

    def _assemble(self):
        if self._only_shot is None:
            existing = self._current_export()
        else:
            existing = self._scoped_export()
        if existing is not None:
            return existing
        compilation = self._compilation()
        full = self._only_shot is None
        input_hash = compilation.compile_hash if full else _hash(
            {"compile_hash": compilation.compile_hash, "only_shot": self._only_shot}
        )
        kind = "assembly" if full else "validation_assembly"
        operation = self.store.begin_operation(
            f"{kind}:{input_hash}", kind, input_hash, None
        )
        if operation.status == "completed":
            artifact = next(
                row for row in self.store.load()["artifacts"] if row["operation_id"] == operation.id
            )
            return _export_from_metadata(artifact["metadata"])
        media = self._media()
        videos = [media.ensure_video(shot.shot_id) for shot in self._selected_shots()]
        spec = self._stage_spec()
        music = self._music()
        output = self.store.path.parent / ("exports" if full else "validation-exports") / operation.id
        assemble_function = self._service("assemble", assemble)
        export = assemble_function(
            spec,
            videos,
            music,
            output_dir=output,
            media_root=self.store.path.parent,
            font_path=self._service("font_path"),
            preset=self._service("ffmpeg_preset", "veryfast"),
        )
        metadata = {
            "export": _json(asdict(export)),
            "export_bundle": export_bundle_metadata(export),
            "technical": _json(asdict(export.technical)),
            "scope": {"only_shot": self._only_shot},
        }
        receipt = {
            "receipt_id": f"receipt-{operation.id}",
            "operation_id": operation.id,
            "kind": operation.kind,
            "input_hash": operation.input_hash,
            "verdict": None,
            "export_bundle": metadata["export_bundle"],
        }
        relative_receipt = f"receipts/{operation.id}.json"
        receipt_path = self.store.path.parent / relative_receipt
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(_canonical(receipt), encoding="utf-8")
        arguments = {
            "path": Path(export.path).relative_to(self.store.path.parent).as_posix(),
            "sha256": export.sha256,
            "receipt_path": relative_receipt,
            "receipt_sha256": _file_hash(receipt_path),
            "metadata": metadata,
        }
        if full:
            arguments["expected_current_hash"] = compilation.compile_hash
        self.store.complete_operation(operation.id, **arguments)
        return export

    def _assembly_stage(self):
        self._assemble()
        return StageOutcome.completed()

    def _review_current_export(self):
        export = self._current_export() if self._only_shot is None else self._scoped_export()
        if export is None:
            export = self._assemble()
        full = self._only_shot is None
        spec = self._stage_spec()
        visual_function = self._service("review_visual", review_visual)
        visual = visual_function(
            export,
            self._service("final_visual_client"),
            episode=spec,
            store=self.store if full else None,
            work_dir=self.store.path.parent / "final-visual" / export.bundle_hash,
            prompt_path=self._service("final_visual_prompt"),
        )
        audio_function = self._service("review_audio", review_audio)
        audio = audio_function(
            export,
            self._service("final_audio_judge"),
            episode=spec,
            runtime=self._service("eureka_runtime"),
            store=self.store if full else None,
            work_dir=self.store.path.parent / "final-audio" / export.bundle_hash,
            worker_runner=self._service("audio_worker_runner"),
        )
        for name, report in (("visual", visual), ("audio", audio)):
            if report.get("complete") is not True:
                detail = "; ".join(report.get("errors") or []) or f"{name} review incomplete"
                if any(token in detail.lower() for token in ("temporary", "timeout", "503", "connection")):
                    raise ConnectionError(detail)
                raise _StageBlock(detail, "resume FINAL_REVIEW")
        if visual.get("verdict") != "accepted" or audio.get("verdict") != "accepted":
            findings = [
                *list(visual.get("issues") or visual.get("findings") or []),
                *list(audio.get("issues") or audio.get("findings") or []),
            ]
            return None, findings
        accepted = accept_export(
            export,
            visual,
            audio,
            export.technical,
            store=self.store if full else None,
        )
        if not full:
            self._scoped_accepted = accepted
        return accepted, ()

    def _restore_final_derivatives(self):
        self._compile()
        source_review = self._review_source()
        if source_review.verdict != "accepted":
            if not source_review.findings:
                raise ValueError("rejected source review requires content findings")
            self._repair_content(source_review.findings, prefer_author=False)
            self._compile()
            source_review = self._review_source()
        if source_review.verdict != "accepted":
            raise _StageBlock(
                "revised source still has confirmed content findings",
                "resume FINAL_REVIEW",
            )
        media = self._media()
        for shot in self._selected_shots():
            media.ensure_first_frame(shot.shot_id)
            media.ensure_video(shot.shot_id)
        self._assemble()

    def _final_review(self):
        if self._only_shot is None and _underlying_status(self.store) in {"accepted", "complete"}:
            return StageOutcome.completed()
        if self._only_shot is None and self._current_export() is None:
            self._restore_final_derivatives()
        accepted, findings = self._review_current_export()
        if accepted is not None:
            return StageOutcome.completed()
        if self._only_shot is not None:
            raise _StageBlock(
                "scoped export has confirmed final-review content findings",
                "resume FINAL_REVIEW",
            )
        if not findings:
            raise ValueError("rejected final review requires content findings")
        self._repair_content(findings, prefer_author=False)
        self._restore_final_derivatives()
        accepted, findings = self._review_current_export()
        if accepted is None:
            raise _StageBlock(
                "rebuilt export still has confirmed final-review content findings",
                "resume FINAL_REVIEW",
            )
        return StageOutcome.completed()

    def _publisher(self):
        publisher = self._service("publisher")
        if publisher is None:
            factory = self._service("publisher_factory")
            publisher = factory(self.store) if factory is not None else None
        if publisher is None:
            raise ValueError("a ModelScope publisher is required")
        return publisher

    def _publishing(self):
        if self._only_shot is not None:
            if self._scoped_accepted is None:
                outcome = self._final_review()
                if outcome.status != "completed":
                    raise _StageBlock(outcome.reason, outcome.next_action)
            publisher = self._service("validation_publisher")
            if publisher is None:
                factory = self._service("validation_publisher_factory")
                publisher = factory(self.store, self._only_shot) if factory is not None else None
            if publisher is None:
                raise _StageBlock(
                    "scoped validation cannot upload without a verified validation publisher",
                    "resume PUBLISHING",
                )
            receipt = publisher.publish(self._scoped_accepted)
            evidence = receipt if receipt.complete else publisher.verify_upload(receipt)
            if not evidence.complete:
                raise _StageBlock("scoped upload could not be remotely verified", "resume PUBLISHING")
            return StageOutcome.completed()
        if _underlying_status(self.store) == "complete":
            return StageOutcome.completed()
        export = self._current_export()
        accepted = accept_export(export, None, None, export.technical, store=self.store)
        receipt = self._publisher().publish(accepted)
        if _underlying_status(self.store) != "complete":
            evidence = self._publisher().verify_upload(receipt)
            if not evidence.complete:
                raise _StageBlock("upload could not be remotely verified", "resume PUBLISHING")
            self._persist_upload(receipt, evidence)
        if _underlying_status(self.store) != "complete":
            raise _StageBlock("verified upload receipt does not match current export", "resume PUBLISHING")
        return StageOutcome.completed()

    def _persist_upload(self, receipt, evidence):
        export = self._current_export()
        target = self.frozen_config.get("publish_target")
        operation = self.store.begin_operation(
            f"upload:{target}:{export.bundle_hash}",
            "upload",
            export.bundle_hash,
            None,
        )
        payload = {
            "receipt_id": f"receipt-{operation.id}",
            "operation_id": operation.id,
            "kind": "upload",
            "input_hash": export.bundle_hash,
            "verdict": None,
            "target": target,
            "bundle_hash": export.bundle_hash,
            "verified": evidence.complete,
            "private": evidence.private,
            "visibility": evidence.visibility,
            "remote_object": evidence.remote_object,
            "remote_sha256": evidence.remote_sha256,
            "cover_object": evidence.cover_object,
            "cover_sha256": evidence.cover_sha256,
        }
        relative = f"receipts/{operation.id}.json"
        path = self.store.path.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(payload), encoding="utf-8")
        self.store.complete_operation(
            operation.id,
            receipt_path=relative,
            receipt_sha256=_file_hash(path),
            metadata={"verified": evidence.complete},
        )

    def status(self, episode_id=None) -> dict:
        if episode_id is not None:
            self.store.load(episode_id)
        return _status_store(Store.open_readonly(self.store.path))


def _temporary_failure(error) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    try:
        import httpx

        if isinstance(error, httpx.TransportError):
            return True
    except ImportError:
        pass
    return False


def _underlying_status(store: Store) -> str:
    with store._db(readonly=True) as connection:
        episode_id = store._resolve_episode_id(connection)
        row = connection.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        episode = dict(row)
        episode["blocked"] = None
        return store._projection(connection, episode)


def _valid_video(store, shot, artifacts) -> bool:
    artifact_id = shot["current_video_artifact_id"]
    artifact = next(
        (
            row for row in artifacts
            if row["id"] == artifact_id and row["kind"] == "video"
        ),
        None,
    )
    if artifact is None or artifact["input_hash"] != shot["video_request_hash"]:
        return False
    quality = artifact["metadata"].get("quality") or {}
    technical = quality.get("technical") or {}
    speech = quality.get("asr") or {}
    review = quality.get("review") or {}
    valid = (
        quality.get("passed") is True
        and technical.get("passed") is True
        and technical.get("fully_decoded") is True
        and technical.get("native_audio") is True
        and technical.get("video_sha256") == artifact["sha256"]
        and speech.get("passed") is True
        and speech.get("evaluation") == "clean-current-shot-dialogue-cer-v2-phonetic"
        and speech.get("video_sha256") == artifact["sha256"]
        and review.get("verdict") == "accepted"
        and review.get("video_sha256") == artifact["sha256"]
        and review.get("findings") == []
    )
    if not valid:
        return False
    path = store.path.parent / artifact["path"]
    try:
        return path.is_file() and _file_hash(path) == artifact["sha256"]
    except OSError:
        return False


def _latest_validation(store):
    with store._db(readonly=True) as connection:
        episode_id = store._resolve_episode_id(connection)
        row = connection.execute(
            "SELECT metadata FROM operations WHERE episode_id = ? AND kind = 'validation' AND status = 'completed' ORDER BY rowid DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
    return json.loads(row["metadata"]) if row is not None else {"scope": {"stop_after": None, "only_shot": None}}


def _scoped_uploads(store, artifacts):
    bundles = {
        artifact["metadata"].get("export_bundle", {}).get("bundle_hash")
        for artifact in artifacts
        if artifact["kind"] == "validation_assembly"
    }
    bundles.discard(None)
    if not bundles:
        return 0
    with store._db(readonly=True) as connection:
        episode_id = store._resolve_episode_id(connection)
        rows = connection.execute(
            """
            SELECT operations.input_hash, artifacts.metadata
            FROM operations
            JOIN artifacts ON artifacts.operation_id = operations.id
             AND artifacts.kind = 'receipt' AND artifacts.path = operations.receipt_path
            WHERE operations.episode_id = ? AND operations.kind = 'upload'
              AND operations.status = 'completed'
            ORDER BY operations.rowid
            """,
            (episode_id,),
        ).fetchall()
    latest = {}
    for row in rows:
        if row["input_hash"] in bundles:
            latest[row["input_hash"]] = json.loads(row["metadata"])
    return sum(
        receipt.get("verified") is True
        and receipt.get("private") is True
        and receipt.get("visibility") == "private"
        and bool(receipt.get("remote_object"))
        for receipt in latest.values()
    )


def _status_store(store: Store) -> dict:
    loaded = store.load()
    underlying = _underlying_status(store)
    artifacts = loaded["artifacts"]
    valid = sum(_valid_video(store, shot, artifacts) for shot in loaded["shots"])
    validation = _latest_validation(store)
    scope = validation.get("scope") or {"stop_after": None, "only_shot": None}
    scoped = any(scope.values())
    validation_uploads = _scoped_uploads(store, artifacts)
    production_validation = (
        loaded["stage"] == "COMPLETE"
        and loaded["status"] == "complete"
        and not loaded["recovery_validation"]
        and not scoped
    )
    nature = (
        "scoped_validation"
        if scoped
        else "recovery_validation"
        if loaded["recovery_validation"]
        else "from_scratch_unattended"
    )
    blocker = None
    if loaded["blocked"] is not None:
        blocker = {
            "stage": loaded["stage"],
            "reason": loaded["blocked"],
            "next_action": loaded["next_action"],
        }
    return {
        "episode_id": loaded["episode_id"],
        "stage": loaded["stage"],
        "status": loaded["status"],
        "valid_videos": valid,
        "planned_videos": len(loaded["spec"].shots),
        "accepted_exports": int(underlying in {"accepted", "complete"}),
        "candidate_exports": int(underlying == "candidate"),
        "uploads": validation_uploads if scope.get("only_shot") else int(underlying == "complete"),
        "validation_uploads": validation_uploads,
        "current_blocker": blocker,
        "blocked": loaded["blocked"],
        "next_action": loaded["next_action"],
        "launches": loaded["launches"],
        "recovery_validation": loaded["recovery_validation"],
        "production_validation": production_validation,
        "validation_nature": nature,
        "validation_scope": scope,
    }


def status(config, *, episode_id=None) -> dict:
    root = _project_root()
    direct_episode = (
        episode_id is None
        and isinstance(config, (str, Path))
        and not Path(config).is_file()
        and Path(config).suffix != ".json"
    )
    if isinstance(config, Store):
        store = Store.open_readonly(config.path)
    elif direct_episode:
        store = Store.open_readonly(_database_path({}, root, str(config)))
    else:
        payload, path = _load_config(config)
        selected = episode_id or payload.get("episode_id")
        if not selected:
            raise ValueError("status requires episode_id when config has no episode_id")
        store = Store.open_readonly(_database_path(payload, root, selected))
    if episode_id is not None:
        store.load(episode_id)
    return _status_store(store)


def produce(
    config,
    *,
    services,
    stop_after=None,
    only_shot=None,
) -> RunResult:
    _validate_scope_syntax(stop_after, only_shot)
    payload, _ = _load_config(config)
    root = _project_root()
    source = services.get("source") if isinstance(services, Mapping) else getattr(services, "source", None)
    references = services.get("references") if isinstance(services, Mapping) else getattr(services, "references", None)
    if source is None:
        archive = payload.get("source_archive") or payload.get("source_zip")
        if not archive:
            raise ValueError("source_archive is required")
        source = load_source(_resolve(root, archive))
    if references is None:
        references = build_reference_manifest(
            source,
            _resolve(root, payload["material_dir"]),
            _resolve(root, payload["style_dir"]),
            config_path=_resolve(root, payload["input_config"]) if payload.get("input_config") else None,
        )
    episode_id = payload.get("episode_id") or source.video_id
    database = _database_path(payload, root, episode_id)
    frozen = _frozen_config(payload, source, references)
    current_code = frozen["_code_hash"]
    recovering = database.is_file()
    launch_evidence = {"previous_code": current_code, "current_code": current_code}
    if recovering:
        loaded = Store.open_readonly(database).load(episode_id)
        _assert_frozen(loaded["frozen_config"], frozen)
        _validate_recovery_scope(loaded, stop_after, only_shot)
        launch_evidence["previous_code"] = loaded["frozen_config"].get("_code_hash")
        store = Store(database)
        if loaded["stage"] != "COMPLETE":
            store.advance(loaded["stage"], new_launch=True)
        frozen = loaded["frozen_config"]
    else:
        draft = EpisodeSpec(
            episode_id=episode_id,
            title=source.title,
            target_seconds=source.target_seconds,
            style_id=references.style_id,
            shots=(),
            fixed_question=source.fixed_question,
            fixed_options=source.fixed_options,
            music_cues=(),
        )
        store = Store(database)
        store.create_episode(draft, frozen)
        loaded = store.load(episode_id)
    pipeline = Pipeline(
        store,
        services,
        store_path=database,
        frozen_config=frozen,
        source=source,
        references=references,
    )
    pipeline._launch_evidence = launch_evidence
    if loaded["stage"] != "COMPLETE":
        pipeline._record_launch()
    return pipeline.run(episode_id, stop_after=stop_after, only_shot=only_shot)
