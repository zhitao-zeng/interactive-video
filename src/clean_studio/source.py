from hashlib import sha256
import json
import os
from pathlib import Path
import re
from zipfile import ZipFile

from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    Reference,
    ReferenceManifest,
    SourcePackage,
    SourceScene,
    SourceSegment,
    SubjectRegistration,
)

STRUCTURED_SCRIPT = "pkg/episode/02-结构化剧本.json"
FULL_SCRIPT = "pkg/episode/01-完整剧本.md"
END_CARD_SECONDS = 3
STYLE_SCOPE = "仅约束色光、材质和渲染；不复制人物、衣装、剧情、文字、布局或POV"


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_config_path() -> Path:
    root = Path(
        os.environ.get(
            "NOVEL_PROJECT_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    )
    return root / "configs/w10.json"


def _load_config(config_path: Path | None) -> dict:
    return json.loads(
        (config_path or _default_config_path()).read_text(encoding="utf-8")
    )


def load_source(zip_path: Path) -> SourcePackage:
    zip_path = Path(zip_path)
    archive_sha256 = _sha256(zip_path)
    with ZipFile(zip_path) as archive:
        structured = json.loads(archive.read(STRUCTURED_SCRIPT).decode("utf-8"))
        full_script = archive.read(FULL_SCRIPT).decode("utf-8")
    raw = structured["episodes"][0]
    headings = re.findall(r"^##\s+场(\d+)\s*｜\s*(.+?)\s*$", full_script, re.MULTILINE)
    if len(headings) != len(raw["scenes"]):
        raise ValueError("scene name alignment with full script failed")

    scenes = []
    for scene_number, (raw_scene, heading) in enumerate(
        zip(raw["scenes"], headings), start=1
    ):
        heading_number, scene_name = heading
        if int(heading_number) != scene_number:
            raise ValueError("scene name alignment with full script failed")
        scene_id = raw_scene["sceneId"]
        segments = tuple(
            SourceSegment(
                segment_id=f"{scene_id}/flow/{index}",
                scene_id=scene_id,
                flow_index=index,
                action=item.get("action"),
                speaker=item.get("speaker"),
                line=item.get("line"),
                delivery=item.get("delivery"),
            )
            for index, item in enumerate(raw_scene["flow"])
        )
        scenes.append(
            SourceScene(
                scene_id=scene_id,
                name=scene_name,
                characters=tuple(raw_scene["characters"]),
                segments=segments,
            )
        )

    return SourcePackage(
        archive_path=str(zip_path.resolve()),
        archive_sha256=archive_sha256,
        material_id=raw["materialId"],
        video_id=raw["videoId"],
        title=raw["title"],
        target_seconds=raw["targetSeconds"],
        scenes=tuple(scenes),
        fixed_question=raw["fixedQuestion"],
        fixed_options=tuple(
            FixedOption(
                label=option["label"],
                target_world=option["targetWorld"],
                target_video=option["targetVideo"],
                title=option["title"],
            )
            for option in raw["fixedOptions"]
        ),
        continuity_exit=raw["continuityExit"],
    )


def build_reference_manifest(
    source: SourcePackage,
    material_dir: Path,
    style_dir: Path,
    *,
    config_path: Path | None = None,
) -> ReferenceManifest:
    config = _load_config(config_path)
    if source.material_id != config["input_id"]:
        raise ValueError("source package does not match input configuration")
    material_dir = Path(material_dir)
    style_dir = Path(style_dir)
    recorded_date = config["snapshot_date"]
    material = json.loads((material_dir / "material.json").read_text(encoding="utf-8"))
    profiles = {
        character["name"]: character
        for character in material.get("bible", {}).get("characters", [])
    }

    subjects = []
    references = []

    def add_reference(
        subject_id: str,
        actual_path: Path,
        relative_path: str,
        role: str,
        scope: str,
    ) -> None:
        if not actual_path.is_file():
            raise FileNotFoundError(f"reference image is missing: {actual_path}")
        references.append(
            Reference(
                subject_id=subject_id,
                path=relative_path,
                sha256=_sha256(actual_path),
                role=role,
                scope=scope,
                order=len(references) + 1,
                source_path=str(actual_path.resolve()),
                recorded_date=recorded_date,
            )
        )

    for subject in config["subjects"]:
        profile = profiles.get(subject["name"])
        if profile is None:
            raise ValueError(f"material profile is missing for {subject['subject_id']}")
        profile_text = "\n".join(
            f"{key}: {profile[key]}"
            for key in ("role", "appearance", "wardrobe")
            if profile.get(key)
        )
        if subject.get("wardrobe_note"):
            profile_text += f"\nwardrobe_note: {subject['wardrobe_note']}"
        subjects.append(
            SubjectRegistration(
                subject_id=subject["subject_id"],
                name=subject["name"],
                subject_type=subject["subject_type"],
                profile=profile_text,
            )
        )
        for reference in subject.get("references", []):
            filename = reference["file"]
            add_reference(
                subject["subject_id"],
                material_dir / filename,
                f"material/{filename}",
                reference["role"],
                reference["scope"],
            )

    style_id = config["style"]["style_id"]
    style_config = json.loads(
        (style_dir / config["style"]["config_file"]).read_text(encoding="utf-8")
    )
    for example in style_config["examples"]:
        filename = Path(example["path"]).name
        add_reference(
            style_id,
            style_dir / filename,
            f"style/{filename}",
            "style",
            STYLE_SCOPE,
        )

    return ReferenceManifest(
        source_path=source.archive_path,
        source_sha256=source.archive_sha256,
        recorded_date=recorded_date,
        style_id=style_id,
        subjects=tuple(subjects),
        references=tuple(references),
    )


def _normalized_state(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


_DIALOGUE_COMPLETE = r"(?:对白|台词)(?:完成[了啦]?|说完[了啦]?|讲完[了啦]?|结束[了啦]?)"
_NEXT_SHOT = r"(?:准备|进入|切到|切换到|转至|转向)?下一镜头?(?:继续|开始|拍摄)?"
_END_STATE_ATOM = rf"(?:{_DIALOGUE_COMPLETE}|{_NEXT_SHOT}|准备转场|(?:镜头)?(?:结束|完成)(?:拍摄)?|拍摄(?:结束|完成|停止)|同上|无|暂无|待定|保持现状|状态不变)"
_PURE_NON_STATE_PATTERN = re.compile(rf"(?:{_END_STATE_ATOM})+")


def _is_vague_end_state(value: str) -> bool:
    normalized = _normalized_state(value)
    return len(normalized) < 2 or bool(
        _PURE_NON_STATE_PATTERN.fullmatch(normalized)
    )


def validate_episode(
    episode: EpisodeSpec,
    source: SourcePackage,
    references: ReferenceManifest,
) -> None:
    if references.source_sha256 != source.archive_sha256:
        raise ValueError("source hash does not match reference manifest")
    if episode.episode_id != source.video_id:
        raise ValueError("episode_id does not match source")
    if episode.title != source.title or episode.target_seconds != source.target_seconds:
        raise ValueError(
            "episode title/target_seconds do not match source: expected title "
            f"{source.title!r} and target_seconds {source.target_seconds}"
        )
    if episode.style_id != references.style_id:
        raise ValueError("style_id does not match reference manifest")
    if episode.fixed_question != source.fixed_question:
        raise ValueError("fixedQuestion must be preserved verbatim")
    if episode.fixed_options != source.fixed_options:
        raise ValueError("fixedOptions must be preserved verbatim")
    if not episode.shots:
        raise ValueError("episode must contain shots")

    shot_ids = set()
    for shot in episode.shots:
        if shot.extra_fields:
            raise ValueError(
                "author-owned fields only; unsupported: " + ", ".join(shot.extra_fields)
            )
        if shot.shot_id in shot_ids:
            raise ValueError(f"duplicate shot_id: {shot.shot_id}")
        shot_ids.add(shot.shot_id)
        for field_name in (
            "location",
            "start_state",
            "action",
            "end_state",
            "camera",
            "shot_scale",
        ):
            value = getattr(shot, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if _is_vague_end_state(shot.end_state):
            raise ValueError("end_state must describe an actual end-of-shot state")
        if type(shot.duration_seconds) is not int or not 4 <= shot.duration_seconds <= 15:
            raise ValueError("duration_seconds must be an integer from 4 to 15")

    body_seconds = sum(shot.duration_seconds for shot in episode.shots)
    total_seconds = body_seconds + END_CARD_SECONDS
    if not 180 <= total_seconds <= 300:
        raise ValueError("total duration including end card must be from 180 to 300")

    for cue in episode.music_cues:
        if not 0 <= cue.start_seconds < cue.end_seconds <= body_seconds:
            raise ValueError(
                "music cue bounds must satisfy 0 <= start < end <= body seconds "
                f"(cues follow the actual shot timeline, host end card excluded);"
                f" body is {body_seconds} seconds, cue was"
                f" {cue.start_seconds}-{cue.end_seconds}"
            )

    subjects = {subject.subject_id: subject for subject in references.subjects}
    for shot in episode.shots:
        for subject_id in shot.visible_characters + shot.reference_subjects:
            if subject_id not in subjects:
                raise ValueError(f"unregistered subject: {subject_id}")
        for subject_id in shot.opening_characters:
            if subject_id not in subjects:
                raise ValueError(f"unregistered subject: {subject_id}")
            if subject_id not in shot.visible_characters:
                raise ValueError(
                    f"opening character must appear in visible_characters: {subject_id}"
                )
        for subject_id in shot.reference_subjects:
            if (
                subjects[subject_id].subject_type == "character"
                and subject_id not in shot.visible_characters
            ):
                raise ValueError(
                    f"character reference must appear in visible_characters: {subject_id}"
                )
            if not references.references_for(subject_id):
                raise ValueError(f"registered subject has no bound image: {subject_id}")

    scenes = {scene.scene_id: scene for scene in source.scenes}
    segments = {segment.segment_id: segment for segment in source.segments}
    flattened = []
    for shot in episode.shots:
        if shot.scene_id not in scenes:
            raise ValueError(f"unknown scene: {shot.scene_id}")
        scene = scenes[shot.scene_id]
        if any(subject_id not in scene.characters for subject_id in shot.visible_characters):
            raise ValueError(f"visible character is not in scene {shot.scene_id}")
        if not shot.source_segments:
            raise ValueError(f"shot {shot.shot_id} has no source segments")
        for segment_id in shot.source_segments:
            if segment_id not in segments:
                raise ValueError(f"unknown source segment: {segment_id}")
            if segments[segment_id].scene_id != shot.scene_id:
                raise ValueError(f"source segment belongs to another scene: {segment_id}")
            flattened.append(segment_id)

    if tuple(flattened) != tuple(segment.segment_id for segment in source.segments):
        raise ValueError("source coverage must include every segment once in source order")

    for shot in episode.shots:
        expected = tuple(
            Dialogue(speaker=segments[segment_id].speaker, line=segments[segment_id].line)
            for segment_id in shot.source_segments
            if segments[segment_id].speaker is not None
        )
        if shot.dialogue != expected:
            raise ValueError(f"dialogue does not match source speaker/line in {shot.shot_id}")
