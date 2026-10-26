from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class Review:
    review_id: str
    content_hash: str
    verdict: str
    findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))


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
