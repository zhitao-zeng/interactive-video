from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess

from clean_studio.domain import MusicCue
from clean_studio.frozen.media import media_duration
from clean_studio.frozen.music import (
    file_sha256,
    local_index,
    read_json,
    recommended_catalogue_rows,
)

AUDITION_SCOPE = "actual_audio_content_listening"


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical(value), encoding="utf-8")


def _number(value, label: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _windows(value, *, duration: float, label: str) -> tuple[dict[str, float], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{label} requires actual listened windows")
    result = []
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} windows must be objects")
        start = _number(row.get("start"), f"{label} start")
        end = _number(row.get("end"), f"{label} end")
        if start < 0 or end <= start or end > duration + 0.02:
            raise ValueError(f"{label} window leaves the actual audio")
        result.append({"start": start, "end": end})
    return tuple(result)


@dataclass(frozen=True)
class AudioEvidence:
    report_path: str
    report_sha256: str
    catalogue_path: str
    catalogue_sha256: str
    source_windows: tuple[Mapping[str, object], ...]
    summary: Mapping[str, object]
    scope: str
    content_accepted: bool = False


@dataclass(frozen=True)
class MusicCandidate:
    cue: MusicCue
    candidate_id: str
    title: str
    artist: str
    audio_path: str
    audio_sha256: str
    duration_seconds: float
    audio_evidence: AudioEvidence
    retrieval_reason: str
    retrieval_method: str
    limitations: tuple[str, ...]
    attribution: str
    license_id: str | None
    license_url: str | None
    source_url: str | None
    usage_policy: Mapping[str, object]
    review_status: str = "unreviewed"
    content_accepted: bool = False


@dataclass(frozen=True)
class MusicAudition:
    candidate_id: str
    cue: MusicCue
    audio_sha256: str
    verdict: str
    actual_listening: bool
    evidence_scope: str
    agent_role: str
    agent_id: str
    reason: str
    limitations: tuple[str, ...]
    listened_windows: tuple[Mapping[str, float], ...]
    receipt_path: str
    receipt_sha256: str


@dataclass(frozen=True)
class AcceptedMusic:
    candidate: MusicCandidate
    audition: MusicAudition
    limitations: tuple[str, ...]
    source_start_seconds: float
    source_seconds: float
    gain_db: float
    fade_in_seconds: float
    fade_out_seconds: float
    loop: bool
    final_audio_accepted: bool = False

    @property
    def cue(self) -> MusicCue:
        return self.candidate.cue

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def audio_path(self) -> str:
        return self.candidate.audio_path

    @property
    def audio_sha256(self) -> str:
        return self.candidate.audio_sha256


def _field(row, name: str):
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def _cue_mapping(value) -> dict:
    if isinstance(value, MusicCue):
        return asdict(value)
    fields = {"start_seconds", "end_seconds", "mood", "soundscape"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("music audition requires the complete cue")
    return dict(value)


def validate_music_audition(
    candidate,
    audition,
    *,
    source_start_seconds: float,
    source_seconds: float,
) -> MusicAudition:
    candidate_id = _field(candidate, "candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("music audition requires a candidate identity")
    candidate_cue = _cue_mapping(_field(candidate, "cue"))
    audition_cue = _cue_mapping(_field(audition, "cue"))
    duration = _number(_field(candidate, "duration_seconds"), "audio duration")
    if duration <= 0:
        raise ValueError("music audition requires positive actual audio duration")
    audio_path = Path(str(_field(candidate, "audio_path") or "")).resolve()
    expected_audio_hash = _field(candidate, "audio_sha256")
    try:
        actual_audio_hash = file_sha256(audio_path)
    except OSError as error:
        raise ValueError("music audio bytes are unavailable") from error
    if actual_audio_hash != expected_audio_hash:
        raise ValueError("music audio bytes or SHA-256 changed")

    receipt_path = Path(str(_field(audition, "receipt_path") or "")).resolve()
    try:
        receipt_hash = file_sha256(receipt_path)
        receipt = read_json(receipt_path)
    except (OSError, ValueError) as error:
        raise ValueError("music audition receipt is unavailable or malformed") from error
    if not isinstance(receipt, dict):
        raise ValueError("music audition receipt must be an object")
    if receipt_hash != _field(audition, "receipt_sha256"):
        raise ValueError("music audition receipt SHA-256 does not match")
    receipt_cue = _cue_mapping(receipt.get("cue"))

    for label, row, row_cue in (
        ("judgment", audition, audition_cue),
        ("receipt", receipt, receipt_cue),
    ):
        if _field(row, "candidate_id") != candidate_id:
            raise ValueError(f"music {label} is bound to a different candidate")
        if row_cue != candidate_cue:
            raise ValueError(f"music {label} is bound to a different cue")
        if _field(row, "audio_sha256") != actual_audio_hash:
            raise ValueError(f"music {label} is not bound to the current audio hash")
        if _field(row, "verdict") != "accepted":
            raise ValueError(f"music {label} verdict must be accepted")
        if _field(row, "actual_listening") is not True:
            raise ValueError(f"music {label} requires actual content listening")
        if _field(row, "evidence_scope") != AUDITION_SCOPE:
            raise ValueError(f"music {label} is not actual audio content listening")
        if _field(row, "agent_role") != "exception_agent":
            raise ValueError(f"music {label} requires the exception Agent")
        agent_id = _field(row, "agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError(f"music {label} requires an Agent identity")
        if agent_id != _field(audition, "agent_id"):
            raise ValueError("music receipt Agent identity differs from the judgment")
    if receipt.get("status") != "completed":
        raise ValueError("music audition receipt must be completed evidence")
    if (
        Path(str(receipt.get("actual_audio") or "")).resolve() != audio_path
        or not str(receipt.get("response") or "").strip()
    ):
        raise ValueError("music audition receipt is not current-audio listening evidence")

    listened_windows = _windows(
        _field(audition, "listened_windows"),
        duration=duration,
        label="music audition",
    )
    receipt_windows = _windows(
        receipt.get("listened_windows"),
        duration=duration,
        label="music audition receipt",
    )
    if receipt_windows != listened_windows:
        raise ValueError("music audition receipt windows differ from the judgment")
    source_start = _number(source_start_seconds, "source start")
    selected_seconds = _number(source_seconds, "source seconds")
    if source_start < 0 or selected_seconds <= 0:
        raise ValueError("music source interval must be positive")
    if source_start + selected_seconds > duration + 0.02:
        raise ValueError("music source interval exceeds the actual audio")
    covered_until = source_start
    for window in sorted(listened_windows, key=lambda row: row["start"]):
        if window["end"] <= covered_until:
            continue
        if window["start"] > covered_until + 1e-6:
            break
        covered_until = max(covered_until, window["end"])
    if covered_until + 1e-6 < source_start + selected_seconds:
        raise ValueError("music listening windows must cover the full selected source interval")

    reason = _field(audition, "reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("music audition requires a content reason")
    limitations = _field(audition, "limitations")
    if (
        not isinstance(limitations, (list, tuple))
        or not limitations
        or any(not isinstance(item, str) or not item.strip() for item in limitations)
    ):
        raise ValueError("music audition must retain explicit limitations")
    cue = _field(candidate, "cue")
    return MusicAudition(
        candidate_id=candidate_id,
        cue=cue if isinstance(cue, MusicCue) else MusicCue(**candidate_cue),
        audio_sha256=actual_audio_hash,
        verdict="accepted",
        actual_listening=True,
        evidence_scope=AUDITION_SCOPE,
        agent_role="exception_agent",
        agent_id=_field(audition, "agent_id"),
        reason=reason,
        limitations=tuple(limitations),
        listened_windows=listened_windows,
        receipt_path=str(receipt_path),
        receipt_sha256=receipt_hash,
    )


class SoulTunerMusicProvider:
    def __init__(
        self,
        config: Mapping[str, object],
        *,
        search_root: str | Path,
        backend=None,
        environment: Mapping[str, str] | None = None,
        runner=None,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
    ) -> None:
        self.config = dict(config)
        self.search_root = Path(search_root)
        self.backend = backend
        self.environment = dict(environment or {})
        self.runner = runner or subprocess.run
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    @classmethod
    def from_project(cls, root: str | Path | None = None) -> "SoulTunerMusicProvider":
        project = Path(
            root
            or os.environ.get("NOVEL_PROJECT_ROOT")
            or Path(__file__).resolve().parents[2]
        ).resolve()
        config_path = project / ".runtime/music-selection/runtime.json"
        if not config_path.is_file():
            raise ValueError("SoulTuner runtime is not configured")
        return cls(
            read_json(config_path),
            search_root=project / ".runtime/clean-music-searches",
        )

    def _query(self, cue: MusicCue) -> str:
        seconds = cue.end_seconds - cue.start_seconds
        return (
            f"{cue.mood}\n{cue.soundscape}\n"
            f"为连续 {seconds:g} 秒剧情选择本地背景音乐；保留原生对白与物理声景。"
            "只返回真实曲库候选，不作试听或采用结论。"
        )

    def _worker_environment(self) -> dict[str, str]:
        names = ("ST_LLM_KEY", "ST_LLM_BASE_URL", "ST_LLM_MODEL")
        explicit = self.environment
        environment = {**os.environ, **explicit}
        llm = self.config.get("llm")
        if llm is None:
            if any(not explicit.get(name) for name in names):
                raise ValueError(
                    "SoulTuner runtime.llm mapping or complete explicit environment is required"
                )
        else:
            if not isinstance(llm, Mapping):
                raise ValueError("SoulTuner runtime.llm must be a public mapping")
            if any(
                not isinstance(llm.get(name), str) or not llm[name].strip()
                for name in ("base_url", "model", "api_key_env")
            ):
                raise ValueError("SoulTuner runtime.llm mapping is incomplete")
            base_url = llm["base_url"].strip()
            if not base_url.startswith(("http://", "https://")):
                raise ValueError("SoulTuner requires an HTTP chat endpoint")
            key = explicit.get("ST_LLM_KEY") or environment.get(llm["api_key_env"])
            if not key:
                raise ValueError("SoulTuner LLM credential environment is required")
            environment.update(
                ST_LLM_KEY=key,
                ST_LLM_BASE_URL=base_url,
                ST_LLM_MODEL=llm["model"].strip(),
            )
        for name in names:
            if name in explicit:
                environment[name] = explicit[name]
        if any(not environment.get(name) for name in names):
            raise ValueError("SoulTuner LLM environment is incomplete")
        return environment

    def _recommend(self, query: str, directory: Path) -> dict:
        required = (
            "python",
            "source_root",
            "neo4j_uri",
            "m2d_checkpoint",
            "bert_path",
            "data_dir",
        )
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise ValueError(f"SoulTuner runtime is incomplete: {', '.join(missing)}")
        environment = self._worker_environment()

        def public(value):
            if isinstance(value, dict):
                return {
                    key: public(item)
                    for key, item in value.items()
                    if not any(
                        marker in key.lower()
                        for marker in ("password", "secret", "token", "api_key", "apikey")
                    )
                    and key.lower() not in {"key", "st_llm_key"}
                }
            if isinstance(value, list):
                return [public(item) for item in value]
            return value

        output = directory / "backend-output.json"
        request = directory / "backend-input.json"
        _write_json(
            request,
            {"query": query, "runtime": public(self.config), "output": str(output)},
        )
        worker = Path(__file__).resolve().parents[1] / "frozen/soultuner_worker.py"
        with local_index(self.config, directory, runner=self.runner):
            with (directory / "backend.log").open("a", encoding="utf-8") as log:
                done = self.runner(
                    [str(self.config["python"]), str(worker), str(request)],
                    cwd=directory,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=200,
                    check=False,
                )
        if done.returncode or not output.is_file():
            raise RuntimeError("SoulTuner failed; retained backend log, no music adopted")
        return read_json(output)

    def _candidate(
        self,
        entry: Mapping[str, object],
        reason: str,
        cue: MusicCue,
        *,
        catalogue_path: Path,
        catalogue_sha256: str,
        retrieval_method: str,
    ) -> MusicCandidate:
        audio = Path(str(entry["local_audio"])).resolve()
        audio_hash = file_sha256(audio)
        duration = media_duration(audio, ffprobe=self.ffprobe, runner=self.runner)
        evidence_path = Path(str(entry.get("audio_audit_report") or "")).resolve()
        if not evidence_path.is_file():
            raise ValueError("music candidate has no actual audio evidence report")
        report = read_json(evidence_path)
        if (
            not isinstance(report, dict)
            or report.get("status") != "completed"
            or not str(report.get("response") or "").strip()
        ):
            raise ValueError("music audio evidence must be a completed nonempty report")
        actual_audio = Path(str(report.get("actual_audio") or "")).resolve()
        if actual_audio != audio:
            raise ValueError("music audio evidence is bound to a different source")
        if report.get("audio_sha256") != audio_hash:
            raise ValueError("music audio evidence is stale for the current audio bytes")
        catalogue_windows = _windows(
            entry.get("audio_audit_source_windows"),
            duration=duration,
            label="catalogue audio evidence",
        )
        report_windows = _windows(
            report.get("source_windows"),
            duration=duration,
            label="report audio evidence",
        )
        if catalogue_windows != report_windows:
            raise ValueError("catalogue and report audio evidence windows differ")
        scope = str(
            entry.get("vocal_evidence_scope")
            or report.get("evidence_scope")
            or "sampled audio evidence; not content acceptance"
        )
        limitations = [scope, str(self.config.get("catalogue_scope") or "local catalogue only")]
        limitations.extend(
            str(item)
            for item in (
                "检索、排名和音频样本分析不代表内容试听 accepted",
                "采用后仍须验证实际混合，整片声音另行验收",
            )
            if item not in limitations
        )
        return MusicCandidate(
            cue=cue,
            candidate_id=str(entry["song_id"]),
            title=str(entry.get("title") or ""),
            artist=str(entry.get("artist") or ""),
            audio_path=str(audio),
            audio_sha256=audio_hash,
            duration_seconds=duration,
            audio_evidence=AudioEvidence(
                report_path=str(evidence_path),
                report_sha256=file_sha256(evidence_path),
                catalogue_path=str(catalogue_path),
                catalogue_sha256=catalogue_sha256,
                source_windows=report_windows,
                summary=dict(entry.get("audio_audit") or {}),
                scope=scope,
            ),
            retrieval_reason=reason,
            retrieval_method=retrieval_method,
            limitations=tuple(dict.fromkeys(item for item in limitations if item.strip())),
            attribution=str(entry.get("attribution") or ""),
            license_id=entry.get("license_id"),
            license_url=entry.get("license_url"),
            source_url=entry.get("source_url"),
            usage_policy=dict(entry.get("usage_policy") or {}),
        )

    def search(self, cue: MusicCue, *, limit: int = 3) -> list[MusicCandidate]:
        if not isinstance(cue, MusicCue):
            raise TypeError("search_music requires an Episode MusicCue")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("music limit must be an integer from 1 to 10")
        if (
            not cue.mood.strip()
            or not cue.soundscape.strip()
            or cue.end_seconds <= cue.start_seconds
        ):
            raise ValueError("music cue requires mood, soundscape and a positive interval")
        catalogue_path = Path(str(self.config.get("catalog") or "")).resolve()
        if not catalogue_path.is_file():
            raise ValueError("SoulTuner local catalogue is missing")
        catalogue_rows = read_json(catalogue_path)
        if not isinstance(catalogue_rows, list):
            raise ValueError("SoulTuner catalogue must be an array")
        catalogue_hash = file_sha256(catalogue_path)
        query = self._query(cue)
        identity = {
            "cue": asdict(cue),
            "query": query,
            "limit": limit,
            "catalogue_sha256": catalogue_hash,
        }
        search_id = sha256(_canonical(identity).encode("utf-8")).hexdigest()[:16]
        self.search_root.mkdir(parents=True, exist_ok=True)
        owner = self.search_root / "owner"
        if not owner.exists():
            owner.write_text(
                "Clean Studio Task6 / real SoulTuner search evidence; no music adoption\n",
                encoding="utf-8",
            )
        directory = self.search_root / f"search-{search_id}"
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "input.json", identity)
        raw_path = directory / "raw.json"
        if raw_path.is_file():
            raw = read_json(raw_path)
        else:
            raw = (self.backend or (lambda _config, text, folder: self._recommend(text, folder)))(
                dict(self.config), query, directory
            )
            _write_json(raw_path, raw)
        rows, unavailable, returned_count = recommended_catalogue_rows(
            raw, catalogue_rows, limit=limit
        )
        candidates = []
        for entry, reason in rows:
            try:
                candidates.append(
                    self._candidate(
                        entry,
                        reason,
                        cue,
                        catalogue_path=catalogue_path,
                        catalogue_sha256=catalogue_hash,
                        retrieval_method=str(raw.get("source") or "soultuner"),
                    )
                )
            except (OSError, ValueError):
                unavailable.append(str(entry.get("song_id")))
        _write_json(
            directory / "result.json",
            {
                "search_id": directory.name,
                "cue": asdict(cue),
                "returned_count": returned_count,
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
                "unavailable_ids": unavailable,
                "requires_actual_agent_audition": True,
                "content_accepted": False,
            },
        )
        return candidates


def search_music(
    cue: MusicCue,
    *,
    provider=None,
    limit: int = 3,
) -> list[MusicCandidate]:
    selected = provider or SoulTunerMusicProvider.from_project()
    if not hasattr(selected, "search"):
        raise TypeError("music provider must implement real candidate search")
    candidates = selected.search(cue, limit=limit)
    if not isinstance(candidates, list) or any(
        not isinstance(candidate, MusicCandidate) for candidate in candidates
    ):
        raise TypeError("music search must return MusicCandidate objects")
    return candidates


def adopt_music(
    candidate: MusicCandidate,
    judgment: Mapping[str, object],
    *,
    source_start_seconds: float = 0,
    source_seconds: float | None = None,
    gain_db: float = -18,
    fade_in_seconds: float = 0,
    fade_out_seconds: float = 0,
    loop: bool = False,
) -> AcceptedMusic:
    if not isinstance(candidate, MusicCandidate):
        raise TypeError("only a real MusicCandidate can be adopted")
    if candidate.review_status != "unreviewed" or candidate.content_accepted is not False:
        raise ValueError("music candidate must begin unreviewed without content acceptance")
    if not isinstance(judgment, Mapping):
        raise TypeError("music adoption requires an exception Agent audition")
    cue_seconds = candidate.cue.end_seconds - candidate.cue.start_seconds
    source_start = _number(source_start_seconds, "source start")
    selected_seconds = _number(
        cue_seconds if source_seconds is None else source_seconds,
        "source seconds",
    )
    audition = validate_music_audition(
        candidate,
        judgment,
        source_start_seconds=source_start,
        source_seconds=selected_seconds,
    )
    gain = _number(gain_db, "gain")
    fade_in = _number(fade_in_seconds, "fade in")
    fade_out = _number(fade_out_seconds, "fade out")
    if type(loop) is not bool:
        raise ValueError("music loop must be boolean")
    if not loop and selected_seconds + 0.02 < cue_seconds:
        raise ValueError("selected music is shorter than its cue; explicit loop is required")
    if not -36 <= gain <= 0:
        raise ValueError("music gain_db must be between -36 and 0")
    if min(fade_in, fade_out) < 0 or fade_in + fade_out > cue_seconds + 1e-6:
        raise ValueError("music fades exceed the cue duration")
    limitations = tuple(
        dict.fromkeys((*candidate.limitations, *audition.limitations))
    )
    return AcceptedMusic(
        candidate=candidate,
        audition=audition,
        limitations=limitations,
        source_start_seconds=source_start,
        source_seconds=selected_seconds,
        gain_db=gain,
        fade_in_seconds=fade_in,
        fade_out_seconds=fade_out,
        loop=loop,
    )
