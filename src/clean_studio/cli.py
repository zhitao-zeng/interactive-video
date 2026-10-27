from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from clean_studio.acceptance import AcceptedExport, accept_export
from clean_studio.agents.authoring import Author
from clean_studio.agents.codex import CodexClient, HttpJsonClient
from clean_studio.agents.review import Reviewer
from clean_studio.frozen.llm import image_part
from clean_studio.frozen.music import file_sha256
from clean_studio.frozen.provider import ResultPurged
from clean_studio.pipeline import produce, status
from clean_studio.providers.asr import SenseVoiceASRProvider
from clean_studio.providers.h3 import H3Provider
from clean_studio.providers.image import ImageProvider
from clean_studio.providers.modelscope import ModelScopePublisher
from clean_studio.providers.modelscope_runtime import (
    DEFAULT_MODELSCOPE_PYTHON,
    ModelScopeWorkerAPI,
)
from clean_studio.providers.music import (
    AUDITION_SCOPE,
    SoulTunerMusicProvider,
    adopt_music,
)
from clean_studio.runtime_env import load_dotenv
from clean_studio.source import build_reference_manifest, load_source


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _codex(config, root):
    llm = config.get("llm")
    if isinstance(llm, dict) and llm.get("base_url"):
        import os

        key = os.environ.get(llm.get("api_key_env", ""), "")
        return HttpJsonClient(
            model=llm.get("model", "kimi-k3"),
            base_url=llm["base_url"],
            api_key=key,
            reasoning=llm.get("reasoning", "low"),
        )
    return CodexClient(
        root=root,
        model=config.get("model", "gpt-5.6-luna"),
        effort=config.get("effort", "low"),
        sandbox=config.get("sandbox"),
    )


class ProviderSubmissionReconciler:
    def __init__(self, image_provider, h3_provider):
        self.providers = {"image": image_provider, "video": h3_provider}

    def __call__(self, operation):
        task_id = operation.external_task_id
        if not task_id:
            return None
        provider = self.providers.get(operation.kind)
        if provider is None:
            raise ValueError(f"unsupported submission kind: {operation.kind}")
        try:
            result = provider.query(task_id)
        except ResultPurged:
            return None
        if result.status not in {"pending", "succeeded", "failed"}:
            return None
        return {"accepted": True, "task_id": task_id}


class CodexRepairAgent:
    def __init__(self, client):
        self.client = client

    def __call__(self, episode, findings, *, grant):
        return self.repair(episode, findings, grant=grant)

    def repair(self, episode, findings, *, grant):
        schema = {
            "type": "object",
            "properties": {
                "patch": {"type": "object"},
                "reason": {"type": "string"},
            },
            "required": ["patch", "reason"],
            "additionalProperties": False,
        }
        payload = {
            "instruction": (
                "Only revise the granted author-owned shot fields. Return a minimal patch; "
                "apply success is not review acceptance and no stage may be selected."
            ),
            "episode": asdict(episode),
            "confirmed_findings": list(findings),
            "grant": grant,
        }
        raw = self.client.ask_json(
            [{"type": "text", "text": _canonical(payload)}],
            schema,
            name="clean_exception_content_repair",
            max_tokens=8000,
            timeout=1200,
            retry_truncated=False,
        )
        if not isinstance(raw, Mapping) or not isinstance(raw.get("patch"), Mapping):
            raise ValueError("exception repair returned no typed patch")
        return {"ok": True, "patch": raw["patch"], "grant": grant}


class CodexVideoReviewer:
    def __init__(self, client, work_dir, *, ffmpeg="ffmpeg", runner=None):
        self.client = client
        self.work_dir = Path(work_dir)
        self.ffmpeg = ffmpeg
        self.runner = runner or subprocess.run

    def __call__(self, video, request, technical, speech):
        video = Path(video)
        duration = float(technical.get("duration_seconds") or request.duration_seconds)
        directory = self.work_dir / request.shot_id / sha256(video.read_bytes()).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        frames = []
        for index, second in enumerate((0.15, duration / 2, max(0.0, duration - 0.20))):
            second = min(max(0.0, second), max(0.0, duration - 0.08))
            path = directory / f"frame-{index}.jpg"
            self.runner(
                [
                    self.ffmpeg,
                    "-nostdin",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    str(second),
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=432:768:force_original_aspect_ratio=decrease",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            if not path.is_file():
                raise RuntimeError("video review frame extraction failed")
            frames.append(path)
        schema = {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "problem": {"type": "string"},
                            "evidence": {"type": "string"},
                            "source_basis": {"type": "string"},
                        },
                        "required": ["problem", "evidence", "source_basis"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["findings"],
            "additionalProperties": False,
        }
        facts = {
            "instruction": (
                "Review only this current shot's sampled frames against the H3 request, source"
                " action/end state, decode and ASR. Watchability bar — report only what makes a"
                " viewer stumble: the person becomes someone else (gender/age), the core action"
                " runs opposite to the request or a key plot event is missing, frames are broken"
                " / black / severely distorted, or speech clearly diverges from the lines."
                " Outfit color, styling, composition and minor wording differences are NOT"
                " findings. This is not final-film visual or complete-audio acceptance."
            ),
            "shot_id": request.shot_id,
            "request": request.prompt,
            "source_segments": list(request.source_segments),
            "source_action": request.source_action,
            "source_end_state": request.source_end_state,
            "technical": dict(technical),
            "asr": dict(speech),
        }
        parts = [{"type": "text", "text": _canonical(facts)}]
        parts.extend(image_part(path, 768) for path in frames)
        raw = self.client.ask_json(
            parts,
            schema,
            name="clean_current_shot_video_review",
            max_tokens=3000,
            timeout=300,
            retry_truncated=False,
        )
        if not isinstance(raw, Mapping) or not isinstance(raw.get("findings"), list):
            raise ValueError("video reviewer returned malformed findings")
        findings = raw["findings"]
        return {
            "verdict": "rejected" if findings else "accepted",
            "video_sha256": sha256(video.read_bytes()).hexdigest(),
            "findings": findings,
            "frames": [str(path) for path in frames],
            "evidence_scope": "current_shot_sampled_visual_technical_asr",
            "final_audio_accepted": False,
        }


class EurekaMusicAgent:
    def __init__(self, client, runtime, work_dir, *, runner=None, ffmpeg="ffmpeg"):
        self.client = client
        self.runtime = dict(runtime)
        self.work_dir = Path(work_dir)
        self.runner = runner or subprocess.run
        self.ffmpeg = ffmpeg

    def __call__(self, candidates, cue):
        return self.select(candidates, cue)

    def _audition(self, candidate, cue):
        cue_seconds = cue.end_seconds - cue.start_seconds
        seconds = min(float(candidate.duration_seconds), float(cue_seconds))
        if seconds <= 0:
            return None
        directory = self.work_dir / candidate.candidate_id
        directory.mkdir(parents=True, exist_ok=True)
        selected_audio = directory / "selected.wav"
        self.runner(
            [
                self.ffmpeg,
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                candidate.audio_path,
                "-t",
                f"{seconds:.6f}",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(selected_audio),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        raw_path = directory / "eureka.json"
        raw_path.unlink(missing_ok=True)
        job = directory / "job.json"
        windows = [{"start": 0.0, "end": seconds}]
        job.write_text(
            _canonical(
                {
                    "action": "audio",
                    "runtime": self.runtime,
                    "jobs": [
                        {
                            "id": "music-audition",
                            "audio": str(selected_audio),
                            "output": str(raw_path),
                            "question": (
                                "只听这段实际候选音乐，报告音乐情绪、声景、人声和可能遮挡对白的因素。"
                                "不得把检索信息当试听结论，也不得推断未见画面。"
                            ),
                            "source_windows": windows,
                            "scope": AUDITION_SCOPE,
                            "max_new_tokens": 240,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        worker = Path(__file__).resolve().parent / "frozen/eureka_worker.py"
        done = self.runner(
            [self.runtime["python"], str(worker), str(job)],
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
        if done.returncode or not raw_path.is_file():
            raise RuntimeError("Eureka music audition failed; no candidate was adopted")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        response = raw.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ValueError("Eureka music audition returned no actual audio evidence")
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["accepted", "rejected"]},
                "reason": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "reason", "limitations"],
            "additionalProperties": False,
        }
        judgment = self.client.ask_json(
            [
                {
                    "type": "text",
                    "text": (
                        "你是背景音乐采用判官。依据实际音频听审结论判断这首本地曲目能否"
                        "作为该 cue 的背景音乐。判准：(1) 不得有与对白冲突的强人声或歌词"
                        "（器乐优先）；(2) 情绪与节奏气质贴近 cue.mood；(3) 时长足以覆盖"
                        "cue 区间。cue.soundscape 中的海浪、脚步、风、鸥鸣等环境声与拟音由"
                        "视频原生声和后期声景承担，不是背景音乐的否决条件——不得仅因音乐"
                        "里缺少这些声景元素而拒绝；轻度风格差异不影响观看时不拒。"
                        "只返回实际听审能支持的理由，不臆测未听内容。"
                    ),
                },
                {
                    "type": "text",
                    "text": _canonical(
                        {
                            "cue": asdict(cue),
                            "candidate": {
                                "candidate_id": candidate.candidate_id,
                                "audio_sha256": candidate.audio_sha256,
                                "limitations": list(candidate.limitations),
                            },
                            "actual_audio_response": response,
                            "listened_windows": windows,
                        }
                    ),
                }
            ],
            schema,
            name="clean_music_exception_audition",
            max_tokens=2000,
            timeout=300,
            retry_truncated=False,
        )
        if judgment.get("verdict") not in {"accepted", "rejected"}:
            raise ValueError("music content Agent returned no valid verdict")
        limitations = judgment.get("limitations")
        if not isinstance(limitations, list) or not limitations:
            raise ValueError("music content Agent must retain explicit limitations")
        receipt = {
            "status": "completed",
            "candidate_id": candidate.candidate_id,
            "cue": asdict(cue),
            "actual_audio": candidate.audio_path,
            "audio_sha256": candidate.audio_sha256,
            "verdict": judgment["verdict"],
            "actual_listening": True,
            "evidence_scope": AUDITION_SCOPE,
            "agent_role": "exception_agent",
            "agent_id": "codex-eureka-music-exception",
            "listened_windows": windows,
            "response": _canonical({"eureka": response, "judgment": judgment}),
        }
        receipt_path = directory / "audition.json"
        receipt_path.write_text(_canonical(receipt), encoding="utf-8")
        return {
            "candidate_id": candidate.candidate_id,
            "cue": asdict(cue),
            "audio_sha256": candidate.audio_sha256,
            "verdict": judgment["verdict"],
            "actual_listening": True,
            "evidence_scope": AUDITION_SCOPE,
            "agent_role": "exception_agent",
            "agent_id": "codex-eureka-music-exception",
            "reason": judgment["reason"],
            "limitations": limitations,
            "listened_windows": windows,
            "receipt_path": str(receipt_path),
            "receipt_sha256": file_sha256(receipt_path),
            "source_seconds": seconds,
            "loop": seconds + 0.02 < cue_seconds,
        }

    def select(self, candidates, cue):
        for candidate in candidates:
            judgment = self._audition(candidate, cue)
            if judgment["verdict"] != "accepted":
                continue
            source_seconds = judgment.pop("source_seconds")
            loop = judgment.pop("loop")
            return adopt_music(
                candidate,
                judgment,
                source_start_seconds=0,
                source_seconds=source_seconds,
                loop=loop,
            )
        return None


class ScopedModelScopePublisher(ModelScopePublisher):
    def __init__(self, *args, only_shot, **kwargs):
        super().__init__(*args, **kwargs)
        self.only_shot = only_shot
        self._accepted = None

    def _scoped(self, accepted):
        if not isinstance(accepted, AcceptedExport):
            raise ValueError("scoped publishing requires an accepted validation export")
        current = accept_export(
            accepted.export,
            accepted.visual,
            accepted.audio,
            accepted.technical,
            store=None,
        )
        return replace(
            current,
            episode_id=f"{current.episode_id}-validation-{self.only_shot}",
        )

    def _current(self, accepted):
        return self._scoped(accepted)

    def _assert_current(self, current):
        self._scoped(current)

    def publish(self, accepted):
        self._accepted = accepted
        return super().publish(accepted)

    def _current_receipt_errors(self, receipt):
        errors = []
        accepted = self._scoped(self._accepted)
        if receipt.input_hash != accepted.bundle_hash:
            errors.append("scoped upload is not bound to the accepted validation export")
        try:
            if file_sha256(receipt.video_path) != receipt.video_sha256:
                errors.append("local validation video SHA-256 changed")
            if file_sha256(receipt.cover_path) != receipt.cover_sha256:
                errors.append("local validation cover SHA-256 changed")
        except OSError as error:
            errors.append(f"scoped accepted source is unavailable: {error}")
        return errors


def build_real_services(
    config,
    *,
    root=None,
    source=None,
    references=None,
):
    root = Path(root or os.environ.get("NOVEL_PROJECT_ROOT") or Path.cwd()).resolve()
    models = config.get("models") or {}
    codex_config = models.get("codex") or {
        "model": config.get("model", "gpt-5.6-luna"),
        "effort": config.get("effort", "low"),
    }
    if models.get("llm"):
        codex_config = {**codex_config, "llm": models["llm"]}
    if source is None:
        source = load_source(_resolve(root, config["source_archive"]))
    if references is None:
        references = build_reference_manifest(
            source,
            _resolve(root, config["material_dir"]),
            _resolve(root, config["style_dir"]),
            config_path=_resolve(root, config["input_config"]) if config.get("input_config") else None,
        )
    author_client = _codex(codex_config, root)
    review_client = _codex(codex_config, root)
    translator = _codex(codex_config, root)
    translation_reviewer = _codex(codex_config, root)
    image_config = models.get("image") or {}
    h3_config = models.get("h3") or {}
    asr_config = models.get("asr") or {}
    music_config = models.get("music") or {}
    modelscope_config = models.get("modelscope") or {}
    eureka_runtime = dict(models.get("eureka") or config.get("eureka") or {})
    image_key = os.environ.get(image_config["api_key_env"]) if image_config.get("api_key_env") else None
    database_path = config.get("database_path") or (
        Path(".runtime") / (config.get("episode_id") or source.video_id) / "studio.sqlite3"
    )
    publication_dir = _resolve(
        root,
        config.get("publication_dir") or Path(database_path).parent / "publication",
    )
    publisher_arguments = {
        "repository": config.get("publish_target"),
        "work_dir": publication_dir,
        "expected_account": config.get("publish_account"),
        "api": ModelScopeWorkerAPI(
            modelscope_config.get("python")
            or config.get("modelscope_python")
            or DEFAULT_MODELSCOPE_PYTHON
        ),
    }
    image_provider = ImageProvider(
        image_config["base_url"],
        model=image_config["model"],
        api_key=image_key,
    )
    h3_provider = H3Provider(h3_config["base_url"], model=h3_config["model"])
    return {
        "source": source,
        "references": references,
        "author": Author(source, references, author_client),
        "reviewer": Reviewer(review_client),
        "translator": translator,
        "translation_reviewer": translation_reviewer,
        "translation_config": codex_config,
        "image_provider": image_provider,
        "h3_provider": h3_provider,
        "submission_reconciler": ProviderSubmissionReconciler(image_provider, h3_provider),
        "asr_provider": SenseVoiceASRProvider(
            asr_config["python"],
            asr_config["model_dir"],
            helper_path=asr_config.get("helper_path"),
            threads=asr_config.get("threads", 4),
        ),
        "frame_review_client": _codex(codex_config, root),
        "video_reviewer": CodexVideoReviewer(
            _codex(codex_config, root), root / ".runtime/clean-video-review"
        ),
        "music_provider": SoulTunerMusicProvider(
            music_config.get("runtime") or {},
            search_root=_resolve(root, music_config.get("search_root") or ".runtime/clean-music-searches"),
        ),
        "music_agent": EurekaMusicAgent(
            _codex(codex_config, root),
            eureka_runtime,
            root / ".runtime/clean-music-auditions",
        ),
        "repair_agent": CodexRepairAgent(_codex(codex_config, root)),
        "final_visual_client": _codex(codex_config, root),
        "final_audio_judge": _codex(codex_config, root),
        "eureka_runtime": eureka_runtime,
        "font_path": config.get("font_path"),
        "ffmpeg_preset": config.get("ffmpeg_preset", "veryfast"),
        "publisher_factory": lambda store: ModelScopePublisher(store=store, **publisher_arguments),
        "validation_publisher_factory": lambda store, only_shot: ScopedModelScopePublisher(
            store=store,
            work_dir=publication_dir / f"validation-{only_shot}",
            only_shot=only_shot,
            **{key: value for key, value in publisher_arguments.items() if key != "work_dir"},
        ),
    }


def _parser():
    parser = argparse.ArgumentParser(prog="clean-studio")
    commands = parser.add_subparsers(dest="command", required=True)
    produce_parser = commands.add_parser("produce")
    produce_parser.add_argument("config")
    produce_parser.add_argument("--stop-after")
    produce_parser.add_argument("--only-shot")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("config")
    status_parser.add_argument("--episode-id")
    return parser


def main(argv=None, *, services=None):
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if arguments.command == "status":
        report = status(arguments.config, episode_id=arguments.episode_id)
        print(_canonical(report))
        return 0
    config_path = Path(arguments.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if services is None:
        root = Path(os.environ.get("NOVEL_PROJECT_ROOT") or Path.cwd()).resolve()
        if config.get("dotenv_path"):
            load_dotenv(_resolve(root, config["dotenv_path"]))
        services = build_real_services(config, root=root)
    result = produce(
        config_path,
        services=services,
        stop_after=arguments.stop_after,
        only_shot=arguments.only_shot,
    )
    print(_canonical(result.to_dict()))
    return int(result.status == "blocked")


if __name__ == "__main__":
    raise SystemExit(main())
