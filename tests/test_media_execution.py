import ast
import base64
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys

import httpx
from PIL import Image
import pytest

from clean_studio.compiler import compile_episode
from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    Reference,
    ReferenceManifest,
    Shot,
    SubjectRegistration,
)
from clean_studio.frozen.provider import (
    ResultPurged,
    SubmissionRejected,
    SubmissionUncertain,
    TaskResult,
)
from clean_studio.media import (
    MediaOperations,
    ensure_first_frame,
    ensure_video,
    probe_video,
    review_frame,
)
from clean_studio.providers.asr import SenseVoiceASRProvider
from clean_studio.providers.h3 import H3Provider
from clean_studio.providers.image import ImageProvider
from clean_studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIDELITY_CHECKS = (
    "events_preserved",
    "participants_preserved",
    "temporal_order_preserved",
    "opening_preserved",
    "end_state_preserved",
    "no_extra_speech",
    "no_production_instruction_changes",
)


def _png(value):
    output = io.BytesIO()
    Image.new("RGB", (12 + value, 20 + value), (value * 40, 20, 120)).save(
        output, format="PNG"
    )
    return output.getvalue()


def _reference(tmp_path, filename, subject_id, role, order):
    path = tmp_path / "assets" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png(order))
    return Reference(
        subject_id=subject_id,
        path=f"registered/{filename}",
        sha256=sha256(path.read_bytes()).hexdigest(),
        role=role,
        scope=f"{role} only",
        order=order,
        source_path=str(path),
        recorded_date="2026-10-26",
    )


@pytest.fixture()
def references(tmp_path):
    return ReferenceManifest(
        source_path="source.zip",
        source_sha256="a" * 64,
        recorded_date="2026-10-26",
        style_id="travel-live",
        subjects=(
            SubjectRegistration("C01", "知遥", "character", "profile"),
            SubjectRegistration("PIER", "码头", "location", "profile"),
        ),
        references=(
            _reference(tmp_path, "style.png", "travel-live", "style", 4),
            _reference(tmp_path, "pier.png", "PIER", "location", 3),
            _reference(tmp_path, "wardrobe.png", "C01", "wardrobe", 2),
            _reference(tmp_path, "identity.png", "C01", "identity", 1),
        ),
    )


def _shot(shot_id="shot-1", segment="S1/flow/0"):
    return Shot(
        shot_id=shot_id,
        scene_id="S1",
        source_segments=(segment,),
        location="码头",
        start_state="知遥站在码头，船灯熄灭",
        action="知遥查看船灯",
        end_state="知遥手持船灯站在码头",
        camera="固定机位",
        shot_scale="中景",
        visible_characters=("C01",),
        dialogue=(Dialogue("C01", "把船灯递给我。"),),
        reference_subjects=("C01", "PIER"),
        duration_seconds=9,
    )


@pytest.fixture()
def episode():
    return EpisodeSpec(
        episode_id="episode-1",
        title="失物海",
        target_seconds=180,
        style_id="travel-live",
        shots=(_shot(), _shot("shot-2", "S1/flow/1")),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


class FakeTranslator:
    model = "fake-translator"
    effort = "low"

    def ask_json(self, parts, schema, **options):
        if options["name"] == "clean_h3_translation_fidelity":
            return {
                **{field: True for field in FIDELITY_CHECKS},
                "violations": [],
            }
        return {
            "opening_en": "opening",
            "action_en": "action",
            "end_state_en": "end state",
        }


@pytest.fixture()
def compilation(episode, references):
    translator = FakeTranslator()
    reviewer = FakeTranslator()
    return compile_episode(
        episode,
        references,
        translator,
        translation_reviewer=reviewer,
    )


@pytest.fixture()
def store(tmp_path, episode):
    store = Store(tmp_path / "studio.sqlite3")
    store.create_episode(episode, {"model": "frozen"})
    return store


class FakeReviewClient:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def ask_json(self, parts, schema, **options):
        self.calls.append({"parts": parts, "schema": schema, "options": options})
        if not self.responses:
            return {"findings": []}
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _rejected(message="wrong opening"):
    return {
        "findings": [
            {
                "status": "confirmed",
                "category": "contradiction",
                "observed": message,
                "expected": "compiled opening",
                "basis": ["opening"],
                "origin": "image",
                "relation": "contradiction",
                "reason": message,
            }
        ]
    }


class FakeProvider:
    def __init__(self, results=()):
        self.results = list(results)
        self.submit_calls = []
        self.query_calls = []
        self.download_calls = []
        self.states = {}

    def payload(self, request, *args, **kwargs):
        return {"request": request, "args": args, "kwargs": kwargs}

    def submit(self, payload):
        request = payload["request"]
        self.submit_calls.append(request.shot_id)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        task_id = f"task-{len(self.submit_calls)}"
        self.states[task_id] = dict(result)
        return task_id

    def query(self, task_id):
        self.query_calls.append(task_id)
        state = self.states[task_id]
        if state.get("query_errors"):
            raise state["query_errors"].pop(0)
        return TaskResult(
            task_id=task_id,
            status="succeeded",
            download_url=f"mock://{task_id}",
            raw={},
        )

    def download(self, result, output):
        self.download_calls.append(result.task_id)
        state = self.states[result.task_id]
        if state.get("download_errors"):
            raise state["download_errors"].pop(0)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(state["bytes"])
        return output


class FakeASR:
    def __init__(self, reports=()):
        self.reports = list(reports)
        self.calls = []

    def transcribe(self, video, *, output, reference="", duration=None):
        self.calls.append((Path(video), Path(output), reference, duration))
        report = (
            self.reports.pop(0)
            if self.reports
            else {
                "status": "completed",
                "backend": "fake-sensevoice",
                "hypothesis": reference,
                "segments": [],
                "passed": True,
            }
        )
        report = {
            **report,
            "video_sha256": sha256(Path(video).read_bytes()).hexdigest(),
            "final_audio_accepted": False,
            "evidence_scope": "asr_only",
        }
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report), encoding="utf-8")
        return report


def _default_video_review(path, request, technical, speech):
    return {
        "verdict": "accepted",
        "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
        "findings": [],
    }


def _operations(
    store,
    compilation,
    images,
    videos=None,
    reviews=None,
    asr=None,
    probe=None,
    video_reviewer=None,
):
    return MediaOperations(
        store,
        compilation,
        image_provider=images,
        h3_provider=videos or FakeProvider(),
        frame_review_client=reviews or FakeReviewClient(),
        asr_provider=asr or FakeASR(),
        probe=probe
        or (
            lambda path, expected_duration: {
                "passed": True,
                "duration_seconds": float(expected_duration),
                "fully_decoded": True,
                "native_audio": True,
                "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
                "content_accepted": False,
                "final_audio_accepted": False,
            }
        ),
        video_reviewer=(
            _default_video_review if video_reviewer is None else video_reviewer
        ),
        sleeper=lambda _: None,
    )


def _accepted_frame(operations):
    return ensure_first_frame(operations, "shot-1")


def test_image_provider_submit_query_download_preserves_ordered_attachments(
    compilation, tmp_path
):
    request = compilation.get("shot-1").frame_request
    calls = []

    def handle(http_request):
        calls.append(http_request)
        if http_request.method == "POST":
            return httpx.Response(200, json={"task_id": "image-task"})
        if http_request.url.host == "cdn.invalid":
            return httpx.Response(200, content=b"downloaded-image")
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "url": "http://cdn.invalid/frame.png",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    provider = ImageProvider(
        "http://image.invalid", model="image-test", client=client
    )
    payload = provider.payload(request)
    encoded = payload["base64Files"]
    assert [base64.b64decode(value) for value in encoded] == [
        Path(reference.source_path).read_bytes() for reference in request.references
    ]
    assert provider.submit(request) == "image-task"
    result = provider.query("image-task")
    output = tmp_path / "result.image"
    provider.download(result, output)
    assert output.read_bytes() == b"downloaded-image"
    assert sum(call.method == "POST" for call in calls) == 1
    assert calls[1].url.path == "/v3/images/generations/image-task"


def test_h3_payload_uses_raw_first_frame_as_first_reference_and_keyframe_zero(
    compilation, tmp_path
):
    compiled = compilation.get("shot-1")
    frame_path = tmp_path / "accepted.png"
    frame_path.write_bytes(_png(9))
    frame_hash = sha256(frame_path.read_bytes()).hexdigest()
    frame = type(
        "Frame",
        (),
        {
            "id": "frame-1",
            "shot_id": compiled.shot_id,
            "sha256": frame_hash,
            "input_hash": compiled.frame_request_hash,
        },
    )()
    from clean_studio.compiler import video_request

    request = video_request(compiled, frame)
    provider = H3Provider("http://h3.invalid", model="h3-test")
    payload = provider.payload(request, frame_path, seed=123)

    assert payload["target"] == {
        "short_edge": 768,
        "aspect_ratio": "9:16",
        "duration_seconds": 9.0,
    }
    assert payload["seconds"] == 9 and type(payload["seconds"]) is int
    assert payload["seed"] == 123
    ordinary = [row for row in payload["conditions"] if row["role"] == "reference"]
    keyframe = next(row for row in payload["conditions"] if row["role"] == "keyframe")
    assert len(ordinary) == 1 + len(request.references)
    assert ordinary[0]["uri"] == keyframe["uri"]
    assert base64.b64decode(ordinary[0]["uri"].split(",", 1)[1]) == frame_path.read_bytes()
    assert keyframe["frame_index"] == 0
    assert payload["conditions"][-1] == keyframe


@pytest.mark.parametrize("duration", [3, 16, 8.2, True, "9"])
def test_h3_duration_is_already_an_integer_from_four_to_fifteen(
    compilation, tmp_path, duration
):
    compiled = compilation.get("shot-1")
    request = replace(
        compilation.get("shot-1").frame_request,
        prompt="h3",
    )
    from clean_studio.compiler import VideoRequest

    video = VideoRequest(
        shot_id=compiled.shot_id,
        prompt="h3",
        duration_seconds=duration,
        references=compiled.references,
        source_segments=compiled.source_segments,
        source_action=compiled.source_action,
        source_end_state=compiled.source_end_state,
        accepted_frame_hash="b" * 64,
        accepted_frame_id="frame",
        frame_request_hash=compiled.frame_request_hash,
        source_compile_hash=compiled.compile_hash,
        input_hash=request.input_hash,
    )
    frame = tmp_path / "frame.png"
    frame.write_bytes(_png(2))
    with pytest.raises(ValueError, match="4|15|integer|duration|seconds"):
        H3Provider("http://h3.invalid", model="h3-test").payload(video, frame)


def test_h3_submit_query_and_content_download(compilation, tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "h3-task"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"h3-video")
        return httpx.Response(200, json={"status": "completed"})

    client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    provider = H3Provider("http://h3.invalid", model="h3-test", client=client)
    task_id = provider.submit({"model": "h3-test"})
    result = provider.query(task_id)
    output = tmp_path / "video.mp4"
    provider.download(result, output)
    assert task_id == "h3-task"
    assert output.read_bytes() == b"h3-video"
    assert [call.url.path for call in calls] == [
        "/v1/videos",
        "/v1/videos/h3-task",
        "/v1/videos/h3-task/content",
    ]


def test_frame_review_gets_candidate_then_exact_generation_reference_order(
    store, compilation, tmp_path, monkeypatch
):
    request = compilation.get("shot-1").frame_request
    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(_png(8))
    attached = []

    def fake_image_part(path, _width):
        attached.append(Path(path))
        return {"type": "image_url", "image_url": {"url": str(path)}}

    monkeypatch.setattr("clean_studio.media.image_part", fake_image_part)
    client = FakeReviewClient()
    first = review_frame(candidate, request, client, store=store)
    second = review_frame(candidate, request, client, store=store)

    assert first.verdict == second.verdict == "accepted"
    assert attached == [candidate, *(Path(ref.source_path) for ref in request.references)]
    assert len(client.calls) == 1
    assert store.usage("shot-1", "image") == 0


def test_two_rejected_frames_third_accepted_consumes_exactly_three(
    store, compilation
):
    images = FakeProvider([{"bytes": _png(10)}, {"bytes": _png(11)}, {"bytes": _png(12)}])
    reviews = FakeReviewClient([_rejected("one"), _rejected("two"), {"findings": []}])
    operations = _operations(store, compilation, images, reviews=reviews)

    artifact = operations.ensure_first_frame("shot-1")
    repeated = operations.ensure_first_frame("shot-1")

    assert repeated.id == artifact.id
    assert images.submit_calls == ["shot-1"] * 3
    assert store.usage("shot-1", "image") == 3
    assert len(reviews.calls) == 3
    frames = {
        a["operation_id"]: a
        for a in store.load()["artifacts"]
        if a["kind"] == "frame"
    }
    assert len(frames) == 3
    submitted = operations.operations(
        "shot-1", "image", compilation.get("shot-1").frame_request_hash
    )
    assert [frames[row["id"]]["metadata"]["review"]["verdict"] for row in submitted] == [
        "rejected",
        "rejected",
        "accepted",
    ]


def test_video_takes_have_the_same_three_candidate_limit(store, compilation):
    images = FakeProvider([{"bytes": _png(10)}])
    videos = FakeProvider(
        [{"bytes": b"video-1"}, {"bytes": b"video-2"}, {"bytes": b"video-3"}]
    )
    probes = []

    def probe(path, expected_duration):
        probes.append(Path(path).read_bytes())
        return {
            "passed": Path(path).read_bytes() == b"video-3",
            "duration_seconds": float(expected_duration),
            "fully_decoded": True,
            "native_audio": True,
            "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
        }

    operations = _operations(store, compilation, images, videos=videos, probe=probe)
    _accepted_frame(operations)
    artifact = operations.ensure_video("shot-1")
    repeated = operations.ensure_video("shot-1")

    assert repeated.id == artifact.id
    assert videos.submit_calls == ["shot-1"] * 3
    assert store.usage("shot-1", "video") == 3
    assert probes == [b"video-1", b"video-2", b"video-3"]
    assert len(operations.asr_provider.calls) == 1
    rows = {
        a["operation_id"]: a
        for a in store.load()["artifacts"]
        if a["kind"] == "video"
    }
    shot = next(row for row in store.load()["shots"] if row["shot_id"] == "shot-1")
    submitted = operations.operations("shot-1", "video", shot["video_request_hash"])
    assert [rows[row["id"]]["metadata"]["quality"]["passed"] for row in submitted] == [
        False,
        False,
        True,
    ]


def test_partial_503_recovers_only_missing_frames_without_repaid_valid_shots(
    store, compilation
):
    images = FakeProvider(
        [
            {"bytes": _png(10)},
            SubmissionRejected(503, "busy", not_accepted=True),
            {"bytes": _png(11)},
        ]
    )
    operations = _operations(store, compilation, images)
    first = operations.ensure_first_frame("shot-1")
    with pytest.raises(SubmissionRejected):
        operations.ensure_first_frame("shot-2")

    assert operations.ensure_first_frame("shot-1").id == first.id
    second = operations.ensure_first_frame("shot-2")
    assert second.shot_id == "shot-2"
    assert images.submit_calls == ["shot-1", "shot-2", "shot-2"]
    assert store.usage("shot-1", "image") == 1
    assert store.usage("shot-2", "image") == 1


def test_submission_uncertain_never_reposts_or_calls_review(store, compilation):
    images = FakeProvider([SubmissionUncertain("answer lost"), {"bytes": _png(10)}])
    reviews = FakeReviewClient()
    operations = _operations(store, compilation, images, reviews=reviews)

    for _ in range(2):
        with pytest.raises(SubmissionUncertain):
            operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]
    assert reviews.calls == []
    assert store.usage("shot-1", "image") == 1
    operation = operations.operations("shot-1", "image", compilation.get("shot-1").frame_request_hash)[0]
    assert operation["status"] == "submission_uncertain"
    assert operation["external_task_id"] is None


def test_poll_failure_recovers_same_external_task(store, compilation):
    images = FakeProvider(
        [{"bytes": _png(10), "query_errors": [ConnectionError("poll unavailable")]}]
    )
    operations = _operations(store, compilation, images)
    with pytest.raises(ConnectionError):
        operations.ensure_first_frame("shot-1")
    operation = operations.operations("shot-1", "image", compilation.get("shot-1").frame_request_hash)[0]
    assert operation["external_task_id"] == "task-1"

    artifact = operations.ensure_first_frame("shot-1")
    assert artifact.id == operation["id"]
    assert images.submit_calls == ["shot-1"]
    assert images.query_calls == ["task-1", "task-1"]


def test_download_failure_recovers_same_external_task(store, compilation):
    images = FakeProvider(
        [{"bytes": _png(10), "download_errors": [ConnectionError("download unavailable")]}]
    )
    operations = _operations(store, compilation, images)
    with pytest.raises(ConnectionError):
        operations.ensure_first_frame("shot-1")
    artifact = operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]
    assert images.download_calls == ["task-1", "task-1"]
    assert artifact.sha256 == sha256(_png(10)).hexdigest()


def test_explicitly_purged_result_requires_a_new_paid_operation(store, compilation):
    images = FakeProvider(
        [
            {"bytes": _png(10), "query_errors": [ResultPurged("task removed")]},
            {"bytes": _png(11)},
        ]
    )
    operations = _operations(store, compilation, images)
    with pytest.raises(ResultPurged):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]

    artifact = operations.ensure_first_frame("shot-1")
    operations_rows = operations.operations(
        "shot-1", "image", compilation.get("shot-1").frame_request_hash
    )
    assert len(operations_rows) == 2
    assert operations_rows[0]["external_task_id"] == "task-1"
    assert operations_rows[0]["status"] == "failed"
    assert operations_rows[0]["error"] == "result_purged"
    assert operations_rows[1]["id"] == artifact.id != operations_rows[0]["id"]
    assert store.usage("shot-1", "image") == 2


def test_missing_accepted_frame_is_restored_from_same_task_without_new_submission(
    store, compilation
):
    images = FakeProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    artifact = operations.ensure_first_frame("shot-1")
    path = store.path.parent / artifact.path
    path.unlink()

    restored = operations.ensure_first_frame("shot-1")
    assert restored.id == artifact.id
    assert path.read_bytes() == _png(10)
    assert images.submit_calls == ["shot-1"]
    assert images.download_calls == ["task-1", "task-1"]
    assert store.usage("shot-1", "image") == 1


def test_every_take_has_independent_decode_asr_and_quality_evidence(store, compilation):
    images = FakeProvider([{"bytes": _png(10)}])
    videos = FakeProvider([{"bytes": b"video-1"}, {"bytes": b"video-2"}])
    asr = FakeASR()
    probes = []
    reviews = []

    def probe(path, expected_duration):
        probes.append((Path(path).read_bytes(), expected_duration))
        return {
            "passed": True,
            "duration_seconds": float(expected_duration),
            "fully_decoded": True,
            "native_audio": True,
            "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
            "content_accepted": False,
            "final_audio_accepted": False,
        }

    def video_review(path, request, technical, speech):
        video_bytes = Path(path).read_bytes()
        reviews.append((video_bytes, technical, speech))
        accepted = video_bytes == b"video-2"
        return {
            "verdict": "accepted" if accepted else "rejected",
            "video_sha256": sha256(video_bytes).hexdigest(),
            "findings": [] if accepted else [{"reason": "video content rejected"}],
        }

    operations = _operations(
        store,
        compilation,
        images,
        videos=videos,
        asr=asr,
        probe=probe,
        video_reviewer=video_review,
    )
    _accepted_frame(operations)
    artifact = ensure_video(operations, "shot-1")

    assert probes == [(b"video-1", 9), (b"video-2", 9)]
    assert [call[0].read_bytes() for call in asr.calls] == [b"video-1", b"video-2"]
    assert [row[0] for row in reviews] == [b"video-1", b"video-2"]
    assert artifact.metadata["quality"]["passed"] is True
    assert artifact.metadata["quality"]["asr"]["final_audio_accepted"] is False
    assert artifact.metadata["quality"]["technical"]["content_accepted"] is False


def test_probe_video_full_decodes_streams_and_does_not_accept_final_audio(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            payload = {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "9.1"},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    report = probe_video(video, expected_duration=9, runner=run)
    assert report["passed"] is True
    assert report["fully_decoded"] is True
    assert report["native_audio"] is True
    assert report["content_accepted"] is False
    assert report["final_audio_accepted"] is False
    decode = next(command for command in commands if command[0] == "ffmpeg")
    assert "-xerror" in decode
    assert decode[-3:] == ["-f", "null", "-"]


def test_sensevoice_uses_independent_python_and_explicit_model_directory(tmp_path):
    with pytest.raises(ValueError, match="independent|main"):
        SenseVoiceASRProvider(sys.executable, tmp_path)

    model_dir = tmp_path / "sensevoice-model"
    model_dir.mkdir()
    (model_dir / "model.int8.onnx").write_bytes(b"model")
    (model_dir / "tokens.txt").write_text("token", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"wav")
        else:
            segments = Path(command[command.index("--segments") + 1])
            assert json.loads(segments.read_text()) == [[0.0, 9.0]]
            output = Path(command[command.index("--output") + 1])
            output.write_text(
                json.dumps(
                    {
                        "backend": "sherpa-onnx-sensevoice-int8-2024-07-17",
                        "segments": [{"start": 0.0, "end": 9.0, "hypothesis": "台词"}],
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = SenseVoiceASRProvider(
        "/opt/sensevoice/bin/python", model_dir, runner=run
    )
    receipt = provider.transcribe(
        video,
        output=tmp_path / "asr.json",
        reference="台词",
        duration=9,
    )
    assert receipt["status"] == "completed"
    assert receipt["hypothesis"] == "台词"
    assert receipt["final_audio_accepted"] is False
    assert commands[0][0] == "ffmpeg"
    assert commands[1][0] == "/opt/sensevoice/bin/python"
    assert commands[1][1].endswith("frozen/asr_worker.py")
    assert commands[1][commands[1].index("--model-dir") + 1] == str(model_dir)


def test_unknown_submission_failure_stops_without_changing_operation(store, compilation):
    images = FakeProvider([RuntimeError("unknown submit failure"), {"bytes": _png(10)}])
    operations = _operations(store, compilation, images)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]
    rows = operations.operations(
        "shot-1", "image", compilation.get("shot-1").frame_request_hash
    )
    assert len(rows) == 1
    assert store.usage("shot-1", "image") == 1


def test_query_protocol_failure_is_not_misclassified_as_a_rejected_image(
    store, compilation
):
    images = FakeProvider(
        [{"bytes": _png(10), "query_errors": [ValueError("malformed task")]}]
    )
    operations = _operations(store, compilation, images)
    with pytest.raises(ValueError, match="malformed task"):
        operations.ensure_first_frame("shot-1")

    artifact = operations.ensure_first_frame("shot-1")
    assert artifact.sha256 == sha256(_png(10)).hexdigest()
    assert images.submit_calls == ["shot-1"]


def test_purged_result_for_completed_artifact_uses_a_new_paid_operation(
    store, compilation
):
    images = FakeProvider([{"bytes": _png(10)}, {"bytes": _png(11)}])
    operations = _operations(store, compilation, images)
    original = operations.ensure_first_frame("shot-1")
    (store.path.parent / original.path).unlink()
    images.states[original.metadata["external_task_id"]]["query_errors"] = [
        ResultPurged("completed result removed")
    ]

    replacement = operations.ensure_first_frame("shot-1")
    assert replacement.id != original.id
    assert replacement.sha256 == sha256(_png(11)).hexdigest()
    assert images.submit_calls == ["shot-1", "shot-1"]
    assert store.usage("shot-1", "image") == 2


@pytest.mark.parametrize(
    "decision",
    [
        None,
        {},
        {"accepted": "false", "task_id": "task-1"},
        {"accepted": 1, "task_id": "task-1"},
        {"accepted": True},
        {"accepted": False, "task_id": "task-1"},
    ],
)
def test_invalid_submission_reconciliation_stays_uncertain_without_repost(
    store, compilation, decision
):
    images = FakeProvider([SubmissionUncertain("answer lost"), {"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    operations.submission_reconciler = lambda _: decision

    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]
    rows = operations.operations(
        "shot-1", "image", compilation.get("shot-1").frame_request_hash
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "submission_uncertain"
    assert store.usage("shot-1", "image") == 1


def test_reconciliation_explicit_not_accepted_releases_before_a_new_operation(
    store, compilation
):
    images = FakeProvider([SubmissionUncertain("answer lost"), {"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    operations.submission_reconciler = lambda _: {"accepted": False}

    artifact = operations.ensure_first_frame("shot-1")
    rows = operations.operations(
        "shot-1", "image", compilation.get("shot-1").frame_request_hash
    )
    assert len(rows) == 2
    assert rows[0]["status"] == "not_accepted"
    assert rows[1]["id"] == artifact.id
    assert images.submit_calls == ["shot-1", "shot-1"]
    assert store.usage("shot-1", "image") == 1


def test_reconciliation_accepted_true_restores_the_original_task(store, compilation):
    images = FakeProvider([SubmissionUncertain("answer lost")])
    images.states["task-1"] = {"bytes": _png(10)}
    operations = _operations(store, compilation, images)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    operations.submission_reconciler = lambda _: {
        "accepted": True,
        "task_id": "task-1",
    }

    artifact = operations.ensure_first_frame("shot-1")
    assert artifact.metadata["external_task_id"] == "task-1"
    assert images.submit_calls == ["shot-1"]
    assert images.query_calls == ["task-1"]


def test_recovered_submitting_without_task_id_requires_reconciliation(
    store, compilation
):
    images = FakeProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    _, request = operations._prepare_frame_request("shot-1")
    operation = store.begin_operation(
        "manual-submitting", "image", request.input_hash, "shot-1"
    )
    store.mark_submitting(operation.id)

    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == []
    recovered = operations.operations("shot-1", "image", request.input_hash)[0]
    assert recovered["status"] == "submission_uncertain"
    assert recovered["external_task_id"] is None


def test_submitting_transaction_failure_happens_before_any_post(
    store, compilation, monkeypatch
):
    images = FakeProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)

    def crash(_operation_id):
        raise RuntimeError("database crashed before POST")

    monkeypatch.setattr(store, "mark_submitting", crash, raising=False)
    with pytest.raises(RuntimeError, match="before POST"):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == []


def test_mark_submitted_crash_cannot_repost_image_and_reconciles_original_task(
    store, compilation, monkeypatch
):
    images = FakeProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    original = store.mark_submitted

    def crash(_operation_id, _task_id):
        raise RuntimeError("crash after provider response")

    monkeypatch.setattr(store, "mark_submitted", crash)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    monkeypatch.setattr(store, "mark_submitted", original)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]

    operations.submission_reconciler = lambda _: {
        "accepted": True,
        "task_id": "task-1",
    }
    artifact = operations.ensure_first_frame("shot-1")
    assert artifact.metadata["external_task_id"] == "task-1"
    assert images.submit_calls == ["shot-1"]


def test_mark_submitted_crash_cannot_repost_h3_and_reconciles_original_task(
    store, compilation, monkeypatch
):
    images = FakeProvider([{"bytes": _png(10)}])
    videos = FakeProvider([{"bytes": b"video-1"}])
    operations = _operations(store, compilation, images, videos=videos)
    _accepted_frame(operations)
    original = store.mark_submitted

    def crash(_operation_id, _task_id):
        raise RuntimeError("crash after H3 response")

    monkeypatch.setattr(store, "mark_submitted", crash)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_video("shot-1")
    monkeypatch.setattr(store, "mark_submitted", original)
    with pytest.raises(SubmissionUncertain):
        operations.ensure_video("shot-1")
    assert videos.submit_calls == ["shot-1"]

    operations.submission_reconciler = lambda _: {
        "accepted": True,
        "task_id": "task-1",
    }
    artifact = operations.ensure_video("shot-1")
    assert artifact.metadata["external_task_id"] == "task-1"
    assert videos.submit_calls == ["shot-1"]


def test_reference_sha_change_after_compile_blocks_operation_payload_and_review_cache(
    store, compilation, tmp_path
):
    request = compilation.get("shot-1").frame_request
    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(_png(8))
    client = FakeReviewClient()
    review_frame(candidate, request, client, store=store)
    before = store.load()["artifacts"]
    changed = Path(request.references[0].source_path)
    changed.write_bytes(_png(12))

    images = FakeProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    with pytest.raises(ValueError, match="SHA-256|hash"):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == []
    assert operations.operations("shot-1", "image", request.input_hash) == []
    assert store.usage("shot-1", "image") == 0

    provider = ImageProvider("http://image.invalid", model="image-test")
    with pytest.raises(ValueError, match="SHA-256|hash"):
        provider.payload(request)
    with pytest.raises(ValueError, match="SHA-256|hash"):
        review_frame(candidate, request, client, store=store)
    assert len(client.calls) == 1
    assert store.load()["artifacts"] == before


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"findings": {}},
        {"findings": [{}]},
        {"findings": [{**_rejected()["findings"][0], "status": "candidate"}]},
    ],
)
def test_malformed_frame_review_is_not_a_verdict_or_receipt(
    store, compilation, tmp_path, raw
):
    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(_png(8))
    client = FakeReviewClient([raw])
    with pytest.raises(ValueError):
        review_frame(
            candidate,
            compilation.get("shot-1").frame_request,
            client,
            store=store,
        )
    assert store.load()["artifacts"] == []


@pytest.mark.parametrize("body", [b"{", b"{}", b'{"task_id": ""}'])
def test_image_2xx_invalid_json_or_task_id_is_submission_uncertain(body):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
        trust_env=False,
    )
    provider = ImageProvider("http://image.invalid", model="image-test", client=client)
    with pytest.raises(SubmissionUncertain):
        provider.submit({"model": "image-test"})


@pytest.mark.parametrize("body", [b"{", b"{}", b'{"id": ""}'])
def test_h3_2xx_invalid_json_or_task_id_is_submission_uncertain(body):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
        trust_env=False,
    )
    provider = H3Provider("http://h3.invalid", model="h3-test", client=client)
    with pytest.raises(SubmissionUncertain):
        provider.submit({"model": "h3-test"})


def test_video_reviewer_is_mandatory(store, compilation):
    with pytest.raises((TypeError, ValueError), match="video.*review|review.*video|required"):
        MediaOperations(
            store,
            compilation,
            image_provider=FakeProvider(),
            h3_provider=FakeProvider(),
            frame_review_client=FakeReviewClient(),
            asr_provider=FakeASR(),
            video_reviewer=None,
        )


@pytest.mark.parametrize("technical_hash", [None, "0" * 64])
def test_valid_video_requires_technical_probe_bound_to_current_bytes(
    store, compilation, technical_hash
):
    def probe(path, expected_duration):
        return {
            "passed": True,
            "duration_seconds": float(expected_duration),
            "fully_decoded": True,
            "native_audio": True,
            "video_sha256": technical_hash,
        }

    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=FakeProvider([{"bytes": b"video-1"}]),
        probe=probe,
    )
    _accepted_frame(operations)
    with pytest.raises((RuntimeError, ValueError), match="technical.*hash|video bytes|video_sha256"):
        operations.ensure_video("shot-1")
    assert [row for row in store.load()["artifacts"] if row["kind"] == "video"] == []


def test_completed_video_recovery_rejects_legacy_missing_technical_hash(store, compilation):
    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=FakeProvider([{"bytes": b"video-1"}]),
    )
    _accepted_frame(operations)
    artifact = operations.ensure_video("shot-1")
    metadata = json.loads(json.dumps(artifact.metadata))
    metadata["quality"]["technical"].pop("video_sha256")
    with store._db() as connection:
        connection.execute(
            "UPDATE artifacts SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), artifact.id),
        )

    with pytest.raises(RuntimeError, match="evidence|hash"):
        operations.ensure_video("shot-1")


def test_valid_video_requires_asr_bound_to_the_current_video_hash(store, compilation):
    class MissingHashASR:
        def transcribe(self, video, *, output, reference="", duration=None):
            return {"status": "completed", "hypothesis": reference}

    def accepted(path, request, technical, speech):
        return {
            "verdict": "accepted",
            "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
            "findings": [],
        }

    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=FakeProvider([{"bytes": b"video-1"}]),
        asr=MissingHashASR(),
        video_reviewer=accepted,
    )
    _accepted_frame(operations)
    with pytest.raises((RuntimeError, ValueError), match="ASR.*hash|ASR.*bound|video bytes"):
        operations.ensure_video("shot-1")
    assert [row for row in store.load()["artifacts"] if row["kind"] == "video"] == []


def test_current_dialogue_cer_not_provider_passed_flag_controls_asr_judgment(
    store, compilation
):
    asr = FakeASR(
        [
            {
                "status": "completed",
                "hypothesis": "完全不同的一段额外语音",
                "passed": True,
            },
            {
                "status": "completed",
                "hypothesis": "把船灯递给我。",
                "passed": False,
            },
        ]
    )

    def accepted(path, request, technical, speech):
        return {
            "verdict": "accepted",
            "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
            "findings": [],
        }

    videos = FakeProvider([{"bytes": b"video-1"}, {"bytes": b"video-2"}])
    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=videos,
        asr=asr,
        video_reviewer=accepted,
    )
    _accepted_frame(operations)
    artifact = operations.ensure_video("shot-1")

    assert videos.submit_calls == ["shot-1", "shot-1"]
    assert artifact.metadata["quality"]["asr"]["video_sha256"] == artifact.sha256
    assert artifact.metadata["quality"]["asr"]["cer"] == 0
    assert artifact.metadata["quality"]["asr"]["passed"] is True
    assert artifact.metadata["quality"]["asr"]["final_audio_accepted"] is False
    receipt = json.loads(
        (store.path.parent / f"receipts/{artifact.id}-asr.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["evaluation"] == "clean-current-shot-dialogue-cer-v2-phonetic"
    assert receipt["passed"] is True
    assert receipt["video_sha256"] == artifact.sha256


@pytest.mark.parametrize(
    "result",
    [
        True,
        {},
        {"verdict": "accepted", "video_sha256": "0" * 64, "findings": []},
        {"verdict": "accepted", "findings": []},
    ],
)
def test_malformed_or_stale_video_review_cannot_accept_a_take(
    store, compilation, result
):
    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=FakeProvider([{"bytes": b"video-1"}]),
        video_reviewer=lambda *args: result,
    )
    _accepted_frame(operations)
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        operations.ensure_video("shot-1")
    assert [row for row in store.load()["artifacts"] if row["kind"] == "video"] == []


class _PartialProvider(FakeProvider):
    def __init__(self, results=()):
        super().__init__(results)
        self.download_paths = []

    def download(self, result, output):
        self.download_calls.append(result.task_id)
        self.download_paths.append(Path(output))
        state = self.states[result.task_id]
        output.parent.mkdir(parents=True, exist_ok=True)
        data = state["bytes"]
        output.write_bytes(data[: max(1, len(data) // 3)] if len(self.download_calls) == 1 else data)
        return output


def test_partial_image_download_is_retried_on_same_task_and_operation(store, compilation):
    from clean_studio.media import DownloadIncomplete

    images = _PartialProvider([{"bytes": _png(10)}])
    operations = _operations(store, compilation, images)
    with pytest.raises(DownloadIncomplete):
        operations.ensure_first_frame("shot-1")
    assert images.submit_calls == ["shot-1"]
    assert images.download_paths[-1].name == "frame.png.partial"
    assert [row for row in store.load()["artifacts"] if row["kind"] == "frame"] == []

    artifact = operations.ensure_first_frame("shot-1")
    assert artifact.sha256 == sha256(_png(10)).hexdigest()
    assert images.submit_calls == ["shot-1"]
    assert images.download_calls == ["task-1", "task-1"]
    assert all(path.name == "frame.png.partial" for path in images.download_paths)
    assert all(not path.exists() for path in images.download_paths)
    assert store.usage("shot-1", "image") == 1


def test_partial_video_download_is_not_a_rejected_take(store, compilation):
    from clean_studio.media import DownloadIncomplete

    videos = _PartialProvider([{"bytes": b"video-1"}])

    def probe(path, expected_duration):
        complete = Path(path).read_bytes() == b"video-1"
        return {
            "passed": complete,
            "duration_seconds": float(expected_duration),
            "fully_decoded": complete,
            "native_audio": True,
            "video_sha256": sha256(Path(path).read_bytes()).hexdigest(),
        }

    operations = _operations(
        store,
        compilation,
        FakeProvider([{"bytes": _png(10)}]),
        videos=videos,
        probe=probe,
    )
    _accepted_frame(operations)
    with pytest.raises(DownloadIncomplete):
        operations.ensure_video("shot-1")
    assert [row for row in store.load()["artifacts"] if row["kind"] == "video"] == []
    assert videos.submit_calls == ["shot-1"]

    artifact = operations.ensure_video("shot-1")
    assert artifact.metadata["quality"]["passed"] is True
    assert videos.submit_calls == ["shot-1"]
    assert videos.download_calls == ["task-1", "task-1"]
    assert all(path.name == "video.mp4.partial" for path in videos.download_paths)
    assert all(not path.exists() for path in videos.download_paths)
    assert store.usage("shot-1", "video") == 1


def test_speech_evaluation_forgives_same_pinyin_proper_nouns():
    from clean_studio.providers.asr import evaluate_speech

    reference = "潮叔拉住知遥，背包仍浮在近岸水面"
    transcript = {
        "status": "completed",
        "video_sha256": "v" * 64,
        "hypothesis": "潮叔拉住之遥，背包仍浮在近岸水面",
    }
    result = evaluate_speech(reference, transcript, video_sha256="v" * 64, max_cer=0.05)
    assert result["cer"] > 0.05
    assert result["passed"] is True
    assert result["phonetic_equivalence"] is True
    assert result["evaluation"] == "clean-current-shot-dialogue-cer-v2-phonetic"

    wrong = {
        "status": "completed",
        "video_sha256": "v" * 64,
        "hypothesis": "潮叔放开背包扔向远处海面",
    }
    rejected = evaluate_speech(reference, wrong, video_sha256="v" * 64, max_cer=0.05)
    assert rejected["passed"] is False
    assert rejected["phonetic_equivalence"] is False


def test_frozen_provider_media_asr_closure_and_provenance():
    for directory in (ROOT / "src/clean_studio/frozen", ROOT / "src/clean_studio/providers"):
        for path in directory.glob("*.py"):
            if path.name not in {
                "provider.py",
                "h3.py",
                "media.py",
                "asr.py",
                "asr_worker.py",
                "image.py",
                "__init__.py",
            }:
                continue
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(not alias.name.startswith("novel_manga") for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("novel_manga")
            assert "episode-production" not in text
            assert "H3Pool" not in text
            assert ".env" not in text

    provenance = json.loads((ROOT / "provenance/adapters.json").read_text(encoding="utf-8"))
    assert provenance["recorded_date"] == "2026-10-26"
    assert provenance["source_revision"] == "c698b644dea9501d290fe3f575fd83a3df9b2db0"
    assert provenance["sources"]
    for source in provenance["sources"].values():
        assert Path(source["path"]).is_absolute()
        assert len(source["sha256"]) == 64
    task8_hashes = {
        "src/clean_studio/media.py": "bb3a883b6016e47612c6092855717c9bd42aa1fdc36fc933510fda1ab423395e",
        "src/clean_studio/frozen/render.py": "5365a96e9d8086f7d5b9090e1d35766cd8bf6018046fbcb9895e207d739c935f",
    }
    for frozen in provenance["frozen_files"]:
        data = (ROOT / frozen["path"]).read_bytes()
        expected = task8_hashes.get(frozen["path"], frozen["sha256"])
        assert sha256(data).hexdigest() == expected
