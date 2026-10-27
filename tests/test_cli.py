from __future__ import annotations

import json
from pathlib import Path

import pytest

from clean_studio.agents.codex import CodexClient
from clean_studio.cli import build_real_services, main
from clean_studio.pipeline import produce, status
from clean_studio.providers.asr import SenseVoiceASRProvider
from clean_studio.providers.h3 import H3Provider
from clean_studio.providers.image import ImageProvider
from clean_studio.providers.modelscope import ModelScopePublisher
from clean_studio.providers.modelscope_runtime import ModelScopeWorkerError
from clean_studio.providers.music import SoulTunerMusicProvider
from clean_studio.store import Store
from test_pipeline import Harness


@pytest.fixture()
def harness(tmp_path):
    return Harness(tmp_path)


def test_cli_produce_and_status_use_real_entrypoints(harness, capsys):
    exit_code = main(
        ["produce", str(harness.config_path), "--stop-after", "SOURCE_REVIEW"],
        services=harness.services,
    )
    assert exit_code == 0
    produced = json.loads(capsys.readouterr().out)
    assert produced["stage"] == "SOURCE_REVIEW"
    assert produced["status"] == "in_progress"

    before = Store.open_readonly(harness.config["database_path"]).load()
    assert main(["status", str(harness.config_path)]) == 0
    reported = json.loads(capsys.readouterr().out)
    after = Store.open_readonly(harness.config["database_path"]).load()
    assert reported["stage"] == "SOURCE_REVIEW"
    assert reported["valid_videos"] == 0 and reported["planned_videos"] == 1
    assert after["launches"] == before["launches"] == 1


def test_cli_status_rejects_produce_only_scope_flags(harness, capsys):
    assert main(["status", str(harness.config_path), "--only-shot", "shot_001"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_real_service_factory_constructs_all_task9_adapters(harness):
    config = dict(
        harness.config,
        source_archive="source.zip",
        material_dir="material",
        style_dir="style",
        models={
            "codex": {"model": "gpt-5.6-luna", "effort": "low"},
            "image": {"base_url": "http://image.invalid", "model": "gpt-image-2", "api_key_env": "UNUSED_IMAGE_KEY"},
            "h3": {"base_url": "http://h3.invalid", "model": "h3"},
            "asr": {"python": "/opt/sensevoice/bin/python", "model_dir": "/models/sensevoice"},
            "music": {"runtime": {"catalog": "/music/catalog.json"}, "search_root": str(harness.tmp_path / "music-search")},
            "eureka": harness.runtime,
            "modelscope": {"python": "/mnt/disk1/zengzhitao/ms_venv/bin/python"},
        },
        publication_dir=str(harness.tmp_path / "real-publication"),
    )
    services = build_real_services(
        config,
        root=harness.tmp_path,
        source=harness.services["source"],
        references=harness.services["references"],
    )

    assert isinstance(services["author"].client, CodexClient)
    assert isinstance(services["reviewer"].client, CodexClient)
    assert isinstance(services["translator"], CodexClient)
    assert isinstance(services["translation_reviewer"], CodexClient)
    assert services["translator"] is not services["translation_reviewer"]
    assert isinstance(services["image_provider"], ImageProvider)
    assert isinstance(services["h3_provider"], H3Provider)
    assert isinstance(services["asr_provider"], SenseVoiceASRProvider)
    assert isinstance(services["music_provider"], SoulTunerMusicProvider)
    assert services["eureka_runtime"] == harness.runtime
    assert isinstance(services["final_visual_client"], CodexClient)
    assert isinstance(services["final_audio_judge"], CodexClient)
    assert callable(services["video_reviewer"])
    assert callable(services["music_agent"])
    assert callable(services["repair_agent"])

    store = Store(harness.tmp_path / "factory.sqlite3")
    store.create_episode(harness.episode, {"publish_target": "owner/private-dataset"})
    publisher = services["publisher_factory"](store)
    assert isinstance(publisher, ModelScopePublisher)
    assert publisher.repository == "owner/private-dataset"
    from clean_studio.providers.modelscope_runtime import ModelScopeWorkerAPI

    assert isinstance(publisher.api, ModelScopeWorkerAPI)
    assert publisher.api.python == "/mnt/disk1/zengzhitao/ms_venv/bin/python"


def test_module_status_matches_cli_status(harness):
    produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    assert status(harness.config_path)["launches"] == 1


def test_status_accepts_episode_id_for_default_edition_path(harness, monkeypatch):
    monkeypatch.setenv("NOVEL_PROJECT_ROOT", str(harness.tmp_path))
    harness.config.pop("database_path")
    harness.config_path.write_text(json.dumps(harness.config), encoding="utf-8")
    produce(harness.config_path, services=harness.services, stop_after="AUTHORING")
    report = status("episode-1")
    assert report["episode_id"] == "episode-1"
    assert report["launches"] == 1


def test_submission_reconciler_queries_original_provider_only_with_task_id():
    from types import SimpleNamespace
    from clean_studio.cli import ProviderSubmissionReconciler
    from clean_studio.frozen.provider import ResultPurged
    from test_pipeline import FakeProvider

    images = FakeProvider(b"image")
    videos = FakeProvider(b"video")
    reconciler = ProviderSubmissionReconciler(images, videos)
    confirmed = reconciler(SimpleNamespace(kind="image", external_task_id="task-1"))
    assert confirmed == {"accepted": True, "task_id": "task-1"}
    assert images.query_calls == ["task-1"] and videos.query_calls == []

    images.query_errors = [ResultPurged("purged")]
    unknown = reconciler(SimpleNamespace(kind="image", external_task_id="task-1"))
    assert unknown is None
    without_id = reconciler(SimpleNamespace(kind="video", external_task_id=None))
    assert without_id is None and videos.query_calls == []


def test_modelscope_worker_adapter_matches_publisher_api_without_network(tmp_path):
    import subprocess
    from types import SimpleNamespace
    from clean_studio.providers.modelscope_runtime import ModelScopeWorkerAPI
    from clean_studio.providers.modelscope_worker import handle_request

    class FakeHub:
        def __init__(self):
            self.calls = []

        def _record(self, method, args, kwargs):
            self.calls.append((method, args, kwargs))

        def whoami(self):
            self._record("whoami", (), {})
            return SimpleNamespace(username="owner")

        def get_repo(self, *args):
            self._record("get_repo", args, {})
            return SimpleNamespace(
                owner="owner", name="data", repo_id="owner/data",
                visibility="private", private=True,
            )

        def repo_exists(self, *args):
            self._record("repo_exists", args, {})
            return True

        def create_repo(self, *args, **kwargs):
            self._record("create_repo", args, kwargs)
            return SimpleNamespace(
                owner="owner", name="data", repo_id="owner/data",
                visibility="private", private=True,
            )

        def list_repo_files(self, *args):
            self._record("list_repo_files", args, {})
            return [SimpleNamespace(path="episodes.json", size=12, sha256=None, type="blob")]

        def upload_folder(self, *args, **kwargs):
            self._record("upload_folder", args, kwargs)
            return {"committed": True}

        def download_file(self, *args, **kwargs):
            self._record("download_file", args, kwargs)
            return str(tmp_path / "downloaded" / args[-1])

    fake = FakeHub()

    def runner(command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        response = handle_request(request, api=fake)
        Path(command[-1]).write_text(json.dumps(response), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    api = ModelScopeWorkerAPI(python="unused-offline-python", runner=runner)
    assert api.whoami() == {"username": "owner"}
    assert api.get_repo("owner/data", "dataset")["repo_id"] == "owner/data"
    assert api.repo_exists("owner/data", "dataset") is True
    assert api.create_repo("owner/data", "dataset", visibility="private")["private"] is True
    assert api.list_repo_files("owner/data", "dataset")[0]["path"] == "episodes.json"
    api.upload_folder(
        "owner/data",
        "dataset",
        tmp_path,
        allow_patterns=["episodes.json"],
        commit_message="offline",
        max_workers=2,
        disable_tqdm=True,
        sync_remote_repo=False,
    )
    downloaded = api.download_file(
        "owner/data", "dataset", "episodes.json",
        local_dir=tmp_path / "downloaded", force=True,
    )
    assert downloaded == tmp_path / "downloaded" / "episodes.json"
    assert fake.calls == [
        ("whoami", (), {}),
        ("get_repo", ("owner/data", "dataset"), {}),
        ("repo_exists", ("owner/data", "dataset"), {}),
        ("create_repo", ("owner/data", "dataset"), {"visibility": "private"}),
        ("list_repo_files", ("owner/data", "dataset"), {}),
        ("upload_folder", ("owner/data", "dataset", str(tmp_path)), {
            "allow_patterns": ["episodes.json"],
            "commit_message": "offline",
            "max_workers": 2,
            "disable_tqdm": True,
            "sync_remote_repo": False,
        }),
        ("download_file", ("owner/data", "dataset", "episodes.json"), {
            "local_dir": str(tmp_path / "downloaded"), "force": True,
        }),
    ]


@pytest.mark.parametrize(
    "error,expected",
    [
        (FileNotFoundError("missing remote object"), FileNotFoundError),
        (ValueError("invalid SDK response"), ValueError),
        (PermissionError("credential rejected"), PermissionError),
        (ConnectionError("response lost"), ConnectionError),
        (RuntimeError("unknown SDK failure"), ModelScopeWorkerError),
    ],
)
def test_modelscope_worker_receipt_error_types_without_network(error, expected):
    import subprocess
    from clean_studio.providers.modelscope_runtime import ModelScopeWorkerAPI
    from clean_studio.providers.modelscope_worker import handle_request

    class BrokenHub:
        def whoami(self):
            raise error

    fake = BrokenHub()

    def runner(command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        response = handle_request(request, api=fake)
        Path(command[-1]).write_text(json.dumps(response), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    api = ModelScopeWorkerAPI(python="unused-offline-python", runner=runner)
    with pytest.raises(expected) as caught:
        api.whoami()
    assert type(caught.value) is expected


def test_supported_modelscope_python_imports_and_constructs_without_network(tmp_path):
    import subprocess
    from clean_studio.providers.modelscope_worker import worker_path

    python = Path("/mnt/disk1/zengzhitao/ms_venv/bin/python")
    assert python.is_file()
    request = tmp_path / "request.json"
    response = tmp_path / "response.json"
    request.write_text('{"action":"construct"}', encoding="utf-8")
    done = subprocess.run(
        [str(python), str(worker_path()), str(request), str(response)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-500:]
    receipt = json.loads(response.read_text(encoding="utf-8"))
    assert receipt["ok"] is True
    assert receipt["result"] == {
        "backend": "modelscope_hub",
        "constructed": True,
    }


def test_media_and_render_provenance_matches_current_bytes():
    from hashlib import sha256

    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / "provenance/adapters.json").read_text(encoding="utf-8"))
    recorded = {row["path"]: row["sha256"] for row in provenance["frozen_files"]}
    for relative in ("src/clean_studio/media.py", "src/clean_studio/frozen/render.py"):
        assert sha256((root / relative).read_bytes()).hexdigest() == recorded[relative]


def test_dotenv_parser_preserves_formal_syntax_and_existing_environment(tmp_path):
    from clean_studio.runtime_env import load_dotenv

    private = tmp_path / "private.env"
    private.write_text(
        "\n".join(
            [
                "# ignored comment",
                "export DOUBLE = \"two words # literal\" # outside comment",
                "SINGLE='single # literal' # outside comment",
                "export UNQUOTED=abc#literal   # outside comment",
                "URL=https://example.invalid/path#fragment",
                "EMPTY=\"\" # outside comment",
                "export KEEP=replacement",
                "not an assignment",
            ]
        ),
        encoding="utf-8",
    )
    environment = {"KEEP": "existing"}
    load_dotenv(private, environment=environment)
    load_dotenv(tmp_path / "missing.env", environment=environment)

    assert environment == {
        "KEEP": "existing",
        "DOUBLE": "two words # literal",
        "SINGLE": "single # literal",
        "UNQUOTED": "abc#literal",
        "URL": "https://example.invalid/path#fragment",
        "EMPTY": "",
    }


def test_cli_loads_configured_dotenv_before_real_factory(tmp_path, monkeypatch, capsys):
    import clean_studio.cli as module

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"dotenv_path": "private/runtime.env"}), encoding="utf-8"
    )
    monkeypatch.setenv("NOVEL_PROJECT_ROOT", str(tmp_path))
    events = []
    services = {"offline": True}
    monkeypatch.setattr(
        module,
        "load_dotenv",
        lambda path: events.append(("dotenv", Path(path))),
    )

    def factory(config, *, root=None):
        events.append(("factory", Path(root)))
        return services

    def fake_produce(path, **kwargs):
        events.append(("produce", kwargs["services"]))
        from types import SimpleNamespace

        return SimpleNamespace(
            status="in_progress",
            to_dict=lambda: {"status": "in_progress"},
        )

    monkeypatch.setattr(module, "build_real_services", factory)
    monkeypatch.setattr(module, "produce", fake_produce)

    assert main(["produce", str(config_path)]) == 0
    assert events == [
        ("dotenv", (tmp_path / "private/runtime.env").resolve()),
        ("factory", tmp_path.resolve()),
        ("produce", services),
    ]
    assert json.loads(capsys.readouterr().out) == {"status": "in_progress"}


@pytest.mark.parametrize(
    "filename",
    ["author-validation.json", "frozen-config.json", "media-validation.json"],
)
def test_real_configs_construct_with_public_llm_mapping(
    filename, harness, monkeypatch
):
    from clean_studio.store import validate_frozen_config

    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / ".runtime/clean-w10-20261026" / filename).read_text(encoding="utf-8")
    )
    config["database_path"] = str(harness.tmp_path / f"{filename}.sqlite3")
    config["publication_dir"] = str(harness.tmp_path / "publication")
    monkeypatch.setenv("PHANROUTER_IMAGE_API_KEY", "offline-image-key")
    monkeypatch.setenv("PHANTHY_API_KEY", "offline-llm-key")

    validate_frozen_config(config)
    services = build_real_services(
        config,
        root=root,
        source=harness.services["source"],
        references=harness.services["references"],
    )

    llm = services["music_provider"].config["llm"]
    assert llm == {
        "base_url": "http://127.0.0.1:8789/v1",
        "model": "deepseek-v4.1-flash-1017",
        "api_key_env": "PHANTHY_API_KEY",
    }
    assert Path(config["dotenv_path"]).is_absolute()
    serialized = json.dumps(config)
    assert "offline-image-key" not in serialized
    assert "offline-llm-key" not in serialized
    services["image_provider"].client.close()
