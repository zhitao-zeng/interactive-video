from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import time

from PIL import Image

from clean_studio.compiler import validate_current_references, video_request
from clean_studio.domain import Operation, Review
from clean_studio.frozen.llm import image_part
from clean_studio.frozen.media import probe_video as _probe_video
from clean_studio.frozen.provider import (
    ProviderTaskFailed,
    ResultPurged,
    SubmissionRejected,
    SubmissionUncertain,
)
from clean_studio.providers.asr import evaluate_speech
from clean_studio.store import Store


FRAME_REVIEW_POLICY = "clean-first-frame-watchability-v6"
FRAME_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "const": "confirmed"},
                    "category": {
                        "type": "string",
                        "enum": ["contradiction", "missing_content"],
                    },
                    "observed": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "minLength": 1},
                    "basis": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "origin": {"type": "string", "enum": ["source", "image"]},
                    "relation": {
                        "type": "string",
                        "enum": ["contradiction", "missing"],
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": [
                    "status",
                    "category",
                    "observed",
                    "expected",
                    "basis",
                    "origin",
                    "relation",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

_RETRY = object()
_REJECTED = object()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_file(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _copy_review(review: Review) -> Review:
    return Review(
        review.review_id,
        review.content_hash,
        review.verdict,
        review.findings,
    )


def _frame_review_hash(candidate_sha256: str, request) -> str:
    references = [
        {
            "position": index,
            "subject_id": reference.subject_id,
            "path": reference.path,
            "role": reference.role,
            "sha256": reference.sha256,
            "scope": reference.scope,
        }
        for index, reference in enumerate(request.references)
    ]
    return sha256(
        _canonical(
            {
                "policy": FRAME_REVIEW_POLICY,
                "shot_id": request.shot_id,
                "request_hash": request.input_hash,
                "candidate_sha256": candidate_sha256,
                "references": references,
            }
        ).encode("utf-8")
    ).hexdigest()


class FrameReviewProtocolError(ValueError):
    pass


class DownloadIncomplete(ConnectionError):
    pass


class TechnicalProbeError(ValueError):
    pass


def _normalize_frame_review(raw, review_id: str, content_hash: str) -> Review:
    if not isinstance(raw, Mapping) or set(raw) != {"findings"}:
        raise FrameReviewProtocolError("frame review must contain exactly findings")
    raw_findings = raw["findings"]
    if not isinstance(raw_findings, list):
        raise FrameReviewProtocolError("frame review findings must be an array")
    required = {
        "status",
        "category",
        "observed",
        "expected",
        "basis",
        "origin",
        "relation",
        "reason",
    }
    findings = []
    for finding in raw_findings:
        if not isinstance(finding, dict) or set(finding) != required:
            raise FrameReviewProtocolError(
                "frame review findings require explicit status, category and evidence"
            )
        if finding["status"] != "confirmed" or finding["category"] not in {
            "contradiction",
            "missing_content",
        }:
            raise FrameReviewProtocolError("frame review finding status/category is invalid")
        expected_relation = (
            "contradiction" if finding["category"] == "contradiction" else "missing"
        )
        if finding["relation"] != expected_relation:
            raise FrameReviewProtocolError("frame review finding relation is invalid")
        if finding["origin"] not in {"source", "image"}:
            raise FrameReviewProtocolError("frame review finding origin is invalid")
        for field in ("observed", "expected", "reason"):
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise FrameReviewProtocolError(
                    f"frame review finding {field} must be nonempty evidence"
                )
        basis = finding["basis"]
        if (
            not isinstance(basis, list)
            or not basis
            or any(not isinstance(item, str) or not item.strip() for item in basis)
        ):
            raise FrameReviewProtocolError("frame review finding basis is invalid")
        findings.append(dict(finding))
    return Review(
        review_id=review_id,
        content_hash=content_hash,
        verdict="rejected" if findings else "accepted",
        findings=tuple(findings),
    )


@dataclass(frozen=True)
class Artifact:
    id: str
    operation_id: str
    shot_id: str | None
    kind: str
    path: str
    sha256: str
    input_hash: str
    metadata: Mapping[str, object]

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "Artifact":
        return cls(
            id=row["id"],
            operation_id=row["operation_id"],
            shot_id=row["shot_id"],
            kind=row["kind"],
            path=row["path"],
            sha256=row["sha256"],
            input_hash=row["input_hash"],
            metadata=dict(row.get("metadata") or {}),
        )

    def __getitem__(self, key: str):
        return getattr(self, key)


def _has_valid_video_quality(artifact: Mapping[str, object]) -> bool:
    quality = artifact["metadata"].get("quality") or {}
    technical = quality.get("technical") or {}
    speech = quality.get("asr") or {}
    review = quality.get("review") or {}
    return (
        quality.get("passed") is True
        and technical.get("passed") is True
        and technical.get("fully_decoded") is True
        and technical.get("native_audio") is True
        and technical.get("video_sha256") == artifact["sha256"]
        and speech.get("passed") is True
        and speech.get("evaluation") == "clean-current-shot-dialogue-cer-v2-phonetic"
        and speech.get("video_sha256") == artifact["sha256"]
        and speech.get("final_audio_accepted") is False
        and review.get("verdict") == "accepted"
        and review.get("video_sha256") == artifact["sha256"]
        and review.get("findings") == []
    )


def review_frame(
    candidate_path: str | Path,
    request,
    client,
    *,
    store: Store | None = None,
) -> Review:
    candidate_path = Path(candidate_path)
    validate_current_references(request.shot_id, request.references)
    candidate_hash = _hash_file(candidate_path)
    content_hash = _frame_review_hash(candidate_hash, request)
    if store is not None:
        cached = store.find_valid_review(content_hash, "frame_review")
        if cached is not None:
            return _copy_review(cached)
    review_id = f"frame-review-{content_hash}"
    operation = None
    if store is not None:
        operation = store.begin_operation(
            review_id,
            "frame_review",
            content_hash,
            request.shot_id,
        )
        review_id = operation.id
        if operation.status == "completed":
            cached = store.find_valid_review(content_hash, "frame_review")
            if cached is None:
                raise ValueError("completed frame review has no valid receipt")
            return _copy_review(cached)

    reference_rows = [
        {
            "image_number": index + 2,
            "subject_id": reference.subject_id,
            "path": reference.path,
            "role": reference.role,
            "scope": reference.scope,
            "sha256": reference.sha256,
        }
        for index, reference in enumerate(request.references)
    ]
    instructions = (
        "检查新生成的单一镜头首帧。第一张是候选首帧，后续图片是与生图请求"
        "完全同序的原始参考附件。只判断时间零点 t=0，不要求后续动作或末态提前出现。"
        "identity/wardrobe/location/style 各附件只约束其 scope；参考卡不增加人物。"
        "判准为观众观看级——单帧里观众看不出问题的一律不报："
        "contradiction 只报两类——身份错乱（人物明显变成另一个人：性别改变、明显年龄段错位）；"
        "t=0 剧情违背（剧本点名的剧情物件类型画错或完全缺失——如把剧情背包画成行李箱、"
        "关键物件不在画面；动作或后加入人物提前发生）。"
        "同一物件在画面中重复出现（如一只在身一只在水面）不报——单帧观众难以察觉，"
        "跨镜剧情一致性由成片终审负责。"
        "以下不报：衣装颜色与色系偏差、衣物款式近似变化（夹克↔衬衫式外套）、发型细节变化"
        "（马尾↔披发/盘发）、鞋包等非剧情装备的款式差异、整体色温——观众单帧无感，"
        "跨镜一致性由成片终审负责。"
        "missing_content——只报剧本明确要求可见且影响理解的剧情物件或入画人物完全没画。"
        "构图或镜头朝向允许背影/侧脸时，身份细节只能记为不可判定，不得臆断为换脸；"
        "只有请求明确要求可辨认而画面背离时才报告。整镜人物名单不是首帧出席要求，"
        "未要求入画的人不在画面不算缺失。无可观察问题返回空 findings，不作内容改稿。\n"
        + _canonical(
            {
                "shot_id": request.shot_id,
                "opening_prompt": request.prompt,
                "candidate_sha256": candidate_hash,
                "reference_images": reference_rows,
            }
        )
    )
    try:
        parts = [
            {"type": "text", "text": instructions},
            image_part(candidate_path, 1280),
            *(
                image_part(Path(reference.source_path), 1024)
                for reference in request.references
            ),
        ]
        raw = client.ask_json(
            parts,
            FRAME_REVIEW_SCHEMA,
            name="clean_first_frame_review",
            max_tokens=4000,
            timeout=240,
        )
        review = _normalize_frame_review(raw, review_id, content_hash)
        if store is not None:
            operation_row = store_operations_row(store, operation.id)
            receipt_path, receipt_hash = _write_receipt(
                store,
                operation_row,
                verdict=review.verdict,
                extra={
                    "policy": FRAME_REVIEW_POLICY,
                    "candidate_sha256": candidate_hash,
                    "reference_images": reference_rows,
                    "findings": review.findings,
                },
            )
            store.complete_operation(
                operation.id,
                verdict=review.verdict,
                findings=review.findings,
                receipt_path=receipt_path,
                receipt_sha256=receipt_hash,
                metadata={"policy": FRAME_REVIEW_POLICY},
            )
        return _copy_review(review)
    except Exception as error:
        if operation is not None:
            store.fail_operation(
                operation.id,
                "invalid_review_result"
                if isinstance(error, ValueError)
                else "review_service_error",
            )
        raise


def probe_video(
    path: str | Path,
    *,
    expected_duration: int,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
) -> dict:
    return _probe_video(
        Path(path),
        expected_duration=expected_duration,
        runner=runner,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )


def transcribe(video: str | Path, provider, **kwargs) -> dict:
    return provider.transcribe(video, **kwargs)


def store_operations_row(store: Store, operation_id: str) -> dict:
    with store._db(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown operation: {operation_id}")
        result = dict(row)
    result["metadata"] = json.loads(result["metadata"])
    return result


def _write_receipt(
    store: Store,
    operation: Mapping[str, object],
    *,
    verdict: str | None,
    extra: Mapping[str, object],
    suffix: str = "",
) -> tuple[str, str]:
    receipt = {
        **dict(extra),
        "receipt_id": f"receipt-{operation['id']}{suffix}",
        "operation_id": operation["id"],
        "kind": operation["kind"],
        "input_hash": operation["input_hash"],
        "verdict": verdict,
    }
    data = _canonical(receipt).encode("utf-8")
    relative = f"receipts/{operation['id']}{suffix}.json"
    path = store.path.parent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative, sha256(data).hexdigest()


def _domain_operation(row: Mapping[str, object]) -> Operation:
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


class MediaOperations:
    def __init__(
        self,
        store: Store,
        compilation,
        *,
        image_provider,
        h3_provider,
        frame_review_client,
        asr_provider,
        video_reviewer,
        probe=None,
        submission_reconciler=None,
        poll_interval: float = 5.0,
        poll_timeout: float = 3600.0,
        sleeper=None,
        clock=None,
        frameless: bool = False,
    ) -> None:
        if not callable(video_reviewer):
            raise ValueError("a callable video reviewer is required")
        self.frameless = bool(frameless)
        self.store = store
        self.compilation = compilation
        self.image_provider = image_provider
        self.h3_provider = h3_provider
        self.frame_review_client = frame_review_client
        self.asr_provider = asr_provider
        self.probe = probe or probe_video
        self.video_reviewer = video_reviewer
        self.submission_reconciler = submission_reconciler
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.sleeper = sleeper or time.sleep
        self.clock = clock or time.monotonic
        self._purged_operations = set()

    def operations(self, shot_id: str, kind: str, input_hash: str) -> list[dict]:
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            rows = connection.execute(
                """
                SELECT * FROM operations
                WHERE episode_id = ? AND shot_id = ? AND kind = ? AND input_hash = ?
                ORDER BY rowid
                """,
                (episode_id, shot_id, kind, input_hash),
            ).fetchall()
            result = [dict(row) for row in rows]
        for row in result:
            row["metadata"] = json.loads(row["metadata"])
        return result

    def _shot(self, shot_id: str) -> dict:
        return next(row for row in self.store.load()["shots"] if row["shot_id"] == shot_id)

    def _prepare_frame_request(self, shot_id: str):
        compiled = self.compilation.get(shot_id)
        shot = self._shot(shot_id)
        if shot["source_compile_hash"] != compiled.compile_hash:
            self.store.compile_shot(
                shot_id,
                expected_source_hash=compiled.source_hash,
                source_compile_hash=compiled.compile_hash,
            )
            shot = self._shot(shot_id)
        request = compiled.frame_request
        if shot["frame_request_hash"] != request.input_hash:
            self.store.set_frame_request(
                shot_id,
                expected_source_compile_hash=compiled.compile_hash,
                frame_request_hash=request.input_hash,
            )
        return compiled, request

    def _artifact_row(self, artifact_id: str, kind: str) -> dict | None:
        return next(
            (
                row
                for row in self.store.load()["artifacts"]
                if row["id"] == artifact_id and row["kind"] == kind
            ),
            None,
        )

    def _operation_artifact(self, operation_id: str, kind: str) -> dict | None:
        return next(
            (
                row
                for row in self.store.load()["artifacts"]
                if row["operation_id"] == operation_id and row["kind"] == kind
            ),
            None,
        )

    def _media_hash(self, path: Path, kind: str) -> str:
        if kind == "frame":
            with Image.open(path) as image:
                image.load()
        return _hash_file(path)

    def _restore_artifact(self, artifact: Mapping[str, object], kind: str) -> Path:
        operation = store_operations_row(self.store, artifact["operation_id"])
        task_id = operation["external_task_id"]
        if not task_id:
            raise ValueError("accepted artifact has no external task for restoration")
        provider = self.image_provider if kind == "frame" else self.h3_provider
        result = self._wait_for_result(operation, provider)
        target = self.store.path.parent / artifact["path"]
        recovery = target.with_name(target.name + ".recovery")
        provider.download(result, recovery)
        if self._media_hash(recovery, kind) != artifact["sha256"]:
            raise ValueError("restored artifact bytes differ from the immutable SHA-256")
        recovery.replace(target)
        return target

    def _artifact_file(self, artifact: Mapping[str, object], kind: str) -> Path:
        path = self.store.path.parent / artifact["path"]
        try:
            if path.is_file() and self._media_hash(path, kind) == artifact["sha256"]:
                return path
        except (OSError, ValueError):
            pass
        return self._restore_artifact(artifact, kind)

    def _accepted_current(self, shot_id: str, kind: str, input_hash: str, request=None):
        shot = self._shot(shot_id)
        artifact_kind = "frame" if kind == "image" else "video"
        pointer = (
            "current_frame_artifact_id" if kind == "image" else "current_video_artifact_id"
        )
        artifact_id = shot[pointer]
        if not artifact_id:
            return None
        artifact = self._artifact_row(artifact_id, artifact_kind)
        if artifact is None or artifact["input_hash"] != input_hash:
            return None
        metadata = artifact["metadata"]
        if kind == "image":
            review = metadata.get("review") or {}
            if review.get("verdict") != "accepted" or not review.get("content_hash"):
                return None
            if request is not None:
                current_hash = _frame_review_hash(artifact["sha256"], request)
                if review.get("content_hash") != current_hash:
                    return None
                valid = self.store.find_valid_review(current_hash, "frame_review")
            else:
                valid = self.store.find_valid_review(review["content_hash"], "frame_review")
            if valid is None or valid.verdict != "accepted":
                return None
        elif not _has_valid_video_quality(artifact):
            return None
        try:
            self._artifact_file(artifact, artifact_kind)
        except ResultPurged:
            self._purged_operations.add(artifact["operation_id"])
            return None
        return Artifact.from_row(artifact)

    @staticmethod
    def _rejected_operation(operation: Mapping[str, object], kind: str) -> bool:
        if operation["status"] != "completed":
            return False
        metadata = operation["metadata"]
        if kind == "image":
            return (metadata.get("review") or {}).get("verdict") == "rejected"
        return (metadata.get("quality") or {}).get("passed") is False

    def _active_operation(self, shot_id: str, kind: str, input_hash: str) -> dict:
        rows = self.operations(shot_id, kind, input_hash)
        for operation in reversed(rows):
            if operation["id"] in self._purged_operations:
                continue
            if operation["status"] == "not_accepted":
                continue
            if self._rejected_operation(operation, kind):
                continue
            if operation["status"] == "failed" and operation["error"] in {
                "result_purged",
                "provider_failed",
                "submission_rejected",
            }:
                continue
            if operation["status"] == "failed" and not operation["external_task_id"]:
                return operation
            return operation
        validate_current_references(shot_id, self.compilation.get(shot_id).references)
        key = f"{kind}:{shot_id}:{input_hash}:{len(rows) + 1}"
        created = self.store.begin_operation(key, kind, input_hash, shot_id)
        return store_operations_row(self.store, created.id)

    def _submit(self, operation: dict, provider, payload) -> dict | None:
        if operation["external_task_id"]:
            return operation
        if operation["status"] == "submitting":
            self.store.fail_operation(operation["id"], "submission_uncertain")
            operation = store_operations_row(self.store, operation["id"])
        if operation["status"] == "submission_uncertain":
            if self.submission_reconciler is None:
                raise SubmissionUncertain(
                    "submission remains unconfirmed; reconciliation is required before any new POST"
                )
            decision = self.submission_reconciler(_domain_operation(operation))
            valid = isinstance(decision, Mapping) and type(decision.get("accepted")) is bool
            accepted = decision.get("accepted") if valid else None
            task_id = decision.get("task_id") if valid else None
            if accepted is True and isinstance(task_id, str) and task_id.strip():
                try:
                    self.store.mark_submitted(operation["id"], task_id)
                except Exception as error:
                    self.store.fail_operation(operation["id"], "submission_uncertain")
                    raise SubmissionUncertain(
                        "reconciled task ID could not be persisted"
                    ) from error
                return store_operations_row(self.store, operation["id"])
            if accepted is False and task_id is None:
                self.store.fail_operation(
                    operation["id"], "not_accepted", not_accepted=True
                )
                return None
            raise SubmissionUncertain(
                "reconciliation must explicitly accept with a task ID or reject without one"
            )
        if operation["status"] != "pending":
            raise SubmissionUncertain(
                "operation has no task ID and cannot be posted again without reconciliation"
            )
        self.store.mark_submitting(operation["id"])
        operation = store_operations_row(self.store, operation["id"])
        try:
            task_id = provider.submit(payload)
        except SubmissionUncertain:
            self.store.fail_operation(operation["id"], "submission_uncertain")
            raise
        except SubmissionRejected as error:
            if error.not_accepted:
                self.store.fail_operation(
                    operation["id"],
                    f"http_{error.status_code}_not_accepted",
                    not_accepted=True,
                )
            else:
                self.store.fail_operation(operation["id"], "submission_rejected")
            raise
        except Exception as error:
            self.store.fail_operation(operation["id"], "submission_uncertain")
            raise SubmissionUncertain(
                "submission outcome is unknown; reconciliation required"
            ) from error
        if not isinstance(task_id, str) or not task_id.strip():
            self.store.fail_operation(operation["id"], "submission_uncertain")
            raise SubmissionUncertain(
                "provider returned no usable task ID; reconciliation required"
            )
        try:
            self.store.mark_submitted(operation["id"], task_id)
        except Exception as error:
            self.store.fail_operation(operation["id"], "submission_uncertain")
            raise SubmissionUncertain(
                "task ID persistence was not confirmed; reconciliation required"
            ) from error
        return store_operations_row(self.store, operation["id"])

    def _wait_for_result(self, operation: Mapping[str, object], provider):
        task_id = operation["external_task_id"]
        if not task_id:
            raise ValueError("cannot query an operation without an external task ID")
        deadline = self.clock() + self.poll_timeout
        while True:
            try:
                result = provider.query(task_id)
            except ResultPurged:
                if operation["status"] != "completed":
                    self.store.fail_operation(operation["id"], "result_purged")
                raise
            except Exception:
                if operation["status"] != "completed":
                    self.store.fail_operation(operation["id"], "query_failed")
                raise
            if result.status == "succeeded":
                return result
            if result.status == "failed":
                if operation["status"] != "completed":
                    self.store.fail_operation(operation["id"], "provider_failed")
                raise ProviderTaskFailed(task_id, result.raw)
            if result.status != "pending":
                if operation["status"] != "completed":
                    self.store.fail_operation(operation["id"], "query_failed")
                raise ValueError(f"unknown provider task status: {result.status}")
            if self.clock() >= deadline:
                if operation["status"] != "completed":
                    self.store.fail_operation(operation["id"], "query_failed")
                raise TimeoutError(f"provider task timed out: {task_id}")
            self.sleeper(self.poll_interval)

    def _validate_download(self, path: Path, kind: str, expected_duration):
        if kind == "image":
            return self._media_hash(path, "frame"), None
        candidate_hash = _hash_file(path)
        try:
            technical = self.probe(path, expected_duration=expected_duration)
        except Exception as error:
            raise DownloadIncomplete(f"video download could not be fully decoded: {path}") from error
        if not isinstance(technical, Mapping):
            raise DownloadIncomplete("video probe returned no structured decode evidence")
        if technical.get("video_sha256") != candidate_hash:
            raise TechnicalProbeError(
                "technical probe hash is not bound to the current video bytes"
            )
        if technical.get("fully_decoded") is not True:
            raise DownloadIncomplete(f"video download is incomplete: {path}")
        return candidate_hash, dict(technical)

    def _candidate_file(
        self, operation: dict, provider, kind: str, expected_duration=None
    ) -> tuple[Path, str, dict | None]:
        filename = "frame.png" if kind == "image" else "video.mp4"
        relative = f"media/{operation['id']}/{filename}"
        path = self.store.path.parent / relative
        if path.is_file():
            try:
                candidate_hash, technical = self._validate_download(
                    path, kind, expected_duration
                )
                return path, candidate_hash, technical
            except TechnicalProbeError:
                raise
            except (OSError, ValueError, RuntimeError, DownloadIncomplete):
                path.unlink(missing_ok=True)
        result = self._wait_for_result(operation, provider)
        partial = path.with_name(path.name + ".partial")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        try:
            provider.download(result, partial)
        except ResultPurged:
            self.store.fail_operation(operation["id"], "result_purged")
            raise
        except Exception:
            self.store.fail_operation(operation["id"], "download_failed")
            raise
        try:
            candidate_hash, technical = self._validate_download(
                partial, kind, expected_duration
            )
        except TechnicalProbeError:
            raise
        except DownloadIncomplete:
            self.store.fail_operation(operation["id"], "download_failed")
            raise
        except (OSError, ValueError, RuntimeError) as error:
            self.store.fail_operation(operation["id"], "download_failed")
            raise DownloadIncomplete(f"media download validation failed: {partial}") from error
        partial.replace(path)
        return path, candidate_hash, technical

    def _complete_candidate(
        self,
        operation: Mapping[str, object],
        *,
        kind: str,
        path: Path,
        candidate_hash: str,
        metadata: Mapping[str, object],
    ) -> Artifact:
        relative = path.relative_to(self.store.path.parent).as_posix()
        receipt_path, receipt_hash = _write_receipt(
            self.store,
            operation,
            verdict=None,
            extra={
                "external_task_id": operation["external_task_id"],
                "candidate_sha256": candidate_hash,
                "evidence": metadata,
            },
        )
        self.store.complete_operation(
            operation["id"],
            path=relative,
            sha256=candidate_hash,
            receipt_path=receipt_path,
            receipt_sha256=receipt_hash,
            expected_current_hash=operation["input_hash"],
            metadata=metadata,
        )
        artifact_kind = "frame" if kind == "image" else "video"
        artifact = self._operation_artifact(operation["id"], artifact_kind)
        if artifact is None:
            raise RuntimeError("completed media operation has no artifact")
        return Artifact.from_row(artifact)

    def _revalidate_frame_review(self, artifact, request):
        """E06: a stored acceptance only counts under the CURRENT review policy.

        Re-reviewing an already-generated image never consumes generation quota;
        the old receipt stays on its own operation."""
        review = (artifact["metadata"].get("review") or {})
        current_hash = _frame_review_hash(artifact["sha256"], request)
        if review.get("verdict") == "accepted" and review.get("content_hash") == current_hash:
            cached = self.store.find_valid_review(current_hash, "frame_review")
            if cached is not None and cached.verdict == "accepted":
                return artifact
        path = self._artifact_file(artifact, "frame")
        fresh = review_frame(
            path,
            request,
            self.frame_review_client,
            store=self.store,
        )
        metadata = {
            **artifact["metadata"],
            "review": {
                "verdict": fresh.verdict,
                "content_hash": fresh.content_hash,
                "review_id": fresh.review_id,
                "findings": fresh.findings,
            },
        }
        self.store.refresh_frame_review(
            artifact["operation_id"],
            metadata["review"],
        )
        return {**artifact, "metadata": metadata}

    def _execute_image(self, operation: dict, request):
        if operation["status"] == "completed":
            artifact = self._operation_artifact(operation["id"], "frame")
            if artifact is None:
                raise RuntimeError("completed image operation has no frame artifact")
            verdict = (artifact["metadata"].get("review") or {}).get("verdict")
            if verdict == "accepted":
                artifact = self._revalidate_frame_review(artifact, request)
                if (artifact["metadata"].get("review") or {}).get("verdict") != "accepted":
                    return _REJECTED
                self._artifact_file(artifact, "frame")
                return Artifact.from_row(artifact)
            self._artifact_file(artifact, "frame")
            return _REJECTED

        validate_current_references(request.shot_id, request.references)
        payload = self.image_provider.payload(request)
        operation = self._submit(operation, self.image_provider, payload)
        if operation is None:
            return _RETRY
        path, candidate_hash, _ = self._candidate_file(
            operation, self.image_provider, "image"
        )
        try:
            with Image.open(path) as image:
                image.load()
        except (OSError, ValueError) as error:
            metadata = {
                "external_task_id": operation["external_task_id"],
                "review": {
                    "verdict": "rejected",
                    "content_hash": None,
                    "findings": [
                        {
                            "origin": "technical",
                            "relation": "contradiction",
                            "reason": f"image decode failed: {error}",
                        }
                    ],
                },
            }
            self._complete_candidate(
                operation,
                kind="image",
                path=path,
                candidate_hash=candidate_hash,
                metadata=metadata,
            )
            return _REJECTED

        review = review_frame(
            path,
            request,
            self.frame_review_client,
            store=self.store,
        )
        metadata = {
            "external_task_id": operation["external_task_id"],
            "references": [
                {
                    "position": index,
                    "subject_id": reference.subject_id,
                    "role": reference.role,
                    "sha256": reference.sha256,
                }
                for index, reference in enumerate(request.references)
            ],
            "review": {
                "verdict": review.verdict,
                "content_hash": review.content_hash,
                "review_id": review.review_id,
                "findings": review.findings,
            },
        }
        artifact = self._complete_candidate(
            operation,
            kind="image",
            path=path,
            candidate_hash=candidate_hash,
            metadata=metadata,
        )
        return artifact if review.verdict == "accepted" else _REJECTED

    @staticmethod
    def _video_review(raw, candidate_hash: str) -> tuple[bool, object]:
        if not isinstance(raw, Mapping):
            raise ValueError("video review must be an object with explicit evidence")
        if raw.get("video_sha256") != candidate_hash:
            raise ValueError("video review is not bound to the current video bytes")
        verdict = raw.get("verdict")
        if verdict not in {"accepted", "rejected"}:
            raise ValueError("video review verdict must be accepted or rejected")
        findings = raw.get("findings")
        if not isinstance(findings, list) or any(
            not isinstance(finding, dict) for finding in findings
        ):
            raise ValueError("video review findings must be an array of objects")
        if verdict == "accepted" and findings:
            raise ValueError("accepted video review must have empty findings")
        if verdict == "rejected" and not findings:
            raise ValueError("rejected video review requires findings")
        return verdict == "accepted", dict(raw)

    def _execute_video(self, operation: dict, request, frame_artifact):
        if operation["status"] == "completed":
            artifact = self._operation_artifact(operation["id"], "video")
            if artifact is None:
                raise RuntimeError("completed video operation has no video artifact")
            if not _has_valid_video_quality(artifact):
                raise RuntimeError("completed video lacks current ASR/review evidence")
            self._artifact_file(artifact, "video")
            return Artifact.from_row(artifact)

        frame_path = (
            self._artifact_file(frame_artifact, "frame") if frame_artifact is not None else None
        )
        seed = int(sha256(f"{operation['id']}:video".encode("utf-8")).hexdigest()[:8], 16) % 2**31
        validate_current_references(request.shot_id, request.references)
        payload = self.h3_provider.payload(request, frame_path, seed=seed)
        operation = self._submit(operation, self.h3_provider, payload)
        if operation is None:
            return _RETRY
        path, candidate_hash, technical = self._candidate_file(
            operation,
            self.h3_provider,
            "video",
            expected_duration=request.duration_seconds,
        )

        if not isinstance(technical, Mapping) or type(technical.get("passed")) is not bool:
            raise RuntimeError("technical probe did not return a valid result")
        technical = {
            **dict(technical),
            "content_accepted": False,
            "final_audio_accepted": False,
        }
        if technical.get("video_sha256") != candidate_hash:
            raise RuntimeError("technical probe hash is not bound to the current video bytes")
        if technical["passed"]:
            speech_path = self.store.path.parent / f"receipts/{operation['id']}-asr.json"
            reference = "\n".join(
                line.line for line in self.compilation.get(request.shot_id).dialogue
            )
            transcript = self.asr_provider.transcribe(
                path,
                output=speech_path,
                reference=reference,
                duration=technical.get("duration_seconds") or request.duration_seconds,
            )
            speech = evaluate_speech(
                reference,
                transcript,
                video_sha256=candidate_hash,
            )
            speech_path.write_text(_canonical(speech), encoding="utf-8")
        else:
            speech = {
                "status": "not_run",
                "passed": False,
                "content_accepted": False,
                "final_audio_accepted": False,
                "evidence_scope": "asr_only",
            }

        passed = technical["passed"] and speech["passed"]
        review_evidence = None
        if passed:
            reviewed, review_evidence = self._video_review(
                self.video_reviewer(path, request, technical, speech),
                candidate_hash,
            )
            passed = reviewed
        metadata = {
            "external_task_id": operation["external_task_id"],
            "seed": seed,
            "quality": {
                "passed": passed,
                "technical": technical,
                "asr": speech,
                "review": review_evidence,
                "final_audio_accepted": False,
            },
        }
        artifact = self._complete_candidate(
            operation,
            kind="video",
            path=path,
            candidate_hash=candidate_hash,
            metadata=metadata,
        )
        return artifact if passed else _REJECTED

    def ensure_first_frame(self, shot_id: str) -> Artifact:
        _, request = self._prepare_frame_request(shot_id)
        validate_current_references(shot_id, request.references)
        while True:
            accepted = self._accepted_current(shot_id, "image", request.input_hash, request)
            if accepted is not None:
                return accepted
            operation = self._active_operation(shot_id, "image", request.input_hash)
            outcome = self._execute_image(operation, request)
            if outcome is _RETRY or outcome is _REJECTED:
                continue
            return outcome

    def ensure_video(self, shot_id: str) -> Artifact:
        compiled, frame_request = self._prepare_frame_request(shot_id)
        if self.frameless:
            validate_current_references(shot_id, compiled.references)
            request = video_request(compiled, None)
            frame_artifact = None
            shot = self._shot(shot_id)
            if shot["video_request_hash"] != request.input_hash:
                self.store.set_video_request(
                    shot_id,
                    expected_frame_request_hash=compiled.frame_request_hash,
                    expected_frame_hash=None,
                    video_request_hash=request.input_hash,
                )
            return self._ensure_video_loop(shot_id, request, frame_artifact)
        validate_current_references(shot_id, frame_request.references)
        frame = self._accepted_current(shot_id, "image", frame_request.input_hash, frame_request)
        if frame is None:
            raise ValueError("a current accepted first frame is required before H3 generation")
        frame_artifact = self._artifact_row(frame.id, "frame")
        if frame_artifact is None:
            raise RuntimeError("accepted frame artifact is missing from the Store")
        request = video_request(compiled, frame)
        shot = self._shot(shot_id)
        if shot["video_request_hash"] != request.input_hash:
            self.store.set_video_request(
                shot_id,
                expected_frame_request_hash=compiled.frame_request_hash,
                expected_frame_hash=frame.sha256,
                video_request_hash=request.input_hash,
            )
        return self._ensure_video_loop(shot_id, request, frame_artifact)

    def _ensure_video_loop(self, shot_id: str, request, frame_artifact):
        while True:
            accepted = self._accepted_current(shot_id, "video", request.input_hash)
            if accepted is not None:
                return accepted
            operation = self._active_operation(shot_id, "video", request.input_hash)
            outcome = self._execute_video(operation, request, frame_artifact)
            if outcome is _RETRY or outcome is _REJECTED:
                continue
            return outcome
        while True:
            accepted = self._accepted_current(shot_id, "video", request.input_hash)
            if accepted is not None:
                return accepted
            operation = self._active_operation(shot_id, "video", request.input_hash)
            outcome = self._execute_video(operation, request, frame_artifact)
            if outcome is _RETRY or outcome is _REJECTED:
                continue
            return outcome


MediaExecutor = MediaOperations


def ensure_first_frame(operations: MediaOperations, shot_id: str) -> Artifact:
    return operations.ensure_first_frame(shot_id)


def ensure_video(operations: MediaOperations, shot_id: str) -> Artifact:
    return operations.ensure_video(shot_id)
