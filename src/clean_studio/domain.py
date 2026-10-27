from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class Dialogue:
    speaker: str
    line: str


@dataclass(frozen=True)
class FixedOption:
    label: str
    target_world: str
    target_video: str
    title: str


@dataclass(frozen=True)
class MusicCue:
    start_seconds: int
    end_seconds: int
    mood: str
    soundscape: str


@dataclass(frozen=True, init=False)
class Shot:
    shot_id: str
    scene_id: str
    source_segments: tuple[str, ...]
    location: str
    start_state: str
    action: str
    end_state: str
    camera: str
    shot_scale: str
    visible_characters: tuple[str, ...]
    opening_characters: tuple[str, ...]
    dialogue: tuple[Dialogue, ...]
    reference_subjects: tuple[str, ...]
    duration_seconds: int
    extra_fields: tuple[str, ...]

    def __init__(
        self,
        shot_id: str,
        scene_id: str,
        source_segments: tuple[str, ...],
        location: str,
        start_state: str,
        action: str,
        end_state: str,
        camera: str,
        shot_scale: str,
        visible_characters: tuple[str, ...],
        dialogue: tuple[Dialogue, ...],
        reference_subjects: tuple[str, ...],
        duration_seconds: int,
        opening_characters: tuple[str, ...] = (),
        extra_fields: tuple[str, ...] = (),
        **non_author_fields: object,
    ) -> None:
        object.__setattr__(self, "shot_id", shot_id)
        object.__setattr__(self, "scene_id", scene_id)
        object.__setattr__(self, "source_segments", tuple(source_segments))
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "start_state", start_state)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "end_state", end_state)
        object.__setattr__(self, "camera", camera)
        object.__setattr__(self, "shot_scale", shot_scale)
        object.__setattr__(self, "visible_characters", tuple(visible_characters))
        object.__setattr__(self, "opening_characters", tuple(opening_characters))
        object.__setattr__(self, "dialogue", tuple(dialogue))
        object.__setattr__(self, "reference_subjects", tuple(reference_subjects))
        object.__setattr__(self, "duration_seconds", duration_seconds)
        object.__setattr__(
            self,
            "extra_fields",
            tuple(extra_fields) + tuple(non_author_fields),
        )


@dataclass(frozen=True)
class EpisodeSpec:
    episode_id: str
    title: str
    target_seconds: int
    style_id: str
    shots: tuple[Shot, ...]
    fixed_question: str
    fixed_options: tuple[FixedOption, ...]
    music_cues: tuple[MusicCue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shots", tuple(self.shots))
        object.__setattr__(self, "fixed_options", tuple(self.fixed_options))
        object.__setattr__(self, "music_cues", tuple(self.music_cues))


@dataclass(frozen=True)
class SourceSegment:
    segment_id: str
    scene_id: str
    flow_index: int
    action: str | None
    speaker: str | None
    line: str | None
    delivery: str | None


@dataclass(frozen=True)
class SourceScene:
    scene_id: str
    name: str
    characters: tuple[str, ...]
    segments: tuple[SourceSegment, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "characters", tuple(self.characters))
        object.__setattr__(self, "segments", tuple(self.segments))


@dataclass(frozen=True)
class SourcePackage:
    archive_path: str
    archive_sha256: str
    material_id: str
    video_id: str
    title: str
    target_seconds: int
    scenes: tuple[SourceScene, ...]
    fixed_question: str
    fixed_options: tuple[FixedOption, ...]
    continuity_exit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenes", tuple(self.scenes))
        object.__setattr__(self, "fixed_options", tuple(self.fixed_options))

    @property
    def segments(self) -> tuple[SourceSegment, ...]:
        return tuple(segment for scene in self.scenes for segment in scene.segments)

    @property
    def dialogue(self) -> tuple[SourceSegment, ...]:
        return tuple(segment for segment in self.segments if segment.speaker is not None)


@dataclass(frozen=True)
class SubjectRegistration:
    subject_id: str
    name: str
    subject_type: str
    profile: str


@dataclass(frozen=True)
class Reference:
    subject_id: str
    path: str
    sha256: str
    role: str
    scope: str
    order: int
    source_path: str
    recorded_date: str


@dataclass(frozen=True)
class ReferenceManifest:
    source_path: str
    source_sha256: str
    recorded_date: str
    style_id: str
    subjects: tuple[SubjectRegistration, ...]
    references: tuple[Reference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "subjects", tuple(self.subjects))
        object.__setattr__(self, "references", tuple(self.references))

    def references_for(self, subject_id: str) -> tuple[Reference, ...]:
        return tuple(
            reference for reference in self.references
            if reference.subject_id == subject_id
        )

    def attachments_for(self, subject_ids: tuple[str, ...]) -> tuple[Reference, ...]:
        selected = set(subject_ids)
        return tuple(
            reference for reference in self.references
            if reference.role == "style" or reference.subject_id in selected
        )

    def to_bytes(self) -> bytes:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (payload + "\n").encode("utf-8")


EXPORT_BUNDLE_SCHEMA = "clean-studio-export-bundle-v1"


def _bundle_value(export, name, alias=None):
    if isinstance(export, Mapping):
        value = export.get(name)
        if value is None and alias is not None:
            value = export.get(alias)
    else:
        value = getattr(export, name, None)
        if value is None and alias is not None:
            value = getattr(export, alias, None)
    if value is None:
        raise ValueError(f"export bundle requires {name}")
    return value


def _bundle_json(value):
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def export_bundle_parts(export):
    if isinstance(export, Mapping) and export.get("schema", EXPORT_BUNDLE_SCHEMA) != EXPORT_BUNDLE_SCHEMA:
        raise ValueError("export bundle schema is unsupported")
    return {
        "schema": EXPORT_BUNDLE_SCHEMA,
        "video_sha256": _bundle_value(export, "video_sha256", "sha256"),
        "cover_sha256": _bundle_value(export, "cover_sha256"),
        "end_card_sha256": _bundle_value(export, "end_card_sha256"),
        "subtitle_sha256": _bundle_value(export, "subtitle_sha256"),
        "credits_sha256": _bundle_value(export, "credits_sha256"),
        "manifest_sha256": _bundle_value(export, "manifest_sha256"),
        "episode_hash": _bundle_value(export, "episode_hash"),
        "expected_duration_seconds": _bundle_value(export, "expected_duration_seconds"),
        "body_seconds": _bundle_value(export, "body_seconds"),
        "expected_frames": _bundle_value(export, "expected_frames"),
        "shot_ids": list(_bundle_value(export, "shot_ids")),
        "fixed_question": _bundle_value(export, "fixed_question"),
        "fixed_options": _bundle_json(_bundle_value(export, "fixed_options")),
        "mixed_music": _bundle_json(_bundle_value(export, "mixed_music")),
        "subtitles": _bundle_json(_bundle_value(export, "subtitles")),
        "source_videos": _bundle_json(_bundle_value(export, "source_videos")),
    }


def export_bundle_identity(export):
    parts = export_bundle_parts(export)
    encoded = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def export_bundle_metadata(export):
    return {
        **export_bundle_parts(export),
        "bundle_hash": export_bundle_identity(export),
    }


Finding = dict[str, object]


@dataclass(frozen=True)
class Review:
    review_id: str
    content_hash: str
    verdict: str
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        findings = tuple(self.findings)
        if any(not isinstance(finding, dict) for finding in findings):
            raise TypeError("findings must be structured objects")
        object.__setattr__(
            self, "findings", tuple(deepcopy(finding) for finding in findings)
        )


@dataclass(frozen=True)
class Operation:
    id: str
    idempotency_key: str
    kind: str
    input_hash: str
    shot_id: str | None
    status: str
    external_task_id: str | None = None
    receipt_path: str | None = None
