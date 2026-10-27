from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path

from clean_studio.assembly import ExportArtifact, TechnicalReport
from clean_studio.domain import EpisodeSpec, export_bundle_identity
from clean_studio.frozen.final_audio import (
    AUDIO_POLICY,
    run_audio_review,
    runtime_identity,
)
from clean_studio.frozen.final_review import (
    FRAME_GROUP_SIZE,
    VISUAL_POLICY,
    blocking_issues,
    caption_cues,
    extract_frames,
    group_schema,
    movie_duration,
    validate_group,
)
from clean_studio.frozen.llm import image_part
from clean_studio.frozen.music import file_sha256
from clean_studio.review_contract import (
    ReviewContractError,
    flatten_visual_issues,
    validate_audio_review,
    validate_visual_review,
)
from clean_studio.store import Store

REVIEW_FACTS_SCHEMA = "clean-studio-final-review-facts-v1"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value):
    return json.loads(_canonical(value))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return sha256(data).hexdigest()


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _project_root():
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT") or Path(__file__).resolve().parents[2]
    ).resolve()


class AcceptanceError(ValueError):
    status = "candidate"

    def __init__(self, message, *, reasons=(), export=None):
        self.reasons = tuple(reasons)
        self.export = export
        super().__init__(message)


@dataclass(frozen=True)
class AcceptedExport:
    export: ExportArtifact
    visual: Mapping[str, object]
    audio: Mapping[str, object]
    technical: TechnicalReport | Mapping[str, object]
    episode_id: str
    title: str
    status: str = "accepted"
    content_accepted: bool = True
    final_audio_accepted: bool = True

    @property
    def complete(self):
        return False

    @property
    def bundle_hash(self):
        return self.export.bundle_hash

    @property
    def export_sha256(self):
        return self.export.sha256

    @property
    def cover_sha256(self):
        return self.export.cover_sha256

    @property
    def visual_review_id(self):
        return self.visual["review_id"]

    @property
    def audio_review_id(self):
        return self.audio["review_id"]

    def __getitem__(self, key):
        return getattr(self, key)


def _load_manifest(export):
    try:
        if file_sha256(export.manifest_path) != export.manifest_sha256:
            raise AcceptanceError("export manifest bytes or SHA-256 changed", export=export)
        manifest = json.loads(Path(export.manifest_path).read_text(encoding="utf-8"))
    except AcceptanceError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"export manifest is unavailable or malformed: {error}", export=export) from error
    if not isinstance(manifest, dict) or manifest.get("schema") != "clean-studio-export-v1":
        raise AcceptanceError("current export requires a Clean export manifest", export=export)
    return manifest


def _validate_export_identity(export):
    if not isinstance(export, ExportArtifact) or export.kind != "export":
        raise AcceptanceError("acceptance requires the current Task6 ExportArtifact")
    if (
        export.status != "candidate"
        or export.content_accepted is not False
        or export.final_audio_accepted is not False
    ):
        raise AcceptanceError("formal acceptance must start from an unaccepted candidate")
    for label, path, expected in (
        ("video", export.path, export.sha256),
        ("cover", export.cover_path, export.cover_sha256),
        ("end card", export.end_card_path, export.end_card_sha256),
        ("subtitles", export.subtitle_path, export.subtitle_sha256),
        ("credits", export.credits_path, export.credits_sha256),
        ("manifest", export.manifest_path, export.manifest_sha256),
    ):
        try:
            actual = file_sha256(path)
        except OSError as error:
            raise AcceptanceError(f"current {label} bytes are unavailable: {error}", export=export) from error
        if actual != expected:
            raise AcceptanceError(
                f"current {label} bytes or SHA-256 changed: expected {expected}, got {actual}",
                export=export,
            )
    manifest = _load_manifest(export)
    if (
        manifest.get("episode_hash") != export.episode_hash
        or manifest.get("status") != "candidate"
        or manifest.get("content_accepted") is not False
        or manifest.get("final_audio_accepted") is not False
        or manifest.get("technical_evidence_scope") != "technical_only"
    ):
        raise AcceptanceError("export manifest is stale or crosses the candidate boundary")
    assets = manifest.get("assets") or {}
    for name, path, expected in (
        ("video", export.path, export.sha256),
        ("cover", export.cover_path, export.cover_sha256),
        ("end_card", export.end_card_path, export.end_card_sha256),
        ("subtitles", export.subtitle_path, export.subtitle_sha256),
        ("credits", export.credits_path, export.credits_sha256),
    ):
        row = assets.get(name) or {}
        if row.get("sha256") != expected or Path(str(row.get("path") or "")).resolve() != Path(path).resolve():
            raise AcceptanceError(f"export manifest {name} identity differs from the current export")
    shot_rows = manifest.get("shots")
    if not isinstance(shot_rows, list) or [row.get("shot_id") for row in shot_rows if isinstance(row, dict)] != list(export.shot_ids):
        raise AcceptanceError("current export does not contain its complete N/N shot timeline")
    return manifest


def final_review_facts(export, episode=None):
    manifest = _load_manifest(export)
    if episode is not None:
        if not isinstance(episode, EpisodeSpec):
            raise TypeError("final review facts require the current EpisodeSpec")
        episode_hash = sha256(_canonical(asdict(episode)).encode("utf-8")).hexdigest()
        if episode_hash != export.episode_hash:
            raise ValueError("episode hash does not match the current export")
    authored = {shot.shot_id: shot for shot in episode.shots} if episode is not None else {}
    shots = []
    timeline = []
    for row in manifest["shots"]:
        shot_id = row["shot_id"]
        shot = authored.get(shot_id)
        if episode is not None and shot is None:
            raise ValueError("current episode is missing an exported shot")
        projected = {
            "shot_id": shot_id,
            "scene_id": shot.scene_id if shot else row.get("scene_id"),
            "source_segments": list(shot.source_segments if shot else row.get("source_segments") or ()),
            "location": shot.location if shot else row.get("location"),
            "start_state": shot.start_state if shot else row.get("start_state"),
            "action": shot.action if shot else row.get("action"),
            "end_state": shot.end_state if shot else row.get("end_state"),
            "camera": shot.camera if shot else row.get("camera"),
            "shot_scale": shot.shot_scale if shot else row.get("shot_scale"),
            "visible_characters": list(shot.visible_characters) if shot else list(row.get("visible_characters") or ()),
            "dialogue": [
                asdict(line) for line in shot.dialogue
            ] if shot else _json_value(row.get("dialogue") or []),
            "duration_seconds": shot.duration_seconds if shot else row.get("duration_seconds"),
            "start_seconds": row.get("start_seconds"),
            "end_seconds": row.get("end_seconds"),
        }
        shots.append(projected)
        timeline.append(
            {
                "clip_id": shot_id,
                "shot_id": shot_id,
                "start": row.get("start_seconds"),
                "end": row.get("end_seconds"),
            }
        )
    if episode is not None and len(authored) != len(shots):
        raise ValueError("current episode has shots outside the current export")
    music = []
    for row in export.mixed_music:
        music.append(
            {
                key: _json_value(row[key])
                for key in (
                    "cue",
                    "start_seconds",
                    "end_seconds",
                    "gain_db",
                    "fade_in_seconds",
                    "fade_out_seconds",
                    "loop",
                    "limitations",
                )
                if key in row
            }
        )
    facts = {
        "schema": REVIEW_FACTS_SCHEMA,
        "export": {
            "bundle_hash": export.bundle_hash,
            "sha256": export.sha256,
            "cover_sha256": export.cover_sha256,
            "manifest_sha256": export.manifest_sha256,
            "episode_hash": export.episode_hash,
            "duration_seconds": export.expected_duration_seconds,
            "body_seconds": export.body_seconds,
            "expected_frames": export.expected_frames,
        },
        "episode": {
            "episode_id": manifest.get("episode_id"),
            "title": episode.title if episode is not None else manifest.get("title"),
            "style_id": episode.style_id if episode is not None else None,
        },
        "shots": shots,
        "timeline": timeline,
        "caption_cues": caption_cues(export.subtitle_path),
        "fixed_question": export.fixed_question,
        "fixed_options": _json_value(export.fixed_options),
        "music": music,
        "postprocessing": {
            "caption_origin": "approved current ASS in global movie time",
            "end_card": "host-owned three-second fixed question and options",
            "native_audio_preserved": True,
        },
    }
    return _json_value(facts)


def _facts_hash(facts):
    return sha256(_canonical(facts).encode("utf-8")).hexdigest()


def _validate_technical(export, technical):
    if technical is None:
        raise AcceptanceError("current export has no technical evidence")
    checks = _get(technical, "checks")
    valid = (
        _get(technical, "passed") is True
        and _get(technical, "fully_decoded") is True
        and _get(technical, "export_sha256") == export.sha256
        and _get(technical, "bundle_hash") == export.bundle_hash
        and _get(technical, "evidence_scope") == "technical_only"
        and _get(technical, "content_accepted") is False
        and _get(technical, "final_audio_accepted") is False
        and isinstance(checks, Mapping)
        and bool(checks)
        and all(isinstance(row, Mapping) and row.get("passed") is True for row in checks.values())
    )
    if not valid:
        raise AcceptanceError("technical evidence is incomplete or not bound to the current export hash")
    duration = _get(technical, "duration_seconds")
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or abs(duration - export.expected_duration_seconds) > 0.04
        or _get(technical, "expected_duration_seconds") != export.expected_duration_seconds
        or _get(technical, "expected_frames") != export.expected_frames
        or _get(technical, "actual_frames") != export.expected_frames
    ):
        raise AcceptanceError("technical duration or frame evidence differs from the current export")
    return technical


def _latest_receipt(store, export_hash, kind, *, required=True):
    try:
        with store._db(readonly=True) as connection:
            episode_id = store._resolve_episode_id(connection)
            row = store._latest_valid_review(connection, episode_id, export_hash, kind)
            if row is None:
                if required:
                    raise AcceptanceError(f"current export has no real {kind} receipt")
                return None
            artifact = connection.execute(
                """
                SELECT sha256 FROM artifacts
                WHERE operation_id = ? AND kind = 'receipt' AND path = ?
                """,
                (row["id"], row["receipt_path"]),
            ).fetchone()
            if artifact is None:
                if required:
                    raise AcceptanceError(f"{kind} has no immutable receipt artifact")
                return None
        receipt, _ = store._read_receipt(row["receipt_path"], artifact["sha256"])
        expected = {
            "operation_id": row["id"],
            "kind": kind,
            "input_hash": export_hash,
            "verdict": row["verdict"],
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise AcceptanceError(f"{kind} receipt does not match its latest real verdict")
        return receipt
    except AcceptanceError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        if required:
            raise AcceptanceError(f"{kind} receipt SHA-256 or verdict is invalid: {error}") from error
        return None


def _review_evidence(store, supplied, export_hash, kind):
    if store is not None:
        return _latest_receipt(store, export_hash, kind)
    if not isinstance(supplied, Mapping):
        raise AcceptanceError(f"{kind} requires formal structured evidence")
    return dict(supplied)


def _validate_visual(evidence, export):
    try:
        decision = validate_visual_review(evidence, export)
    except ReviewContractError as error:
        raise AcceptanceError(str(error)) from error
    return decision.accepted, "visual review rejected the current export"


def _validate_audio(evidence, export):
    try:
        decision = validate_audio_review(evidence, export)
    except ReviewContractError as error:
        raise AcceptanceError(str(error)) from error
    return decision.accepted, "complete audio review rejected the current export"


def _check_current_store_export(store, export):
    loaded = store.load()
    current_id = loaded["current_export_artifact_id"]
    current = next(
        (
            row
            for row in loaded["artifacts"]
            if row["id"] == current_id and row["kind"] == "export"
        ),
        None,
    )
    if current is None or current["sha256"] != export.sha256:
        raise AcceptanceError("export is not the Store's current accepted-export candidate")
    bundle = current["metadata"].get("export_bundle")
    try:
        valid_bundle = (
            isinstance(bundle, Mapping)
            and bundle.get("bundle_hash") == export.bundle_hash
            and bundle.get("video_sha256") == export.sha256
            and export_bundle_identity(bundle) == export.bundle_hash
        )
    except ValueError as error:
        raise AcceptanceError("Store current export bundle is malformed") from error
    if not valid_bundle:
        raise AcceptanceError("export bundle is not the Store's current export bundle")


def accept_export(export, visual, audio, technical, *, store=None):
    manifest = _validate_export_identity(export)
    if store is not None:
        _check_current_store_export(store, export)
    technical = _validate_technical(export, technical)
    visual = _review_evidence(store, visual, export.bundle_hash, "final_visual_review")
    audio = _review_evidence(store, audio, export.bundle_hash, "final_audio_review")
    visual_ok, visual_reason = _validate_visual(visual, export)
    audio_ok, audio_reason = _validate_audio(audio, export)
    if not visual_ok or not audio_ok:
        reasons = [reason for ok, reason in ((visual_ok, visual_reason), (audio_ok, audio_reason)) if not ok]
        raise AcceptanceError(
            "candidate: " + "; ".join(reasons), reasons=reasons, export=export
        )
    episode_id = manifest.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise AcceptanceError("current export lacks an episode identity")
    title = episode_id
    if store is not None:
        title = store.load()["spec"].title
    return AcceptedExport(
        export=export,
        visual=_json_value(visual),
        audio=_json_value(audio),
        technical=technical,
        episode_id=episode_id,
        title=title,
    )


def _cached_review(store, export, kind, policy, facts_hash):
    if store is None:
        return None
    receipt = _latest_receipt(store, export.bundle_hash, kind, required=False)
    if (
        receipt is not None
        and receipt.get("policy") == policy
        and receipt.get("facts_sha256") == facts_hash
        and receipt.get("cover_sha256", export.cover_sha256) == export.cover_sha256
        and receipt.get("complete") is True
    ):
        return receipt
    return None


def _begin_review(store, export, kind, facts_hash):
    if store is None:
        return None
    return store.begin_operation(
        f"{kind}:{export.bundle_hash}:{facts_hash}", kind, export.bundle_hash, None
    )


def _persist_review(store, operation, report):
    if store is None:
        return
    receipt = {
        **report,
        "receipt_id": f"receipt-{operation.id}",
        "operation_id": operation.id,
        "kind": operation.kind,
        "input_hash": operation.input_hash,
        "verdict": report["verdict"],
    }
    relative = f"receipts/{operation.id}.json"
    _write_json(store.path.parent / relative, receipt)
    store.complete_operation(
        operation.id,
        verdict=report["verdict"],
        findings=tuple(report.get("issues") or ()),
        receipt_path=relative,
        receipt_sha256=file_sha256(store.path.parent / relative),
        metadata={"policy": report["policy"], "facts_sha256": report["facts_sha256"]},
    )


def _review_work_dir(store, export, supplied, kind, facts_hash):
    if supplied is not None:
        root = Path(supplied)
    elif store is not None:
        root = store.path.parent / kind / facts_hash
    else:
        root = Path(export.path).parent / kind / facts_hash
    root.mkdir(parents=True, exist_ok=True)
    (root / "owner").write_text(
        "Clean Studio Task7 formal current-export review evidence\n", encoding="utf-8"
    )
    return root


def _base_report(export, operation, kind, policy, facts_hash):
    review_id = operation.id if operation is not None else f"{kind}-{export.bundle_hash}-{facts_hash[:16]}"
    return {
        "policy": policy,
        "complete": False,
        "passed": False,
        "verdict": None,
        "review_id": review_id,
        "content_hash": export.bundle_hash,
        "input_hash": export.bundle_hash,
        "bundle_hash": export.bundle_hash,
        "export_sha256": export.sha256,
        "video_sha256": export.sha256,
        "cover_sha256": export.cover_sha256,
        "facts_sha256": facts_hash,
        "issues": [],
        "findings": [],
        "errors": [],
    }


def review_visual(
    export,
    client,
    *,
    episode=None,
    store=None,
    work_dir=None,
    prompt_path=None,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
):
    _validate_export_identity(export)
    facts = final_review_facts(export, episode)
    facts_hash = _facts_hash(facts)
    cached = _cached_review(store, export, "final_visual_review", VISUAL_POLICY, facts_hash)
    if cached is not None:
        return cached
    operation = _begin_review(store, export, "final_visual_review", facts_hash)
    report = _base_report(export, operation, "final_visual_review", VISUAL_POLICY, facts_hash)
    report.update(frames=[], groups=[], responses=[])
    directory = _review_work_dir(store, export, work_dir, "final-visual-review", facts_hash)
    try:
        duration = movie_duration(export.path, ffprobe=ffprobe, runner=runner)
        if abs(duration - export.expected_duration_seconds) > 0.04:
            raise ValueError("actual movie duration differs from the current export")
        frames = extract_frames(
            export.path,
            facts["timeline"],
            duration=duration,
            body_seconds=export.body_seconds,
            work_dir=directory,
            ffmpeg=ffmpeg,
            runner=runner,
        )
        cues = caption_cues(export.subtitle_path)
        approved_end = [export.fixed_question] + [
            f"{row.get('label')}: {row.get('title')}" for row in export.fixed_options
        ]
        for row in frames:
            row["expected_captions"] = [
                cue["text"] for cue in cues if cue["start"] <= row["seconds"] < cue["end"]
            ]
            row["approved_labels"] = approved_end if row["clip_id"] == "end_card" else []
        report["frames"] = frames
        prompt = Path(
            prompt_path or _project_root() / "prompts/final-visual-review.md"
        ).read_text(encoding="utf-8")
        for group, start in enumerate(range(0, len(frames), FRAME_GROUP_SIZE), 1):
            selected = frames[start:start + FRAME_GROUP_SIZE]
            parts = []
            for number, row in enumerate(selected, 1):
                facts_row = {key: value for key, value in row.items() if key != "path"}
                parts.extend(
                    [
                        {
                            "type": "text",
                            "text": f"frame {number}: " + _canonical(facts_row),
                        },
                        image_part(Path(row["path"]), 768),
                    ]
                )
            parts.append(
                {
                    "type": "text",
                    "text": prompt + "\nCURRENT CLEAN FACTS\n" + _canonical(
                        {"episode_facts": facts, "selected_frames": [
                            {key: value for key, value in row.items() if key != "path"}
                            for row in selected
                        ]}
                    ),
                }
            )
            answer = None
            for revision in range(2):
                answer = client.ask_json(
                    parts,
                    group_schema(selected),
                    name="final_movie_continuity",
                    max_tokens=2600,
                    timeout=300,
                )
                response = {"group": group, "revision": revision + 1, "answer": answer}
                report["responses"].append(response)
                try:
                    validate_group(answer, selected)
                    break
                except ValueError as error:
                    response["protocol_error"] = str(error)
                    if revision == 1:
                        raise
                    parts.append(
                        {
                            "type": "text",
                            "text": (
                                "上一份完整回复：" + _canonical(answer)
                                + f"\n格式校验失败：{error}。保留有依据的实际观察与问题，纠正响应协议。"
                                "observations须非空；每个issue须有severity（must_fix或minor）；"
                                "passed须严格等于‘无must_fix级issue’；所有clip_ids必须来自本组并有对应所引用帧。"
                            ),
                        }
                    )
            for issue in answer["issues"]:
                issue["frame_paths"] = [selected[number - 1]["path"] for number in issue["frame_numbers"]]
            report["groups"].append(answer)
        _validate_export_identity(export)
        report["issues"] = flatten_visual_issues(report["groups"])
        report["findings"] = report["issues"]
        blocking = blocking_issues(report["issues"])
        report.update(
            complete=True,
            passed=not blocking,
            verdict="rejected" if blocking else "accepted",
        )
        decision = validate_visual_review(report, export)
        report.update(passed=decision.accepted, verdict=decision.verdict)
        _persist_review(store, operation, report)
    except Exception as error:
        report.update(
            complete=False,
            passed=False,
            verdict=None,
            errors=[f"{type(error).__name__}: {error}"],
        )
        if operation is not None:
            store.fail_operation(
                operation.id,
                "invalid_review_result" if isinstance(error, ValueError) else "review_service_error",
            )
    _write_json(directory / "last-result.json", report)
    return report


def review_audio(
    export,
    judge,
    *,
    episode=None,
    runtime,
    store=None,
    work_dir=None,
    worker_path=None,
    worker_runner=None,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
):
    _validate_export_identity(export)
    facts = final_review_facts(export, episode)
    eureka_identity = runtime_identity(runtime)
    facts_hash = _facts_hash({"facts": facts, "eureka_runtime": eureka_identity})
    cached = _cached_review(store, export, "final_audio_review", AUDIO_POLICY, facts_hash)
    if cached is not None:
        return cached
    operation = _begin_review(store, export, "final_audio_review", facts_hash)
    directory = _review_work_dir(store, export, work_dir, "final-audio-review", facts_hash)
    report = run_audio_review(
        export.path,
        facts,
        judge,
        runtime=runtime,
        work_dir=directory,
        worker_path=worker_path,
        worker_runner=worker_runner,
        runner=runner,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    base = _base_report(export, operation, "final_audio_review", AUDIO_POLICY, facts_hash)
    report = {**base, **report}
    report.update(
        review_id=base["review_id"],
        content_hash=export.bundle_hash,
        input_hash=export.bundle_hash,
        bundle_hash=export.bundle_hash,
        export_sha256=export.sha256,
        video_sha256=export.sha256,
        cover_sha256=export.cover_sha256,
        facts_sha256=facts_hash,
        runtime_identity=eureka_identity,
        findings=report.get("issues") or [],
    )
    if isinstance(report.get("decode"), dict):
        report["decode"]["export_sha256"] = export.sha256
    try:
        _validate_export_identity(export)
        if report.get("complete") is True:
            report["verdict"] = "accepted" if report.get("passed") is True else "rejected"
            decision = validate_audio_review(report, export)
            report.update(passed=decision.accepted, verdict=decision.verdict)
            _persist_review(store, operation, report)
        else:
            report["verdict"] = None
            if operation is not None:
                malformed = any("malformed" in error for error in report.get("errors") or [])
                store.fail_operation(
                    operation.id,
                    "invalid_review_result" if malformed else "review_service_error",
                )
    except Exception as error:
        report.update(
            complete=False,
            passed=False,
            verdict=None,
            errors=[*(report.get("errors") or []), f"{type(error).__name__}: {error}"],
        )
        if operation is not None:
            store.fail_operation(operation.id, "review_service_error")
    _write_json(directory / "last-result.json", report)
    return report


def publish(accepted, publisher):
    return publisher.publish(accepted)


def verify_upload(receipt, publisher):
    return publisher.verify_upload(receipt)
