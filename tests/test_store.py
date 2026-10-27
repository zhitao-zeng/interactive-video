import json
import sqlite3
from dataclasses import fields, replace
from hashlib import sha256
from typing import get_type_hints

import pytest

from clean_studio.agents.repair import check_patch
from clean_studio.domain import EpisodeSpec, FixedOption, Operation, Shot
from clean_studio.frozen.final_audio import AUDIO_POLICY
from clean_studio.frozen.final_review import VISUAL_POLICY
from clean_studio.store import Store

PUBLISH_TARGET = "team/private-dataset"


def _episode() -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="episode-1",
        title="失物海",
        target_seconds=180,
        style_id="travel-live",
        shots=(
            Shot(
                shot_id="shot-1",
                scene_id="S1",
                source_segments=("S1/flow/0",),
                location="码头",
                start_state="知遥站在码头，船灯熄灭",
                action="知遥点亮船灯",
                end_state="知遥手持点亮的船灯站在码头",
                camera="固定机位",
                shot_scale="中景",
                visible_characters=("C01",),
                dialogue=(),
                reference_subjects=("C01",),
                duration_seconds=9,
            ),
        ),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


def _two_shot_episode() -> EpisodeSpec:
    episode = _episode()
    second = replace(
        episode.shots[0], shot_id="shot-2", source_segments=("S1/flow/1",)
    )
    return replace(episode, shots=(episode.shots[0], second))


def _revision_plan(current, revised):
    patch = {}
    grant = {}
    for old, new in zip(current.shots, revised.shots):
        changes = {}
        for field in fields(Shot):
            if field.name in {"shot_id", "extra_fields"}:
                continue
            if getattr(old, field.name) != getattr(new, field.name):
                changes[field.name] = getattr(new, field.name)
        if changes:
            patch[old.shot_id] = changes
            grant[old.shot_id] = set(changes)
    return check_patch(current, patch, grant)


def _config():
    return {"model": "frozen", "max_launches": 3, "publish_target": PUBLISH_TARGET}


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _bundle_for_content(content):
    parts = {
        "schema": "clean-studio-export-bundle-v1",
        "video_sha256": _hash(content),
        "cover_sha256": _hash(content + ":cover"),
        "end_card_sha256": _hash(content + ":end"),
        "subtitle_sha256": _hash(content + ":subtitles"),
        "credits_sha256": _hash(content + ":credits"),
        "manifest_sha256": _hash(content + ":manifest"),
        "episode_hash": _hash("episode:" + content),
        "expected_duration_seconds": 12.0,
        "body_seconds": 9.0,
        "expected_frames": 300,
        "shot_ids": ["shot-1"],
        "fixed_question": "下一站？",
        "fixed_options": [{"label": "A", "target_world": "W11"}],
        "mixed_music": [],
        "subtitles": [],
        "source_videos": [],
    }
    encoded = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**parts, "bundle_hash": sha256(encoded).hexdigest()}


def _bundle(store, bundle_hash):
    for artifact in store.load()["artifacts"]:
        bundle = artifact["metadata"].get("export_bundle")
        if bundle and bundle["bundle_hash"] == bundle_hash:
            return bundle
    raise KeyError(bundle_hash)


def _receipt(operation, *, receipt_id=None, verdict=None, **evidence):
    return {
        "receipt_id": receipt_id or f"receipt-{operation.id}",
        "operation_id": operation.id,
        "kind": operation.kind,
        "input_hash": operation.input_hash,
        "verdict": verdict,
        **evidence,
    }


def _write_receipt(store, operation, receipt=None, *, path=None, pretty=False):
    payload = _receipt(operation) if receipt is None else receipt
    if pretty:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    else:
        data = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    relative_path = path or f"receipts/{operation.id}.json"
    actual_path = store.path.parent / relative_path
    actual_path.parent.mkdir(parents=True, exist_ok=True)
    actual_path.write_bytes(data)
    return relative_path, sha256(data).hexdigest()


@pytest.fixture()
def store(tmp_path):
    store = Store(tmp_path / "studio.sqlite3")
    store.create_episode(_episode(), _config())
    return store


def _begin_image(store, key="image-1", input_hash="frame-request-1"):
    return store.begin_operation(key, "image", input_hash, "shot-1")


def _shot(store, shot_id="shot-1"):
    return next(item for item in store.load()["shots"] if item["shot_id"] == shot_id)


def _artifact(store, artifact_id):
    return next(item for item in store.load()["artifacts"] if item["id"] == artifact_id)


def _prepare_frame_request(
    store,
    shot_id="shot-1",
    source_compile="source-compile-1",
    frame_request="frame-request-1",
):
    shot = _shot(store, shot_id)
    if shot["source_compile_hash"] != source_compile:
        store.compile_shot(
            shot_id,
            expected_source_hash=shot["source_hash"],
            source_compile_hash=source_compile,
        )
    store.set_frame_request(
        shot_id,
        expected_source_compile_hash=source_compile,
        frame_request_hash=frame_request,
    )


def _set_video_request(store, video_request="video-request-1", shot_id="shot-1"):
    shot = _shot(store, shot_id)
    assert shot["current_frame_artifact_id"] is not None
    frame = _artifact(store, shot["current_frame_artifact_id"])
    store.set_video_request(
        shot_id,
        expected_frame_request_hash=shot["frame_request_hash"],
        expected_frame_hash=frame["sha256"],
        video_request_hash=video_request,
    )


def _complete_frame(store, key="image-1", shot_id="shot-1", content="frame-1"):
    _prepare_frame_request(store, shot_id=shot_id)
    operation = store.begin_operation(key, "image", "frame-request-1", shot_id)
    _complete_media(store, operation, content)
    return operation


def _ready_shot(store, shot_id="shot-1"):
    frame = _complete_frame(store, key=f"image-{shot_id}", shot_id=shot_id)
    _set_video_request(store, shot_id=shot_id)
    video = store.begin_operation(
        f"video-{shot_id}", "video", "video-request-1", shot_id
    )
    _complete_media(store, video, f"video-{shot_id}-1")
    return frame, video


def _complete_media(store, operation, content, *, expected_current_hash=None, receipt=None, receipt_path=None):
    path, receipt_hash = _write_receipt(
        store, operation, receipt=receipt, path=receipt_path
    )
    metadata = {"input": operation.input_hash}
    if operation.kind == "assembly":
        metadata["export_bundle"] = _bundle_for_content(content)
    return store.complete_operation(
        operation.id,
        path=f"media/{operation.id}.bin",
        sha256=_hash(content),
        receipt_path=path,
        receipt_sha256=receipt_hash,
        expected_current_hash=(
            operation.input_hash if expected_current_hash is None else expected_current_hash
        ),
        metadata=metadata,
    )


def _final_evidence(store, key, kind, content_hash, verdict):
    bundle = _bundle(store, content_hash)
    issues = []
    if verdict == "rejected" and kind == "final_visual_review":
        issues = [{
            "clip_ids": ["shot-1"],
            "frame_numbers": [1],
            "severity": "must_fix",
            "problem": "关键画面错误",
            "evidence": "第 1 帧",
            "source_basis": "当前镜头事实",
        }]
    if verdict == "rejected" and kind == "final_audio_review":
        issues = [{
            "start": 0.0,
            "end": 1.0,
            "problem": "必需对白不可懂",
            "evidence": "完整窗口声音证据",
            "source_basis": "当前对白",
            "clip_ids": ["shot-1"],
        }]
    common = {
        "complete": True,
        "passed": verdict == "accepted",
        "review_id": key,
        "content_hash": content_hash,
        "input_hash": content_hash,
        "bundle_hash": content_hash,
        "export_sha256": bundle["video_sha256"],
        "video_sha256": bundle["video_sha256"],
        "issues": issues,
        "findings": issues,
        "errors": [],
    }
    if kind == "final_visual_review":
        group = {
            "passed": verdict == "accepted",
            "observations": ["实际抽帧"],
            "issues": issues,
        }
        return {
            **common,
            "policy": VISUAL_POLICY,
            "cover_sha256": bundle["cover_sha256"],
            "frames": [{"clip_id": "shot-1", "seconds": 0.15}],
            "groups": [group],
        }
    duration = bundle["expected_duration_seconds"]
    return {
        **common,
        "policy": AUDIO_POLICY,
        "movie": "current-export.mp4",
        "decode": {
            "fully_decoded": True,
            "samples": round(duration * 16000),
            "sample_rate": 16000,
            "export_sha256": bundle["video_sha256"],
        },
        "coverage_seconds": duration,
        "coverage": [{"start": 0.0, "end": duration}],
        "windows": [{"start": 0.0, "end": duration, "response": "完整声音"}],
        "summary": "完整声音判断",
    }


def _complete_review(
    store,
    key,
    kind,
    content_hash,
    verdict,
    *,
    receipt_id=None,
    receipt_path=None,
    evidence_changes=None,
):
    operation = store.begin_operation(key, kind, content_hash, None)
    final = kind in {"final_visual_review", "final_audio_review"}
    evidence = _final_evidence(store, key, kind, content_hash, verdict) if final else {}
    evidence.update(evidence_changes or {})
    path, receipt_hash = _write_receipt(
        store,
        operation,
        receipt=_receipt(
            operation, receipt_id=receipt_id, verdict=verdict, **evidence
        ),
        path=receipt_path or f"receipts/{key}.json",
    )
    return store.complete_operation(
        operation.id,
        verdict=verdict,
        findings=tuple(evidence.get("issues") or ()),
        receipt_path=path,
        receipt_sha256=receipt_hash,
    )


def _compile_episode(store, compile_hash):
    store.compile_episode(
        expected_script_hash=store.load()["current_script_hash"],
        compile_hash=compile_hash,
    )


def _export(store, key, content):
    compile_hash = f"compile-{content}"
    _compile_episode(store, compile_hash)
    operation = store.begin_operation(key, "assembly", compile_hash, None)
    _complete_media(store, operation, content)
    return _bundle_for_content(content)["bundle_hash"]


def _accepted_export(store, key="assembly-1", content="export-1"):
    export_hash = _export(store, key, content)
    _complete_review(store, f"visual-{key}", "final_visual_review", export_hash, "accepted")
    _complete_review(store, f"audio-{key}", "final_audio_review", export_hash, "accepted")
    return export_hash


def _upload(store, key, export_hash, **evidence_changes):
    bundle = _bundle(store, export_hash)
    operation = store.begin_operation(key, "upload", export_hash, None)
    evidence = {
        "target": PUBLISH_TARGET,
        "bundle_hash": export_hash,
        "verified": True,
        "private": True,
        "visibility": "private",
        "remote_object": "objects/current-export.mp4",
        "remote_sha256": bundle["video_sha256"],
        "cover_object": "objects/current-cover.jpg",
        "cover_sha256": bundle["cover_sha256"],
    }
    evidence.update(evidence_changes)
    path, receipt_hash = _write_receipt(
        store, operation, receipt=_receipt(operation, **evidence)
    )
    store.complete_operation(
        operation.id,
        receipt_path=path,
        receipt_sha256=receipt_hash,
        metadata={"verified": True},
    )
    return operation


def test_schema_is_four_tables_and_episode_round_trips(store):
    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"episodes", "shots", "operations", "artifacts"}

    loaded = store.load()
    assert loaded["episode_id"] == "episode-1"
    assert loaded["spec"] == _episode()
    assert loaded["frozen_config"] == _config()
    assert loaded["stage"] == "AUTHORING"
    assert loaded["status"] == "in_progress"
    assert loaded["launches"] == 1
    assert loaded["recovery_validation"] is False
    assert loaded["compile_hash"] is None
    assert len(loaded["shots"]) == 1
    shot = loaded["shots"][0]
    assert shot["source_hash"]
    assert shot["source_compile_hash"] is None
    assert shot["frame_request_hash"] is None
    assert shot["video_request_hash"] is None


def test_operation_allows_episode_scope_in_domain_contract():
    assert get_type_hints(Operation)["shot_id"] == str | None


def test_begin_operation_is_idempotent_and_binds_all_identity_fields(store):
    operation = _begin_image(store)
    assert _begin_image(store).id == operation.id
    assert store.usage("shot-1", "image") == 1
    for kind, input_hash, shot_id in (
        ("image", "different-hash", "shot-1"),
        ("video", "frame-request-1", "shot-1"),
        ("image", "frame-request-1", "other-shot"),
    ):
        with pytest.raises(ValueError, match="idempotency"):
            store.begin_operation("image-1", kind, input_hash, shot_id)


def test_pending_images_fill_quota_and_fourth_paid_call_stops(store):
    operations = [_begin_image(store, f"image-{index}") for index in range(3)]
    assert all(operation.status == "pending" for operation in operations)
    assert store.usage("shot-1") == {"image": 3, "video": 0, "input": 0}
    with pytest.raises(ValueError, match="image.*quota|quota.*image"):
        _begin_image(store, "image-fourth-paid")
    assert store.usage("shot-1", "image") == 3
    assert store.begin_operation("image-2", "image", "frame-request-1", "shot-1").id == operations[2].id


def test_submission_poll_download_and_failure_do_not_add_paid_operations(store):
    first = _begin_image(store, "image-1")
    store.mark_submitted(first.id, "task-1")
    second = _begin_image(store, "image-2")
    _complete_media(store, second, "frame-2")
    third = _begin_image(store, "image-3")
    store.mark_submitted(third.id, "task-3")
    store.fail_operation(third.id, "download_failed")
    for key, kind in (
        ("query-1", "query"),
        ("poll-1", "poll"),
        ("download-1", "download"),
        ("image-query-1", "image_query"),
    ):
        store.begin_operation(key, kind, "task-1", "shot-1")
    assert store.usage("shot-1", "image") == 3
    with pytest.raises(ValueError, match="quota"):
        _begin_image(store, "image-4")


def test_submission_uncertain_holds_slot_until_explicit_not_accepted(store):
    uncertain = _begin_image(store, "image-1")
    store.fail_operation(uncertain.id, "submission_uncertain")
    _begin_image(store, "image-2")
    _begin_image(store, "image-3")
    assert store.usage("shot-1", "image") == 3
    with pytest.raises(ValueError, match="quota"):
        _begin_image(store, "image-4")
    store.fail_operation(uncertain.id, "not_accepted", not_accepted=True)
    assert store.usage("shot-1", "image") == 2
    assert store.fail_operation(
        uncertain.id, "not_accepted", not_accepted=True
    ).status == "not_accepted"
    replacement = _begin_image(store, "image-4")
    assert replacement.id != uncertain.id
    assert store.usage("shot-1", "image") == 3


def test_accepted_task_cannot_be_reclassified_as_not_accepted(store):
    operation = _begin_image(store)
    store.mark_submitted(operation.id, "task-1")
    with pytest.raises(ValueError, match="not_accepted"):
        store.fail_operation(operation.id, "not_accepted", not_accepted=True)
    assert store.usage("shot-1", "image") == 1


@pytest.mark.parametrize("reason", ["failed", "download_failed", "result_purged"])
def test_failed_operations_cannot_be_reclassified_as_not_accepted(store, reason):
    operation = _begin_image(store)
    if reason != "failed":
        store.mark_submitted(operation.id, "task-1")
    store.fail_operation(operation.id, reason)
    with pytest.raises(ValueError, match="not_accepted"):
        store.fail_operation(operation.id, "not_accepted", not_accepted=True)
    assert store.usage("shot-1", "image") == 1


def test_input_operations_must_use_atomic_script_revision(store):
    with pytest.raises(ValueError, match="apply_script_revision"):
        store.begin_operation("patch", "input", "patch-hash", None)


def test_video_pending_can_be_explicitly_reconciled_as_not_accepted(store):
    operation = store.begin_operation("video", "video", "compile", "shot-1")
    store.fail_operation(operation.id, "not_accepted", not_accepted=True)
    assert store.usage("shot-1", "video") == 0


def test_download_failure_requires_a_submitted_task_id(store):
    operation = _begin_image(store)
    with pytest.raises(ValueError, match="task ID"):
        store.fail_operation(operation.id, "download_failed")
    assert _begin_image(store).status == "pending"


def test_resubmission_is_a_new_paid_operation(store):
    original = _begin_image(store, "image-1")
    store.mark_submitted(original.id, "task-1")
    store.fail_operation(original.id, "result_purged")
    replacement = _begin_image(store, "image-2", "frame-request-1")
    assert replacement.id != original.id
    assert replacement.status == "pending"
    assert replacement.external_task_id is None
    assert store.usage("shot-1", "image") == 2


def test_video_takes_have_an_independent_limit(store):
    for index in range(3):
        store.begin_operation(f"video-{index}", "video", f"h3-{index}", "shot-1")
    with pytest.raises(ValueError, match="video.*quota|quota.*video"):
        store.begin_operation("video-3", "video", "h3-3", "shot-1")


def test_submitting_is_transactional_and_can_be_marked_submitted(store):
    operation = _begin_image(store)
    submitting = store.mark_submitting(operation.id)
    assert submitting.status == "submitting"
    assert submitting.external_task_id is None
    assert store.mark_submitting(operation.id).status == "submitting"
    assert store.usage("shot-1", "image") == 1

    reopened = Store(store.path)
    recovered = reopened.begin_operation(
        "image-1", "image", "frame-request-1", "shot-1"
    )
    assert recovered.status == "submitting"
    assert recovered.external_task_id is None
    submitted = reopened.mark_submitted(operation.id, "external-task-123")
    assert submitted.status == "submitted"
    assert submitted.external_task_id == "external-task-123"


def test_crash_after_submission_recovers_same_operation_and_task_id(store):
    operation = _begin_image(store)
    store.mark_submitted(operation.id, "external-task-123")
    reopened = Store(store.path)
    recovered = reopened.begin_operation("image-1", "image", "frame-request-1", "shot-1")
    assert recovered.id == operation.id
    assert recovered.status == "submitted"
    assert recovered.external_task_id == "external-task-123"
    assert reopened.usage("shot-1", "image") == 1
    with pytest.raises(ValueError, match="external task"):
        reopened.mark_submitted(operation.id, "different-task")


def test_review_errors_are_not_verdicts_and_content_change_invalidates_review(store):
    _complete_review(store, "review-old", "source_review", "content-old", "accepted")
    review = store.find_valid_review("content-old", "source_review")
    assert review is not None and review.verdict == "accepted"
    assert store.find_valid_review("content-new", "source_review") is None
    failed = store.begin_operation("review-error", "source_review", "content-error", None)
    store.fail_operation(failed.id, "review_service_error")
    assert store.find_valid_review("content-error", "source_review") is None
    malformed = store.begin_operation("review-malformed", "source_review", "content-malformed", None)
    with pytest.raises(ValueError, match="verdict"):
        store.complete_operation(malformed.id, verdict="service_error")
    assert store.find_valid_review("content-malformed", "source_review") is None


def test_latest_receipted_verdict_supersedes_old_accepted_and_upload(store):
    export_hash = _accepted_export(store)
    _upload(store, "upload-1", export_hash)
    assert store.load()["status"] == "complete"
    _complete_review(store, "visual-new", "final_visual_review", export_hash, "rejected")
    assert store.load()["status"] == "candidate"
    assert store.find_valid_review(export_hash, "final_visual_review").verdict == "rejected"
    failed = store.begin_operation("visual-error", "final_visual_review", export_hash, None)
    store.fail_operation(failed.id, "review_service_error")
    assert store.find_valid_review(export_hash, "final_visual_review").verdict == "rejected"
    assert store.load()["status"] == "candidate"


def test_find_valid_review_requires_receipt_artifact_and_review_kind(store):
    operation = _complete_review(store, "review", "source_review", "content", "accepted")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM artifacts WHERE operation_id = ? AND kind = 'receipt'", (operation.id,))
    assert store.find_valid_review("content", "source_review") is None
    with pytest.raises(ValueError, match="review kind"):
        store.find_valid_review("frame-request-1", "image")


def test_artifact_receipt_and_current_pointer_commit_together(store):
    operation = _begin_image(store)
    _prepare_frame_request(store, frame_request=operation.input_hash)
    store.mark_submitted(operation.id, "task-1")
    completed = _complete_media(store, operation, "frame-bytes")
    assert completed.status == "completed"
    assert completed.receipt_path == f"receipts/{operation.id}.json"
    loaded = store.load()
    assert loaded["shots"][0]["current_frame_artifact_id"] == operation.id
    artifacts = {artifact["kind"]: artifact for artifact in loaded["artifacts"]}
    assert artifacts["frame"]["sha256"] == _hash("frame-bytes")
    assert artifacts["receipt"]["metadata"]["operation_id"] == operation.id
    assert _complete_media(store, operation, "frame-bytes").id == operation.id
    assert len(store.load()["artifacts"]) == 2
    with pytest.raises(ValueError, match="idempotency"):
        _complete_media(store, operation, "different-frame")


def test_receipt_failure_rolls_back_artifact_and_pointer(store):
    operation = _begin_image(store)
    _prepare_frame_request(store, frame_request=operation.input_hash)
    store.mark_submitted(operation.id, "task-1")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_receipt BEFORE INSERT ON artifacts
            WHEN NEW.kind = 'receipt'
            BEGIN SELECT RAISE(ABORT, 'receipt failed'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="receipt failed"):
        _complete_media(store, operation, "frame-bytes")
    loaded = store.load()
    assert loaded["shots"][0]["current_frame_artifact_id"] is None
    assert loaded["artifacts"] == []
    recovered = _begin_image(store)
    assert recovered.status == "submitted"
    assert recovered.external_task_id == "task-1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "another-operation"),
        ("kind", "video"),
        ("input_hash", "another-hash"),
        ("verdict", "accepted"),
    ],
)
def test_media_receipt_must_match_operation_identity(store, field, value):
    operation = _begin_image(store)
    receipt = _receipt(operation)
    receipt[field] = value
    with pytest.raises(ValueError, match="receipt"):
        _complete_media(store, operation, "frame", receipt=receipt)
    assert _begin_image(store).status == "pending"
    assert store.load()["artifacts"] == []


def test_media_receipt_requires_explicit_verdict_field(store):
    operation = _begin_image(store)
    receipt = _receipt(operation)
    del receipt["verdict"]
    with pytest.raises(ValueError, match="receipt.*verdict"):
        _complete_media(store, operation, "frame", receipt=receipt)
    assert _begin_image(store).status == "pending"


def test_receipt_sha_binds_actual_file_bytes_not_canonical_dict(store):
    operation = _begin_image(store)
    payload = _receipt(operation)
    path, actual_hash = _write_receipt(store, operation, receipt=payload, pretty=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    canonical_hash = sha256(canonical).hexdigest()
    assert canonical_hash != actual_hash
    with pytest.raises(ValueError, match="receipt SHA-256"):
        store.complete_operation(
            operation.id,
            path="media/frame.png",
            sha256=_hash("frame"),
            receipt_path=path,
            receipt_sha256=canonical_hash,
            expected_current_hash=operation.input_hash,
        )
    store.complete_operation(
        operation.id,
        path="media/frame.png",
        sha256=_hash("frame"),
        receipt_path=path,
        receipt_sha256=actual_hash,
        expected_current_hash=operation.input_hash,
    )
    receipt_artifact = next(a for a in store.load()["artifacts"] if a["kind"] == "receipt")
    assert receipt_artifact["sha256"] == actual_hash


def test_receipt_file_must_exist_and_contain_json_object(store, tmp_path):
    operation = store.begin_operation("review", "source_review", "content", None)
    with pytest.raises(FileNotFoundError):
        store.complete_operation(
            operation.id,
            verdict="accepted",
            receipt_path="receipts/missing.json",
            receipt_sha256=_hash("missing"),
        )
    path = store.path.parent / "receipts/invalid.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"[]")
    with pytest.raises(ValueError, match="receipt.*object"):
        store.complete_operation(
            operation.id,
            verdict="accepted",
            receipt_path="receipts/invalid.json",
            receipt_sha256=sha256(b"[]").hexdigest(),
        )


def test_receipt_identity_cannot_be_reused_across_operations_or_hashes(store):
    _complete_review(store, "review-1", "source_review", "content-1", "accepted", receipt_id="shared-receipt")
    for key, content_hash in (("review-2", "content-1"), ("review-3", "content-2")):
        operation = store.begin_operation(key, "source_review", content_hash, None)
        path, receipt_hash = _write_receipt(
            store,
            operation,
            receipt=_receipt(operation, receipt_id="shared-receipt", verdict="accepted"),
        )
        with pytest.raises(ValueError, match="receipt.*identity|receipt.*already"):
            store.complete_operation(
                operation.id,
                verdict="accepted",
                receipt_path=path,
                receipt_sha256=receipt_hash,
            )


@pytest.mark.parametrize("second_hash", ["content-1", "content-2"])
def test_receipt_path_cannot_be_rebound_to_another_operation(store, second_hash):
    _complete_review(
        store,
        "review-1",
        "source_review",
        "content-1",
        "accepted",
        receipt_path="receipts/shared.json",
    )
    operation = store.begin_operation("review-2", "source_review", second_hash, None)
    path, receipt_hash = _write_receipt(
        store,
        operation,
        receipt=_receipt(operation, verdict="accepted"),
        path="receipts/shared.json",
    )
    with pytest.raises(ValueError, match="receipt path"):
        store.complete_operation(
            operation.id,
            verdict="accepted",
            receipt_path=path,
            receipt_sha256=receipt_hash,
        )


@pytest.mark.parametrize("kind,shot_id", [("image", "shot-1"), ("video", "shot-1"), ("assembly", None)])
def test_media_completion_requires_artifact_receipt_and_expected_hash(store, kind, shot_id):
    operation = store.begin_operation(f"operation-{kind}", kind, f"input-{kind}", shot_id)
    receipt_path, receipt_hash = _write_receipt(store, operation)
    with pytest.raises(ValueError, match="expected_current_hash"):
        store.complete_operation(
            operation.id,
            path=f"media/{kind}.bin",
            sha256=_hash(kind),
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
        )
    with pytest.raises(ValueError, match="artifact"):
        store.complete_operation(
            operation.id,
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
            expected_current_hash=operation.input_hash,
        )
    with pytest.raises(ValueError, match="receipt"):
        store.complete_operation(
            operation.id,
            path=f"media/{kind}.bin",
            sha256=_hash(kind),
            expected_current_hash=operation.input_hash,
        )
    with pytest.raises(ValueError, match="review kind"):
        store.complete_operation(
            operation.id,
            path=f"media/{kind}.bin",
            sha256=_hash(kind),
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
            expected_current_hash=operation.input_hash,
            verdict="accepted",
        )
    assert store.load()["artifacts"] == []


def test_review_completion_requires_verdict_and_receipt(store):
    operation = store.begin_operation("review", "source_review", "content", None)
    with pytest.raises(ValueError, match="receipt"):
        store.complete_operation(operation.id, verdict="accepted")
    path, receipt_hash = _write_receipt(store, operation)
    with pytest.raises(ValueError, match="verdict"):
        store.complete_operation(
            operation.id, receipt_path=path, receipt_sha256=receipt_hash
        )
    assert store.find_valid_review("content", "source_review") is None


def test_current_export_projects_candidate_accepted_complete_and_stale_evidence(store):
    export_1 = _export(store, "assembly-1", "export-1")
    assert store.load()["status"] == "candidate"
    _complete_review(store, "visual-1", "final_visual_review", export_1, "accepted")
    assert store.load()["status"] == "candidate"
    _complete_review(store, "audio-1", "final_audio_review", export_1, "accepted")
    assert store.load()["status"] == "accepted"
    _upload(store, "upload-1", export_1)
    assert store.load()["status"] == "complete"
    export_2 = _export(store, "assembly-2", "export-2")
    assert store.load()["status"] == "candidate"
    _complete_review(store, "visual-2", "final_visual_review", export_2, "accepted")
    _complete_review(store, "audio-2", "final_audio_review", export_2, "rejected")
    assert store.load()["status"] == "candidate"


def test_export_bundle_is_persisted_and_same_video_new_cover_is_candidate(store):
    export_hash = _accepted_export(store)
    artifact = _artifact(store, store.load()["current_export_artifact_id"])
    assert artifact["metadata"]["export_bundle"]["bundle_hash"] == export_hash
    assert store.load()["status"] == "accepted"

    changed = dict(_bundle_for_content("export-1"))
    changed["cover_sha256"] = _hash("different-cover")
    parts = {key: value for key, value in changed.items() if key != "bundle_hash"}
    changed["bundle_hash"] = sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert changed["video_sha256"] == artifact["sha256"]
    assert changed["bundle_hash"] != export_hash
    _compile_episode(store, "compile-new-cover")
    operation = store.begin_operation("assembly-new-cover", "assembly", "compile-new-cover", None)
    receipt_path, receipt_hash = _write_receipt(store, operation)
    store.complete_operation(
        operation.id,
        path=f"media/{operation.id}.bin",
        sha256=artifact["sha256"],
        receipt_path=receipt_path,
        receipt_sha256=receipt_hash,
        expected_current_hash="compile-new-cover",
        metadata={"export_bundle": changed},
    )
    assert store.load()["status"] == "candidate"


def test_store_visual_projection_uses_group_aggregation_not_only_verdict(store):
    export_hash = _accepted_export(store)
    assert store.load()["status"] == "accepted"
    issue = {
        "clip_ids": ["shot-1"],
        "frame_numbers": [1],
        "severity": "must_fix",
        "problem": "组内关键错误",
        "evidence": "第 1 帧",
        "source_basis": "当前镜头事实",
    }
    _complete_review(
        store,
        "visual-malformed-aggregate",
        "final_visual_review",
        export_hash,
        "accepted",
        evidence_changes={
            "groups": [{"passed": False, "observations": ["实际错误"], "issues": [issue]}],
            "issues": [],
            "findings": [],
        },
    )
    assert store.load()["status"] == "candidate"


@pytest.mark.parametrize(
    "changes",
    [
        {"private": False},
        {"visibility": "public"},
        {"remote_object": ""},
        {"remote_sha256": _hash("different-export")},
    ],
)
def test_latest_remote_verification_replaces_older_complete_fact(store, changes):
    export_hash = _accepted_export(store)
    _upload(store, "upload-good", export_hash)
    assert store.load()["status"] == "complete"
    _upload(store, "upload-new-negative", export_hash, **changes)
    assert store.load()["status"] == "accepted"


def test_upload_target_must_exactly_match_frozen_target(store):
    export_hash = _accepted_export(store)
    operation = store.begin_operation("upload-wrong-target", "upload", export_hash, None)
    path, receipt_hash = _write_receipt(
        store,
        operation,
        receipt=_receipt(
            operation,
            target="team/another-target",
            private=True,
            remote_object="objects/export.mp4",
            remote_sha256=_bundle(store, export_hash)["video_sha256"],
        ),
    )
    with pytest.raises(ValueError, match="target"):
        store.complete_operation(
            operation.id, receipt_path=path, receipt_sha256=receipt_hash
        )
    assert store.load()["status"] == "accepted"


def test_network_failed_upload_does_not_clear_verified_remote_fact(store):
    export_hash = _accepted_export(store)
    _upload(store, "upload-good", export_hash)
    failed = store.begin_operation("upload-network", "upload", export_hash, None)
    store.fail_operation(failed.id, "network_failed")
    assert store.load()["status"] == "complete"


def test_source_compile_change_invalidates_all_derived_state_but_preserves_history(store):
    _ready_shot(store)
    export_hash = _accepted_export(store)
    _upload(store, "upload-1", export_hash)
    assert store.load()["status"] == "complete"
    before_artifacts = len(store.load()["artifacts"])
    source_hash = _shot(store)["source_hash"]
    store.compile_shot(
        "shot-1", expected_source_hash=source_hash, source_compile_hash="source-compile-2"
    )
    loaded = store.load()
    shot = loaded["shots"][0]
    assert loaded["status"] == "in_progress"
    assert loaded["compile_hash"] is None
    assert loaded["current_export_artifact_id"] is None
    assert shot["source_compile_hash"] == "source-compile-2"
    assert shot["frame_request_hash"] is None
    assert shot["video_request_hash"] is None
    assert shot["current_frame_artifact_id"] is None
    assert shot["current_video_artifact_id"] is None
    assert len(loaded["artifacts"]) == before_artifacts
    assert store.find_valid_review(export_hash, "final_visual_review").verdict == "accepted"


def test_frame_request_change_clears_frame_video_and_episode_but_keeps_source_compile(store):
    _ready_shot(store)
    _export(store, "assembly-old", "export-old")
    store.set_frame_request(
        "shot-1",
        expected_source_compile_hash="source-compile-1",
        frame_request_hash="frame-request-2",
    )
    loaded = store.load()
    shot = loaded["shots"][0]
    assert shot["source_compile_hash"] == "source-compile-1"
    assert shot["frame_request_hash"] == "frame-request-2"
    assert shot["video_request_hash"] is None
    assert shot["current_frame_artifact_id"] is None
    assert shot["current_video_artifact_id"] is None
    assert loaded["compile_hash"] is None
    assert loaded["current_export_artifact_id"] is None


def test_video_request_change_preserves_accepted_frame_and_only_clears_video_dependencies(store):
    _ready_shot(store)
    _export(store, "assembly-old", "export-old")
    before = _shot(store)
    _set_video_request(store, video_request="video-request-1")
    assert store.load()["current_export_artifact_id"] is not None
    _set_video_request(store, video_request="video-request-2")
    loaded = store.load()
    shot = loaded["shots"][0]
    assert shot["source_compile_hash"] == before["source_compile_hash"]
    assert shot["frame_request_hash"] == before["frame_request_hash"]
    assert shot["current_frame_artifact_id"] == before["current_frame_artifact_id"]
    assert shot["video_request_hash"] == "video-request-2"
    assert shot["current_video_artifact_id"] is None
    assert loaded["compile_hash"] is None
    assert loaded["current_export_artifact_id"] is None


def test_current_frame_change_invalidates_video_and_complete_export(store):
    frame, _ = _ready_shot(store)
    export_hash = _accepted_export(store)
    _upload(store, "upload-1", export_hash)
    assert store.load()["status"] == "complete"
    replacement = store.begin_operation("image-new", "image", "frame-request-1", "shot-1")
    _complete_media(store, replacement, "frame-new")
    loaded = store.load()
    shot = loaded["shots"][0]
    assert shot["current_frame_artifact_id"] == replacement.id != frame.id
    assert shot["frame_request_hash"] == "frame-request-1"
    assert shot["video_request_hash"] is None
    assert shot["current_video_artifact_id"] is None
    assert loaded["compile_hash"] is None
    assert loaded["current_export_artifact_id"] is None
    assert loaded["status"] == "in_progress"


def test_current_video_change_invalidates_episode_but_preserves_frame(store):
    frame, video = _ready_shot(store)
    export_hash = _accepted_export(store)
    _upload(store, "upload-1", export_hash)
    assert store.load()["status"] == "complete"
    replacement = store.begin_operation("video-new", "video", "video-request-1", "shot-1")
    _complete_media(store, replacement, "video-new")
    loaded = store.load()
    shot = loaded["shots"][0]
    assert shot["current_video_artifact_id"] == replacement.id != video.id
    assert shot["current_frame_artifact_id"] == frame.id
    assert shot["video_request_hash"] == "video-request-1"
    assert loaded["compile_hash"] is None
    assert loaded["current_export_artifact_id"] is None
    assert loaded["status"] == "in_progress"


def test_script_revision_invalidates_only_affected_shots_and_preserves_artifacts(tmp_path):
    store = Store(tmp_path / "revision-state.sqlite3")
    store.create_episode(_two_shot_episode(), _config())
    _ready_shot(store, "shot-1")
    _ready_shot(store, "shot-2")
    _export(store, "assembly-old", "export-old")
    before = store.load()
    changed = replace(before["spec"].shots[0], start_state="知遥坐在码头，船灯已经点亮")
    revision = replace(before["spec"], shots=(changed, before["spec"].shots[1]))
    plan = _revision_plan(before["spec"], revision)
    operation = store.apply_script_revision(
        "patch-1",
        plan.input_hash,
        revision,
        expected_script_hash=before["current_script_hash"],
        plan=plan,
    )
    assert operation.status == "completed"
    assert operation.shot_id is None
    revised = store.load()
    first, second = revised["shots"]
    assert revised["spec"] == revision
    assert revised["compile_hash"] is None
    assert revised["current_export_artifact_id"] is None
    assert first["source_compile_hash"] is None
    assert first["frame_request_hash"] is None
    assert first["video_request_hash"] is None
    assert first["current_frame_artifact_id"] is None
    assert first["current_video_artifact_id"] is None
    assert second == before["shots"][1]
    assert len(revised["artifacts"]) == len(before["artifacts"])
    assert store.usage("shot-1", "input") == 1
    assert store.usage("shot-2", "input") == 0


def test_multi_shot_revision_prechecks_actual_usage_and_atomically_compares_script(tmp_path):
    store = Store(tmp_path / "revision-usage.sqlite3")
    store.create_episode(_two_shot_episode(), _config())
    initial = store.load()

    revision_1 = replace(
        initial["spec"],
        shots=tuple(replace(shot, action=f"多镜修订一 {shot.shot_id}") for shot in initial["spec"].shots),
    )
    plan_1 = _revision_plan(initial["spec"], revision_1)
    operation_1 = store.apply_script_revision(
        "patch-1",
        plan_1.input_hash,
        revision_1,
        expected_script_hash=initial["current_script_hash"],
        plan=plan_1,
    )
    assert store.usage("shot-1", "input") == 1
    assert store.usage("shot-2", "input") == 1

    revision_2 = replace(
        revision_1,
        shots=(replace(revision_1.shots[0], action="只修第一镜"), revision_1.shots[1]),
    )
    plan_2 = _revision_plan(revision_1, revision_2)
    store.apply_script_revision(
        "patch-2",
        plan_2.input_hash,
        revision_2,
        expected_script_hash=store.load()["current_script_hash"],
        plan=plan_2,
    )
    assert store.usage("shot-1", "input") == 2
    assert store.usage("shot-2", "input") == 1

    revision_both = replace(
        revision_2,
        shots=tuple(replace(shot, end_state=f"新状态 {shot.shot_id}") for shot in revision_2.shots),
    )
    plan_both = _revision_plan(revision_2, revision_both)
    with pytest.raises(ValueError, match="shot-1.*input.*quota|input.*quota.*shot-1"):
        store.apply_script_revision(
            "patch-3",
            plan_both.input_hash,
            revision_both,
            expected_script_hash=store.load()["current_script_hash"],
            plan=plan_both,
        )
    assert store.usage("shot-1", "input") == 2
    assert store.usage("shot-2", "input") == 1
    assert store.load()["spec"] == revision_2

    revision_stale = replace(revision_2.shots[1], action="过期旧稿")
    stale_spec = replace(revision_2, shots=(revision_2.shots[0], revision_stale))
    stale_plan = _revision_plan(revision_2, stale_spec)
    with pytest.raises(ValueError, match="expected_script_hash"):
        store.apply_script_revision(
            "patch-stale",
            stale_plan.input_hash,
            stale_spec,
            expected_script_hash=initial["current_script_hash"],
            plan=stale_plan,
        )
    assert store.load()["spec"] == revision_2

    assert store.apply_script_revision(
        "patch-1",
        plan_1.input_hash,
        revision_1,
        expected_script_hash=initial["current_script_hash"],
        plan=plan_1,
    ).id == operation_1.id
    assert store.load()["spec"] == revision_2

    noop_plan = check_patch(
        revision_2,
        {"shot-2": {"action": "计划变化但提交未变化"}},
        {"shot-2": {"action"}},
    )
    with pytest.raises(ValueError, match="no changes|does not match.*plan"):
        store.apply_script_revision(
            "patch-noop",
            noop_plan.input_hash,
            revision_2,
            expected_script_hash=store.load()["current_script_hash"],
            plan=noop_plan,
        )
    assert store.usage("shot-2", "input") == 1

    revision_4 = replace(
        revision_2,
        shots=(revision_2.shots[0], replace(revision_2.shots[1], action="只修第二镜")),
    )
    plan_4 = _revision_plan(revision_2, revision_4)
    store.apply_script_revision(
        "patch-4",
        plan_4.input_hash,
        revision_4,
        expected_script_hash=store.load()["current_script_hash"],
        plan=plan_4,
    )
    assert store.usage("shot-1", "input") == 2
    assert store.usage("shot-2", "input") == 2
    with pytest.raises(ValueError, match="idempotency"):
        store.apply_script_revision(
            "patch-1",
            plan_1.input_hash,
            replace(revision_1, title="冲突旧稿"),
            expected_script_hash=initial["current_script_hash"],
            plan=plan_1,
        )
    assert store.load()["spec"] == revision_4


@pytest.mark.parametrize(
    "kind,pointer",
    [("image", "current_frame_artifact_id"), ("video", "current_video_artifact_id")],
)
def test_late_shot_operation_stores_artifact_without_overwriting_current_pointer(store, kind, pointer):
    if kind == "image":
        _prepare_frame_request(store, frame_request="request-1")
        old = store.begin_operation("image-old", "image", "request-1", "shot-1")
        store.set_frame_request(
            "shot-1", expected_source_compile_hash="source-compile-1", frame_request_hash="request-2"
        )
        current = store.begin_operation("image-current", "image", "request-2", "shot-1")
    else:
        frame = _complete_frame(store)
        _set_video_request(store, video_request="request-1")
        old = store.begin_operation("video-old", "video", "request-1", "shot-1")
        _set_video_request(store, video_request="request-2")
        current = store.begin_operation("video-current", "video", "request-2", "shot-1")
    _complete_media(store, current, "current-artifact")
    assert _shot(store)[pointer] == current.id
    with pytest.raises(ValueError, match="expected_current_hash"):
        _complete_media(store, old, "old-wrong", expected_current_hash="request-2")
    _complete_media(store, old, "old-artifact")
    assert _shot(store)[pointer] == current.id
    primary_ids = {a["id"] for a in store.load()["artifacts"] if a["kind"] != "receipt"}
    expected_ids = {old.id, current.id}
    if kind == "video":
        expected_ids.add(frame.id)
    assert primary_ids == expected_ids


def test_late_export_operation_cannot_replace_current_export(store):
    _compile_episode(store, "compile-1")
    old = store.begin_operation("assembly-old", "assembly", "compile-1", None)
    _compile_episode(store, "compile-2")
    current = store.begin_operation("assembly-current", "assembly", "compile-2", None)
    _complete_media(store, current, "export-current")
    assert store.load()["current_export_artifact_id"] == current.id
    _complete_media(store, old, "export-old")
    loaded = store.load()
    assert loaded["current_export_artifact_id"] == current.id
    assert loaded["status"] == "candidate"
    export_ids = {a["id"] for a in loaded["artifacts"] if a["kind"] == "export"}
    assert export_ids == {old.id, current.id}


def test_blocked_dominates_existing_accepted_export_until_explicitly_cleared(store):
    _accepted_export(store)
    store.advance("FINAL_REVIEW", blocked="audio unavailable", next_action="retry audio")
    assert store.load()["status"] == "blocked"
    store.advance("PUBLISHING")
    assert store.load()["status"] == "accepted"


def test_open_readonly_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing" / "status.sqlite3"
    with pytest.raises(FileNotFoundError):
        Store.open_readonly(path)
    assert not path.exists()
    assert not path.parent.exists()


def test_open_readonly_loads_status_without_mutating_launches_or_stage(store):
    store.advance("SOURCE_REVIEW")
    before = store.load()
    readonly = Store.open_readonly(store.path)
    after = readonly.load()
    assert after["stage"] == before["stage"] == "SOURCE_REVIEW"
    assert after["launches"] == before["launches"] == 1


def test_load_is_read_only_and_launches_are_bounded(store):
    store.advance("SOURCE_REVIEW")
    before = store.load()
    after = store.load()
    assert after["stage"] == before["stage"] == "SOURCE_REVIEW"
    assert after["launches"] == before["launches"] == 1
    store.advance("SOURCE_REVIEW", new_launch=True)
    store.advance("SOURCE_REVIEW", new_launch=True)
    assert store.load()["launches"] == 3
    assert store.load()["recovery_validation"] is True
    with pytest.raises(ValueError, match="launch"):
        store.advance("SOURCE_REVIEW", new_launch=True)
    assert store.load()["launches"] == 3


def test_create_cannot_rebuild_episode_to_clear_usage(store):
    operation = _begin_image(store)
    with pytest.raises(ValueError, match="already exists"):
        store.create_episode(_episode(), {"model": "different"})
    assert store.begin_operation("image-1", "image", "frame-request-1", "shot-1").id == operation.id
    assert store.usage("shot-1", "image") == 1


def test_artifacts_are_relative_and_metadata_is_json(store):
    operation = _begin_image(store)
    receipt_path, receipt_hash = _write_receipt(store, operation)
    with pytest.raises(ValueError, match="relative"):
        store.complete_operation(
            operation.id,
            path="/absolute/frame.png",
            sha256=_hash("frame"),
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
            expected_current_hash=operation.input_hash,
        )
    with pytest.raises(TypeError):
        store.complete_operation(
            operation.id,
            path="media/frame.png",
            sha256=_hash("frame"),
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
            expected_current_hash=operation.input_hash,
            metadata={"not_json": object()},
        )
    assert store.load()["artifacts"] == []
