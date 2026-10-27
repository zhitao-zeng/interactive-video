from __future__ import annotations

import ast
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from clean_studio.acceptance import (
    AcceptanceError,
    AcceptedExport,
    accept_export,
    final_review_facts,
    publish,
    review_audio,
    review_visual,
    verify_upload,
)
from clean_studio.assembly import ExportArtifact, TechnicalReport
from clean_studio.domain import Dialogue, EpisodeSpec, FixedOption, Shot
from clean_studio.frozen.final_audio import AUDIO_POLICY
from clean_studio.frozen.final_review import (
    VISUAL_POLICY,
    blocking_issues,
    validate_group,
)
from clean_studio.providers.modelscope import (
    ModelScopePublisher,
    UploadOutcomeUncertain,
)
from clean_studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
TARGET = "owner/private-dataset"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sha256(data).hexdigest()


def _file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value).encode("utf-8")
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _episode(*, seconds=4):
    return EpisodeSpec(
        episode_id="episode-1",
        title="失物海",
        target_seconds=seconds,
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
                dialogue=(Dialogue("C01", "把船灯递给我。"),),
                reference_subjects=("C01",),
                duration_seconds=seconds - 3,
            ),
        ),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


def _export(tmp_path, *, seconds=4, real_media=False):
    output = tmp_path / "export"
    output.mkdir(parents=True, exist_ok=True)
    movie = output / "candidate.mp4"
    if real_media:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"color=c=navy:s=64x64:r=25:d={seconds}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(movie),
            ],
            check=True,
        )
    else:
        movie.write_bytes(f"current movie {seconds}".encode())
    cover = output / "cover.jpg"
    end_card = output / "end.jpg"
    for path, color in ((cover, "blue"), (end_card, "white")):
        Image.new("RGB", (32, 32), color).save(path, format="JPEG")
    subtitles = output / "subtitles.ass"
    subtitles.write_text(
        "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,把船灯递给我。\n",
        encoding="utf-8",
    )
    credits = output / "music-credits.txt"
    credits.write_text("No added music. Native video audio retained.\n", encoding="utf-8")
    episode = _episode(seconds=seconds)
    episode_hash = _hash(_canonical(asdict(episode)))
    body = float(seconds - 3)
    shot_row = {
        "shot_id": "shot-1",
        "source_segments": ["S1/flow/0"],
        "dialogue": [{"speaker": "C01", "line": "把船灯递给我。"}],
        "start_seconds": 0.0,
        "end_seconds": body,
        "duration_seconds": body,
        "video_sha256": _file_hash(movie),
    }
    assets = {}
    for name, path in (
        ("video", movie), ("cover", cover), ("end_card", end_card),
        ("subtitles", subtitles), ("credits", credits),
    ):
        assets[name] = {"path": str(path), "sha256": _file_hash(path)}
    manifest_path = output / "manifest.json"
    manifest = {
        "schema": "clean-studio-export-v1",
        "episode_id": episode.episode_id,
        "episode_hash": episode_hash,
        "status": "candidate",
        "content_accepted": False,
        "final_audio_accepted": False,
        "technical_evidence_scope": "technical_only",
        "expected_duration_seconds": float(seconds),
        "body_seconds": body,
        "expected_frames": seconds * 25,
        "fixed_question": episode.fixed_question,
        "fixed_options": [asdict(option) for option in episode.fixed_options],
        "shots": [shot_row],
        "subtitles": [],
        "assets": assets,
        "music_mix": {
            "output_sha256": _file_hash(movie),
            "native_audio_preserved": True,
            "cues": [],
            "final_audio_accepted": False,
        },
    }
    manifest_hash = _write_json(manifest_path, manifest)
    return ExportArtifact(
        id="export-current",
        kind="export",
        path=str(movie),
        sha256=_file_hash(movie),
        cover_path=str(cover),
        cover_sha256=_file_hash(cover),
        end_card_path=str(end_card),
        end_card_sha256=_file_hash(end_card),
        subtitle_path=str(subtitles),
        subtitle_sha256=_file_hash(subtitles),
        credits_path=str(credits),
        credits_sha256=_file_hash(credits),
        manifest_path=str(manifest_path),
        manifest_sha256=manifest_hash,
        episode_hash=episode_hash,
        expected_duration_seconds=float(seconds),
        body_seconds=body,
        expected_frames=seconds * 25,
        shot_ids=("shot-1",),
        source_videos=({"shot_id": "shot-1", "video_sha256": _file_hash(movie)},),
        subtitles=(),
        mixed_music=(),
        fixed_question=episode.fixed_question,
        fixed_options=tuple(manifest["fixed_options"]),
    )


def _bundle_metadata(export):
    return {
        "schema": "clean-studio-export-bundle-v1",
        "video_sha256": export.sha256,
        "cover_sha256": export.cover_sha256,
        "end_card_sha256": export.end_card_sha256,
        "subtitle_sha256": export.subtitle_sha256,
        "credits_sha256": export.credits_sha256,
        "manifest_sha256": export.manifest_sha256,
        "episode_hash": export.episode_hash,
        "expected_duration_seconds": export.expected_duration_seconds,
        "body_seconds": export.body_seconds,
        "expected_frames": export.expected_frames,
        "shot_ids": list(export.shot_ids),
        "fixed_question": export.fixed_question,
        "fixed_options": json.loads(_canonical(export.fixed_options)),
        "mixed_music": json.loads(_canonical(export.mixed_music)),
        "subtitles": json.loads(_canonical(export.subtitles)),
        "source_videos": json.loads(_canonical(export.source_videos)),
        "bundle_hash": export.bundle_hash,
    }


def _technical(export):
    return TechnicalReport(
        passed=True,
        export_sha256=export.sha256,
        bundle_hash=export.bundle_hash,
        duration_seconds=export.expected_duration_seconds,
        expected_duration_seconds=export.expected_duration_seconds,
        expected_frames=export.expected_frames,
        actual_frames=export.expected_frames,
        fully_decoded=True,
        checks={
            "export_identity": {"passed": True, "actual_sha256": export.sha256},
            "cover": {"passed": True, "identity": {"actual_sha256": export.cover_sha256}},
        },
    )


def _visual(export, *, verdict="accepted", severity=None):
    issue = None
    if severity:
        issue = {
            "clip_ids": ["shot-1"],
            "frame_numbers": [1],
            "severity": severity,
            "problem": "可见关键事件错误",
            "evidence": "第 1 帧的实际画面",
            "source_basis": "当前镜头明确动作",
        }
    issues = [issue] if issue else []
    passed = verdict == "accepted"
    group = {"passed": passed, "observations": ["实际抽帧观察"], "issues": issues}
    return {
        "policy": VISUAL_POLICY,
        "complete": True,
        "passed": passed,
        "verdict": verdict,
        "review_id": f"visual-{verdict}-{severity or 'none'}",
        "content_hash": export.bundle_hash,
        "input_hash": export.bundle_hash,
        "bundle_hash": export.bundle_hash,
        "export_sha256": export.sha256,
        "video_sha256": export.sha256,
        "cover_sha256": export.cover_sha256,
        "facts_sha256": "current-facts",
        "frames": [{"clip_id": "shot-1", "seconds": 0.15}],
        "groups": [group],
        "issues": issues,
        "findings": issues,
        "errors": [],
    }


def _coverage(duration):
    rows = []
    start = 0.0
    while start < duration:
        end = min(start + 30.0, duration)
        rows.append({"start": round(start, 3), "end": round(end, 3)})
        if end >= duration:
            break
        start += 28.0
    return rows


def _audio(export, *, verdict="accepted"):
    duration = export.expected_duration_seconds
    coverage = _coverage(duration)
    issues = []
    if verdict == "rejected":
        issues = [{
            "start": 0.2,
            "end": min(1.0, duration),
            "problem": "必需对白不可懂",
            "evidence": "完整窗口中的持续严重遮挡",
            "source_basis": "当前镜头有明确对白",
            "clip_ids": ["shot-1"],
        }]
    return {
        "policy": AUDIO_POLICY,
        "complete": True,
        "passed": verdict == "accepted",
        "verdict": verdict,
        "review_id": f"audio-{verdict}",
        "content_hash": export.bundle_hash,
        "input_hash": export.bundle_hash,
        "bundle_hash": export.bundle_hash,
        "export_sha256": export.sha256,
        "video_sha256": export.sha256,
        "movie": str(Path(export.path).resolve()),
        "facts_sha256": "current-facts",
        "decode": {
            "fully_decoded": True,
            "samples": round(duration * 16000),
            "sample_rate": 16000,
            "export_sha256": export.sha256,
        },
        "coverage_seconds": duration,
        "coverage": coverage,
        "windows": [{**row, "response": "实际 Eureka 声音证据"} for row in coverage],
        "issues": issues,
        "findings": issues,
        "summary": "完整声音文本判断",
        "errors": [],
    }


def _store(tmp_path, seconds=4):
    store = Store(tmp_path / "studio.sqlite3")
    store.create_episode(
        _episode(seconds=seconds),
        {"publish_target": TARGET, "model": "frozen"},
    )
    return store


def _receipt(operation, verdict=None, **extra):
    return {
        "receipt_id": f"receipt-{operation.id}",
        "operation_id": operation.id,
        "kind": operation.kind,
        "input_hash": operation.input_hash,
        "verdict": verdict,
        **extra,
    }


def _persist_export(store, export, *, key="assembly-current", compile_hash="compile-current"):
    store.compile_episode(
        expected_script_hash=store.load()["current_script_hash"],
        compile_hash=compile_hash,
    )
    operation = store.begin_operation(key, "assembly", compile_hash, None)
    relative = Path(export.path).relative_to(store.path.parent).as_posix()
    receipt_relative = f"receipts/{operation.id}.json"
    receipt_path = store.path.parent / receipt_relative
    _write_json(receipt_path, _receipt(operation))
    store.complete_operation(
        operation.id,
        path=relative,
        sha256=export.sha256,
        receipt_path=receipt_relative,
        receipt_sha256=_file_hash(receipt_path),
        expected_current_hash=compile_hash,
        metadata={"export_id": export.id, "export_bundle": _bundle_metadata(export)},
    )
    return operation


def _persist_review(store, kind, export, evidence, *, key=None):
    operation = store.begin_operation(
        key or f"{kind}-{evidence['review_id']}", kind, export.bundle_hash, None
    )
    verdict = evidence["verdict"]
    payload = {
        **evidence,
        "review_id": operation.id,
        **_receipt(operation, verdict),
    }
    relative = f"receipts/{operation.id}.json"
    actual = store.path.parent / relative
    _write_json(actual, payload)
    store.complete_operation(
        operation.id,
        verdict=verdict,
        findings=tuple(evidence.get("issues") or evidence.get("findings") or ()),
        receipt_path=relative,
        receipt_sha256=_file_hash(actual),
        metadata={"policy": evidence["policy"]},
    )
    return payload


def _accepted(store, export):
    _persist_review(store, "final_visual_review", export, _visual(export))
    _persist_review(store, "final_audio_review", export, _audio(export))
    result = accept_export(export, None, None, _technical(export), store=store)
    assert isinstance(result, AcceptedExport)
    assert result.bundle_hash == export.bundle_hash
    assert store.load()["status"] == "accepted"
    return result


class FakeAPI:
    def __init__(self, *, visibility="private", exists=True, account="owner"):
        self.files = {}
        self.uploads = []
        self.visibility = visibility
        self.exists = exists
        self.account = account
        self.create_calls = []
        self.commit = True
        self.hide_inventory = False
        self.upload_hook = None
        self.reads = []

    def whoami(self):
        return SimpleNamespace(username=self.account)

    def repo_exists(self, repository, kind):
        assert (repository, kind) == (TARGET, "dataset")
        return self.exists

    def get_repo(self, repository, kind):
        owner, name = repository.split("/", 1)
        return SimpleNamespace(
            owner=owner,
            name=name,
            repo_id=repository,
            visibility=self.visibility,
            private=self.visibility in {"private", 1},
        )

    def create_repo(self, repository, kind, **kwargs):
        self.create_calls.append((repository, kind, kwargs))
        self.exists = True
        self.visibility = kwargs["visibility"]
        return self.get_repo(repository, kind)

    def list_repo_files(self, repository, kind):
        if self.hide_inventory:
            return []
        return [
            SimpleNamespace(path=path, size=len(data), sha256=None, type="blob")
            for path, data in sorted(self.files.items())
        ]

    def upload_folder(self, repository, kind, stage, **kwargs):
        paths = list(kwargs["allow_patterns"])
        self.uploads.append(paths)
        if self.commit:
            for path in paths:
                self.files[path] = (Path(stage) / path).read_bytes()
        if self.upload_hook is not None:
            self.upload_hook(paths)

    def download_file(self, repository, kind, path, **kwargs):
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        target = Path(kwargs["local_dir"]) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[path])
        return target


def _publisher(store, api, tmp_path, *, through_worker=False):
    if through_worker:
        from clean_studio.providers.modelscope_runtime import ModelScopeWorkerAPI
        from clean_studio.providers.modelscope_worker import handle_request

        fake = api

        def runner(command, **kwargs):
            request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
            response = handle_request(request, api=fake)
            Path(command[-1]).write_text(json.dumps(response), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        api = ModelScopeWorkerAPI(python="unused-offline-python", runner=runner)
    return ModelScopePublisher(
        store=store,
        api=api,
        repository=TARGET,
        work_dir=tmp_path / "publication",
    )


def test_accept_export_returns_current_accepted_but_not_complete(tmp_path):
    export = _export(tmp_path)
    accepted = accept_export(export, _visual(export), _audio(export), _technical(export))
    assert isinstance(accepted, AcceptedExport)
    assert accepted.status == "accepted"
    assert accepted.complete is False
    assert accepted.export_sha256 == export.sha256
    assert accepted.cover_sha256 == export.cover_sha256
    assert accepted.export.status == "candidate"


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("cover_sha256", "different-cover"),
        ("subtitle_sha256", "different-subtitles"),
        ("manifest_sha256", "different-manifest"),
        ("episode_hash", "different-episode"),
    ],
)
def test_export_bundle_identity_covers_asset_and_episode_changes(tmp_path, field, replacement):
    export = _export(tmp_path)
    changed = replace(export, **{field: _hash(replacement)})
    assert changed.sha256 == export.sha256
    assert changed.bundle_hash != export.bundle_hash


def test_same_video_new_bundle_invalidates_reviews_and_blocks_old_upload(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)

    changed = replace(export, cover_sha256=_hash("new-cover-bundle"))
    assert changed.sha256 == export.sha256
    assert changed.bundle_hash != export.bundle_hash
    _persist_export(
        store,
        changed,
        key="assembly-new-cover",
        compile_hash="compile-new-cover",
    )
    assert store.load()["status"] == "candidate"
    with pytest.raises(AcceptanceError, match="current|bundle|candidate"):
        publish(accepted, publisher)
    assert api.uploads == []


@pytest.mark.parametrize(
    "field",
    ["fixed_question", "fixed_options", "mixed_music", "subtitles", "source_videos"],
)
def test_bundle_content_fields_invalidate_each_old_evidence_and_upload(tmp_path, field):
    export = _export(tmp_path)
    replacements = {
        "fixed_question": "另一个固定问题",
        "fixed_options": ({
            "label": "B",
            "target_world": "W12",
            "target_video": "EP05B",
            "title": "瓶中四季",
        },),
        "mixed_music": ({"candidate_id": "different-music"},),
        "subtitles": ({
            "start": 0.0,
            "end": 1.0,
            "text": "不同字幕",
            "role": "C01",
            "subtitle_source": "current_episode_dialogue",
            "shot_id": "shot-1",
        },),
        "source_videos": ({
            "shot_id": "shot-1",
            "path": "different-source.mp4",
            "video_sha256": _hash("different-source"),
        },),
    }
    changed = replace(export, **{field: replacements[field]})
    assert changed.sha256 == export.sha256
    assert changed.bundle_hash != export.bundle_hash

    for stale in ("technical", "visual", "audio"):
        evidence = {
            "visual": _visual(changed),
            "audio": _audio(changed),
            "technical": _technical(changed),
        }
        if stale == "technical":
            evidence[stale] = _technical(export)
        elif stale == "visual":
            evidence[stale] = _visual(export)
        else:
            evidence[stale] = _audio(export)
        with pytest.raises(AcceptanceError, match="bundle|current"):
            accept_export(
                changed,
                evidence["visual"],
                evidence["audio"],
                evidence["technical"],
            )

    store = _store(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    _persist_export(
        store,
        changed,
        key=f"assembly-{field}",
        compile_hash=f"compile-{field}",
    )
    assert store.load()["status"] == "candidate"
    api = FakeAPI()
    with pytest.raises(AcceptanceError, match="bundle|current|candidate"):
        publish(accepted, _publisher(store, api, tmp_path))
    assert api.uploads == []


@pytest.mark.parametrize("which", ["visual", "audio", "technical"])
def test_stale_video_hash_is_rejected(tmp_path, which):
    export = _export(tmp_path)
    evidence = {"visual": _visual(export), "audio": _audio(export), "technical": _technical(export)}
    value = evidence[which]
    if which == "technical":
        value = replace(value, export_sha256=_hash("old export"))
    else:
        value = {**value, "export_sha256": _hash("old export")}
    evidence[which] = value
    with pytest.raises(AcceptanceError, match="current|hash|SHA"):
        accept_export(export, evidence["visual"], evidence["audio"], evidence["technical"])


def test_changed_video_or_cover_bytes_invalidate_formal_reviews(tmp_path):
    export = _export(tmp_path)
    Path(export.path).write_bytes(b"changed movie")
    with pytest.raises(AcceptanceError, match="bytes|SHA|changed"):
        accept_export(export, _visual(export), _audio(export), _technical(export))
    Path(export.path).write_bytes(b"current movie 4")
    Path(export.cover_path).write_bytes(b"changed cover")
    with pytest.raises(AcceptanceError, match="cover|SHA|changed"):
        accept_export(export, _visual(export), _audio(export), _technical(export))


def test_visual_accepted_asr_accepted_audio_rejected_remains_candidate(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    _persist_review(store, "final_visual_review", export, _visual(export))
    _persist_review(store, "final_audio_review", export, _audio(export, verdict="rejected"))
    asr = {"status": "completed", "passed": True, "evidence_scope": "asr_only"}
    assert asr["passed"] is True
    with pytest.raises(AcceptanceError) as caught:
        accept_export(export, None, None, _technical(export), store=store)
    assert caught.value.status == "candidate"
    assert store.load()["status"] == "candidate"


def test_clean_latest_real_receipt_supersedes_supplied_old_acceptance(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    _persist_review(store, "final_visual_review", export, _visual(export), key="visual-old")
    rejected = _visual(export, verdict="rejected", severity="must_fix")
    _persist_review(store, "final_visual_review", export, rejected, key="visual-new")
    _persist_review(store, "final_audio_review", export, _audio(export))
    failed = store.begin_operation("visual-service", "final_visual_review", export.bundle_hash, None)
    store.fail_operation(failed.id, "review_service_error")

    with pytest.raises(AcceptanceError, match="visual|rejected|must_fix"):
        accept_export(export, _visual(export), _audio(export), _technical(export), store=store)
    assert store.find_valid_review(export.bundle_hash, "final_visual_review").verdict == "rejected"


def test_tampered_receipt_is_not_a_real_current_verdict(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    visual = _persist_review(store, "final_visual_review", export, _visual(export))
    _persist_review(store, "final_audio_review", export, _audio(export))
    receipt = store.path.parent / "receipts" / f"{visual['operation_id']}.json"
    receipt.write_text(_canonical({**visual, "tampered": True}), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="receipt|SHA|verdict"):
        accept_export(export, None, None, _technical(export), store=store)


@pytest.mark.parametrize("case", ["missing_window", "service_error", "malformed", "asr_only"])
def test_incomplete_or_nonformal_audio_never_passes(tmp_path, case):
    export = _export(tmp_path)
    audio = _audio(export)
    if case == "missing_window":
        audio["coverage"] = audio["coverage"][:0]
        audio["windows"] = audio["windows"][:0]
    elif case == "service_error":
        audio.update(complete=False, passed=False, verdict=None, errors=["Eureka unavailable"])
    elif case == "malformed":
        audio["windows"][0]["response"] = ""
    else:
        audio = {"status": "completed", "passed": True, "evidence_scope": "asr_only"}
    with pytest.raises(AcceptanceError):
        accept_export(export, _visual(export), audio, _technical(export))


def test_accept_export_requires_full_thirty_second_two_second_overlap(tmp_path):
    export = _export(tmp_path, seconds=65)
    audio = _audio(export)
    assert [row["start"] for row in audio["coverage"]] == [0.0, 28.0, 56.0]
    accepted = accept_export(export, _visual(export), audio, _technical(export))
    assert accepted.status == "accepted"
    audio["coverage"][1]["start"] = 30.0
    audio["windows"][1]["start"] = 30.0
    with pytest.raises(AcceptanceError, match="window|coverage|overlap"):
        accept_export(export, _visual(export), audio, _technical(export))


def test_visual_validate_and_blocking_contract_preserved():
    selected = [{"clip_id": "shot-1"}]
    minor = {
        "clip_ids": ["shot-1"], "frame_numbers": [1], "severity": "minor",
        "problem": "非关键衣饰差异", "evidence": "第 1 帧", "source_basis": "原稿衣饰",
    }
    answer = {"passed": True, "observations": ["实际观察"], "issues": [minor]}
    validate_group(answer, selected)
    assert blocking_issues(answer["issues"]) == []
    bad = {**minor, "frame_numbers": [2]}
    with pytest.raises(ValueError, match="frame|visible|available"):
        validate_group({"passed": True, "observations": ["观察"], "issues": [bad]}, selected)


def test_visual_top_level_issues_must_exactly_match_group_aggregation(tmp_path):
    export = _export(tmp_path)
    visual = _visual(export, verdict="rejected", severity="must_fix")
    visual.update(verdict="accepted", passed=True, issues=[], findings=[])
    with pytest.raises(AcceptanceError, match="aggregate|issues|must_fix|visual"):
        accept_export(export, visual, _audio(export), _technical(export))


def test_default_prompt_root_without_environment_override(monkeypatch):
    from clean_studio.acceptance import _project_root

    monkeypatch.delenv("NOVEL_PROJECT_ROOT", raising=False)
    assert _project_root() == ROOT
    assert (_project_root() / "prompts/final-visual-review.md").is_file()


def test_final_review_facts_are_pure_json_and_bind_episode(tmp_path):
    export = _export(tmp_path)
    episode = _episode()
    facts = final_review_facts(export, episode)
    encoded = _canonical(facts)
    assert "video_url" not in encoded
    assert "prompt_h3" not in encoded
    assert facts["export"]["sha256"] == export.sha256
    assert facts["shots"][0]["action"] == "知遥点亮船灯"
    with pytest.raises(ValueError, match="episode|hash|current"):
        final_review_facts(export, replace(episode, title="另一稿"))


def test_visual_review_uses_local_frames_prompt_and_validate_retry(tmp_path):
    export = _export(tmp_path, real_media=True)
    episode = _episode()
    calls = []

    class Client:
        def ask_json(self, parts, schema, **kwargs):
            calls.append((parts, schema, kwargs))
            if len(calls) == 1:
                return {"passed": True, "observations": [], "issues": []}
            return {"passed": True, "observations": ["实际画面与批准字幕"], "issues": []}

    result = review_visual(
        export,
        Client(),
        episode=episode,
        work_dir=tmp_path / "visual-review",
    )
    assert result["complete"] is True and result["passed"] is True
    assert result["verdict"] == "accepted"
    assert len(calls) == 2
    assert "conflicting passage" in calls[1][0][-1]["text"]
    assert len(result["frames"]) == 4
    assert [row["label"] for row in result["frames"][:3]] == ["start", "middle", "end"]
    assert result["frames"][-1]["clip_id"] == "end_card"
    serialized = _canonical(calls[0][0])
    assert "video_url" not in serialized
    assert "image_url" in serialized
    assert "验收以观众能顺畅看懂剧情为准" in serialized
    assert "当前镜头明确动作" not in serialized or "知遥点亮船灯" in serialized


def test_visual_service_or_malformed_results_have_no_valid_verdict(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path, real_media=True)
    _persist_export(store, export)

    class Broken:
        def ask_json(self, *args, **kwargs):
            return {"passed": False, "observations": ["不确定"], "issues": []}

    result = review_visual(
        export, Broken(), episode=_episode(), store=store,
        work_dir=tmp_path / "visual-review",
    )
    assert result["complete"] is False and result["passed"] is False
    assert store.find_valid_review(export.bundle_hash, "final_visual_review") is None


def _runtime(tmp_path):
    model = tmp_path / "eureka-model"
    model.mkdir()
    (model / "weights.bin").write_bytes(b"weights")
    source = tmp_path / "eureka-source"
    source.mkdir()
    return {
        "python": "fake-python-not-executed",
        "audio_source_root": str(source),
        "audio_model_dir": str(model),
    }


def test_audio_review_decodes_complete_movie_and_covers_all_windows(tmp_path):
    export = _export(tmp_path, seconds=65, real_media=True)
    runtime = _runtime(tmp_path)
    worker_calls = []
    judgments = []

    def worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        worker_calls.append(job)
        for row in job["jobs"]:
            Path(row["output"]).write_text(
                _canonical({"response": f"实际声音 {row['source_windows']}"}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def judge(facts, windows):
        judgments.append((facts, windows))
        return {"summary": "完整声音通过", "issues": []}

    result = review_audio(
        export,
        judge,
        episode=_episode(seconds=65),
        runtime=runtime,
        work_dir=tmp_path / "audio-review",
        worker_runner=worker,
    )
    assert result["complete"] is True and result["passed"] is True
    assert result["verdict"] == "accepted"
    assert result["decode"]["fully_decoded"] is True
    assert [row["start"] for row in result["coverage"]] == [0.0, 28.0, 56.0]
    assert result["coverage"][-1]["end"] >= 64.9
    assert len(worker_calls[0]["jobs"]) == 3
    assert len(judgments) == 1
    assert judgments[0][0]["export"]["sha256"] == export.sha256
    accepted = accept_export(export, _visual(export), result, _technical(export))
    assert accepted.status == "accepted"


def test_audio_missing_windows_service_error_and_malformed_judgment_do_not_pass(tmp_path):
    export = _export(tmp_path, real_media=True)
    runtime = _runtime(tmp_path)

    def failed_worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        row = job["jobs"][0]
        Path(row["output"]).write_text('{"response":"第一段"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, "", "worker failed")

    failed = review_audio(
        export, lambda *_: {"summary": "不应通过", "issues": []},
        runtime=runtime, work_dir=tmp_path / "audio-review", worker_runner=failed_worker,
    )
    assert failed["complete"] is False and failed["passed"] is False
    assert failed["errors"]
    with pytest.raises(AcceptanceError):
        accept_export(export, _visual(export), failed, _technical(export))

    def fill_worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        for row in job["jobs"]:
            Path(row["output"]).write_text('{"response":"完整声音"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    malformed = review_audio(
        export, lambda *_: {"summary": "坏结构", "issues": "bad"},
        runtime=runtime, work_dir=tmp_path / "audio-review", worker_runner=fill_worker,
    )
    assert malformed["complete"] is False and malformed["passed"] is False
    assert "malformed" in " ".join(malformed["errors"])


def test_audio_model_change_invalidates_raw_window_cache(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path, real_media=True)
    _persist_export(store, export)
    runtime = _runtime(tmp_path)
    calls = []

    def worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        calls.append(job)
        for row in job["jobs"]:
            Path(row["output"]).write_text('{"response":"当前模型声音证据"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    judge = lambda *_: {"summary": "通过", "issues": []}
    first = review_audio(
        export, judge, runtime=runtime, store=store,
        work_dir=tmp_path / "audio-review", worker_runner=worker,
    )
    assert first["complete"] is True
    (Path(runtime["audio_model_dir"]) / "weights.bin").write_bytes(b"changed-model")
    second = review_audio(
        export, judge, runtime=runtime, store=store,
        work_dir=tmp_path / "audio-review", worker_runner=worker,
    )
    assert second["complete"] is True
    assert len(calls) == 2
    assert first["review_id"] != second["review_id"]


def test_candidate_is_not_published_and_accepted_without_upload_is_not_complete(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    assert accepted.complete is False
    assert store.load()["status"] == "accepted"
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)
    with pytest.raises((AcceptanceError, ValueError), match="accepted|candidate"):
        publish(export, publisher)
    assert api.uploads == []
    assert store.load()["status"] == "accepted"


def test_existing_public_repo_and_wrong_account_stop_before_any_upload(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    for api in (FakeAPI(visibility="public"), FakeAPI(account="other")):
        with pytest.raises(ValueError, match="public|private|account"):
            publish(accepted, _publisher(store, api, tmp_path))
        assert api.uploads == []
        assert api.create_calls == []
        assert store.load()["status"] == "accepted"


def test_repo_metadata_must_explicitly_bind_the_target(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)

    class MalformedAPI(FakeAPI):
        def get_repo(self, repository, kind):
            return SimpleNamespace(visibility="private", private=True)

    api = MalformedAPI()
    with pytest.raises(ValueError, match="identity|target"):
        publish(accepted, _publisher(store, api, tmp_path))
    assert api.uploads == []


def test_private_publish_verifies_remote_bytes_receipt_index_and_store_projection(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    receipt = publish(accepted, _publisher(store, api, tmp_path))
    evidence = verify_upload(receipt, _publisher(store, api, tmp_path))

    assert receipt.complete is True
    assert evidence.complete is True
    assert receipt.target == TARGET
    assert receipt.input_hash == export.bundle_hash
    assert receipt.remote_sha256 == export.sha256
    assert receipt.private is True and receipt.visibility == "private"
    assert api.files[receipt.remote_object] == Path(export.path).read_bytes()
    assert api.files[receipt.cover_object] == Path(export.cover_path).read_bytes()
    remote_index = json.loads(api.files["episodes.json"])
    assert remote_index["episodes"][0]["bundle_hash"] == export.bundle_hash
    assert remote_index["episodes"][0]["video_sha256"] == export.sha256
    assert remote_index["episodes"][0]["cover_sha256"] == export.cover_sha256
    assert store.load()["status"] == "complete"


def test_upload_intent_and_unverifiable_upload_are_not_complete(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    api.commit = False
    receipt = publish(accepted, _publisher(store, api, tmp_path))
    assert api.uploads
    assert receipt.complete is False
    assert receipt.verified is False
    assert store.load()["status"] == "accepted"


@pytest.mark.parametrize("through_worker", [False, True])
def test_committed_upload_then_connection_loss_reconciles_without_duplicate(
    tmp_path, through_worker
):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    lost = [True]

    def hook(paths):
        if "episodes/episode-1.mp4" in paths and lost[0]:
            lost[0] = False
            raise ConnectionError("server committed; response lost")

    api.upload_hook = hook
    publisher = _publisher(store, api, tmp_path, through_worker=through_worker)
    with pytest.raises(ConnectionError):
        publish(accepted, publisher)
    video_uploads = sum("episodes/episode-1.mp4" in paths for paths in api.uploads)
    assert video_uploads == 1

    receipt = publish(accepted, publisher)
    assert receipt.complete is True
    assert sum("episodes/episode-1.mp4" in paths for paths in api.uploads) == 1
    assert store.load()["status"] == "complete"
    uploads = len(api.uploads)
    assert publish(accepted, publisher).complete is True
    assert len(api.uploads) == uploads


def test_worker_missing_remote_video_is_negative_verification_evidence(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path, through_worker=True)
    receipt = publish(accepted, publisher)
    assert receipt.complete is True

    remote = api.list_repo_files(TARGET, "dataset")
    api.files.pop(receipt.remote_object)
    api.list_repo_files = lambda repository, kind: remote
    api.reads.clear()
    evidence = verify_upload(receipt, publisher)

    assert evidence.complete is False
    assert evidence.remote_object == ""
    assert evidence.cover_object == receipt.cover_object
    assert evidence.remote_index == receipt.index
    assert api.reads == [receipt.remote_object, receipt.cover_object, "episodes.json"]
    assert any("remote video" in error for error in evidence.errors)
    assert store.load()["status"] == "accepted"


def test_worker_missing_remote_episode_index_is_negative_verification_evidence(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path, through_worker=True)
    receipt = publish(accepted, publisher)
    assert receipt.complete is True

    remote = api.list_repo_files(TARGET, "dataset")
    api.files.pop("episodes.json")
    api.list_repo_files = lambda repository, kind: remote
    api.reads.clear()
    evidence = verify_upload(receipt, publisher)

    assert evidence.complete is False
    assert evidence.remote_object == receipt.remote_object
    assert evidence.cover_object == receipt.cover_object
    assert evidence.remote_index is None
    assert api.reads == [receipt.remote_object, receipt.cover_object, "episodes.json"]
    assert any("remote episode index" in error for error in evidence.errors)
    assert store.load()["status"] == "accepted"


def test_uncertain_inventory_after_lost_response_stops_instead_of_reupload(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    lost = [True]

    def hook(paths):
        if lost[0]:
            lost[0] = False
            raise ConnectionError("lost")

    api.upload_hook = hook
    publisher = _publisher(store, api, tmp_path)
    with pytest.raises(ConnectionError):
        publish(accepted, publisher)
    api.hide_inventory = True
    with pytest.raises(UploadOutcomeUncertain):
        publish(accepted, publisher)
    assert len(api.uploads) == 1


@pytest.mark.parametrize("lost_path", ["covers/episode-1.jpg", "episodes.json"])
def test_later_inflight_upload_is_not_cleared_by_earlier_reconciliation(tmp_path, lost_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    lost = [True]

    def hook(paths):
        if lost_path in paths and lost[0]:
            lost[0] = False
            raise ConnectionError("later upload response lost")

    api.upload_hook = hook
    publisher = _publisher(store, api, tmp_path)
    with pytest.raises(ConnectionError):
        publish(accepted, publisher)
    api.files.pop(lost_path, None)
    with pytest.raises(UploadOutcomeUncertain):
        publish(accepted, publisher)
    assert sum(lost_path in paths for paths in api.uploads) == 1


def test_committed_index_then_connection_loss_is_read_back_without_reupload(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    lost = [True]

    def hook(paths):
        if "episodes.json" in paths and lost[0]:
            lost[0] = False
            raise ConnectionError("index committed; response lost")

    api.upload_hook = hook
    publisher = _publisher(store, api, tmp_path)
    with pytest.raises(ConnectionError):
        publish(accepted, publisher)
    count = len(api.uploads)
    receipt = publish(accepted, publisher)
    assert receipt.complete is True
    assert len(api.uploads) == count


def test_latest_remote_verification_replaces_older_complete_fact(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)
    receipt = publish(accepted, publisher)
    assert store.load()["status"] == "complete"
    api.visibility = "public"
    evidence = verify_upload(receipt, publisher)
    assert evidence.complete is False
    assert store.load()["status"] == "accepted"


def test_upload_verification_alternation_appends_observation_chain(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)
    receipt = publish(accepted, publisher)

    observations = [(receipt, True, "complete")]
    for visibility, complete, status in (
        ("public", False, "accepted"),
        ("private", True, "complete"),
        ("public", False, "accepted"),
        ("private", True, "complete"),
    ):
        api.visibility = visibility
        evidence = verify_upload(receipt, publisher)
        observations.append((evidence, complete, status))
        assert evidence.complete is complete
        assert store.load()["status"] == status

    with store._db(readonly=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM operations WHERE kind = 'upload' ORDER BY rowid"
            )
        ]
    assert len(ids) == len(observations) == 5
    assert len(set(ids)) == 5
    assert verify_upload(receipt, publisher).complete is True
    with store._db(readonly=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE kind = 'upload'"
        ).fetchone()[0] == 5


def test_missing_remote_repo_is_negative_evidence_not_an_inventory_read(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)
    receipt = publish(accepted, publisher)
    assert store.load()["status"] == "complete"
    api.exists = False

    def forbidden_inventory(*args):
        raise AssertionError("a missing repository has no readable inventory")

    api.list_repo_files = forbidden_inventory
    evidence = verify_upload(receipt, publisher)
    assert evidence.complete is False
    assert "missing" in evidence.errors
    assert store.load()["status"] == "accepted"


def test_changed_accepted_source_cannot_be_published_again(tmp_path):
    store = _store(tmp_path)
    export = _export(tmp_path)
    _persist_export(store, export)
    accepted = _accepted(store, export)
    api = FakeAPI()
    publisher = _publisher(store, api, tmp_path)
    publish(accepted, publisher)
    count = len(api.uploads)
    Path(export.path).write_bytes(b"changed after acceptance")
    with pytest.raises((AcceptanceError, ValueError), match="SHA|hash|bytes|changed"):
        publish(accepted, publisher)
    assert len(api.uploads) == count


def _accepting_audio_worker(command, **kwargs):
    job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
    for row in job["jobs"]:
        Path(row["output"]).write_text(
            _canonical({"response": "完整对白、音乐和片尾声音覆盖"}),
            encoding="utf-8",
        )
    return subprocess.CompletedProcess(command, 0, "", "")


def test_real_seven_second_export_has_175_frames_and_complete_audio(tmp_path):
    from test_assembly import FONT, _episode, _valid_artifact, _video
    from clean_studio.assembly import assemble

    episode = _episode(with_music=False)
    episode = replace(episode, shots=(episode.shots[0],), target_seconds=7)
    movie = _video(tmp_path / "inputs" / "shot-1.mp4", "navy", 440)
    artifact = _valid_artifact(episode.shots[0], movie)
    export = assemble(
        episode,
        (artifact,),
        (),
        output_dir=tmp_path / "export",
        media_root=tmp_path,
        font_path=FONT,
        preset="ultrafast",
    )
    assert export.expected_frames == 175
    assert export.technical.actual_frames == 175

    audio = review_audio(
        export,
        lambda *_: {"summary": "完整声音通过", "issues": []},
        episode=episode,
        runtime=_runtime(tmp_path),
        work_dir=tmp_path / "audio-review",
        worker_runner=_accepting_audio_worker,
    )
    assert audio["complete"] is True and audio["verdict"] == "accepted"
    assert audio["decode"]["samples"] >= 112000 - 1
    assert audio["coverage_seconds"] == 7
    assert accept_export(export, _visual(export), audio, export.technical).status == "accepted"


def test_real_eleven_second_music_export_has_complete_audio_coverage(tmp_path):
    from test_assembly import _assemble, _episode

    export, _, _ = _assemble(tmp_path)
    episode = _episode()
    assert export.expected_frames == 275
    assert export.technical.actual_frames == 275
    assert export.mixed_music

    audio = review_audio(
        export,
        lambda *_: {"summary": "完整音乐与声音通过", "issues": []},
        episode=episode,
        runtime=_runtime(tmp_path),
        work_dir=tmp_path / "audio-review",
        worker_runner=_accepting_audio_worker,
    )
    assert audio["complete"] is True and audio["verdict"] == "accepted"
    assert audio["decode"]["samples"] >= 176000 - 1
    assert audio["coverage"][-1]["end"] == 11
    assert accept_export(export, _visual(export), audio, export.technical).status == "accepted"


def test_short_111275_sample_evidence_is_still_rejected(tmp_path):
    export = _export(tmp_path, seconds=7)
    audio = _audio(export)
    audio["decode"]["samples"] = 111275
    with pytest.raises(AcceptanceError, match="decoded|samples"):
        accept_export(export, _visual(export), audio, _technical(export))


def test_task7_frozen_closure_and_provenance_are_local_and_exact():
    provenance = {}
    documents = [
        json.loads((ROOT / "provenance" / name).read_text(encoding="utf-8"))
        for name in ("final-review.json", "modelscope.json")
    ]
    for document in documents:
        provenance.update(document["sources"])
        for row in document["frozen_files"]:
            assert _file_hash(ROOT / row["path"]) == row["sha256"]
    for source in provenance.values():
        path = Path(source["path"])
        assert path.is_absolute()
        assert _file_hash(path) == source["sha256"]

    files = [
        "src/clean_studio/acceptance.py",
        "src/clean_studio/review_contract.py",
        "src/clean_studio/frozen/final_review.py",
        "src/clean_studio/frozen/final_audio.py",
        "src/clean_studio/frozen/eureka_worker.py",
        "src/clean_studio/frozen/modelscope.py",
        "src/clean_studio/providers/modelscope.py",
    ]
    for relative in files:
        text = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(text, filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("novel_manga") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("novel_manga")
        assert '".env"' not in text and "'.env'" not in text
        assert "dry_run" not in text and "dry-run" not in text
    visual = (ROOT / "src/clean_studio/frozen/final_review.py").read_text(encoding="utf-8")
    prompt = (ROOT / "prompts/final-visual-review.md").read_text(encoding="utf-8")
    assert "video_url" not in visual
    assert "video_url" not in prompt
