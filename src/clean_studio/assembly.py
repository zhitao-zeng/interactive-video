from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from clean_studio.domain import (
    EpisodeSpec,
    export_bundle_identity,
    export_bundle_metadata,
)
from clean_studio.frozen.music import file_sha256
from clean_studio.frozen.quality import black_intervals, jpeg_check, stream_checks
from clean_studio.frozen.render import (
    END_CARD_SECONDS,
    FPS,
    HEIGHT,
    WIDTH,
    TimedSubtitle,
    make_end_card,
    render_command,
    write_ass,
)
from clean_studio.media import Artifact
from clean_studio.providers.ffmpeg import FFmpeg
from clean_studio.providers.music import AcceptedMusic, validate_music_audition


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value):
    return json.loads(_canonical(value))


def _write_json(path: Path, value) -> None:
    path.write_text(_canonical(value), encoding="utf-8")


def _default_font() -> Path:
    candidates = (
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


@dataclass(frozen=True)
class TechnicalReport:
    passed: bool
    export_sha256: str
    duration_seconds: float | None
    expected_duration_seconds: float
    expected_frames: int
    actual_frames: int | None
    fully_decoded: bool
    checks: Mapping[str, object]
    content_accepted: bool = False
    final_audio_accepted: bool = False
    evidence_scope: str = "technical_only"
    status: str = "candidate"
    bundle_hash: str | None = None

    def __getitem__(self, key: str):
        return getattr(self, key)

    def to_dict(self) -> dict:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ExportArtifact:
    id: str
    kind: str
    path: str
    sha256: str
    cover_path: str
    cover_sha256: str
    end_card_path: str
    end_card_sha256: str
    subtitle_path: str
    subtitle_sha256: str
    credits_path: str
    credits_sha256: str
    manifest_path: str
    manifest_sha256: str
    episode_hash: str
    expected_duration_seconds: float
    body_seconds: float
    expected_frames: int
    shot_ids: tuple[str, ...]
    source_videos: tuple[Mapping[str, object], ...]
    subtitles: tuple[Mapping[str, object], ...]
    mixed_music: tuple[Mapping[str, object], ...]
    fixed_question: str
    fixed_options: tuple[Mapping[str, object], ...]
    status: str = "candidate"
    content_accepted: bool = False
    final_audio_accepted: bool = False
    technical: TechnicalReport | None = None
    bundle_hash: str = field(init=False, default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_hash", export_bundle_identity(self))

    def __getitem__(self, key: str):
        return getattr(self, key)


def export_artifact_metadata(export: ExportArtifact) -> dict:
    return {
        "export_bundle": export_bundle_metadata(export),
        "technical": export.technical.to_dict() if export.technical is not None else None,
    }


class AssemblyError(RuntimeError):
    def __init__(self, report: TechnicalReport) -> None:
        self.report = report
        failures = [name for name, row in report.checks.items() if not row.get("passed")]
        super().__init__(f"assembled export failed technical gate: {', '.join(failures)}")


def _artifact(value) -> Artifact:
    if isinstance(value, Artifact):
        return value
    if isinstance(value, Mapping):
        return Artifact.from_row(value)
    raise TypeError("valid_videos must contain Task5 video Artifact objects")


def _video_map(valid_videos) -> dict[str, Artifact]:
    rows = valid_videos.items() if isinstance(valid_videos, Mapping) else (
        (None, value) for value in valid_videos
    )
    result = {}
    for key, value in rows:
        artifact = _artifact(value)
        if key is not None and key != artifact.shot_id:
            raise ValueError("valid video mapping key differs from artifact shot_id")
        if not artifact.shot_id or artifact.shot_id in result:
            raise ValueError("valid videos require distinct shot IDs")
        if artifact.kind != "video":
            raise ValueError("assembly accepts only valid video artifacts")
        result[artifact.shot_id] = artifact
    return result


def _video_path(artifact: Artifact, media_root: Path) -> Path:
    path = Path(artifact.path)
    return path if path.is_absolute() else media_root / path


def _validate_video(shot, artifact: Artifact, path: Path) -> None:
    if artifact.shot_id != shot.shot_id:
        raise ValueError(f"video {artifact.shot_id} does not match {shot.shot_id}")
    if not path.is_file() or file_sha256(path) != artifact.sha256:
        raise ValueError(f"video {shot.shot_id} bytes or SHA-256 changed")
    quality = artifact.metadata.get("quality") or {}
    technical = quality.get("technical") or {}
    speech = quality.get("asr") or {}
    review = quality.get("review") or {}
    valid = (
        quality.get("passed") is True
        and quality.get("final_audio_accepted") is False
        and technical.get("passed") is True
        and technical.get("fully_decoded") is True
        and technical.get("native_audio") is True
        and technical.get("video_sha256") == artifact.sha256
        and speech.get("passed") is True
        and speech.get("evaluation") == "clean-current-shot-dialogue-cer-v2-phonetic"
        and speech.get("video_sha256") == artifact.sha256
        and speech.get("final_audio_accepted") is False
        and review.get("verdict") == "accepted"
        and review.get("video_sha256") == artifact.sha256
        and review.get("findings") == []
    )
    if not valid:
        raise ValueError(
            f"video {shot.shot_id} lacks current Task5 technical, ASR and content evidence"
        )


def _validate_music(accepted: AcceptedMusic) -> None:
    if not isinstance(accepted, AcceptedMusic):
        raise TypeError("accepted_music must contain adopted music, not unreviewed candidates")
    candidate = accepted.candidate
    if (
        candidate.review_status != "unreviewed"
        or candidate.content_accepted is not False
        or not accepted.limitations
        or accepted.final_audio_accepted is not False
    ):
        raise ValueError("accepted music lacks actual exception-Agent listening evidence")
    validate_music_audition(
        candidate,
        accepted.audition,
        source_start_seconds=accepted.source_start_seconds,
        source_seconds=accepted.source_seconds,
    )
    evidence = candidate.audio_evidence
    if file_sha256(evidence.report_path) != evidence.report_sha256:
        raise ValueError("music audio evidence changed after candidate search")
    if file_sha256(evidence.catalogue_path) != evidence.catalogue_sha256:
        raise ValueError("music catalogue changed after candidate search")


def _ordered_music(episode: EpisodeSpec, accepted_music) -> tuple[AcceptedMusic, ...]:
    remaining = list(accepted_music)
    ordered = []
    for cue in episode.music_cues:
        index = next(
            (
                position
                for position, accepted in enumerate(remaining)
                if isinstance(accepted, AcceptedMusic) and accepted.cue == cue
            ),
            None,
        )
        if index is None:
            # no auditioned track cleared the content bar for this cue: the segment
            # plays with native audio only (silent-cue degradation, recorded upstream)
            continue
        ordered.append(remaining.pop(index))
    if remaining:
        raise ValueError("accepted music contains an unauthored or duplicate cue")
    for accepted in ordered:
        _validate_music(accepted)
    return tuple(ordered)


def _subtitle_events(shot, artifact: Artifact, start: float) -> list[TimedSubtitle]:
    speech = artifact.metadata["quality"]["asr"]
    segments = speech.get("segments") or []
    events = []
    if segments:
        for segment in segments:
            if not isinstance(segment, Mapping):
                raise ValueError("current ASR subtitle segment must be an object")
            text = segment.get("hypothesis")
            local_start = segment.get("start")
            local_end = segment.get("end")
            if (
                not isinstance(text, str)
                or not text.strip()
                or type(local_start) not in {int, float}
                or type(local_end) not in {int, float}
                or not math.isfinite(local_start)
                or not math.isfinite(local_end)
            ):
                raise ValueError("current ASR subtitle segment lacks text or timing")
            local_start = max(0.0, float(local_start))
            local_end = min(float(shot.duration_seconds), float(local_end))
            if local_end <= local_start:
                raise ValueError("current ASR subtitle segment has invalid timing")
            events.append(
                TimedSubtitle(
                    start=start + local_start,
                    end=start + local_end,
                    text=text,
                    role=str(segment.get("speaker") or "dialogue"),
                    subtitle_source="current_video_asr",
                    shot_id=shot.shot_id,
                )
            )
    elif shot.dialogue:
        events.append(
            TimedSubtitle(
                start=start,
                end=start + shot.duration_seconds,
                text="\n".join(line.line for line in shot.dialogue),
                role="/".join(line.speaker for line in shot.dialogue),
                subtitle_source="current_episode_dialogue",
                shot_id=shot.shot_id,
            )
        )
    return events


def _music_record(accepted: AcceptedMusic) -> dict:
    cue = accepted.cue
    return {
        "cue": asdict(cue),
        "candidate_id": accepted.candidate_id,
        "audio_path": accepted.audio_path,
        "audio_sha256": accepted.audio_sha256,
        "duration_seconds": accepted.candidate.duration_seconds,
        "audio_evidence": asdict(accepted.candidate.audio_evidence),
        "audition": asdict(accepted.audition),
        "limitations": list(accepted.limitations),
        "attribution": accepted.candidate.attribution,
        "license_id": accepted.candidate.license_id,
        "license_url": accepted.candidate.license_url,
        "source_url": accepted.candidate.source_url,
        "usage_policy": dict(accepted.candidate.usage_policy),
        "source_start_seconds": accepted.source_start_seconds,
        "source_seconds": accepted.source_seconds,
        "start_seconds": cue.start_seconds,
        "end_seconds": cue.end_seconds,
        "gain_db": accepted.gain_db,
        "fade_in_seconds": accepted.fade_in_seconds,
        "fade_out_seconds": accepted.fade_out_seconds,
        "loop": accepted.loop,
        "final_audio_accepted": False,
    }


def _credits(music: list[dict]) -> str:
    if not music:
        return "No added music. Native video audio retained.\n"
    blocks = []
    for row in music:
        lines = [
            f"Candidate: {row['candidate_id']}",
            f"Attribution: {row['attribution']}",
            f"License: {row['license_id'] or 'unspecified'}",
            f"License URL: {row['license_url'] or 'unspecified'}",
            f"Source URL: {row['source_url'] or 'unspecified'}",
            f"Audio SHA-256: {row['audio_sha256']}",
            (
                f"Edit: source {row['source_start_seconds']:g}s for "
                f"{row['source_seconds']:g}s; timeline {row['start_seconds']:g}-"
                f"{row['end_seconds']:g}s; gain {row['gain_db']:g} dB; "
                f"loop={str(row['loop']).lower()}"
            ),
            "Limitations:",
            *(f"- {item}" for item in row["limitations"]),
            "Final film audio accepted: false",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def assemble(
    episode: EpisodeSpec,
    valid_videos,
    accepted_music,
    *,
    output_dir: str | Path | None = None,
    media_root: str | Path | None = None,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
    font_path: str | Path | None = None,
    preset="veryfast",
) -> ExportArtifact:
    if not isinstance(episode, EpisodeSpec) or not episode.shots:
        raise ValueError("assembly requires a nonempty EpisodeSpec")
    artifacts = _video_map(valid_videos)
    expected_ids = tuple(shot.shot_id for shot in episode.shots)
    missing = [shot_id for shot_id in expected_ids if shot_id not in artifacts]
    extra = [shot_id for shot_id in artifacts if shot_id not in expected_ids]
    if missing or extra:
        raise ValueError(
            f"valid videos must be N/N before assembly; missing={missing}, extra={extra}"
        )
    root = Path(media_root or Path.cwd()).resolve()
    paths = {}
    for shot in episode.shots:
        artifact = artifacts[shot.shot_id]
        path = _video_path(artifact, root).resolve()
        _validate_video(shot, artifact, path)
        paths[shot.shot_id] = path
    body_seconds = float(sum(shot.duration_seconds for shot in episode.shots))
    for accepted in accepted_music:
        if isinstance(accepted, AcceptedMusic):
            cue = accepted.cue
            if (
                cue.start_seconds < 0
                or cue.end_seconds <= cue.start_seconds
                or cue.end_seconds > body_seconds
            ):
                raise ValueError("music cue leaves the actual story timeline")
    music = _ordered_music(episode, accepted_music)

    episode_hash = sha256(_canonical(asdict(episode)).encode("utf-8")).hexdigest()
    output = Path(
        output_dir or Path.cwd() / "outputs" / episode.episode_id / "export"
    ).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("assembly output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    (output / "owner").write_text(
        "Clean Studio Task6 / offline candidate assembly; technical pass is not content acceptance\n",
        encoding="utf-8",
    )
    font = Path(font_path or _default_font())
    end_card = make_end_card(episode, output / "end.jpg", font_path=font)
    engine = FFmpeg(ffmpeg=ffmpeg, ffprobe=ffprobe, runner=runner)
    cover = engine.extract_cover(paths[expected_ids[0]], output / "cover.jpg")

    subtitles = []
    shot_rows = []
    source_videos = []
    cursor = 0.0
    for shot in episode.shots:
        artifact = artifacts[shot.shot_id]
        events = _subtitle_events(shot, artifact, cursor)
        subtitles.extend(events)
        shot_rows.append(
            {
                "shot_id": shot.shot_id,
                "artifact_id": artifact.id,
                "operation_id": artifact.operation_id,
                "input_hash": artifact.input_hash,
                "path": str(paths[shot.shot_id]),
                "video_sha256": artifact.sha256,
                "source_segments": list(shot.source_segments),
                "dialogue": [asdict(line) for line in shot.dialogue],
                "start_seconds": cursor,
                "end_seconds": cursor + shot.duration_seconds,
                "duration_seconds": shot.duration_seconds,
            }
        )
        source_videos.append(
            {
                "shot_id": shot.shot_id,
                "path": str(paths[shot.shot_id]),
                "video_sha256": artifact.sha256,
            }
        )
        cursor += shot.duration_seconds
    ass = write_ass(output / "subtitles.ass", subtitles)
    subtitle_rows = [
        {
            "start": event.start,
            "end": event.end,
            "text": event.text,
            "role": event.role,
            "subtitle_source": event.subtitle_source,
            "shot_id": event.shot_id,
        }
        for event in subtitles
    ]
    music_rows = [_music_record(accepted) for accepted in music]
    credits = output / "music-credits.txt"
    credits.write_text(_credits(music_rows), encoding="utf-8")

    partial = output / "candidate.partial.mp4"
    movie = output / "candidate.mp4"
    command = render_command(
        [paths[shot_id] for shot_id in expected_ids],
        [shot.duration_seconds for shot in episode.shots],
        end_card,
        music_rows,
        ass,
        partial,
        preset=preset,
    )
    engine.run(command)
    if not partial.is_file():
        raise RuntimeError("FFmpeg did not create the assembled candidate")
    os.replace(partial, movie)

    movie_hash = file_sha256(movie)
    for row in music_rows:
        row["mixed_into_sha256"] = movie_hash
    expected_seconds = body_seconds + END_CARD_SECONDS
    manifest_path = output / "manifest.json"
    manifest = {
        "schema": "clean-studio-export-v1",
        "episode_id": episode.episode_id,
        "episode_hash": episode_hash,
        "status": "candidate",
        "content_accepted": False,
        "final_audio_accepted": False,
        "technical_evidence_scope": "technical_only",
        "profile": {
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "video_codec": "h264",
            "audio_codec": "aac",
        },
        "expected_duration_seconds": expected_seconds,
        "body_seconds": body_seconds,
        "expected_frames": round(expected_seconds * FPS),
        "fixed_question": episode.fixed_question,
        "fixed_options": [asdict(option) for option in episode.fixed_options],
        "shots": shot_rows,
        "subtitles": subtitle_rows,
        "assets": {
            "video": {"path": str(movie), "sha256": movie_hash},
            "cover": {"path": str(cover), "sha256": file_sha256(cover)},
            "end_card": {"path": str(end_card), "sha256": file_sha256(end_card)},
            "subtitles": {"path": str(ass), "sha256": file_sha256(ass)},
            "credits": {"path": str(credits), "sha256": file_sha256(credits)},
        },
        "music_mix": {
            "output_sha256": movie_hash,
            "native_audio_preserved": True,
            "cues": music_rows,
            "final_audio_accepted": False,
        },
    }
    _write_json(manifest_path, manifest)
    export = ExportArtifact(
        id=f"export-{movie_hash[:24]}",
        kind="export",
        path=str(movie),
        sha256=movie_hash,
        cover_path=str(cover),
        cover_sha256=file_sha256(cover),
        end_card_path=str(end_card),
        end_card_sha256=file_sha256(end_card),
        subtitle_path=str(ass),
        subtitle_sha256=file_sha256(ass),
        credits_path=str(credits),
        credits_sha256=file_sha256(credits),
        manifest_path=str(manifest_path),
        manifest_sha256=file_sha256(manifest_path),
        episode_hash=episode_hash,
        expected_duration_seconds=expected_seconds,
        body_seconds=body_seconds,
        expected_frames=round(expected_seconds * FPS),
        shot_ids=expected_ids,
        source_videos=tuple(source_videos),
        subtitles=tuple(subtitle_rows),
        mixed_music=tuple(_json_value(music_rows)),
        fixed_question=episode.fixed_question,
        fixed_options=tuple(asdict(option) for option in episode.fixed_options),
    )
    report = probe_export(export, runner=runner, ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not report.passed:
        raise AssemblyError(report)
    return replace(export, technical=report)


def _identity_check(path: str, expected_hash: str) -> dict:
    try:
        actual = file_sha256(path)
        return {
            "passed": actual == expected_hash,
            "expected_sha256": expected_hash,
            "actual_sha256": actual,
        }
    except OSError as error:
        return {"passed": False, "detail": str(error)}


def probe_export(
    export: ExportArtifact,
    *,
    runner=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
) -> TechnicalReport:
    if not isinstance(export, ExportArtifact):
        raise TypeError("probe_export requires an ExportArtifact")
    checks = {}
    checks["export_identity"] = _identity_check(export.path, export.sha256)
    try:
        actual_export_hash = file_sha256(export.path)
    except OSError:
        actual_export_hash = export.sha256

    manifest = {}
    try:
        manifest_identity = _identity_check(export.manifest_path, export.manifest_sha256)
        if manifest_identity["passed"]:
            loaded = json.loads(Path(export.manifest_path).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("export manifest must be an object")
            manifest = loaded
        else:
            manifest_identity["detail"] = "manifest identity does not match"
        checks["manifest"] = manifest_identity
    except (OSError, ValueError, json.JSONDecodeError) as error:
        checks["manifest"] = {"passed": False, "detail": str(error)}

    checks["candidate_boundary"] = {
        "passed": (
            export.status == "candidate"
            and export.content_accepted is False
            and export.final_audio_accepted is False
            and manifest.get("status") == "candidate"
            and manifest.get("content_accepted") is False
            and manifest.get("final_audio_accepted") is False
            and manifest.get("technical_evidence_scope") == "technical_only"
        ),
        "content_accepted": False,
        "final_audio_accepted": False,
    }
    for name, path, digest in (
        ("cover", export.cover_path, export.cover_sha256),
        ("end_image", export.end_card_path, export.end_card_sha256),
    ):
        result = jpeg_check(path, width=WIDTH, height=HEIGHT)
        identity = _identity_check(path, digest)
        result["passed"] = result["passed"] and identity["passed"]
        result["identity"] = identity
        checks[name] = result
    checks["subtitle_file"] = _identity_check(
        export.subtitle_path, export.subtitle_sha256
    )
    checks["credits_file"] = _identity_check(export.credits_path, export.credits_sha256)

    engine = FFmpeg(ffmpeg=ffmpeg, ffprobe=ffprobe, runner=runner)
    try:
        probe = engine.probe(export.path)
    except Exception as error:
        probe = {}
        checks["probe"] = {"passed": False, "detail": str(error)}
    else:
        checks["probe"] = {"passed": True}
    try:
        decoded = engine.decode(export.path)
    except Exception as error:
        decoded = SimpleNamespace(returncode=1, stderr=str(error))
    checks.update(stream_checks(probe, decoded, width=WIDTH, height=HEIGHT, fps=FPS))
    duration = checks["duration"]["seconds"]
    checks["duration"]["expected_seconds"] = export.expected_duration_seconds
    checks["duration"]["passed"] = (
        duration is not None and abs(duration - export.expected_duration_seconds) <= 0.04
    )
    actual_frames = checks["frames"]["count"]
    checks["frames"]["expected"] = export.expected_frames
    checks["frames"]["passed"] = actual_frames == export.expected_frames

    shot_rows = manifest.get("shots") if isinstance(manifest.get("shots"), list) else []
    actual_ids = [row.get("shot_id") for row in shot_rows if isinstance(row, dict)]
    checks["shots"] = {
        "passed": (
            actual_ids == list(export.shot_ids)
            and len(shot_rows) == len(export.shot_ids)
            and manifest.get("episode_hash") == export.episode_hash
        ),
        "expected": len(export.shot_ids),
        "actual": len(shot_rows),
        "shot_ids": actual_ids,
    }
    source_failures = []
    for row in export.source_videos:
        identity = _identity_check(str(row["path"]), str(row["video_sha256"]))
        if not identity["passed"]:
            source_failures.append(row["shot_id"])
    checks["source_inputs"] = {
        "passed": not source_failures and len(export.source_videos) == len(export.shot_ids),
        "changed_shots": source_failures,
    }

    subtitle_rows = manifest.get("subtitles")
    try:
        ass = Path(export.subtitle_path).read_text(encoding="utf-8")
    except OSError:
        ass = ""
    subtitles_valid = (
        subtitle_rows == list(export.subtitles)
        and all(
            isinstance(row, dict)
            and isinstance(row.get("text"), str)
            and bool(row["text"].strip())
            and row.get("subtitle_source") in {"current_video_asr", "current_episode_dialogue"}
            and type(row.get("start")) in {int, float}
            and type(row.get("end")) in {int, float}
            and 0 <= row["start"] < row["end"] <= export.body_seconds + 0.001
            for row in subtitle_rows or []
        )
        and (bool(export.subtitles) == bool(subtitle_rows))
        and (not export.subtitles or "Dialogue:" in ass)
    )
    checks["subtitles"] = {
        "passed": subtitles_valid,
        "events": len(subtitle_rows) if isinstance(subtitle_rows, list) else 0,
    }

    end_seconds = actual_frames / FPS - export.body_seconds if actual_frames is not None else None
    end_frames = (
        actual_frames - round(export.body_seconds * FPS)
        if actual_frames is not None
        else None
    )
    try:
        black = engine.blackdetect(
            export.path,
            start=export.body_seconds,
            seconds=END_CARD_SECONDS,
        )
        intervals = black_intervals(black.stderr)
        not_black = black.returncode == 0 and not intervals
        black_detail = intervals
    except Exception as error:
        not_black = False
        black_detail = str(error)
    fixed_valid = (
        manifest.get("fixed_question") == export.fixed_question
        and manifest.get("fixed_options") == list(export.fixed_options)
    )
    checks["end_card"] = {
        "passed": (
            end_seconds is not None
            and abs(end_seconds - END_CARD_SECONDS) < 0.001
            and end_frames == round(END_CARD_SECONDS * FPS)
            and not_black
            and fixed_valid
        ),
        "seconds": end_seconds,
        "frames": end_frames,
        "not_black": not_black,
        "black_intervals": black_detail,
        "fixed_text_preserved": fixed_valid,
    }

    mix = manifest.get("music_mix")
    cues = mix.get("cues") if isinstance(mix, dict) else None
    mix_valid = (
        isinstance(mix, dict)
        and isinstance(cues, list)
        and cues == list(export.mixed_music)
        and mix.get("output_sha256") == export.sha256 == actual_export_hash
        and mix.get("native_audio_preserved") is True
        and mix.get("final_audio_accepted") is False
    )
    music_failures = []
    for row in cues if isinstance(cues, list) else []:
        try:
            validate_music_audition(
                row,
                row["audition"],
                source_start_seconds=row["source_start_seconds"],
                source_seconds=row["source_seconds"],
            )
            valid = (
                file_sha256(row["audio_evidence"]["report_path"])
                == row["audio_evidence"]["report_sha256"]
                and file_sha256(row["audio_evidence"]["catalogue_path"])
                == row["audio_evidence"]["catalogue_sha256"]
                and row["mixed_into_sha256"] == actual_export_hash
                and 0 <= row["start_seconds"] < row["end_seconds"] <= export.body_seconds
                and bool(row["limitations"])
                and row["final_audio_accepted"] is False
            )
        except (KeyError, OSError, TypeError, ValueError):
            valid = False
        if not valid:
            music_failures.append(row.get("candidate_id") if isinstance(row, dict) else None)
    checks["music_mix"] = {
        "passed": mix_valid and not music_failures,
        "invalid_candidates": music_failures,
        "mixed_cues": len(cues) if isinstance(cues, list) else 0,
    }

    passed = all(row.get("passed") is True for row in checks.values())
    return TechnicalReport(
        passed=passed,
        export_sha256=actual_export_hash,
        duration_seconds=duration,
        expected_duration_seconds=export.expected_duration_seconds,
        expected_frames=export.expected_frames,
        actual_frames=actual_frames,
        fully_decoded=checks["decode"]["fully_decoded"],
        checks=checks,
        bundle_hash=export.bundle_hash,
    )
