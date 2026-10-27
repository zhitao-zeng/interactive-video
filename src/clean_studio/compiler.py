from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    Reference,
    ReferenceManifest,
    Shot,
)

COMPILER_VERSION = "clean-h3-compiler-v1"
_ROLE_ORDER = {"identity": 0, "wardrobe": 1, "location": 2, "style": 3}
_TRANSLATION_FIELDS = ("opening_en", "action_en", "end_state_en")
TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {field: {"type": "string"} for field in _TRANSLATION_FIELDS},
    "required": list(_TRANSLATION_FIELDS),
    "additionalProperties": False,
}
_FIDELITY_CHECKS = (
    "events_preserved",
    "participants_preserved",
    "temporal_order_preserved",
    "opening_preserved",
    "end_state_preserved",
    "no_extra_speech",
    "no_production_instruction_changes",
)
FIDELITY_SCHEMA = {
    "type": "object",
    "properties": {
        **{field: {"type": "boolean"} for field in _FIDELITY_CHECKS},
        "violations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*_FIDELITY_CHECKS, "violations"],
    "additionalProperties": False,
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value_hash(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def translation_config_hash(config: Mapping[str, object]) -> str:
    if not isinstance(config, Mapping):
        raise TypeError("translation config must be a mapping")
    return _value_hash(config)


def _client_translation_identity(client: object) -> dict[str, object]:
    if client is None:
        return {"adapter": None, "model": None, "effort": None}
    client_type = type(client)
    return {
        "adapter": f"{client_type.__module__}.{client_type.__qualname__}",
        "model": getattr(client, "model", None),
        "effort": getattr(client, "effort", None),
    }


def _translation_config_hash(
    translator: object,
    reviewer: object,
    config: Mapping[str, object] | None,
) -> str:
    if config is not None and not isinstance(config, Mapping):
        raise TypeError("translation_config must be a mapping")
    return translation_config_hash(
        {
            "translator": _client_translation_identity(translator),
            "reviewer": _client_translation_identity(reviewer),
            "config": dict(config or {}),
        }
    )


def _require_independent_reviewer(translator: object, reviewer: object):
    if reviewer is None:
        raise ValueError("an explicit independent translation_reviewer is required")
    if reviewer is translator:
        raise ValueError("translation reviewer must be independent, not the same object as translator")
    return reviewer


def script_hash(episode: EpisodeSpec) -> str:
    return _value_hash(asdict(episode))


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_root() -> Path:
    return Path(
        os.environ.get("NOVEL_PROJECT_ROOT")
        or Path(__file__).resolve().parents[2]
    ).resolve()


@dataclass(frozen=True)
class ReferenceIssue:
    shot_id: str
    code: str
    owner: str
    subject_id: str
    message: str
    reference_path: str | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None


class CompilationError(ValueError):
    def __init__(self, issues) -> None:
        self.issues = tuple(issues)
        if not self.issues:
            raise ValueError("CompilationError requires at least one issue")
        super().__init__(
            "\n".join(
                f"{issue.shot_id or 'episode'} [{issue.owner}/{issue.code}] "
                f"{issue.subject_id}: {issue.message}"
                for issue in self.issues
            )
        )

    @property
    def errors(self) -> tuple[ReferenceIssue, ...]:
        return self.issues


class TranslationError(ValueError):
    pass


class TranslationFidelityFailed(TranslationError):
    """A content-level fidelity rejection; the host may re-translate once."""


_TRANSLATION_ATTEMPTS = 3


@dataclass(frozen=True)
class FrameRequest:
    shot_id: str
    prompt: str
    references: tuple[Reference, ...]
    input_hash: str

    @property
    def request_hash(self) -> str:
        return self.input_hash


@dataclass(frozen=True)
class CompiledShot:
    source_shot: Shot
    style_id: str
    opening: str
    h3_prompt: str
    references: tuple[Reference, ...]
    source_hash: str
    compile_hash: str
    translation_cache_key: str
    translation_config_hash: str
    translation_receipt: TranslationReceipt
    frame_request_hash: str
    compiler_version: str

    @property
    def shot_id(self) -> str:
        return self.source_shot.shot_id

    @property
    def source_compile_hash(self) -> str:
        return self.compile_hash

    @property
    def source_segments(self) -> tuple[str, ...]:
        return self.source_shot.source_segments

    @property
    def dialogue(self) -> tuple[Dialogue, ...]:
        return self.source_shot.dialogue

    @property
    def duration_seconds(self) -> int:
        return self.source_shot.duration_seconds

    @property
    def source_action(self) -> str:
        return self.source_shot.action

    @property
    def source_end_state(self) -> str:
        return self.source_shot.end_state

    @property
    def attachments(self) -> tuple[Reference, ...]:
        return self.references

    @property
    def review_references(self) -> tuple[Reference, ...]:
        return self.references

    @property
    def frame_request(self) -> FrameRequest:
        return FrameRequest(
            shot_id=self.shot_id,
            prompt=self.opening,
            references=_frame_references(self.source_shot, self.references),
            input_hash=self.frame_request_hash,
        )


@dataclass(frozen=True)
class Compilation:
    episode_id: str
    shots: tuple[CompiledShot, ...]
    compile_hash: str
    source_script_hash: str
    compiler_version: str

    def get(self, shot_id: str) -> CompiledShot:
        for shot in self.shots:
            if shot.shot_id == shot_id:
                return shot
        raise KeyError(f"unknown compiled shot: {shot_id}")


@dataclass(frozen=True)
class VideoRequest:
    shot_id: str
    prompt: str
    duration_seconds: int
    references: tuple[Reference, ...]
    source_segments: tuple[str, ...]
    source_action: str
    source_end_state: str
    accepted_frame_hash: str
    accepted_frame_id: str | None
    frame_request_hash: str
    source_compile_hash: str
    input_hash: str

    @property
    def h3_prompt(self) -> str:
        return self.prompt

    @property
    def request_hash(self) -> str:
        return self.input_hash

    @property
    def attachments(self) -> tuple[Reference, ...]:
        return self.references


@dataclass(frozen=True)
class _Translation:
    opening_en: str
    action_en: str
    end_state_en: str


@dataclass(frozen=True)
class TranslationReceipt:
    translation: _Translation
    translation_cache_key: str
    translation_config_hash: str
    compiler_version: str
    receipt_hash: str


def _translation_receipt_hash(
    translated: _Translation,
    cache_key: str,
    config_hash: str,
    compiler_version: str,
) -> str:
    return _value_hash(
        {
            "translation": asdict(translated),
            "translation_cache_key": cache_key,
            "translation_config_hash": config_hash,
            "compiler_version": compiler_version,
        }
    )


def _make_translation_receipt(
    translated: _Translation, cache_key: str, config_hash: str
) -> TranslationReceipt:
    return TranslationReceipt(
        translation=translated,
        translation_cache_key=cache_key,
        translation_config_hash=config_hash,
        compiler_version=COMPILER_VERSION,
        receipt_hash=_translation_receipt_hash(
            translated, cache_key, config_hash, COMPILER_VERSION
        ),
    )


def _validate_translation_receipt(
    receipt: TranslationReceipt, cache_key: str, config_hash: str
) -> None:
    if not isinstance(receipt, TranslationReceipt):
        raise TranslationError("previous compilation has no typed translation receipt")
    if (
        receipt.translation_cache_key != cache_key
        or receipt.translation_config_hash != config_hash
        or receipt.compiler_version != COMPILER_VERSION
    ):
        raise TranslationError("translation receipt is not bound to the current compilation")
    expected = _translation_receipt_hash(
        receipt.translation, cache_key, config_hash, receipt.compiler_version
    )
    if receipt.receipt_hash != expected:
        raise TranslationError("translation receipt hash does not match typed translation")


def _issue(
    shot_id: str,
    code: str,
    owner: str,
    subject_id: str,
    message: str,
    **evidence,
) -> ReferenceIssue:
    return ReferenceIssue(shot_id, code, owner, subject_id, message, **evidence)


def _extra_field_issues(shot: Shot) -> tuple[ReferenceIssue, ...]:
    if not shot.extra_fields:
        return ()
    return (
        _issue(
            shot.shot_id,
            "author_owned_fields",
            "author",
            shot.shot_id,
            "author-owned fields only; unsupported: " + ", ".join(shot.extra_fields),
        ),
    )


def _verify_reference(shot_id: str, reference: Reference) -> ReferenceIssue | None:
    path = Path(reference.source_path)
    if not path.is_file():
        return _issue(
            shot_id,
            "missing_file",
            "host",
            reference.subject_id,
            "registered reference file does not exist",
            reference_path=str(path),
            expected_sha256=reference.sha256,
        )
    actual = _file_hash(path)
    if actual != reference.sha256:
        return _issue(
            shot_id,
            "hash_mismatch",
            "host",
            reference.subject_id,
            "current reference file SHA-256 differs from the manifest",
            reference_path=str(path),
            expected_sha256=reference.sha256,
            actual_sha256=actual,
        )
    return None


def validate_current_references(
    shot_id: str, references: tuple[Reference, ...]
) -> None:
    issues = tuple(
        issue
        for reference in references
        if (issue := _verify_reference(shot_id, reference)) is not None
    )
    if issues:
        raise CompilationError(issues)


def _expand_references(
    shot: Shot,
    manifest: ReferenceManifest,
    *,
    style_id: str,
) -> tuple[tuple[Reference, ...], tuple[ReferenceIssue, ...]]:
    issues = []
    registrations = {subject.subject_id: subject for subject in manifest.subjects}
    mentioned = tuple(dict.fromkeys(shot.visible_characters + shot.reference_subjects))
    for subject_id in mentioned:
        if subject_id not in registrations:
            issues.append(
                _issue(
                    shot.shot_id,
                    "unregistered_subject",
                    "author",
                    subject_id,
                    "shot names a subject ID that is not registered",
                )
            )

    selected = []
    for subject_id in dict.fromkeys(shot.reference_subjects):
        if subject_id not in registrations:
            continue
        bound = tuple(
            reference
            for reference in manifest.references
            if reference.subject_id == subject_id and reference.role != "style"
        )
        if not bound:
            issues.append(
                _issue(
                    shot.shot_id,
                    "unbound_subject",
                    "host",
                    subject_id,
                    "registered subject has no bound non-style reference image",
                )
            )
        selected.extend(bound)

    style = tuple(
        reference
        for reference in manifest.references
        if reference.subject_id == style_id and reference.role == "style"
    )
    if not style:
        issues.append(
            _issue(
                shot.shot_id,
                "unbound_subject",
                "host",
                style_id,
                "episode style has no bound style reference image",
            )
        )
    selected.extend(style)

    for reference in selected:
        if reference.role not in _ROLE_ORDER:
            issues.append(
                _issue(
                    shot.shot_id,
                    "invalid_role",
                    "host",
                    reference.subject_id,
                    f"unsupported reference role: {reference.role}",
                    reference_path=reference.source_path,
                )
            )
            continue
        current_issue = _verify_reference(shot.shot_id, reference)
        if current_issue is not None:
            issues.append(current_issue)

    ordered = tuple(
        sorted(
            selected,
            key=lambda reference: (
                _ROLE_ORDER.get(reference.role, 99),
                reference.order,
                reference.subject_id,
                reference.path,
            ),
        )
    )
    return ordered, tuple(issues)


def expand_references(
    shot: Shot,
    manifest: ReferenceManifest,
    *,
    style_id: str | None = None,
) -> tuple[Reference, ...]:
    references, issues = _expand_references(
        shot, manifest, style_id=style_id or manifest.style_id
    )
    if issues:
        raise CompilationError(issues)
    return references


def _frame_references(shot: Shot, references: tuple[Reference, ...]) -> tuple[Reference, ...]:
    """References for the t=0 frame: character/location cards only for people the
    author marked as actually in the opening frame; style cards always stay."""
    opening = set(shot.opening_characters)
    return tuple(
        reference
        for reference in references
        if reference.role == "location" or reference.subject_id in opening
    )


def _subject_profiles(manifest: ReferenceManifest) -> dict[str, dict[str, str]]:
    return {
        subject.subject_id: {
            "name": subject.name,
            "anchor": f"{subject.name}（{subject.subject_id}）：{subject.profile}",
        }
        for subject in manifest.subjects
        if subject.profile
    }


def _initial_scale(shot_scale: str) -> str:
    for separator in ("至", "到", "—", "–", "-"):
        if separator in shot_scale:
            head = shot_scale.split(separator, 1)[0].strip()
            if head:
                return head
    return shot_scale.strip()


def _attachment_map(references: tuple[Reference, ...], profiles=None) -> str:
    lines = []
    for index, reference in enumerate(references, 1):
        if reference.role == "style":
            lines.append(f"附件{index}（style）：仅约束光色、材质与渲染，不添加人物或衣装")
            continue  # kept for callers that do include style; frame requests exclude it
        if profiles and reference.subject_id in profiles:
            who = f"，{profiles[reference.subject_id]['name']} {reference.subject_id}"
        else:
            who = f"，{reference.subject_id}"
        label = {"identity": "身份", "wardrobe": "基础衣装", "location": "场景"}.get(
            reference.role, reference.role
        )
        lines.append(f"附件{index}（{reference.role}，{label}{who}）：{reference.scope}")
    return "\n".join(lines)


def _opening(
    shot: Shot,
    profiles: Mapping[str, str] | None = None,
    frame_refs: tuple[Reference, ...] = (),
) -> str:
    lines = [
        "首帧只表现镜头零点：采用本镜开场机位与初始构图，镜内后续的运镜、景别切换、"
        "抓握与位移等全部动作过程均不进入首帧；起始状态中已在进行的动作只表现当前这一瞬间。",
        f"地点：{shot.location}",
        f"起始状态：{shot.start_state}",
        f"景别：{_initial_scale(shot.shot_scale)}（本镜起始景别，单一定格构图）",
        "出场人物的长相以身份参考附件为准（同一张脸），发型与衣装以衣装参考附件为准；"
        "基础衣装与随身装备默认沿用对应角色的衣装参考卡，"
        "只有起始状态明确写出的不同穿戴或携带（如物件已脱手、外衣已交给他人）才覆盖默认，"
        "未提及的部分保持参考卡造型。"
        "起始状态中正在滑落、漂浮、被追赶或抛出的随身物件视为已离开人物身体"
        "（同一物件，不是第二件）：其外观仍依参考卡，位置与状态依起始状态。",
        "参考卡的版面、文字与示范姿势不进入画面；整体色光与材质风格由宿主在视频生成阶段统一控制。",
        "画面中出现的人物只由起始状态决定；身份与衣装参考用于保持长相一致，不代表人物必须在场。",
    ]
    if frame_refs:
        lines.append(
            "附件清单（与本次请求图片顺序一致，每张只约束其注明的对象）：\n"
            + _attachment_map(frame_refs, profiles)
        )
    anchors = [
        profiles[sid]["anchor"]
        for sid in shot.opening_characters
        if profiles and sid in profiles
    ]
    if anchors:
        lines.append(
            "人物锚定（仅限 opening_characters 声明的首帧入画者，与附件同源的文字约束）：\n"
            + "\n".join(anchors)
        )
    return "\n".join(lines)


def _translation_input(shot: Shot, opening: str, *, include_camera: bool = False) -> dict:
    payload = {
        "shot_id": shot.shot_id,
        "source_segments": list(shot.source_segments),
        "opening": opening,
        "action": shot.action,
        "end_state": shot.end_state,
        "dialogue": [asdict(line) for line in shot.dialogue],
        "duration_seconds": shot.duration_seconds,
    }
    if include_camera:
        payload["camera"] = shot.camera
        payload["shot_scale"] = shot.shot_scale
    return payload


def _parse_translation(raw: object) -> _Translation:
    if isinstance(raw, _Translation):
        return raw
    if not isinstance(raw, Mapping) or set(raw) != set(_TRANSLATION_FIELDS):
        raise TranslationError(
            "translation response structure must contain exactly opening_en, action_en, end_state_en"
        )
    values = {}
    for field in _TRANSLATION_FIELDS:
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            raise TranslationError(f"translation response {field} must be nonempty")
        values[field] = value
    return _Translation(**values)


def _review_translation(
    shot: Shot,
    opening: str,
    translated: _Translation,
    reviewer,
    *,
    prompt: str,
) -> None:
    if reviewer is None:
        raise ValueError("a typed H3 translation fidelity reviewer is required")
    payload = {
        "task": "fidelity_review",
        "source": _translation_input(shot, opening, include_camera=True),
        "translation": asdict(translated),
    }
    raw = reviewer.ask_json(
        [
            {"type": "text", "text": prompt},
            {"type": "text", "text": _canonical(payload)},
        ],
        FIDELITY_SCHEMA,
        name="clean_h3_translation_fidelity",
        max_tokens=2000,
        timeout=1200,
        retry_truncated=False,
    )
    expected_fields = {*_FIDELITY_CHECKS, "violations"}
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise TranslationError("translation fidelity review response structure is invalid")
    failed = []
    for field in _FIDELITY_CHECKS:
        if type(raw[field]) is not bool:
            raise TranslationError(f"translation fidelity check {field} must be boolean")
        if not raw[field]:
            failed.append(field)
    violations = raw["violations"]
    if not isinstance(violations, list) or any(
        not isinstance(item, str) or not item.strip() for item in violations
    ):
        raise TranslationError("translation fidelity violations must be nonempty strings")
    if failed or violations:
        details = [*failed, *violations]
        raise TranslationFidelityFailed(
            "translation fidelity review failed: " + "; ".join(details)
        )


def _translate(
    shot: Shot,
    opening: str,
    translator,
    *,
    reviewer,
    prompt: str,
    cache: MutableMapping[str, object],
    cache_key: str,
) -> _Translation:
    _require_independent_reviewer(translator, reviewer)
    source = _translation_input(shot, opening)
    cached = cache.get(cache_key)
    fresh = cached is None
    if fresh:
        if translator is None:
            raise ValueError("a typed H3 translator is required")
        feedback = []
        attempts = 0
        while True:
            attempts += 1
            payload = {"task": "translate", **source}
            if feedback:
                payload["previous_attempt_rejected_by_fidelity_review"] = feedback
            raw = translator.ask_json(
                [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": _canonical(payload)},
                ],
                TRANSLATION_SCHEMA,
                name="clean_h3_translation",
                max_tokens=4000,
                timeout=1200,
                retry_truncated=False,
            )
            translated = _parse_translation(raw)
            try:
                _review_translation(
                    shot,
                    opening,
                    translated,
                    reviewer,
                    prompt=prompt,
                )
            except TranslationFidelityFailed as error:
                if attempts >= _TRANSLATION_ATTEMPTS:
                    raise TranslationError(
                        f"translation fidelity failed after {attempts} bounded"
                        f" attempts; last rejection: {error}"
                    ) from error
                feedback.append(str(error))
                continue
            break
    else:
        translated = _parse_translation(cached)
        _review_translation(
            shot,
            opening,
            translated,
            reviewer,
            prompt=prompt,
        )
    if fresh:
        cache[cache_key] = asdict(translated)
    return translated


def _h3_prompt(
    shot: Shot,
    translated: _Translation,
    profiles: Mapping[str, str] | None = None,
    references: tuple[Reference, ...] = (),
    keyframe_first: bool = True,
) -> str:
    lines = [
        "Generate one continuous shot from the supplied accepted first frame.",
        "OPENING AT t=0 (preserve this state):",
        translated.opening_en,
        "CAMERA & FRAMING (the authored camera intent for this single continuous shot):",
        shot.camera,
        f"SCALE PROGRESSION: {shot.shot_scale} (the frame starts at {_initial_scale(shot.shot_scale)})",
        "ACTION DURING THE SHOT (preserve the complete process and order):",
        translated.action_en,
        "END STATE AT THE END OF THE SHOT:",
        translated.end_state_en,
    ]
    if profiles:
        known = [
            f"{sid} = {profiles[sid]['name']}"
            for sid in shot.visible_characters
            if sid in profiles
        ]
        if known:
            lines.append("CHARACTERS: " + "; ".join(known))
    if references:
        offset_base = 2 if keyframe_first else 1
        if keyframe_first:
            lines.append(
                "ATTACHMENTS (in request order; image 1 is the accepted first frame — start from it):"
            )
            lines.append("image 1 = accepted first frame for this shot")
        else:
            lines.append(
                "ATTACHMENTS (reference mode; no keyframe — realize the described t=0 state"
                " yourself, keeping every subject's look strictly tied to their cards):"
            )
        for offset, reference in enumerate(references):
            who = (
                f" for {profiles[reference.subject_id]['name']} ({reference.subject_id})"
                if profiles and reference.subject_id in profiles
                else f" for {reference.subject_id}"
            )
            if reference.role == "style":
                lines.append(
                    f"image {offset + offset_base} = style card: constrains color, light and material only"
                )
            else:
                lines.append(
                    f"image {offset + offset_base} = {reference.role} card{who}:"
                    " constrains that subject's look only"
                )
    if shot.dialogue:
        lines.append(
            "DIALOGUE (speak every Chinese line verbatim, exactly once, in the listed"
            " order, by its speaker; the English ACTION describes the same process —"
            " do not add, drop, translate or reorder speech):"
        )
        lines.extend(f"{line.speaker}: {line.line}" for line in shot.dialogue)
    else:
        lines.append("DIALOGUE: none; do not add speech.")
    lines.extend(
        [
            f"DURATION: exactly {shot.duration_seconds} seconds.",
            "SOURCE SEGMENTS (host trace; preserve their source events without additions or omissions): "
            + ", ".join(shot.source_segments),
            "SOURCE ACTION (authoritative Chinese trace; preserve against the English action):",
            shot.action,
            "SOURCE END STATE (authoritative Chinese trace; preserve against the English end state):",
            shot.end_state,
        ]
    )
    return "\n".join(lines)


def _reference_data(references: tuple[Reference, ...]) -> list[dict]:
    return [asdict(reference) for reference in references]


def _cache_key(
    shot: Shot,
    style_id: str,
    references: tuple[Reference, ...],
    prompt_sha256: str,
    config_hash: str,
    profiles: Mapping[str, str] | None = None,
) -> str:
    return _value_hash(
        {
            "compiler_version": COMPILER_VERSION,
            "source_shot": asdict(shot),
            "style_id": style_id,
            "references": _reference_data(references),
            "translation_prompt_sha256": prompt_sha256,
            "translation_config_hash": config_hash,
            "subject_profiles": dict(sorted((profiles or {}).items())),
        }
    )


def _assemble_compiled(
    shot: Shot,
    style_id: str,
    references: tuple[Reference, ...],
    translated: _Translation,
    *,
    cache_key: str,
    config_hash: str,
    profiles: Mapping[str, str] | None = None,
) -> CompiledShot:
    frame_refs = _frame_references(shot, references)
    opening = _opening(shot, profiles, frame_refs)
    h3_prompt = _h3_prompt(shot, translated, profiles, references)
    source_hash = _value_hash(asdict(shot))
    reference_data = _reference_data(references)
    frame_reference_data = _reference_data(frame_refs)
    receipt = _make_translation_receipt(translated, cache_key, config_hash)
    frame_request_hash = _value_hash(
        {
            "compiler_version": COMPILER_VERSION,
            "shot_id": shot.shot_id,
            "source_hash": source_hash,
            "style_id": style_id,
            "opening": opening,
            "references": frame_reference_data,
        }
    )
    compile_hash = _value_hash(
        {
            "compiler_version": COMPILER_VERSION,
            "shot_id": shot.shot_id,
            "source_hash": source_hash,
            "style_id": style_id,
            "opening": opening,
            "h3_prompt": h3_prompt,
            "references": reference_data,
            "translation_cache_key": cache_key,
            "translation_config_hash": config_hash,
            "translation_receipt_hash": receipt.receipt_hash,
            "frame_request_hash": frame_request_hash,
        }
    )
    return CompiledShot(
        source_shot=shot,
        style_id=style_id,
        opening=opening,
        h3_prompt=h3_prompt,
        references=references,
        source_hash=source_hash,
        compile_hash=compile_hash,
        translation_cache_key=cache_key,
        translation_config_hash=config_hash,
        translation_receipt=receipt,
        frame_request_hash=frame_request_hash,
        compiler_version=COMPILER_VERSION,
    )


def _compile_expanded(
    shot: Shot,
    style_id: str,
    references: tuple[Reference, ...],
    translator,
    *,
    reviewer,
    prompt: str,
    cache: MutableMapping[str, object],
    cache_key: str,
    config_hash: str,
    profiles: Mapping[str, str] | None = None,
) -> CompiledShot:
    opening = _opening(shot, profiles, _frame_references(shot, references))
    translated = _translate(
        shot,
        opening,
        translator,
        reviewer=reviewer,
        prompt=prompt,
        cache=cache,
        cache_key=cache_key,
    )
    return _assemble_compiled(
        shot,
        style_id,
        references,
        translated,
        cache_key=cache_key,
        config_hash=config_hash,
        profiles=profiles,
    )


def _prompt(prompt_path: str | Path | None) -> tuple[str, str]:
    path = Path(prompt_path or _project_root() / "prompts/h3-translation.md")
    data = path.read_bytes()
    return data.decode("utf-8"), sha256(data).hexdigest()


def compile_shot(
    shot: Shot,
    references: ReferenceManifest,
    translator=None,
    *,
    style_id: str | None = None,
    cache: MutableMapping[str, object] | None = None,
    translation_reviewer=None,
    translation_config: Mapping[str, object] | None = None,
    prompt_path: str | Path | None = None,
) -> CompiledShot:
    reviewer = _require_independent_reviewer(translator, translation_reviewer)
    extra_issues = _extra_field_issues(shot)
    if extra_issues:
        raise CompilationError(extra_issues)
    selected_style = style_id or references.style_id
    if selected_style != references.style_id:
        raise CompilationError(
            [
                _issue(
                    shot.shot_id,
                    "style_mismatch",
                    "host",
                    selected_style,
                    "episode style does not match the reference manifest",
                )
            ]
        )
    expanded = expand_references(shot, references, style_id=selected_style)
    profiles = _subject_profiles(references)
    prompt, prompt_sha256 = _prompt(prompt_path)
    config_hash = _translation_config_hash(
        translator, reviewer, translation_config
    )
    cache_key = _cache_key(
        shot, selected_style, expanded, prompt_sha256, config_hash, profiles
    )
    return _compile_expanded(
        shot,
        selected_style,
        expanded,
        translator,
        reviewer=reviewer,
        prompt=prompt,
        cache={} if cache is None else cache,
        cache_key=cache_key,
        config_hash=config_hash,
        profiles=profiles,
    )


def compile_episode(
    episode: EpisodeSpec,
    references: ReferenceManifest,
    translator=None,
    *,
    cache: MutableMapping[str, object] | None = None,
    previous: Compilation | None = None,
    translation_reviewer=None,
    translation_config: Mapping[str, object] | None = None,
    prompt_path: str | Path | None = None,
) -> Compilation:
    reviewer = _require_independent_reviewer(translator, translation_reviewer)
    extra_issues = tuple(
        issue for shot in episode.shots for issue in _extra_field_issues(shot)
    )
    if extra_issues:
        raise CompilationError(extra_issues)
    if episode.style_id != references.style_id:
        raise CompilationError(
            [
                _issue(
                    "",
                    "style_mismatch",
                    "host",
                    episode.style_id,
                    "episode style does not match the reference manifest",
                )
            ]
        )
    if previous is not None and previous.episode_id != episode.episode_id:
        raise ValueError("previous compilation belongs to another episode")
    shot_ids = [shot.shot_id for shot in episode.shots]
    if len(shot_ids) != len(set(shot_ids)):
        raise ValueError("duplicate shot_id in episode")

    expanded = {}
    issues = []
    for shot in episode.shots:
        selected, current_issues = _expand_references(
            shot, references, style_id=episode.style_id
        )
        expanded[shot.shot_id] = selected
        issues.extend(current_issues)
    if issues:
        raise CompilationError(issues)

    prompt, prompt_sha256 = _prompt(prompt_path)
    translation_cache = {} if cache is None else cache
    config_hash = _translation_config_hash(
        translator, reviewer, translation_config
    )
    previous_shots = (
        {shot.shot_id: shot for shot in previous.shots}
        if previous is not None
        else {}
    )
    profiles = _subject_profiles(references)
    compiled = []
    for shot in episode.shots:
        selected = expanded[shot.shot_id]
        cache_key = _cache_key(
            shot, episode.style_id, selected, prompt_sha256, config_hash, profiles
        )
        old = previous_shots.get(shot.shot_id)
        if (
            old is not None
            and old.compiler_version == COMPILER_VERSION
            and old.source_shot == shot
            and old.style_id == episode.style_id
            and old.references == selected
            and old.translation_cache_key == cache_key
            and old.translation_config_hash == config_hash
        ):
            receipt = old.translation_receipt
            translated = _parse_translation(receipt.translation)
            _review_translation(
                shot,
                _opening(shot, profiles, _frame_references(shot, selected)),
                translated,
                reviewer,
                prompt=prompt,
            )
            _validate_translation_receipt(receipt, cache_key, config_hash)
            compiled.append(
                _assemble_compiled(
                    shot,
                    episode.style_id,
                    selected,
                    translated,
                    cache_key=cache_key,
                    config_hash=config_hash,
                    profiles=profiles,
                )
            )
            continue
        compiled.append(
            _compile_expanded(
                shot,
                episode.style_id,
                selected,
                translator,
                reviewer=reviewer,
                prompt=prompt,
                cache=translation_cache,
                cache_key=cache_key,
                config_hash=config_hash,
                profiles=profiles,
            )
        )
    shots = tuple(compiled)
    compile_hash = _value_hash(
        {
            "compiler_version": COMPILER_VERSION,
            "episode_id": episode.episode_id,
            "source_script_hash": script_hash(episode),
            "shots": [shot.compile_hash for shot in shots],
        }
    )
    return Compilation(
        episode_id=episode.episode_id,
        shots=shots,
        compile_hash=compile_hash,
        source_script_hash=script_hash(episode),
        compiler_version=COMPILER_VERSION,
    )


def _frame_value(frame: object, name: str, *, required: bool = True):
    if isinstance(frame, Mapping):
        value = frame.get(name)
    else:
        value = getattr(frame, name, None)
    if required and (value is None or value == ""):
        raise ValueError(f"accepted frame must be bound by {name}")
    return value


def video_request(compiled: CompiledShot, accepted_frame: object) -> VideoRequest:
    frameless = accepted_frame is None
    if frameless:
        frame_hash = ""
        frame_id = None
    else:
        frame_hash = _frame_value(accepted_frame, "sha256")
        frame_input_hash = _frame_value(accepted_frame, "input_hash")
        frame_shot_id = _frame_value(accepted_frame, "shot_id")
        frame_id = _frame_value(accepted_frame, "id", required=False)
        if not isinstance(frame_hash, str) or re.fullmatch(r"[0-9a-f]{64}", frame_hash) is None:
            raise ValueError("accepted frame sha256 must be 64 lowercase hex characters")
        if frame_input_hash != compiled.frame_request_hash:
            raise ValueError("accepted frame is stale for the current frame request/H3 compilation")
        if frame_shot_id != compiled.shot_id:
            raise ValueError("accepted frame belongs to another shot")

    validate_current_references(compiled.shot_id, compiled.references)

    input_hash = _value_hash(
        {
            "compiler_version": compiled.compiler_version,
            "shot_id": compiled.shot_id,
            "source_compile_hash": compiled.compile_hash,
            "frame_request_hash": compiled.frame_request_hash,
            "mode": "reference" if frameless else "keyframe",
            "accepted_frame_hash": frame_hash or None,
            "h3_prompt": compiled.h3_prompt,
            "duration_seconds": compiled.duration_seconds,
            "references": _reference_data(compiled.references),
        }
    )
    return VideoRequest(
        shot_id=compiled.shot_id,
        prompt=compiled.h3_prompt,
        duration_seconds=compiled.duration_seconds,
        references=compiled.references,
        source_segments=compiled.source_segments,
        source_action=compiled.source_action,
        source_end_state=compiled.source_end_state,
        accepted_frame_hash=frame_hash,
        accepted_frame_id=frame_id,
        frame_request_hash=compiled.frame_request_hash,
        source_compile_hash=compiled.compile_hash,
        input_hash=input_hash,
    )
