from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from clean_studio.compiler import script_hash
from clean_studio.frozen.final_audio import AUDIO_POLICY
from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    Reference,
    ReferenceManifest,
    Review,
    Shot,
    SourcePackage,
    SourceScene,
    SourceSegment,
    SubjectRegistration,
)
from clean_studio.frozen.provider import TaskResult
from clean_studio.pipeline import Pipeline, STAGES, produce, status
from clean_studio.providers.modelscope import ModelScopePublisher
from clean_studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
TARGET = "owner/private-dataset"
FIDELITY_CHECKS = (
    "events_preserved",
    "participants_preserved",
    "temporal_order_preserved",
    "opening_preserved",
    "end_state_preserved",
    "no_extra_speech",
    "no_production_instruction_changes",
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _png(path, color=(20, 80, 120)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (36, 64), color).save(path, format="PNG")
    return path.read_bytes()


def _video_bytes(tmp_path):
    path = tmp_path / "provider-video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=160x288:r=25:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _shot():
    return Shot(
        shot_id="shot_001",
        scene_id="S1",
        source_segments=("S1/flow/0",),
        location="失物海码头",
        start_state="知遥站在码头，船灯熄灭",
        action="知遥点亮船灯",
        end_state="知遥手持点亮的船灯站在码头",
        camera="固定机位",
        shot_scale="中景",
        visible_characters=("C01",),
        dialogue=(Dialogue("C01", "把船灯递给我。"),),
        reference_subjects=("C01",),
        duration_seconds=4,
    )


def _episode():
    return EpisodeSpec(
        episode_id="episode-1",
        title="失物海",
        target_seconds=7,
        style_id="travel-live",
        shots=(_shot(),),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


def _source():
    segment = SourceSegment("S1/flow/0", "S1", 0, None, "C01", "把船灯递给我。", None)
    return SourcePackage(
        archive_path="unused-source.zip",
        archive_sha256="a" * 64,
        material_id="W10",
        video_id="episode-1",
        title="失物海",
        target_seconds=7,
        scenes=(SourceScene("S1", "码头", ("C01",), (segment,)),),
        fixed_question="下一站？",
        fixed_options=_episode().fixed_options,
        continuity_exit="继续",
    )


def _references(tmp_path):
    identity = tmp_path / "assets" / "identity.png"
    style = tmp_path / "assets" / "style.png"
    identity_bytes = _png(identity)
    style_bytes = _png(style, (80, 40, 20))
    return ReferenceManifest(
        source_path="unused-source.zip",
        source_sha256="a" * 64,
        recorded_date="2026-10-26",
        style_id="travel-live",
        subjects=(SubjectRegistration("C01", "知遥", "character", "profile"),),
        references=(
            Reference("C01", "material/identity.png", sha256(identity_bytes).hexdigest(), "identity", "identity only", 1, str(identity), "2026-10-26"),
            Reference("travel-live", "style/style.png", sha256(style_bytes).hexdigest(), "style", "style only", 2, str(style), "2026-10-26"),
        ),
    )


class FakeAuthor:
    def __init__(self, episode):
        self._current = episode
        self.start_calls = 0
        self.revise_calls = 0

    @property
    def current(self):
        return self._current

    def start(self):
        self.start_calls += 1
        return self._current

    def revise(self, findings):
        self.revise_calls += 1
        self._current = replace(
            self._current,
            shots=(replace(self._current.shots[0], action="知遥检查后点亮船灯"),),
        )
        return self._current


class FakeReviewer:
    def __init__(self, verdicts=("accepted",)):
        self.verdicts = list(verdicts)
        self.calls = []
        self.store = None

    def review(self, episode, sources):
        self.calls.append(episode)
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        findings = ()
        if verdict == "rejected":
            findings = ({
                "id": "finding-1",
                "status": "confirmed",
                "category": "contradiction",
                "evidence_origin": "authored_field",
                "field_path": "/shots/0/action",
                "quote": episode.shots[0].action,
                "time": "during",
                "relation": {"type": "temporal_conflict"},
                "message": "动作时序需要明确",
            },)
        return Review(f"review-{len(self.calls)}", script_hash(episode), verdict, findings)


class FakeTranslator:
    model = "fake-translator"
    effort = "low"

    def __init__(self):
        self.calls = []

    def ask_json(self, parts, schema, **options):
        self.calls.append(options["name"])
        if options["name"] == "clean_h3_translation_fidelity":
            return {**{field: True for field in FIDELITY_CHECKS}, "violations": []}
        return {"opening_en": "opening", "action_en": "action", "end_state_en": "end state"}


class FakeClient:
    def __init__(self):
        self.calls = []

    def ask_json(self, parts, schema, **options):
        self.calls.append(options["name"])
        if options["name"] == "final_movie_continuity":
            return {"passed": True, "observations": ["实际画面、字幕与片尾完整"], "issues": []}
        if options["name"] == "final_movie_audio_review":
            return {"summary": "完整声音通过", "issues": []}
        return {"findings": []}


def fake_final_audio_review(export, judge, *, episode, runtime, store, work_dir, worker_runner):
    duration = export.expected_duration_seconds
    coverage = [{"start": 0.0, "end": duration}]
    report = {
        "policy": AUDIO_POLICY,
        "complete": True,
        "passed": True,
        "verdict": "accepted",
        "review_id": "fake-final-audio",
        "content_hash": export.bundle_hash,
        "input_hash": export.bundle_hash,
        "bundle_hash": export.bundle_hash,
        "export_sha256": export.sha256,
        "video_sha256": export.sha256,
        "cover_sha256": export.cover_sha256,
        "movie": str(Path(export.path).resolve()),
        "facts_sha256": "fake-audio-facts",
        "decode": {
            "fully_decoded": True,
            "samples": round(duration * 16000),
            "sample_rate": 16000,
            "export_sha256": export.sha256,
        },
        "coverage_seconds": duration,
        "coverage": coverage,
        "windows": [{**coverage[0], "response": "fake 完整声音审核证据"}],
        "issues": [],
        "findings": [],
        "errors": [],
    }
    if store is not None:
        operation = store.begin_operation(
            f"fake-final-audio:{export.bundle_hash}",
            "final_audio_review",
            export.bundle_hash,
            None,
        )
        report.update(
            review_id=operation.id,
            receipt_id=f"receipt-{operation.id}",
            operation_id=operation.id,
            kind=operation.kind,
        )
        relative = f"receipts/{operation.id}.json"
        path = store.path.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(report), encoding="utf-8")
        store.complete_operation(
            operation.id,
            verdict="accepted",
            receipt_path=relative,
            receipt_sha256=sha256(path.read_bytes()).hexdigest(),
            metadata={"policy": AUDIO_POLICY},
        )
    return report


class FakeProvider:
    def __init__(self, content, *, query_errors=()):
        self.content = content
        self.query_errors = list(query_errors)
        self.submit_calls = []
        self.query_calls = []
        self.states = {}

    def payload(self, request, *args, **kwargs):
        return {"request": request}

    def submit(self, payload):
        self.submit_calls.append(payload["request"].shot_id)
        task_id = f"task-{len(self.submit_calls)}"
        self.states[task_id] = self.content
        return task_id

    def query(self, task_id):
        self.query_calls.append(task_id)
        if self.query_errors:
            raise self.query_errors.pop(0)
        return TaskResult(task_id, "succeeded", f"mock://{task_id}", {})

    def download(self, result, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.states[result.task_id])
        return output


class FakeASR:
    def transcribe(self, video, *, output, reference="", duration=None):
        report = {
            "status": "completed",
            "backend": "fake-sensevoice",
            "hypothesis": reference,
            "segments": [],
            "video_sha256": sha256(Path(video).read_bytes()).hexdigest(),
            "final_audio_accepted": False,
            "evidence_scope": "asr_only",
        }
        Path(output).write_text(_canonical(report), encoding="utf-8")
        return report


class ForbiddenRepair:
    def __init__(self):
        self.calls = []

    def repair(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("正常生产不得调用异常修复 Agent")


class FakeAPI:
    def __init__(self):
        self.files = {}
        self.uploads = []

    def whoami(self):
        return SimpleNamespace(username="owner")

    def repo_exists(self, repository, kind):
        return True

    def get_repo(self, repository, kind):
        owner, name = repository.split("/", 1)
        return SimpleNamespace(owner=owner, name=name, repo_id=repository, visibility="private", private=True)

    def list_repo_files(self, repository, kind):
        return [SimpleNamespace(path=path, size=len(data), sha256=None, type="blob") for path, data in sorted(self.files.items())]

    def upload_folder(self, repository, kind, stage, **kwargs):
        paths = list(kwargs["allow_patterns"])
        self.uploads.append(paths)
        for path in paths:
            self.files[path] = (Path(stage) / path).read_bytes()

    def download_file(self, repository, kind, path, **kwargs):
        if path not in self.files:
            raise FileNotFoundError(path)
        target = Path(kwargs["local_dir"]) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[path])
        return target


class Harness:
    def __init__(self, tmp_path, *, verdicts=("accepted",), query_errors=()):
        self.tmp_path = tmp_path
        self.episode = _episode()
        self.author = FakeAuthor(self.episode)
        self.reviewer = FakeReviewer(verdicts)
        self.translator = FakeTranslator()
        self.translation_reviewer = FakeTranslator()
        self.client = FakeClient()
        self.images = FakeProvider(_png(tmp_path / "generated.png"), query_errors=query_errors)
        self.videos = FakeProvider(_video_bytes(tmp_path))
        self.repair = ForbiddenRepair()
        self.api = FakeAPI()
        model = tmp_path / "eureka-model"
        model.mkdir()
        (model / "weights.bin").write_bytes(b"weights")
        source = tmp_path / "eureka-source"
        source.mkdir()
        self.runtime = {"python": "fake-eureka-python", "audio_source_root": str(source), "audio_model_dir": str(model)}
        self.config = {
            "episode_id": "episode-1",
            "database_path": str(tmp_path / "studio.sqlite3"),
            "model": "frozen-model",
            "channel": "h3-first-frame",
            "input_id": "W10",
            "publish_target": TARGET,
        }
        self.config_path = tmp_path / "config.json"
        self.config_path.write_text(_canonical(self.config), encoding="utf-8")
        self.services = {
            "source": _source(),
            "references": _references(tmp_path),
            "author": self.author,
            "reviewer": self.reviewer,
            "translator": self.translator,
            "translation_reviewer": self.translation_reviewer,
            "image_provider": self.images,
            "h3_provider": self.videos,
            "frame_review_client": self.client,
            "asr_provider": FakeASR(),
            "video_reviewer": self.video_review,
            "final_visual_client": self.client,
            "final_audio_judge": self.client,
            "review_audio": fake_final_audio_review,
            "eureka_runtime": self.runtime,
            "audio_worker_runner": self.audio_worker,
            "repair_agent": self.repair,
            "font_path": "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "publisher_factory": self.publisher,
        }

    def publisher(self, store):
        return ModelScopePublisher(store=store, api=self.api, repository=TARGET, work_dir=self.tmp_path / "publication")

    def video_review(self, path, request, technical, speech):
        return {"verdict": "accepted", "video_sha256": sha256(Path(path).read_bytes()).hexdigest(), "findings": []}

    def audio_worker(self, command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        for row in job["jobs"]:
            Path(row["output"]).write_text(_canonical({"response": "完整对白清晰，音乐不遮挡"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture()
def harness(tmp_path):
    return Harness(tmp_path)


def test_fixed_stages_host_executes_missing_frames_and_readonly_status(harness):
    first = produce(harness.config_path, services=harness.services, stop_after="FINAL_REVIEW")

    assert first.stages == STAGES[:8]
    assert first.stage == "FINAL_REVIEW"
    assert first.status == "accepted"
    assert first.recovery_validation is False
    assert harness.author.start_calls == 1
    assert harness.repair.calls == []
    assert harness.images.submit_calls == ["shot_001"]
    assert harness.videos.submit_calls == ["shot_001"]
    assert harness.api.uploads == []
    before = Store.open_readonly(harness.config["database_path"]).load()
    report = status(harness.config_path)
    after = Store.open_readonly(harness.config["database_path"]).load()
    assert report["valid_videos"] == 1 and report["planned_videos"] == 1
    assert report["accepted_exports"] == 1 and report["uploads"] == 0
    assert report["stage"] == "FINAL_REVIEW" and report["status"] == "accepted"
    assert report["production_validation"] is False
    assert report["validation_nature"] == "scoped_validation"
    assert after["launches"] == before["launches"] == 1
    assert after["stage"] == before["stage"]

    second = produce(harness.config_path, services=harness.services)
    assert second.stages == ("FINAL_REVIEW", "PUBLISHING", "COMPLETE")
    assert second.stage == "COMPLETE" and second.status == "complete"
    assert second.recovery_validation is True
    assert len(harness.api.uploads) == 3
    final = status(harness.config_path)
    assert final["accepted_exports"] == 1 and final["uploads"] == 1
    assert final["launches"] == 2 and final["recovery_validation"] is True
    assert final["production_validation"] is False
    assert final["validation_nature"] == "recovery_validation"
    assert first.stages + second.stages[1:] == STAGES


def test_scoped_validation_never_projects_episode_complete(harness):
    result = produce(
        harness.config_path,
        services=harness.services,
        stop_after="FIRST_FRAMES",
        only_shot="shot_001",
    )
    assert result.stage == "FIRST_FRAMES"
    assert result.status != "complete" and result.stage != "COMPLETE"
    assert result.validation_scope == {"stop_after": "FIRST_FRAMES", "only_shot": "shot_001"}
    loaded = Store.open_readonly(harness.config["database_path"]).load()
    assert loaded["stage"] == "FIRST_FRAMES" and loaded["status"] == "in_progress"
    assert status(harness.config_path)["validation_nature"] == "scoped_validation"
    with pytest.raises(ValueError, match="only_shot|unknown shot"):
        produce(harness.config_path, services=harness.services, only_shot="missing")


def test_content_revision_stays_in_source_review_then_recompiles_and_reviews(harness):
    harness.reviewer.verdicts = ["rejected", "accepted"]
    result = produce(harness.config_path, services=harness.services, stop_after="SOURCE_REVIEW")

    assert result.stage == "SOURCE_REVIEW" and result.status == "in_progress"
    assert harness.author.revise_calls == 1
    assert len(harness.reviewer.calls) == 2
    assert harness.translator.calls.count("clean_h3_translation") == 1
    store = Store.open_readonly(harness.config["database_path"])
    loaded = store.load()
    assert loaded["stage"] == "SOURCE_REVIEW"
    assert loaded["spec"].shots[0].action == "知遥检查后点亮船灯"
    assert loaded["compile_hash"] is not None
    assert store.usage("shot_001", "input") == 1


def test_temporary_failure_retries_same_operation_without_content_agent(harness):
    harness.images.query_errors = [ConnectionError("temporary 503")]
    result = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")

    assert result.stage == "FIRST_FRAMES" and result.status != "blocked"
    assert harness.images.submit_calls == ["shot_001"]
    assert harness.images.query_calls == ["task-1", "task-1"]
    assert harness.repair.calls == []
    store = Store.open_readonly(harness.config["database_path"])
    assert store.usage("shot_001", "image") == 1


def test_unknown_failure_blocks_real_stage_and_preserves_next_step(harness):
    harness.videos.query_errors = [RuntimeError("unknown host defect")]
    result = produce(harness.config_path, services=harness.services)

    assert result.status == "blocked" and result.stage == "VIDEOS"
    assert "unknown host defect" in result.blocked
    assert result.next_action == "resume VIDEOS"
    assert harness.repair.calls == []
    loaded = Store.open_readonly(harness.config["database_path"]).load()
    assert loaded["stage"] == "VIDEOS" and loaded["status"] == "blocked"


def test_cold_recovery_code_evidence_frozen_identity_and_launch_cap(harness, monkeypatch):
    import clean_studio.pipeline as module

    monkeypatch.setattr(module, "_code_identity", lambda: "code-one")
    first = produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert first.launches == 1 and first.recovery_validation is False
    monkeypatch.setattr(module, "_code_identity", lambda: "code-two")
    second = produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert second.launches == 2 and second.recovery_validation is True
    store = Store(harness.config["database_path"])
    with store._db(readonly=True) as connection:
        recovery = connection.execute("SELECT metadata FROM operations WHERE kind = 'recovery'").fetchall()
    assert len(recovery) == 1
    evidence = json.loads(recovery[0]["metadata"])
    assert evidence["previous_code"] == "code-one" and evidence["current_code"] == "code-two"

    third = produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert third.launches == 3
    with pytest.raises(ValueError, match="launch|启动"):
        produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert store.load()["launches"] == 3

    for key, value in (("model", "other"), ("channel", "other"), ("input_id", "other"), ("publish_target", "owner/other")):
        changed = dict(harness.config, **{key: value})
        with pytest.raises(ValueError, match="frozen|冻结"):
            produce(changed, services=harness.services, stop_after="AUTHORING")
        assert store.load()["launches"] == 3


def test_pipeline_run_does_not_start_another_advance_loop(harness):
    store = Store(harness.config["database_path"])
    store.create_episode(harness.episode, {"publish_target": TARGET})
    pipeline = Pipeline(store, harness.services)
    result = pipeline.run("episode-1", stop_after="SOURCE_REVIEW")
    assert result.stages == ("AUTHORING", "SOURCE_REVIEW")
    assert store.load()["launches"] == 1


def test_final_content_apply_rebuilds_and_reviews_without_agent_stage_control(harness):
    class Repair:
        def __init__(self):
            self.calls = []
            self.old_export = None

        def repair(self, episode, findings, *, grant):
            self.calls.append(findings)
            self.old_export = Store(harness.config["database_path"]).load()["current_export_artifact_id"]
            return {
                "ok": True,
                "patch": {"shot_001": {"action": "知遥检查后重新点亮船灯"}},
                "grant": {"shot_001": ["action"]},
            }

    class RejectOnce(FakeClient):
        def ask_json(self, parts, schema, **options):
            if options["name"] == "final_movie_continuity" and not any(
                call == "final_movie_continuity" for call in self.calls
            ):
                self.calls.append(options["name"])
                return {
                    "passed": False,
                    "observations": ["实际首帧动作不明确"],
                    "issues": [{
                        "clip_ids": ["shot_001"],
                        "frame_numbers": [1],
                        "severity": "must_fix",
                        "problem": "关键动作与当前稿不一致",
                        "evidence": "实际抽帧中的动作",
                        "source_basis": "当前 shot_001.action",
                    }],
                }
            return super().ask_json(parts, schema, **options)

    repair = Repair()
    harness.services["repair_agent"] = repair
    harness.services["final_visual_client"] = RejectOnce()
    result = produce(harness.config_path, services=harness.services, stop_after="FINAL_REVIEW")

    assert result.stage == "FINAL_REVIEW" and result.status == "accepted"
    assert len(repair.calls) == 1
    store = Store.open_readonly(harness.config["database_path"])
    loaded = store.load()
    assert loaded["current_export_artifact_id"] != repair.old_export
    assert store.usage("shot_001", "input") == 1
    assert harness.images.submit_calls == ["shot_001", "shot_001"]
    assert harness.videos.submit_calls == ["shot_001", "shot_001"]
    assert loaded["spec"].shots[0].action == "知遥检查后重新点亮船灯"


def test_only_shot_full_validation_upload_is_not_episode_complete(harness):
    from clean_studio.cli import ScopedModelScopePublisher

    harness.services["validation_publisher_factory"] = lambda store, shot: ScopedModelScopePublisher(
        store=store,
        api=harness.api,
        repository=TARGET,
        work_dir=harness.tmp_path / "scoped-publication",
        only_shot=shot,
    )
    result = produce(harness.config_path, services=harness.services, only_shot="shot_001")

    assert result.stage == "PUBLISHING" and result.status != "complete"
    assert result.complete is False
    loaded = Store.open_readonly(harness.config["database_path"]).load()
    assert loaded["stage"] == "PUBLISHING" and loaded["status"] != "complete"
    assert "episodes/episode-1-validation-shot_001.mp4" in harness.api.files
    report = status(harness.config_path)
    assert report["uploads"] == 1
    assert report["accepted_exports"] == 0
    assert report["production_validation"] is False
    assert report["validation_nature"] == "scoped_validation"

    completed = produce(harness.config_path, services=harness.services)
    assert completed.stages == ("FINAL_REVIEW", "PUBLISHING", "COMPLETE")
    assert completed.stage == "COMPLETE" and completed.status == "complete"
    final = status(harness.config_path)
    assert final["valid_videos"] == final["planned_videos"] == 1
    assert final["accepted_exports"] == 1 and final["uploads"] == 1
    assert final["recovery_validation"] is True


def test_temporary_failure_is_bounded_to_three_same_operation_attempts(harness):
    harness.images.query_errors = [ConnectionError("temporary 503")] * 3
    result = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert result.status == "blocked" and result.stage == "FIRST_FRAMES"
    assert harness.images.submit_calls == ["shot_001"]
    assert harness.images.query_calls == ["task-1"] * 3
    assert Store.open_readonly(harness.config["database_path"]).load()["launches"] == 1


@pytest.mark.parametrize("error", [PermissionError("credential expired"), ValueError("image quota exhausted")])
def test_credentials_and_quota_stop_immediately(harness, error):
    harness.images.query_errors = [error]
    result = produce(harness.config_path, services=harness.services)
    assert result.status == "blocked" and result.stage == "FIRST_FRAMES"
    assert harness.images.query_calls == ["task-1"]
    assert harness.repair.calls == []


def test_blocked_candidate_is_not_overwritten_by_pipeline_status(harness):
    candidate = produce(harness.config_path, services=harness.services, stop_after="ASSEMBLY")
    assert candidate.status == "candidate"
    store = Store(harness.config["database_path"])
    export_id = store.load()["current_export_artifact_id"]
    store.advance("FINAL_REVIEW", blocked="audio unavailable", next_action="resume FINAL_REVIEW")
    result = Pipeline(store, harness.services).run("episode-1")
    assert result.status == "blocked" and result.stage == "FINAL_REVIEW"
    assert store.load()["current_export_artifact_id"] == export_id
    report = status(harness.config_path)
    assert report["candidate_exports"] == 1
    assert report["accepted_exports"] == 0 and report["uploads"] == 0


def test_authoring_failure_recovers_same_edition_and_launch_balance(harness):
    original_start = harness.author.start

    def fail_once():
        harness.author.start = original_start
        raise RuntimeError("author host interrupted")

    harness.author.start = fail_once
    first = produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert first.status == "blocked" and first.stage == "AUTHORING"
    assert first.launches == 1

    second = produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert second.status != "blocked" and second.stage == "AUTHORING"
    assert second.launches == 2 and second.recovery_validation is True
    loaded = Store.open_readonly(harness.config["database_path"]).load()
    assert loaded["spec"] == harness.episode
    assert loaded["launches"] == 2


def test_cold_media_recovery_preserves_external_task_usage_and_balance(harness):
    harness.images.query_errors = [RuntimeError("host interrupted after submit")]
    first = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert first.status == "blocked" and first.launches == 1

    second = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert second.status != "blocked" and second.launches == 2
    assert second.recovery_validation is True
    assert harness.images.submit_calls == ["shot_001"]
    assert harness.images.query_calls == ["task-1", "task-1"]
    store = Store.open_readonly(harness.config["database_path"])
    assert store.usage("shot_001", "image") == 1
    assert store.load()["launches"] == 2


def test_completed_edition_rejects_scoped_reuse(harness):
    store = Store(harness.config["database_path"])
    store.create_episode(harness.episode, {"publish_target": TARGET})
    store.advance("COMPLETE")
    with pytest.raises(ValueError, match="only_shot|scoped|COMPLETE"):
        Pipeline(store, harness.services).run("episode-1", only_shot="shot_001")
    assert store.load()["launches"] == 1


def test_final_review_rebuild_recovers_without_repeating_apply(harness):
    class Repair:
        def __init__(self):
            self.calls = 0

        def repair(self, episode, findings, *, grant):
            self.calls += 1
            return {
                "ok": True,
                "patch": {"shot_001": {"action": "知遥冷恢复后点亮船灯"}},
                "grant": {"shot_001": ["action"]},
            }

    class ReviewerCrash(FakeReviewer):
        def review(self, episode, sources):
            if len(self.calls) == 1:
                self.calls.append(episode)
                raise RuntimeError("source review interrupted after apply")
            return super().review(episode, sources)

    class RejectOnce(FakeClient):
        def ask_json(self, parts, schema, **options):
            if options["name"] == "final_movie_continuity" and not any(
                call == "final_movie_continuity" for call in self.calls
            ):
                self.calls.append(options["name"])
                return {
                    "passed": False,
                    "observations": ["实际动作需要修复"],
                    "issues": [{
                        "clip_ids": ["shot_001"],
                        "frame_numbers": [1],
                        "severity": "must_fix",
                        "problem": "动作与当前稿冲突",
                        "evidence": "实际抽帧",
                        "source_basis": "当前 action",
                    }],
                }
            return super().ask_json(parts, schema, **options)

    repair = Repair()
    reviewer = ReviewerCrash()
    harness.services["repair_agent"] = repair
    harness.services["reviewer"] = reviewer
    harness.services["final_visual_client"] = RejectOnce()
    first = produce(harness.config_path, services=harness.services, stop_after="FINAL_REVIEW")
    assert first.status == "blocked" and first.stage == "FINAL_REVIEW"
    assert repair.calls == 1
    assert Store.open_readonly(harness.config["database_path"]).load()["current_export_artifact_id"] is None

    second = produce(harness.config_path, services=harness.services, stop_after="FINAL_REVIEW")
    assert second.status == "accepted" and second.stage == "FINAL_REVIEW"
    assert second.launches == 2 and second.recovery_validation is True
    assert repair.calls == 1
    assert harness.images.submit_calls == ["shot_001", "shot_001"]
    assert harness.videos.submit_calls == ["shot_001", "shot_001"]


def test_music_evidence_is_reused_after_cold_recovery(harness):
    from test_assembly import _accepted_music

    accepted, _ = _accepted_music(harness.tmp_path / "music-evidence")
    harness.episode = replace(harness.episode, music_cues=(accepted.cue,))
    harness.author._current = harness.episode

    class MusicProvider:
        def __init__(self):
            self.calls = 0

        def search(self, cue, *, limit=3):
            self.calls += 1
            assert cue == accepted.cue
            return [accepted.candidate]

    class MusicAgent:
        def __init__(self):
            self.calls = 0

        def __call__(self, candidates, cue):
            self.calls += 1
            assert candidates == [accepted.candidate]
            return accepted

    provider = MusicProvider()
    agent = MusicAgent()
    harness.services["music_provider"] = provider
    harness.services["music_agent"] = agent
    first = produce(harness.config_path, services=harness.services, stop_after="MUSIC")
    assert first.stage == "MUSIC" and first.status != "blocked"
    assert provider.calls == 1 and agent.calls == 1

    second = produce(harness.config_path, services=harness.services, stop_after="MUSIC")
    assert second.stage == "MUSIC" and second.status != "blocked"
    assert second.launches == 2 and second.recovery_validation is True
    assert provider.calls == 1 and agent.calls == 1
    store = Store(harness.config["database_path"])
    with store._db(readonly=True) as connection:
        count = connection.execute("SELECT COUNT(*) FROM operations WHERE kind = 'music'").fetchone()[0]
    assert count == 1


@pytest.mark.parametrize("scope", [{"stop_after": "INVALID"}, {"stop_after": "COMPLETE"}, {"only_shot": " "}])
def test_invalid_new_scope_does_not_create_database(harness, scope):
    database = harness.tmp_path / "nested" / "studio.sqlite3"
    config = dict(harness.config, database_path=str(database))
    with pytest.raises(ValueError, match="stop_after|only_shot"):
        produce(config, services=harness.services, **scope)
    assert not database.exists()
    assert not database.parent.exists()


@pytest.mark.parametrize("scope", [{"stop_after": "INVALID"}, {"stop_after": "COMPLETE"}, {"only_shot": " "}, {"only_shot": "missing"}])
def test_invalid_recovery_leaves_sqlite_snapshot_and_block_untouched(harness, scope):
    produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    store = Store(harness.config["database_path"])
    store.advance("AUTHORING", blocked="held for recovery", next_action="resume AUTHORING")
    database = Path(harness.config["database_path"])
    before = database.read_bytes()

    with pytest.raises(ValueError, match="stop_after|only_shot"):
        produce(harness.config_path, services=harness.services, **scope)

    assert database.read_bytes() == before
    loaded = Store.open_readonly(database).load()
    assert loaded["launches"] == 1
    assert loaded["blocked"] == "held for recovery"
    assert loaded["next_action"] == "resume AUTHORING"


def test_recovery_rejects_stage_rewind_before_new_launch(harness):
    produce(harness.config_path, services=harness.services, stop_after="SOURCE_REVIEW")
    database = Path(harness.config["database_path"])
    before = database.read_bytes()
    with pytest.raises(ValueError, match="stop_after|current stage"):
        produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert database.read_bytes() == before
    assert Store.open_readonly(database).load()["launches"] == 1


def _execution_config(harness):
    return dict(
        harness.config,
        effort="low",
        ffmpeg={"binary": "ffmpeg", "preset": "veryfast"},
        font_path="/fonts/frozen.ttf",
        publish_account="owner",
        runtime={"eureka": {"threads": 1}},
        quotas={"image": 3, "video": 3, "input": 2, "launches": 3},
        models={
            "codex": {"model": "gpt-5.6-luna", "effort": "low"},
            "music": {"search_root": str(harness.tmp_path / "search-a")},
        },
        publication_dir=str(harness.tmp_path / "publication-a"),
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("effort", "high"),
        ("ffmpeg", {"binary": "ffmpeg", "preset": "ultrafast"}),
        ("font_path", "/fonts/other.ttf"),
        ("publish_account", "other"),
        ("runtime", {"eureka": {"threads": 2}}),
        ("quotas", {"image": 4, "video": 3, "input": 2, "launches": 3}),
        ("models", {"codex": {"model": "gpt-5.6-luna", "effort": "high"}}),
        ("unexpected_execution_option", True),
    ],
)
def test_complete_execution_config_is_frozen_without_launch(harness, key, value):
    config = _execution_config(harness)
    produce(config, services=harness.services, stop_after="AUTHORING")
    database = Path(config["database_path"])
    before = database.read_bytes()
    changed = dict(config, **{key: value})
    with pytest.raises(ValueError, match="frozen|冻结"):
        produce(changed, services=harness.services, stop_after="AUTHORING")
    assert database.read_bytes() == before
    assert Store.open_readonly(database).load()["launches"] == 1


def test_only_explicit_output_and_search_paths_may_relocate(harness):
    config = _execution_config(harness)
    produce(config, services=harness.services, stop_after="AUTHORING")
    changed = dict(
        config,
        publication_dir=str(harness.tmp_path / "publication-b"),
        models={
            **config["models"],
            "music": {"search_root": str(harness.tmp_path / "search-b")},
        },
    )
    result = produce(changed, services=harness.services, stop_after="AUTHORING")
    assert result.launches == 2 and result.recovery_validation is True


@pytest.mark.parametrize("field", ["apiKey", "authorization", "key", "ST_LLM_KEY", "api_key", "sessionToken"])
def test_secret_fields_are_rejected_before_sqlite_without_leaking_values(harness, field):
    database = harness.tmp_path / "secret-state" / "studio.sqlite3"
    config = dict(
        harness.config,
        database_path=str(database),
        models={"image": {field: "do-not-print-this-secret"}},
    )
    with pytest.raises(ValueError, match="secret|configuration|credential") as error:
        produce(config, services=harness.services, stop_after="AUTHORING")
    assert "do-not-print-this-secret" not in str(error.value)
    assert not database.exists()


def test_secret_change_during_recovery_preserves_sqlite_snapshot(harness):
    produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    database = Path(harness.config["database_path"])
    before = database.read_bytes()
    config = dict(harness.config, models={"image": {"apiKey": "recovery-secret"}})
    with pytest.raises(ValueError, match="secret|configuration") as error:
        produce(config, services=harness.services, stop_after="AUTHORING")
    assert "recovery-secret" not in str(error.value)
    assert database.read_bytes() == before
    assert Store.open_readonly(database).load()["launches"] == 1


def test_environment_variable_names_are_allowed_in_frozen_config(harness):
    llm = {
        "base_url": "http://127.0.0.1:8789/v1",
        "model": "deepseek-v4.1-flash-1017",
        "api_key_env": "PHANTHY_API_KEY",
    }
    config = dict(
        harness.config,
        dotenv_path="/private/runtime.env",
        models={
            "image": {"api_key_env": "IMAGE_API_KEY"},
            "music": {"runtime": {"llm": llm}},
        },
    )
    result = produce(config, services=harness.services, stop_after="AUTHORING")
    assert result.launches == 1
    stored = Store.open_readonly(config["database_path"]).load()["frozen_config"]
    assert stored["dotenv_path"] == "/private/runtime.env"
    assert stored["models"]["image"]["api_key_env"] == "IMAGE_API_KEY"
    assert stored["models"]["music"]["runtime"]["llm"] == llm


@pytest.mark.parametrize(
    "decision,expected_posts",
    [
        ({"accepted": True, "task_id": "task-1"}, 1),
        ({"accepted": False}, 2),
    ],
)
def test_reconciled_resume_or_repost_counts_the_second_launch(
    harness, decision, expected_posts
):
    from clean_studio.frozen.provider import SubmissionUncertain

    class UncertainOnceProvider(FakeProvider):
        def submit(self, payload):
            if not self.submit_calls:
                self.submit_calls.append(payload["request"].shot_id)
                raise SubmissionUncertain("connection lost before task ID")
            return super().submit(payload)

    images = UncertainOnceProvider(_png(harness.tmp_path / "uncertain-resume.png"))
    harness.services["image_provider"] = images
    harness.services["submission_reconciler"] = lambda operation: decision

    first = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert first.status == "blocked" and first.launches == 1
    assert first.next_action == "reconcile_submission"
    images.states["task-1"] = images.content

    second = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert second.status != "blocked" and second.stage == "FIRST_FRAMES"
    assert second.launches == 2 and second.recovery_validation is True
    assert images.submit_calls == ["shot_001"] * expected_posts


def test_uncertain_reconciliation_without_progress_stops_before_fourth_launch(harness):
    from clean_studio.cli import ProviderSubmissionReconciler
    from clean_studio.frozen.provider import SubmissionUncertain

    class UncertainProvider(FakeProvider):
        def submit(self, payload):
            self.submit_calls.append(payload["request"].shot_id)
            raise SubmissionUncertain("connection lost before task ID")

    images = UncertainProvider(_png(harness.tmp_path / "uncertain.png"))
    harness.services["image_provider"] = images
    harness.services["submission_reconciler"] = ProviderSubmissionReconciler(images, harness.videos)

    first = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert first.status == "blocked" and first.stage == "FIRST_FRAMES"
    assert first.next_action == "reconcile_submission"
    assert first.launches == 1 and images.submit_calls == ["shot_001"]

    second = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    third = produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert second.status == third.status == "blocked"
    assert second.next_action == third.next_action == "reconcile_submission"
    assert (second.launches, third.launches) == (2, 3)
    assert second.recovery_validation is third.recovery_validation is True
    assert images.submit_calls == ["shot_001"] and images.query_calls == []
    store = Store(harness.config["database_path"])
    with store._db(readonly=True) as connection:
        operation = connection.execute(
            "SELECT status, external_task_id FROM operations WHERE kind = 'image'"
        ).fetchone()
    assert operation["status"] == "submission_uncertain"
    assert operation["external_task_id"] is None

    database = Path(harness.config["database_path"])
    before = database.read_bytes()
    with pytest.raises(ValueError, match="launch|启动"):
        produce(harness.config_path, services=harness.services, stop_after="FIRST_FRAMES")
    assert database.read_bytes() == before
    assert store.load()["launches"] == 3
    assert images.submit_calls == ["shot_001"] and images.query_calls == []


@pytest.mark.parametrize("branch", ["mapping", "plan", "episode", "cross_shot"])
def test_every_repair_branch_enforces_host_grant_without_mutation(harness, branch):
    from clean_studio.agents.repair import check_patch

    harness.reviewer.verdicts = ["rejected", "accepted"]
    original = harness.episode
    if branch == "cross_shot":
        second = replace(original.shots[0], shot_id="shot_002", source_segments=("S1/flow/1",))
        original = replace(original, shots=(original.shots[0], second))
        harness.episode = original
        harness.author._current = original

    if branch == "episode":
        def revise_outside_grant(findings):
            harness.author.revise_calls += 1
            return replace(
                original,
                shots=(replace(original.shots[0], camera="越权机位"), *original.shots[1:]),
            )
        harness.author.revise = revise_outside_grant
    else:
        harness.author._current = None
        harness.author.start = lambda: original

        class Repair:
            def repair(self, episode, findings, *, grant):
                if branch == "mapping":
                    return {
                        "ok": True,
                        "patch": {"shot_001": {"camera": "越权机位"}},
                        "grant": {"shot_001": ["camera"]},
                    }
                if branch == "plan":
                    return check_patch(
                        episode,
                        {"shot_001": {"camera": "越权机位"}},
                        {"shot_001": ["camera"]},
                    )
                return {
                    "ok": True,
                    "patch": {"shot_002": {"action": "跨镜越权"}},
                    "grant": {"shot_002": ["action"]},
                }

        harness.services["repair_agent"] = Repair()

    result = produce(harness.config_path, services=harness.services, stop_after="SOURCE_REVIEW")
    assert result.status == "blocked" and result.stage == "SOURCE_REVIEW"
    store = Store.open_readonly(harness.config["database_path"])
    assert store.load()["spec"] == original
    assert store.usage("shot_001", "input") == 0
    if branch == "cross_shot":
        assert store.usage("shot_002", "input") == 0
    assert len(harness.reviewer.calls) == 1


def test_host_grant_is_computed_exactly_once(harness, monkeypatch):
    harness.reviewer.verdicts = ["rejected", "accepted"]
    harness.author._current = None
    harness.author.start = lambda: harness.episode
    harness.services["repair_agent"] = type(
        "Repair",
        (),
        {
            "repair": lambda self, episode, findings, grant: {
                "ok": True,
                "patch": {"shot_001": {"action": "host grant 内修复"}},
            }
        },
    )()
    calls = []
    original = Pipeline._grant_from_findings

    def counted(self, findings):
        calls.append(findings)
        return original(self, findings)

    monkeypatch.setattr(Pipeline, "_grant_from_findings", counted)
    result = produce(harness.config_path, services=harness.services, stop_after="SOURCE_REVIEW")
    assert result.status != "blocked"
    assert len(calls) == 1
    assert Store.open_readonly(harness.config["database_path"]).load()["spec"].shots[0].action == "host grant 内修复"


def test_scoped_music_retimes_reauditions_and_full_production_completes(harness):
    from clean_studio.cli import ScopedModelScopePublisher
    from clean_studio.domain import MusicCue
    from test_assembly import _accepted_music

    full_cue = MusicCue(2, 6, "克制、逐渐明亮", "保留对白和海水，不遮挡")
    scoped_cue = MusicCue(0, 2, full_cue.mood, full_cue.soundscape)
    second = replace(harness.episode.shots[0], shot_id="shot_002", source_segments=("S1/flow/1",))
    harness.episode = replace(
        harness.episode,
        shots=(harness.episode.shots[0], second),
        music_cues=(full_cue,),
    )
    harness.author._current = harness.episode
    scoped_music, _ = _accepted_music(
        harness.tmp_path / "scoped-music", cue=scoped_cue, seconds=2
    )
    full_music, _ = _accepted_music(
        harness.tmp_path / "full-music", cue=full_cue, seconds=4
    )
    accepted_by_cue = {scoped_cue: scoped_music, full_cue: full_music}

    class MusicProvider:
        def __init__(self):
            self.calls = []

        def search(self, cue, *, limit=3):
            self.calls.append(cue)
            return [accepted_by_cue[cue].candidate]

    class MusicAgent:
        def __init__(self):
            self.calls = []

        def __call__(self, candidates, cue):
            self.calls.append(cue)
            return accepted_by_cue[cue]

    provider = MusicProvider()
    agent = MusicAgent()
    harness.services["music_provider"] = provider
    harness.services["music_agent"] = agent
    harness.services.pop("review_audio")
    harness.services["validation_publisher_factory"] = lambda store, shot: ScopedModelScopePublisher(
        store=store,
        api=harness.api,
        repository=TARGET,
        work_dir=harness.tmp_path / "scoped-music-publication",
        only_shot=shot,
    )

    scoped = produce(harness.config_path, services=harness.services, only_shot="shot_002")
    assert scoped.stage == "PUBLISHING" and scoped.status != "complete"
    store = Store.open_readonly(harness.config["database_path"])
    validation = next(
        row for row in store.load()["artifacts"] if row["kind"] == "validation_assembly"
    )
    scoped_export = validation["metadata"]["export"]
    assert scoped_export["technical"]["actual_frames"] == 175
    assert len(scoped_export["mixed_music"]) == 1
    mixed = scoped_export["mixed_music"][0]
    assert mixed["cue"] == asdict(scoped_cue)
    assert mixed["audition"]["cue"] == asdict(scoped_cue)
    receipt = json.loads(Path(mixed["audition"]["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["cue"] == asdict(scoped_cue)
    assert mixed["mixed_into_sha256"] == scoped_export["sha256"]
    scoped_audio = json.loads(
        (store.path.parent / "final-audio" / scoped_export["bundle_hash"] / "last-result.json").read_text(encoding="utf-8")
    )
    assert scoped_audio["movie"] == scoped_export["path"]
    assert scoped_audio["complete"] is True and scoped_audio["coverage_seconds"] == 7
    assert scoped_audio["decode"]["samples"] >= 112000 - 1
    assert provider.calls == [scoped_cue] and agent.calls == [scoped_cue]

    completed = produce(harness.config_path, services=harness.services)
    assert completed.stages == ("FINAL_REVIEW", "PUBLISHING", "COMPLETE")
    assert completed.status == "complete"
    assert provider.calls == [scoped_cue, full_cue]
    assert agent.calls == [scoped_cue, full_cue]
    loaded = store.load()
    current = next(
        row for row in loaded["artifacts"] if row["id"] == loaded["current_export_artifact_id"]
    )
    full_export = current["metadata"]["export"]
    assert full_export["technical"]["actual_frames"] == 275
    assert full_export["mixed_music"][0]["cue"] == asdict(full_cue)
    full_audio = json.loads(
        (store.path.parent / "final-audio" / full_export["bundle_hash"] / "last-result.json").read_text(encoding="utf-8")
    )
    assert full_audio["movie"] == full_export["path"]
    assert full_audio["complete"] is True and full_audio["coverage_seconds"] == 11
    assert full_audio["decode"]["samples"] >= 176000 - 1
    report = status(harness.config_path)
    assert report["valid_videos"] == report["planned_videos"] == 2
    assert report["accepted_exports"] == 1 and report["uploads"] == 1
    assert report["recovery_validation"] is True
