"""Offline vertical acceptance through the existing Episode driver and host tools.

Only the external Agent, image/video suppliers, and independent reviewer are
deterministic fakes. ``run_episode``, EpisodeBridge, EpisodeTools, readiness,
the thin renderer, assembly, delivery, receipts, and resume state stay real.
"""
from __future__ import annotations

import json
import copy
import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from PIL import Image, ImageChops

from novel_manga.application.agents.episode import client, turns
from novel_manga.application.agents.episode.flow import run_episode
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.agents.sandbox import Attempt
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.review.contracts import REVIEW_POLICY
from novel_manga.review.storage import take_identity
from novel_manga.util import atomic_write_json, read_json
from support.episode_agent import CATALOG, SOURCES, episode
from novel_manga.application.agents.episode.media_operations import MediaOperations
from novel_manga.application.agents.episode import first_frames
import novel_manga.application.agents.episode.postproduction as postproduction
from novel_manga.application.repair.flow import rebuild_clips as real_rebuild_clips
from novel_manga.application.repair import history
from novel_manga.application.production.episode_state import FILE as STATE_EVENTS


def _write_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=16000:duration=2", "-c:a", "pcm_s16le", str(path),
    ], check=True, capture_output=True)


def _make_case(tmp_path, monkeypatch):
    """Build the same prepared-script context used by current episode tests."""
    ctx, _, _ = __import__("support.episode_existing", fromlist=["prepared"]).prepared(tmp_path, monkeypatch)
    ctx.asset_snapshot_dir = ctx.run_dir.parent / "assets-snapshot"
    ctx.target_seconds = (15, 15)
    # Undo only the broad test fixture's two fake host endpoints. Keep its
    # immutable script/config construction and run the real host services.
    # The fixture replaced the class method, so recover the production method
    # imported before fixture setup below.
    monkeypatch.setattr(MediaOperations, "runner", _REAL_RUNNER)
    monkeypatch.setattr("novel_manga.application.review.execution.review_saved", _review_saved)

    script = read_json(ctx.episode_dir / "chapter_script.json")
    plan = read_json(ctx.episode_dir / "clip_plan.json")
    script["shots"] = script["shots"][:1]
    plan["clips"] = plan["clips"][:1]
    clip = plan["clips"][0]
    clip["request_seconds"] = 15
    clip["shot_indexes"] = [1]
    atomic_write_json(ctx.episode_dir / "chapter_script.json", script)
    atomic_write_json(ctx.episode_dir / "clip_plan.json", plan)
    requests = read_json(ctx.work_dir / "output/requests.json")
    requests["prompts"] = {clip["clip_id"]: requests["prompts"][clip["clip_id"]]}
    atomic_write_json(ctx.work_dir / "output/requests.json", requests)
    atomic_write_json(ctx.source_episode_dir / "chapter_script.json", script)
    atomic_write_json(ctx.source_episode_dir / "clip_plan.json", plan)
    atomic_write_json(ctx.episode_dir / "segments.json", SOURCES)
    atomic_write_json(ctx.source_episode_dir / "segments.json", SOURCES)
    atomic_write_json(ctx.run_dir / "catalog.json", CATALOG)
    atomic_write_json(ctx.novel_dir / "story_bible.json", {
        "novel_title": "offline workflow", "genre": "generic", "visual_style": "2d",
        "palette": "blue", "style_fingerprint": "offline-test",
        "characters": [
            {"name": "甲", "appearance": "黑发青年", "wardrobe": "青色外衣"},
            {"name": "乙", "appearance": "灰发青年", "wardrobe": "灰衣"}],
        "locations": ["房间"],
    })
    atomic_write_json(ctx.novel_dir / "bible_aliases.json", {})
    profile = read_json(ctx.novel_dir / "profile.json", {})
    profile.update(style="2d", tier="quality", h3_input_mode="first-frame",
                   frame="9:16", resolution="720p")
    atomic_write_json(ctx.novel_dir / "profile.json", profile)
    atomic_write_json(ctx.source_novel_dir / "profile.json", profile)

    # Real, tiny reference assets satisfy the actual first-frame/render gates.
    for root in (ctx.novel_dir, ctx.source_novel_dir):
        for name in ("a.jpeg", "b.jpeg", "room.jpeg"):
            path = root / "cards" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32), (90, 120, 150)).save(path, "JPEG")
        _write_audio(root / "voices/a.wav")
        for relative in (
            "characters/character_001/turnaround.jpeg",
            "characters/character_002/turnaround.jpeg",
            "locations/location_001/establishing.jpeg",
        ):
            path = root / "series_assets" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32), (80, 100, 120)).save(path, "JPEG")

    # A pre-existing session suppresses startup's automatic render; the fake
    # Agent then has to use the real bridge in the same order as the workflow.
    atomic_write_json(ctx.work_dir / "episode-session.json", {"session_id": "offline-workflow-session"})
    frame_calls = []

    def build_frame(_ctx, _row, path):
        frame_calls.append(path)
        Image.new("RGB", (64, 112), (60, 85, 110)).save(path, "PNG")

    monkeypatch.setattr(first_frames, "build", build_frame)
    monkeypatch.setattr(first_frames, "check", lambda *_: {
        "consistent": True, "findings": [], "problems": []})
    def fake_asset_image(_self, _prompt, output, **_kwargs):
        from novel_manga.providers.base import ImageResult
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 48), (75, 95, 115)).save(output, "JPEG")
        return ImageResult(path=output, request_current=True)

    monkeypatch.setattr("novel_manga.providers.phanrouter.PhanRouterMediaProvider.create_image",
                        fake_asset_image)
    monkeypatch.setenv("PHANROUTER_API_KEY", "offline-provider-token")
    monkeypatch.setenv("PHANROUTER_IMAGE_API_KEY", "offline-image-token")
    monkeypatch.setattr("novel_manga.application.agents.episode.local_preparation.audit", lambda *_a, **_k: {
        "source_readable": True, "issues": []})

    def translate_request(clip, **_kwargs):
        from novel_manga.application.profiles import h3_stamp
        clip["prompt_h3_of"] = h3_stamp(clip, "")
        return True

    monkeypatch.setattr("novel_manga.application.rendering.h3.convert", translate_request)

    # Exercise preparation before the opening is accepted. This also registers
    # the exact card files the later render will use.
    from novel_manga.application.agents.episode.inputs import prepare_inputs
    prepare_inputs(ctx)
    plan = read_json(ctx.episode_dir / "clip_plan.json")
    clip = plan["clips"][0]
    atomic_write_json(ctx.source_episode_dir / "chapter_script.json",
                      read_json(ctx.episode_dir / "chapter_script.json"))
    atomic_write_json(ctx.source_episode_dir / "clip_plan.json", plan)
    tools = EpisodeTools(ctx)
    tools.media.runner().build_assets(clips=[clip])

    # Seed a genuinely accepted opening. The first Agent apply changes the
    # source camera fact, so readiness must reject this old frame and ask for
    # a new candidate before render.
    opening_row = {
        "clip_id": clip["clip_id"],
        "image_prompt": "甲在房间门口回头，保持原文服饰与室内关系。",
        "image_references": [next(row["path"] for row in read_json(ctx.run_dir / "catalog.json", [])
                                   if row.get("role") == "character" and row.get("name") == "甲")],
        "extra_references": [],
        "visible_characters": ["甲"], "source_quote": "甲走进房间",
        "h3": {"shots": ["The visible subject turns toward the entrance and pauses. <DIALOGUE_1>"],
               "soundscape": "Quiet room tone."},
    }
    atomic_write_json(ctx.work_dir / "output/first-frames.json", {"frames": [opening_row]})
    first = EpisodeTools(ctx).first_frames({"file": "output/first-frames.json"})
    assert first["ok"] and first["clips"][clip["clip_id"]]["ok"], first
    accepted_plan = read_json(ctx.episode_dir / "clip_plan.json")
    clip = accepted_plan["clips"][0]
    requests = read_json(ctx.work_dir / "output/requests.json")
    requests["prompts"][clip["clip_id"]] = clip["prompt_h3"]
    atomic_write_json(ctx.work_dir / "output/requests.json", requests)
    source_script = read_json(ctx.source_episode_dir / "chapter_script.json")
    source_plan = read_json(ctx.source_episode_dir / "clip_plan.json")
    source_plan["clips"] = accepted_plan["clips"]
    atomic_write_json(ctx.source_episode_dir / "chapter_script.json", source_script)
    atomic_write_json(ctx.source_episode_dir / "clip_plan.json", source_plan)

    repair = {"decisions": [{
        "clip_id": clip["clip_id"], "action": "input",
        "reason": "原镜头没有拍清甲从入口进入，调整机位以呈现原文动作。",
        "source_quote": "甲走进房间",
        "shots": [{"index": 1, "camera": "从门内侧肩后固定拍向入口，等甲走入画面后停住。"}],
    }]}
    atomic_write_json(ctx.work_dir / "output/repair.json", repair)
    from novel_manga.application.agents.episode import input_repair
    source_review_calls = []

    def source_review(_source, _quote, _patches, _phases, _locations=None,
                      scoped_shots=None, **_kwargs):
        source_review_calls.append(scoped_shots)
        return {"consistent": True, "problems": []}

    monkeypatch.setattr(input_repair, "source_check", source_review)
    monkeypatch.setattr("novel_manga.application.repair.flow.rebuild_clips", real_rebuild_clips)

    # Fake the provider boundary, retaining real generation receipts, request
    # compilation, budget accounting, take selection, and FFmpeg assembly.
    generated = []

    def fake_video_submit(render_ctx, clip, request, output, references):
        del render_ctx, clip, request, references
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=1080x1920:r=25:d=15", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=15", "-t", "15",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(output),
        ], check=True, capture_output=True)
        atomic_write_json(output.with_suffix(output.suffix + ".task.json"),
                          {"task_id": f"offline-{len(generated) + 1}", "status": "completed"})
        generated.append(output)

    monkeypatch.setattr("novel_manga.media.generation.submit", fake_video_submit)
    # Keep real audio extraction, chunk detection, speech policy and saved
    # native.wav evidence. Replace only the external ASR helper response.
    import subprocess as subprocess_module
    actual_run = subprocess_module.run

    def fake_asr(command, *args, **kwargs):
        if (isinstance(command, (list, tuple)) and len(command) > 1
                and str(command[1]).endswith("thin_asr_segments.py")):
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"segments": [{
                "start": 0.2, "end": 0.8, "hypothesis": "你来啦。"}]}, ensure_ascii=False))
            return subprocess_module.CompletedProcess(command, 0, "", "")
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess_module, "run", fake_asr)
    return ctx, clip, opening_row, frame_calls, generated, source_review_calls


_REAL_RUNNER = MediaOperations.runner


def _review_saved(directory, *, fresh):
    del fresh
    plan = read_json(directory / "clip_plan.json")
    report = read_json(directory / "thin_media_report.json")
    rows = {}
    for result in report.get("clips", []):
        selected = result.get("selected") or {}
        video = selected.get("video")
        if not video:
            continue
        path = Path(video)
        rows[result["clip_id"]] = {
            "video": str(path), "take": take_identity(path), "severity": "pass",
        }
    review = {"policy": REVIEW_POLICY, "review_mode": "classic", "clips": rows, "feedback": {}}
    atomic_write_json(directory / "episode_review.json", review)
    return review


def test_run_episode_drives_check_apply_opening_render_review_assembly_and_finish(
        tmp_path, monkeypatch):
    """One real driver run crosses the host bridge and closes an offline episode."""
    monkeypatch.setattr(httpx.Client, "send",
                        lambda *a, **k: pytest.fail("offline workflow attempted an HTTP API call"))
    monkeypatch.setattr(httpx.AsyncClient, "send",
                        lambda *a, **k: pytest.fail("offline workflow attempted an async HTTP API call"))
    ctx, clip, opening_row, frame_calls, generated, source_review_calls = _make_case(tmp_path, monkeypatch)
    original_client_request = client.request
    calls = []

    def bridge_socket_path():
        # run_episode binds through its open work-directory fd so a deep
        # pytest temporary path stays below Linux's AF_UNIX path limit.
        for fd in os.listdir("/proc/self/fd"):
            descriptor = Path(f"/proc/self/fd/{fd}")
            try:
                if Path(os.readlink(descriptor)).resolve() == ctx.work_dir.resolve():
                    candidate = descriptor / "episode-tools.sock"
                    if candidate.is_socket():
                        return candidate
            except OSError:
                continue
        pytest.fail("run_episode bridge socket was not found through its directory fd")

    def fake_agent_turn(work_dir, prompt, *, resume=False, timeout=1200, config=None,
                        expected_output=None, enable_tools=False, project_root=None):
        del prompt, timeout, config, expected_output, enable_tools, project_root
        session_id = "offline-workflow-session"
        if not calls:
            checked = original_client_request("check", {"file": "output/requests.json"},
                                              socket_path=bridge_socket_path())
            assert checked.get("valid") is True
            repair_check = original_client_request("check", {"file": "output/repair.json"},
                                                   socket_path=bridge_socket_path())
            assert repair_check.get("valid") is True, repair_check
            answer = original_client_request("apply", {"file": "output/repair.json"},
                                             socket_path=bridge_socket_path())
            calls.append(("apply", answer))
        else:
            socket_path = bridge_socket_path()
            state = original_client_request("status", {}, socket_path=socket_path)
            action = ((state.get("next_action") or {}).get("action")
                      or (state.get("next_action") or {}).get("next_action"))
            if action == "first_frames":
                from novel_manga.application.repair import history
                assert history.generation_counts(ctx.episode_dir) == {}
                atomic_write_json(work_dir / "output/first-frames.json", {
                    "frames": [opening_row]})
                answer = original_client_request("first_frames", {"file": "output/first-frames.json"},
                                                 socket_path=socket_path)
            elif action == "render":
                answer = original_client_request("render", {}, socket_path=socket_path)
            elif action == "review":
                answer = original_client_request("review", {}, socket_path=socket_path)
            elif action == "finish":
                answer = original_client_request("finish", {}, socket_path=socket_path)
            else:
                pytest.fail(f"offline Agent received no actionable host transition: {state}")
            calls.append((action, answer))
        # The endpoint fake returns as if a normal Agent turn ended. The bridge
        # still owns/dispatches every accepted operation and the driver waits.
        from novel_manga.util import atomic_write_json as write_json
        write_json(work_dir / "episode-session.json", {"session_id": session_id})
        attempt_dir = ctx.run_dir / "offline-attempts" / str(len(calls))
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return Attempt(str(attempt_dir), attempt_dir, 0, 0, record={"session_id": session_id})

    monkeypatch.setattr(turns, "run_session", fake_agent_turn)
    monkeypatch.setenv("PHANROUTER_API_KEY", "offline-provider-token")
    monkeypatch.setenv("PHANROUTER_IMAGE_API_KEY", "offline-image-token")
    monkeypatch.setattr("novel_manga.application.agents.episode.lifecycle.resolve_agent_environment",
                        lambda _ctx, _config: {
                            "NOVEL_RENDER_INFLIGHT": "1", "NOVEL_REVIEW_MODE": "classic",
                            "QWEN38_LOCAL_BASE_URL": "http://offline.invalid/v1",
                            "QWEN38_LOCAL_MODEL": "offline", "QWEN38_LOCAL_API_KEY_VAR": "OFFLINE",
                            "SECOND_REVIEW_JUDGE": "ds41", "NOVEL_REVIEW_VIDEO_JUDGE": "ds41",
                        })
    config = {"backend": "codex", "provider": "offline", "base_url": "http://offline.invalid",
              "model": "offline", "key_var": "OFFLINE", "timeout_seconds": 600,
              "production_mode": "standard", "agent_invocations_per_slice": 8,
              "agent_slice_seconds": 60}

    result = run_episode(ctx, config=config, timeout=600)

    assert result["status"] == "finished" and result["passed"] is True, result
    assert [action for action, _ in calls[:2]] == ["apply", "first_frames"]
    assert any(action == "render" for action, _ in calls)
    assert len(frame_calls) == 2 and len(generated) == 1
    assert len(source_review_calls) == 1
    reviewed_shot = source_review_calls[0][0]
    assert reviewed_shot["before"]["camera"] != reviewed_shot["after"]["camera"]
    assert reviewed_shot["changed_fields"] == ["camera"]
    action_order = [action for action, _ in calls]
    assert action_order.index("first_frames") < action_order.index("render")
    report = read_json(ctx.episode_dir / "thin_media_report.json")
    assert report["assembly"]["final_video"] and Path(report["assembly"]["final_video"]).is_file()
    assert report["assembly"]["thin_passed"] is True
    state_events = [json.loads(line) for line in
                    (ctx.run_dir / STATE_EVENTS).read_text().splitlines()]
    assert any(row.get("stage") == "review_saved" for row in state_events)
    assert any(row.get("stage") == "acceptance_saved" and row.get("passed") is True
               for row in state_events)
    assert read_json(ctx.work_dir / "input/latest-finish.json")["passed"] is True
    final_movie = read_json(ctx.work_dir / "input/latest-finish.json")["movie"]
    streams = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", final_movie,
    ], check=True, capture_output=True, text=True).stdout)["streams"]
    assert any(stream.get("codec_type") == "audio" and stream.get("codec_name") == "aac"
               for stream in streams)
    assert read_json(ctx.run_dir / "agent-run.json")["attempts"]
    assert plan_fingerprint(read_json(ctx.episode_dir / "clip_plan.json")) == report["clip_plan_fingerprint"]
    current_clip = read_json(ctx.episode_dir / "clip_plan.json")["clips"][0]
    reuse_row = {**opening_row, "reuse_candidate": current_clip["first_frame"]["candidate"]}
    atomic_write_json(ctx.work_dir / "output/first-frames.json", {"frames": [reuse_row]})
    reused = EpisodeTools(ctx).first_frames({"file": "output/first-frames.json"})
    assert reused["ok"] and len(frame_calls) == 2
    assert EpisodeTools(ctx).status({})["generation_counts"] == {clip["clip_id"]: 1}


def test_run_episode_applies_authorized_cutaway_and_gates_interactive_audio_delivery(tmp_path, monkeypatch):
    """The existing driver renders two takes, then its real delivery path cuts pictures and gates audio."""
    monkeypatch.setattr(httpx.Client, "send",
                        lambda *a, **k: pytest.fail("offline workflow attempted an HTTP API call"))
    monkeypatch.setattr(httpx.AsyncClient, "send",
                        lambda *a, **k: pytest.fail("offline workflow attempted an async HTTP API call"))
    ctx, clip, opening_row, frame_calls, generated, _source_calls = _make_case(tmp_path, monkeypatch)
    from novel_manga.application.rendering.flow import ThinMediaRunner
    real_assemble = ThinMediaRunner.assemble
    assembly_calls = []

    def counted_assemble(runner, results):
        assembly_calls.append(True)
        return real_assemble(runner, results)

    monkeypatch.setattr(ThinMediaRunner, "assemble", counted_assemble)

    # Add a second authored clip with the same source-backed opening facts; the
    # B operation is registered before generation and bound to its real takes at
    # the normal renderer assembly boundary.
    plan = read_json(ctx.episode_dir / "clip_plan.json")
    script = read_json(ctx.episode_dir / "chapter_script.json")
    source_clip = plan["clips"][0]
    source_shot = script["shots"][0]
    target_clip = copy.deepcopy(source_clip)
    target_clip.update(clip_id="clip_02", shot_indexes=[2], segment_ids=["s2"],
                       shot_parts=[{"index": 2, "part": [1, 1]}])
    from novel_manga.application.profiles import h3_stamp
    target_clip["prompt_h3_of"] = h3_stamp(target_clip, "")
    target_shot = {**copy.deepcopy(source_shot), "index": 2, "segment_ids": ["s2"]}
    script["shots"].append(target_shot)
    plan["clips"].append(target_clip)
    plan.setdefault("totals", {}).update(clip_count=2, video_clip_count=2,
                                          shot_count=2, estimated_seconds=30,
                                          requested_seconds=30)
    atomic_write_json(ctx.episode_dir / "chapter_script.json", script)
    atomic_write_json(ctx.episode_dir / "clip_plan.json", plan)
    atomic_write_json(ctx.source_episode_dir / "chapter_script.json", script)
    atomic_write_json(ctx.source_episode_dir / "clip_plan.json", plan)
    requests = read_json(ctx.work_dir / "output/requests.json")
    requests["prompts"]["clip_02"] = target_clip["prompt_h3"]
    atomic_write_json(ctx.work_dir / "output/requests.json", requests)
    first_frames = read_json(ctx.work_dir / "output/first-frames.json")
    first_frames["frames"].append({**opening_row, "clip_id": "clip_02",
        "source_quote": "乙看着桌边的野山羊", "image_prompt": "乙停在房间门口，保持原文服饰。"})
    atomic_write_json(ctx.work_dir / "output/first-frames.json", first_frames)
    frames = EpisodeTools(ctx).first_frames({"file": "output/first-frames.json"})
    assert frames["ok"] and frames["clips"]["clip_02"]["ok"], frames
    plan = read_json(ctx.episode_dir / "clip_plan.json")
    atomic_write_json(ctx.source_episode_dir / "clip_plan.json", plan)
    requests["prompts"]["clip_01"] = plan["clips"][0]["prompt_h3"]
    requests["prompts"]["clip_02"] = plan["clips"][1]["prompt_h3"]
    atomic_write_json(ctx.work_dir / "output/requests.json", requests)

    delivery = tmp_path / "interactive-delivery"
    atomic_write_json(ctx.run_dir / "interactive-delivery.json",
                      {"destination": str(delivery), "project": "adventure"})
    atomic_write_json(ctx.episode_dir / "interaction.json", {
        "seconds": 3, "question": "Choose a path", "options": [
            {"id": "A", "label": "A"}, {"id": "B", "label": "B"}]})
    atomic_write_json(ctx.episode_dir / "overlays.json", [])
    atomic_write_json(ctx.episode_dir / "delivery_policy.json", {"extra_visual_overlays": False})
    atomic_write_json(ctx.episode_dir / "authored_groups.json", [])

    from novel_manga.application.review import final_cut, final_audio
    audio_ready = {"value": False}
    audio_worker_calls, judged_audio = [], []
    eureka = tmp_path / "eureka"
    audio_source_root, audio_model_dir = eureka / "source", eureka / "model"
    audio_source_root.mkdir(parents=True); audio_model_dir.mkdir(parents=True)
    (audio_model_dir / "model.int8.onnx").write_bytes(b"synthetic model fixture")

    def audio_worker(runtime, root, job_path):
        del runtime, root
        job = read_json(job_path)
        audio_worker_calls.append(copy.deepcopy(job))
        if not audio_ready["value"]:
            return subprocess.CompletedProcess(["offline-eureka"], 1, "", "synthetic missing raw windows")
        for row in job["jobs"]:
            atomic_write_json(Path(row["output"]), {"response": "Speech and a steady room tone; no abrupt change."})
        return subprocess.CompletedProcess(["offline-eureka"], 0, "", "")

    def audio_judge(_facts, windows):
        judged_audio.append(copy.deepcopy(windows))
        return {"summary": "Final movie audio matches the source sound plan.", "issues": []}

    monkeypatch.setattr(final_audio, "_run_worker", audio_worker)
    monkeypatch.setattr(final_audio, "_judge", audio_judge)
    monkeypatch.setattr("novel_manga.application.music_selection.configuration", lambda _root: {
        "python": str(Path("/usr/bin/python3")), "audio_source_root": str(audio_source_root),
        "audio_model_dir": str(audio_model_dir)})

    def final_picture_judge(runner, media, *, receipt=None):
        movie = Path((receipt or {}).get("final_video") or media["assembly"]["final_video"]).resolve()
        context = final_cut.context(runner.context.episode_dir, media, str(movie))
        value = {"policy": final_cut.POLICY, "movie": str(movie), "context": context,
                 "complete": True, "passed": True, "frames": [], "groups": [],
                 "responses": [], "issues": [], "errors": [], "scope": "offline visual judge"}
        atomic_write_json(runner.context.episode_dir / "final_cut_review.json", value)
        return value

    monkeypatch.setattr(final_cut, "review_assembly", final_picture_judge)
    monkeypatch.setattr("novel_manga.application.agents.episode.lifecycle.resolve_agent_environment",
                        lambda _ctx, _config: {
                            "NOVEL_RENDER_INFLIGHT": "1", "NOVEL_REVIEW_MODE": "classic",
                            "QWEN38_LOCAL_BASE_URL": "http://offline.invalid/v1",
                            "QWEN38_LOCAL_MODEL": "offline", "QWEN38_LOCAL_API_KEY_VAR": "OFFLINE",
                            "SECOND_REVIEW_JUDGE": "ds41", "NOVEL_REVIEW_VIDEO_JUDGE": "ds41",
                        })

    from novel_manga.application.agents.episode import client, turns
    original_request = client.request
    actions = []
    cutaway = {"decisions": [{"clip_id": "clip_02", "action": "cutaway",
        "source_clip_id": "clip_01", "source_start_seconds": 3.6, "source_end_seconds": 4.8,
        "target_clip_id": "clip_02", "target_start_seconds": 1.12, "target_end_seconds": 2.32,
        "reason": "Use the already accepted hallway picture over the target take and preserve target audio."}]}
    postproduction.authorize_cutaway(ctx.episode_dir, cutaway["decisions"][0])

    def socket_path():
        for fd in os.listdir("/proc/self/fd"):
            descriptor = Path(f"/proc/self/fd/{fd}")
            try:
                if Path(os.readlink(descriptor)).resolve() == ctx.work_dir.resolve():
                    candidate = descriptor / "episode-tools.sock"
                    if candidate.is_socket(): return candidate
            except OSError:
                continue
        pytest.fail("run_episode bridge socket was not found")

    def offline_agent_turn(work_dir, prompt, *, resume=False, timeout=1200, config=None,
                           expected_output=None, enable_tools=False, project_root=None):
        del prompt, resume, timeout, config, expected_output, enable_tools, project_root
        endpoint = socket_path()
        state = original_request("status", {}, socket_path=endpoint) if actions else None
        previous_job = (state or {}).get("job") or {}
        failed_audio = read_json(ctx.episode_dir / "final_audio_review.json", {})
        recovered_audio = (previous_job.get("action") == "finish"
                           and previous_job.get("status") == "completed"
                           and failed_audio.get("complete") is not True
                           and bool(failed_audio.get("errors") or failed_audio.get("issues")))
        if recovered_audio:
            audio_ready["value"] = True
        action = ("render" if not actions else "finish" if recovered_audio else
                  (state.get("next_action") or {}).get("action"))
        if action == "render":
            answer = original_request("render", {}, socket_path=endpoint)
        elif action == "review":
            answer = original_request("review", {}, socket_path=endpoint)
        elif action == "finish":
            answer = original_request("finish", {}, socket_path=endpoint)
        else:
            pytest.fail(f"offline Agent received unexpected transition {state}")
        actions.append(action)
        atomic_write_json(work_dir / "episode-session.json", {"session_id": "offline-cutaway-session"})
        attempt_dir = ctx.run_dir / "offline-cutaway-attempts" / str(len(actions))
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return Attempt(str(attempt_dir), attempt_dir, 0, 0,
                       record={"session_id": "offline-cutaway-session"})

    monkeypatch.setattr(turns, "run_session", offline_agent_turn)
    config = {"backend": "codex", "provider": "offline", "base_url": "http://offline.invalid",
        "model": "offline", "key_var": "OFFLINE", "timeout_seconds": 600,
        "production_mode": "standard", "agent_invocations_per_slice": 8, "agent_slice_seconds": 60}
    result = run_episode(ctx, config=config, timeout=600)
    if result.get("status") != "finished":
        failed_audio = read_json(ctx.episode_dir / "final_audio_review.json", {})
        assert failed_audio.get("complete") is not True and failed_audio.get("errors"), result
        audio_ready["value"] = True
        result = run_episode(ctx, config=config, timeout=600, retry_issues=True)

    assert result["status"] == "finished" and result["passed"] is True, {"result": result, "actions": actions}
    assert actions.count("render") == 1
    assert "finish" in actions
    assert len(assembly_calls) == 1
    report = read_json(ctx.episode_dir / "thin_media_report.json")
    trace = report["assembly"]["postproduction"]["visual_inserts"][0]
    assert trace["audio_copy_verified"] is True
    receipt_path = ctx.episode_dir / "work/postproduction" / trace["binding_id"] / "attempts/attempt-001/receipt.json"
    cutaway_receipt = read_json(receipt_path)
    target_video = Path(cutaway_receipt["target"]["video"])
    derived_video = Path(cutaway_receipt["derived_video"])
    assert postproduction._audio_payload(target_video) == postproduction._audio_payload(derived_video)

    def frame_at(path, seconds):
        result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(seconds), "-i", str(path),
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], check=True, capture_output=True)
        return Image.open(io.BytesIO(result.stdout)).convert("RGB")

    assert ImageChops.difference(frame_at(target_video, 1.5), frame_at(derived_video, 1.5)).getbbox()
    # This is the same receipt-closing call made by the standard interactive
    # wrapper after its driver returns; it must not encode or review again.
    from novel_manga.interactive.delivery import finish_reviewed_delivery
    receipt = finish_reviewed_delivery(EpisodeTools(ctx).media.runner(), report, delivery)
    assert len(assembly_calls) == 1
    assert receipt["status"] == "accepted" and receipt["final_audio_review_passed"] is True
    audio = receipt["final_audio_review"]
    assert audio["complete"] is True and audio["passed"] is True and not audio["errors"] and not audio["issues"]
    assert len(audio_worker_calls) >= 2 and judged_audio
    coverage = audio["coverage"]
    assert coverage[0]["start"] == 0 and coverage[0]["end"] - coverage[0]["start"] <= 30
    assert coverage[-1]["end"] >= audio["coverage_seconds"] - .02
    if len(coverage) > 1:
        assert abs(coverage[1]["start"] - (coverage[0]["end"] - 2)) < .02
    assert history.generation_counts(ctx.episode_dir) == {"clip_01": 1, "clip_02": 1}
