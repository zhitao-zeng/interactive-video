import array
import ast
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import wave

from PIL import Image, ImageChops, ImageStat
import pytest

from clean_studio.assembly import assemble, probe_export
from clean_studio.domain import Dialogue, EpisodeSpec, FixedOption, MusicCue, Shot
from clean_studio.media import Artifact
from clean_studio.providers.music import (
    SoulTunerMusicProvider,
    adopt_music,
    search_music,
)

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value).encode("utf-8")
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _tone(path, frequency, seconds=8, amplitude=12000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(
            b"".join(
                int(amplitude * math.sin(2 * math.pi * frequency * index / 16000)).to_bytes(
                    2, "little", signed=True
                )
                for index in range(seconds * 16000)
            )
        )


def _cue():
    return MusicCue(
        start_seconds=1,
        end_seconds=7,
        mood="克制、逐渐明亮",
        soundscape="保留海水和对白，无突出人声",
    )


def _music_search(tmp_path, cue=None):
    cue = cue or _cue()
    audio = tmp_path / "catalogue" / "BGM-1.wav"
    _tone(audio, 300)
    audio_hash = sha256(audio.read_bytes()).hexdigest()
    evidence = tmp_path / "catalogue" / "BGM-1-audio.json"
    evidence_hash = _write_json(
        evidence,
        {
            "status": "completed",
            "actual_audio": str(audio),
            "audio_sha256": audio_hash,
            "source_windows": [{"start": 0, "end": 6}],
            "response": "实际样本中有柔和弦乐，没有听到人声。",
            "evidence_scope": "sampled_audio_analysis_only",
        },
    )
    catalogue_entry = {
        "song_id": "BGM-1",
        "title": "潮汐之后",
        "artist": "测试作曲家",
        "local_audio": str(audio),
        "license_id": "CC-BY-4.0",
        "license_url": "https://example.invalid/license",
        "source_url": "https://example.invalid/track",
        "attribution": "潮汐之后 / 测试作曲家 / CC-BY-4.0",
        "usage_policy": {"requires_visible_attribution": True},
        "audio_audit_report": str(evidence),
        "audio_audit_source_windows": [{"start": 0, "end": 6}],
        "audio_audit": {"has_human_vocals": False, "uncertain": False},
        "vocal_evidence_scope": "6 秒样本；不是完整曲目或成片试听验收",
    }
    catalogue = tmp_path / "catalogue" / "catalog.json"
    catalogue_hash = _write_json(catalogue, [catalogue_entry])
    calls = []

    def backend(config, query, directory):
        calls.append((config, query, directory))
        return {
            "success": True,
            "source": "soultuner",
            "response": "仅检索候选，不代表剧情适配或试听通过。",
            "recommendations": {
                "success": True,
                "data": [
                    {"song": {"music_id": "BGM-1"}, "reason": "弦乐与克制情绪相符"},
                    {"song": {"music_id": "BGM-1"}, "reason": "重复结果"},
                    {"song": {"music_id": "MISSING"}, "reason": "不存在"},
                ],
            },
        }

    provider = SoulTunerMusicProvider(
        {
            "catalog": str(catalogue),
            "catalogue_scope": "一首离线测试曲；未验证流行度",
        },
        search_root=tmp_path / "searches",
        backend=backend,
    )
    return cue, audio, evidence, evidence_hash, catalogue_hash, provider, calls


def _audition(candidate, tmp_path, seconds=6):
    receipt = tmp_path / "audition" / f"{candidate.candidate_id}.json"
    receipt_hash = _write_json(
        receipt,
        {
            "status": "completed",
            "candidate_id": candidate.candidate_id,
            "cue": asdict(candidate.cue),
            "actual_audio": candidate.audio_path,
            "audio_sha256": candidate.audio_sha256,
            "verdict": "accepted",
            "actual_listening": True,
            "evidence_scope": "actual_audio_content_listening",
            "agent_role": "exception_agent",
            "agent_id": "offline-fake-audio-agent",
            "listened_windows": [{"start": 0, "end": seconds}],
            "response": "异常 Agent 实际听到所选区间；弦乐不遮盖对白，采用。",
        },
    )
    return {
        "candidate_id": candidate.candidate_id,
        "cue": asdict(candidate.cue),
        "audio_sha256": candidate.audio_sha256,
        "verdict": "accepted",
        "actual_listening": True,
        "evidence_scope": "actual_audio_content_listening",
        "agent_role": "exception_agent",
        "agent_id": "offline-fake-audio-agent",
        "reason": "实际试听所选区间，情绪与声景意图一致。",
        "limitations": ["只验收所选源区间；不代表混合后整片声音验收"],
        "listened_windows": [{"start": 0, "end": seconds}],
        "receipt_path": str(receipt),
        "receipt_sha256": receipt_hash,
    }


def _accepted_music(tmp_path, cue=None, seconds=None):
    cue = cue or _cue()
    seconds = cue.end_seconds - cue.start_seconds if seconds is None else seconds
    cue, audio, _, _, _, provider, _ = _music_search(tmp_path, cue=cue)
    candidate = search_music(cue, provider=provider)[0]
    accepted = adopt_music(
        candidate,
        _audition(candidate, tmp_path, seconds=seconds),
        source_start_seconds=0,
        source_seconds=seconds,
        gain_db=-12,
        fade_in_seconds=0.2,
        fade_out_seconds=0.2,
    )
    return accepted, audio


def _shot(shot_id="shot_001", segment="S1/flow/0", line="把船灯递给我。"):
    return Shot(
        shot_id=shot_id,
        scene_id="S1",
        source_segments=(segment,),
        location="失物海码头",
        start_state="知遥站在码头，船灯熄灭",
        action="知遥查看船灯",
        end_state="知遥手持船灯站在码头",
        camera="固定机位",
        shot_scale="中景",
        visible_characters=("C01",),
        dialogue=(Dialogue("C01", line),),
        reference_subjects=("C01",),
        duration_seconds=4,
    )


def _episode(with_music=True):
    return EpisodeSpec(
        episode_id="episode-assembly",
        title="失物海",
        target_seconds=11,
        style_id="travel-live",
        shots=(_shot(), _shot("shot_002", "S1/flow/1", "我们一起找答案。")),
        fixed_question="下一站，你替知遥选哪里？",
        fixed_options=(
            FixedOption("A", "W11", "EP05A", "静音群岛"),
            FixedOption("B", "W12", "EP05B", "瓶中四季"),
        ),
        music_cues=(_cue(),) if with_music else (),
    )


def _video(path, color, frequency, seconds=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x288:r=25:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={seconds},volume=0.5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _valid_artifact(shot, path, *, asr_text=None):
    digest = sha256(path.read_bytes()).hexdigest()
    hypothesis = asr_text or shot.dialogue[0].line
    segments = (
        [{"start": 0.2, "end": 2.4, "hypothesis": hypothesis}]
        if asr_text is not None
        else []
    )
    quality = {
        "passed": True,
        "technical": {
            "passed": True,
            "fully_decoded": True,
            "native_audio": True,
            "video_sha256": digest,
            "content_accepted": False,
            "final_audio_accepted": False,
        },
        "asr": {
            "status": "completed",
            "passed": True,
            "evaluation": "clean-current-shot-dialogue-cer-v2-phonetic",
            "video_sha256": digest,
            "hypothesis": hypothesis,
            "segments": segments,
            "final_audio_accepted": False,
            "evidence_scope": "asr_only",
        },
        "review": {
            "verdict": "accepted",
            "video_sha256": digest,
            "findings": [],
        },
        "final_audio_accepted": False,
    }
    return Artifact(
        id=f"video-{shot.shot_id}",
        operation_id=f"operation-{shot.shot_id}",
        shot_id=shot.shot_id,
        kind="video",
        path=str(path),
        sha256=digest,
        input_hash=f"request-{shot.shot_id}",
        metadata={"quality": quality},
    )


def _valid_videos(episode, tmp_path):
    first = _video(tmp_path / "inputs" / "shot-1.mp4", "blue", 1000)
    second = _video(tmp_path / "inputs" / "shot-2.mp4", "darkred", 1200)
    return {
        episode.shots[0].shot_id: _valid_artifact(
            episode.shots[0], first, asr_text="当前 ASR 字幕一"
        ),
        episode.shots[1].shot_id: _valid_artifact(episode.shots[1], second),
    }


def _assemble(tmp_path, *, runner=None):
    episode = _episode()
    videos = _valid_videos(episode, tmp_path)
    accepted, audio = _accepted_music(tmp_path / "music")
    output = tmp_path / "export"
    export = assemble(
        episode,
        videos,
        (accepted,),
        output_dir=output,
        media_root=tmp_path,
        runner=runner,
        font_path=FONT,
        preset="ultrafast",
    )
    return export, output, audio


def _pcm(path, start, seconds=0.4):
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            str(path),
            "-t",
            str(seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-",
        ]
    )
    return array.array("h", data)


def _energy(samples, frequency):
    return abs(
        sum(
            value
            * complex(
                math.cos(2 * math.pi * frequency * index / 16000),
                math.sin(2 * math.pi * frequency * index / 16000),
            )
            for index, value in enumerate(samples)
        )
    ) / max(1, len(samples))


def test_search_returns_real_unreviewed_candidate_and_audio_evidence(tmp_path):
    cue, audio, evidence, evidence_hash, catalogue_hash, provider, calls = _music_search(
        tmp_path
    )
    candidates = search_music(cue, provider=provider, limit=3)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.cue == cue
    assert candidate.candidate_id == "BGM-1"
    assert Path(candidate.audio_path) == audio
    assert candidate.audio_sha256 == sha256(audio.read_bytes()).hexdigest()
    assert candidate.audio_evidence.report_path == str(evidence)
    assert candidate.audio_evidence.report_sha256 == evidence_hash
    assert candidate.audio_evidence.catalogue_sha256 == catalogue_hash
    assert candidate.audio_evidence.source_windows == ({"start": 0, "end": 6},)
    assert candidate.review_status == "unreviewed"
    assert candidate.content_accepted is False
    assert any("不是完整曲目" in item for item in candidate.limitations)
    assert "克制、逐渐明亮" in calls[0][1]
    assert "保留海水和对白" in calls[0][1]
    assert (tmp_path / "searches" / "owner").is_file()


@pytest.mark.parametrize(
    "mutation",
    [
        {"verdict": "selected"},
        {"actual_listening": False},
        {"evidence_scope": "asr_only"},
        {"agent_role": "host"},
        {"limitations": []},
    ],
)
def test_search_selection_or_asr_cannot_replace_actual_agent_audition(
    tmp_path, mutation
):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    candidate = search_music(cue, provider=provider)[0]
    judgment = {**_audition(candidate, tmp_path), **mutation}
    with pytest.raises(ValueError, match="accepted|listening|[Aa]gent|limitations"):
        adopt_music(candidate, judgment)


def test_adoption_binds_cue_current_audio_hash_receipt_and_limitations(tmp_path):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    candidate = search_music(cue, provider=provider)[0]
    accepted = adopt_music(candidate, _audition(candidate, tmp_path), source_seconds=6)

    assert accepted.cue == cue
    assert accepted.audio_sha256 == candidate.audio_sha256
    assert accepted.audition.verdict == "accepted"
    assert accepted.audition.actual_listening is True
    assert accepted.audition.agent_role == "exception_agent"
    assert accepted.final_audio_accepted is False
    assert accepted.limitations


def test_missing_video_fails_before_any_encode(tmp_path):
    episode = _episode(with_music=False)
    videos = _valid_videos(episode, tmp_path)
    videos.pop("shot_002")
    commands = []

    def forbidden(*args, **kwargs):
        commands.append(args)
        pytest.fail("missing N/N must fail before FFmpeg")

    with pytest.raises(ValueError, match="shot_002|N/N|missing"):
        assemble(
            episode,
            videos,
            (),
            output_dir=tmp_path / "export",
            runner=forbidden,
        )
    assert commands == []
    assert not (tmp_path / "export").exists()


def test_video_bytes_or_stale_asr_cannot_enter_assembly(tmp_path):
    episode = _episode(with_music=False)
    videos = _valid_videos(episode, tmp_path)
    artifact = videos["shot_001"]
    Path(artifact.path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256|hash|bytes"):
        assemble(episode, videos, (), output_dir=tmp_path / "changed")


def test_authored_cue_requires_accepted_and_mixed_music(tmp_path):
    episode = _episode()
    videos = _valid_videos(episode, tmp_path)
    with pytest.raises(ValueError, match="music|cue|accepted"):
        assemble(episode, videos, (), output_dir=tmp_path / "export")


def test_audio_bytes_change_after_audition_blocks_export(tmp_path):
    episode = _episode()
    videos = _valid_videos(episode, tmp_path)
    accepted, audio = _accepted_music(tmp_path / "music")
    _tone(audio, 900)

    with pytest.raises(ValueError, match="SHA-256|hash|bytes"):
        assemble(
            episode,
            videos,
            (accepted,),
            output_dir=tmp_path / "export",
            font_path=FONT,
            preset="ultrafast",
        )


def test_real_single_encode_export_has_streams_audio_subtitles_and_exact_tail(tmp_path):
    commands = []

    def recording_runner(command, **kwargs):
        commands.append(command)
        return subprocess.run(command, **kwargs)

    export, output, audio = _assemble(tmp_path, runner=recording_runner)
    report = probe_export(export)
    movie = Path(export.path)
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(movie),
            ]
        )
    )
    video = next(row for row in probe["streams"] if row["codec_type"] == "video")
    audio_stream = next(row for row in probe["streams"] if row["codec_type"] == "audio")

    assert report.passed is True
    assert report.fully_decoded is True
    assert report.content_accepted is False
    assert report.final_audio_accepted is False
    assert export.status == "candidate"
    assert export.content_accepted is False
    assert export.final_audio_accepted is False
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["avg_frame_rate"] == "25/1"
    assert video["codec_name"] == "h264"
    assert int(video["nb_frames"]) == 275
    assert audio_stream["codec_name"] == "aac"
    assert abs(float(video["duration"]) - 11) < 0.04
    assert report.checks["end_card"]["seconds"] == 3
    assert report.checks["end_card"]["frames"] == 75
    assert report.checks["end_card"]["not_black"] is True
    assert report.checks["shots"]["expected"] == 2
    assert report.checks["shots"]["actual"] == 2

    for name in ("cover.jpg", "end.jpg"):
        with Image.open(output / name) as image:
            assert image.format == "JPEG"
            assert image.size == (1080, 1920)
    tail = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "9.5",
            "-i",
            str(movie),
            "-frames:v",
            "1",
            "-vf",
            "scale=270:480",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )
    tail_image = Image.frombytes("RGB", (270, 480), tail)
    assert sum(ImageStat.Stat(tail_image).mean) / 3 > 25
    assert sum(ImageStat.Stat(tail_image).stddev) / 3 > 10
    source_end = _rgb_frame(output / "end.jpg", None)
    assert _difference(tail_image, source_end) < 10
    subtitle_frame = _rgb_frame(movie, 1.0)
    clean_frame = _rgb_frame(tmp_path / "inputs" / "shot-1.mp4", 1.0)
    assert _difference(subtitle_frame, clean_frame, (20, 370, 250, 412)) > 5
    assert _difference(subtitle_frame, clean_frame, (10, 20, 260, 180)) < 5

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fixed_question"] == "下一站，你替知遥选哪里？"
    assert manifest["fixed_options"] == [
        {"label": "A", "target_world": "W11", "target_video": "EP05A", "title": "静音群岛"},
        {"label": "B", "target_world": "W12", "target_video": "EP05B", "title": "瓶中四季"},
    ]
    assert [row["source_segments"] for row in manifest["shots"]] == [
        ["S1/flow/0"],
        ["S1/flow/1"],
    ]
    assert manifest["music_mix"]["output_sha256"] == export.sha256
    accepted_hash = manifest["music_mix"]["cues"][0]["audio_sha256"]
    assert accepted_hash == sha256(audio.read_bytes()).hexdigest()
    assert manifest["content_accepted"] is False
    assert manifest["final_audio_accepted"] is False

    ass = (output / "subtitles.ass").read_text(encoding="utf-8")
    assert "当前 ASR 字幕一" in ass
    assert "我们一起找答案。" in ass
    sources = [row["subtitle_source"] for row in manifest["subtitles"]]
    assert sources == ["current_video_asr", "current_episode_dialogue"]

    native = _pcm(tmp_path / "inputs" / "shot-1.mp4", 2)
    mixed = _pcm(movie, 2)
    assert _energy(mixed, 1000) > 0.5 * _energy(native, 1000)
    assert _energy(mixed, 300) > 5 * _energy(native, 300)
    second = _pcm(movie, 5)
    assert _energy(second, 1200) > 100
    assert _energy(second, 300) > 20
    tail_audio = _pcm(movie, 8.5)
    assert max(map(abs, tail_audio)) < 100

    encodes = [
        command
        for command in commands
        if "libx264" in command and str(command[-1]).endswith(".mp4")
    ]
    assert len(encodes) == 1
    assert not (output / "joined.mp4").exists()
    credits = (output / "music-credits.txt").read_text(encoding="utf-8")
    assert "潮汐之后 / 测试作曲家 / CC-BY-4.0" in credits
    assert "不代表混合后整片声音验收" in credits

    _tone(audio, 900)
    stale = probe_export(export)
    assert stale.passed is False
    assert stale.checks["music_mix"]["passed"] is False
    assert stale.content_accepted is False


def test_selected_but_not_currently_mixed_music_fails_probe(tmp_path):
    export, output, _ = _assemble(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["music_mix"]["output_sha256"] = "0" * 64
    manifest_hash = _write_json(manifest_path, manifest)
    tampered = replace(export, manifest_sha256=manifest_hash)

    report = probe_export(tampered)
    assert report.passed is False
    assert report.checks["music_mix"]["passed"] is False
    assert report.content_accepted is False


def test_audition_windows_must_cover_the_selected_source_interval(tmp_path):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    candidate = search_music(cue, provider=provider)[0]
    judgment = _audition(candidate, tmp_path)
    judgment["listened_windows"] = [{"start": 0, "end": 1}]
    receipt_path = Path(judgment["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["listened_windows"] = judgment["listened_windows"]
    judgment["receipt_sha256"] = _write_json(receipt_path, receipt)

    with pytest.raises(ValueError, match="cover|window|listening"):
        adopt_music(candidate, judgment, source_start_seconds=0, source_seconds=6)


def test_probe_rejects_self_acceptance_and_malformed_music_receipt(tmp_path):
    export, output, _ = _assemble(tmp_path)
    self_accepted = replace(export, status="accepted", content_accepted=True)
    boundary = probe_export(self_accepted)
    assert boundary.passed is False
    assert boundary.checks["candidate_boundary"]["passed"] is False
    assert boundary.content_accepted is False

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["music_mix"]["cues"] = None
    manifest_hash = _write_json(manifest_path, manifest)
    malformed = replace(export, manifest_sha256=manifest_hash)
    report = probe_export(malformed)
    assert report.passed is False
    assert report.checks["music_mix"]["passed"] is False


@pytest.mark.parametrize(
    "field",
    ["verdict", "actual_listening", "candidate_id", "agent_id", "cue"],
)
def test_audition_receipt_must_bind_complete_current_decision(tmp_path, field):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    candidate = search_music(cue, provider=provider)[0]
    judgment = _audition(candidate, tmp_path)
    receipt_path = Path(judgment["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if field == "verdict":
        receipt["verdict"] = "rejected"
    elif field == "actual_listening":
        receipt["actual_listening"] = False
    elif field == "candidate_id":
        receipt["candidate_id"] = "OTHER"
    elif field == "agent_id":
        receipt["agent_id"] = ""
    else:
        receipt["cue"] = {**receipt["cue"], "mood": "被改写的 cue"}
    judgment["receipt_sha256"] = _write_json(receipt_path, receipt)

    with pytest.raises(ValueError, match="candidate|cue|accepted|listening|[Aa]gent"):
        adopt_music(candidate, judgment, source_seconds=6)


def test_music_audition_persists_the_complete_cue(tmp_path):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    candidate = search_music(cue, provider=provider)[0]
    accepted = adopt_music(candidate, _audition(candidate, tmp_path), source_seconds=6)
    assert accepted.audition.cue == cue
    assert asdict(accepted.audition.cue) == asdict(cue)


@pytest.mark.parametrize("mutation", ["cue", "windows", "stale"])
def test_post_adoption_cue_windows_and_receipt_are_revalidated(tmp_path, mutation):
    episode = _episode()
    videos = _valid_videos(episode, tmp_path)
    accepted, _ = _accepted_music(tmp_path / "music")
    audition = accepted.audition
    if mutation == "cue":
        audition = replace(audition, cue=replace(audition.cue, mood="采用后换 cue"))
    elif mutation == "windows":
        audition = replace(audition, listened_windows=())
    else:
        receipt_path = Path(audition.receipt_path)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["verdict"] = "rejected"
        receipt_hash = _write_json(receipt_path, receipt)
        audition = replace(audition, receipt_sha256=receipt_hash)
    accepted = replace(accepted, audition=audition)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid audition must fail before FFmpeg")

    with pytest.raises(ValueError, match="cue|window|accepted|listening|receipt"):
        assemble(
            episode,
            videos,
            (accepted,),
            output_dir=tmp_path / "export",
            runner=forbidden,
            font_path=FONT,
        )


def test_probe_revalidates_current_receipt_not_only_saved_judgment(tmp_path):
    export, output, _ = _assemble(tmp_path)
    mixed = deepcopy(export.mixed_music)
    audition = mixed[0]["audition"]
    receipt_path = Path(audition["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["verdict"] = "rejected"
    audition["receipt_sha256"] = _write_json(receipt_path, receipt)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["music_mix"]["cues"] = list(mixed)
    manifest_hash = _write_json(manifest_path, manifest)
    tampered = replace(export, mixed_music=mixed, manifest_sha256=manifest_hash)

    report = probe_export(tampered)
    assert report.passed is False
    assert report.checks["music_mix"]["passed"] is False
    assert report.checks["music_mix"]["invalid_candidates"] == ["BGM-1"]


@pytest.mark.parametrize(
    "case",
    ["failed", "missing_hash", "mismatch_windows", "invalid_windows", "missing_catalogue_windows"],
)
def test_search_rejects_incomplete_stale_or_inconsistent_audio_evidence(tmp_path, case):
    cue, _, evidence, _, _, provider, _ = _music_search(tmp_path)
    report = json.loads(evidence.read_text(encoding="utf-8"))
    catalogue_path = evidence.parent / "catalog.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    if case == "failed":
        report["status"] = "failed"
    elif case == "missing_hash":
        report.pop("audio_sha256")
    elif case == "mismatch_windows":
        report["source_windows"] = [{"start": 0, "end": 3}]
    elif case == "invalid_windows":
        report["source_windows"] = [{"start": 0, "end": 0}]
        catalogue[0]["audio_audit_source_windows"] = report["source_windows"]
    else:
        catalogue[0].pop("audio_audit_source_windows")
    _write_json(evidence, report)
    _write_json(catalogue_path, catalogue)

    assert search_music(cue, provider=provider) == []


def test_short_source_loop_still_has_music_near_cue_end(tmp_path):
    episode = _episode()
    videos = _valid_videos(episode, tmp_path)
    cue, _, _, _, _, provider, _ = _music_search(tmp_path / "music")
    candidate = search_music(cue, provider=provider)[0]
    accepted = adopt_music(
        candidate,
        _audition(candidate, tmp_path / "music"),
        source_start_seconds=0,
        source_seconds=2,
        gain_db=-12,
        loop=True,
    )
    export = assemble(
        episode,
        videos,
        (accepted,),
        output_dir=tmp_path / "export",
        runner=None,
        font_path=FONT,
        preset="ultrafast",
    )

    mixed = _pcm(export.path, 5.1)
    native = _pcm(tmp_path / "inputs" / "shot-2.mp4", 1.1)
    assert _energy(mixed, 300) > 20
    assert _energy(mixed, 300) > 5 * _energy(native, 300)


def test_missing_technical_video_hash_blocks_assembly_before_encode(tmp_path):
    episode = _episode(with_music=False)
    videos = _valid_videos(episode, tmp_path)
    artifact = videos["shot_001"]
    metadata = deepcopy(artifact.metadata)
    metadata["quality"]["technical"]["video_sha256"] = None
    videos["shot_001"] = replace(artifact, metadata=metadata)

    def forbidden(*args, **kwargs):
        pytest.fail("missing technical video hash must fail before FFmpeg")

    with pytest.raises(ValueError, match="technical|ASR|evidence|hash"):
        assemble(
            episode,
            videos,
            (),
            output_dir=tmp_path / "export",
            runner=forbidden,
        )


def _rgb_frame(path, start, *, size=(270, 480)):
    width, height = size
    command = ["ffmpeg", "-v", "error"]
    if start is not None:
        command += ["-ss", str(start)]
    command += [
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    return Image.frombytes("RGB", size, subprocess.check_output(command))


def _difference(actual, expected, box=None):
    if box is not None:
        actual = actual.crop(box)
        expected = expected.crop(box)
    return sum(ImageStat.Stat(ImageChops.difference(actual, expected)).mean) / 3


def test_export_bundle_and_technical_identity_are_persistable(tmp_path):
    export, _, _ = _assemble(tmp_path)
    assert len(export.bundle_hash) == 64
    assert export.technical.bundle_hash == export.bundle_hash
    from clean_studio.assembly import export_artifact_metadata

    metadata = export_artifact_metadata(export)
    bundle = metadata["export_bundle"]
    assert bundle["bundle_hash"] == export.bundle_hash
    assert bundle["video_sha256"] == export.sha256
    assert bundle["cover_sha256"] == export.cover_sha256
    assert bundle["manifest_sha256"] == export.manifest_sha256
    assert bundle["episode_hash"] == export.episode_hash
    assert bundle["fixed_question"] == export.fixed_question
    assert bundle["fixed_options"] == list(export.fixed_options)
    assert bundle["mixed_music"] == list(export.mixed_music)
    assert bundle["subtitles"] == list(export.subtitles)
    assert bundle["source_videos"] == list(export.source_videos)
    assert replace(export, subtitle_sha256="0" * 64).bundle_hash != export.bundle_hash


def test_task6_frozen_closure_and_provenance_are_local_and_exact():
    provenance = json.loads(
        (ROOT / "provenance" / "adapters.json").read_text(encoding="utf-8")
    )
    for key in (
        "music_selection",
        "soultuner_worker",
        "music_timeline",
        "renderer",
        "quality",
    ):
        source = provenance["sources"][key]
        source_path = Path(source["path"])
        assert source_path.is_absolute()
        assert sha256(source_path.read_bytes()).hexdigest() == source["sha256"]

    new_files = [
        "src/clean_studio/frozen/music.py",
        "src/clean_studio/frozen/soultuner_worker.py",
        "src/clean_studio/frozen/render.py",
        "src/clean_studio/frozen/quality.py",
        "src/clean_studio/providers/music.py",
        "src/clean_studio/providers/ffmpeg.py",
        "src/clean_studio/assembly.py",
    ]
    recorded = {row["path"]: row for row in provenance["frozen_files"]}
    task8_hashes = {
        "src/clean_studio/frozen/render.py": "5365a96e9d8086f7d5b9090e1d35766cd8bf6018046fbcb9895e207d739c935f",
    }
    for relative in new_files:
        data = (ROOT / relative).read_bytes()
        expected = task8_hashes.get(relative, recorded[relative]["sha256"])
        assert sha256(data).hexdigest() == expected
        text = data.decode("utf-8")
        tree = ast.parse(text, filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("novel_manga") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("novel_manga")
        assert '".env"' not in text
        assert "'.env'" not in text
        assert "episode_music_errors" not in text


def _offline_recommend_runtime(tmp_path, *, mapped):
    config = {
        "python": "offline-python",
        "source_root": str(tmp_path / "soultuner-source"),
        "neo4j_uri": "bolt://127.0.0.1:17687",
        "m2d_checkpoint": str(tmp_path / "m2d.pth"),
        "bert_path": str(tmp_path / "bert"),
        "data_dir": str(tmp_path / "data"),
    }
    if mapped:
        config["llm"] = {
            "base_url": "http://127.0.0.1:8789/v1",
            "model": "deepseek-v4.1-flash-1017",
            "api_key_env": "PHANTHY_API_KEY",
        }
    return config


@pytest.mark.parametrize("mapped", [True, False])
def test_recommend_derives_worker_environment_without_secret_persistence(
    tmp_path, monkeypatch, mapped
):
    from contextlib import contextmanager
    import clean_studio.providers.music as module

    environment = (
        {"PHANTHY_API_KEY": "offline-dotenv-key"}
        if mapped
        else {
            "ST_LLM_KEY": "offline-explicit-key",
            "ST_LLM_BASE_URL": "http://explicit.invalid/v1",
            "ST_LLM_MODEL": "explicit-model",
        }
    )
    secret = environment.get("PHANTHY_API_KEY") or environment["ST_LLM_KEY"]
    for name in ("ST_LLM_KEY", "ST_LLM_BASE_URL", "ST_LLM_MODEL"):
        monkeypatch.setenv(name, f"shell-{name}")
    monkeypatch.setenv("PHANTHY_API_KEY", "shell-PHANTHY_API_KEY")
    events = []
    captured = {}

    def runner(command, **kwargs):
        events.append("worker")
        captured["kwargs"] = kwargs
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        Path(request["output"]).write_text('{"success":true}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    @contextmanager
    def lifecycle(config, directory, **kwargs):
        assert kwargs["runner"] is runner
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(module, "local_index", lifecycle)
    provider = SoulTunerMusicProvider(
        _offline_recommend_runtime(tmp_path, mapped=mapped),
        search_root=tmp_path / "searches",
        environment=environment,
        runner=runner,
    )

    assert provider._recommend("offline query", tmp_path) == {"success": True}
    worker_environment = captured["kwargs"]["env"]
    expected = {
        "ST_LLM_KEY": secret,
        "ST_LLM_BASE_URL": (
            "http://127.0.0.1:8789/v1" if mapped else "http://explicit.invalid/v1"
        ),
        "ST_LLM_MODEL": "deepseek-v4.1-flash-1017" if mapped else "explicit-model",
    }
    assert {key: worker_environment[key] for key in expected} == expected
    assert events == ["enter", "worker", "exit"]
    persisted = [
        json.dumps(provider.config),
        (tmp_path / "backend-input.json").read_text(encoding="utf-8"),
        (tmp_path / "backend.log").read_text(encoding="utf-8"),
    ]
    assert all(secret not in value for value in persisted)


def test_recommend_does_not_fall_back_to_shell_st_environment(tmp_path, monkeypatch):
    for name in ("ST_LLM_KEY", "ST_LLM_BASE_URL", "ST_LLM_MODEL"):
        monkeypatch.setenv(name, f"shell-{name}")
    called = []

    def forbidden(*args, **kwargs):
        called.append(args)
        pytest.fail("unmapped shell ST variables must not reach the worker")

    provider = SoulTunerMusicProvider(
        _offline_recommend_runtime(tmp_path, mapped=False),
        search_root=tmp_path / "searches",
        runner=forbidden,
    )
    with pytest.raises(ValueError, match="llm|mapping|environment"):
        provider._recommend("offline query", tmp_path)
    assert called == []


def _index_config(tmp_path):
    return {
        "neo4j_uri": "bolt://127.0.0.1:17687",
        "neo4j_home": str(tmp_path / "neo4j"),
        "java_home": str(tmp_path / "java"),
    }


def _online(*args, **kwargs):
    from contextlib import nullcontext

    return nullcontext()


def _offline(*args, **kwargs):
    raise OSError("offline connection refused")


def _sequence_connector(*results):
    from contextlib import nullcontext

    remaining = iter(results)

    def connect(address, timeout):
        result = next(remaining)
        if isinstance(result, BaseException):
            raise result
        return nullcontext()

    return connect


def test_existing_index_is_locked_but_never_stopped(tmp_path, monkeypatch):
    import fcntl
    from clean_studio.frozen.music import local_index

    config = _index_config(tmp_path)
    locks = []
    monkeypatch.setattr(
        fcntl,
        "flock",
        lambda handle, operation: locks.append((Path(handle.name), operation)),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("an already running index must not be started or stopped")

    with local_index(config, tmp_path, runner=forbidden, connector=_online):
        pass

    lock = Path(config["neo4j_home"]) / "run/studio-music-selection.lock"
    assert locks == [(lock, fcntl.LOCK_EX)]


@pytest.mark.parametrize("body_error", [False, True])
def test_owned_index_is_stopped_after_success_and_worker_exception(
    tmp_path, body_error
):
    from contextlib import nullcontext
    from clean_studio.frozen.music import local_index

    config = _index_config(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    outcome = pytest.raises(RuntimeError, match="worker failed") if body_error else nullcontext()
    with outcome:
        with local_index(
            config,
            tmp_path,
            runner=runner,
            connector=_sequence_connector(OSError("down"), None),
        ):
            if body_error:
                raise RuntimeError("worker failed")

    assert [call[0][-1] for call in calls] == ["start", "stop"]
    assert all(call[1]["env"]["JAVA_HOME"] == config["java_home"] for call in calls)


def test_unhealthy_owned_index_is_stopped_on_timeout(tmp_path):
    from clean_studio.frozen.music import local_index

    config = _index_config(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    clock = iter([0.0, 21.0])
    with pytest.raises(RuntimeError, match="ready|health|unavailable"):
        with local_index(
            config,
            tmp_path,
            runner=runner,
            connector=_offline,
            clock=lambda: next(clock),
            sleep=lambda seconds: None,
        ):
            pass
    assert [command[-1] for command in calls] == ["start", "stop"]


def test_failed_start_is_not_owned_and_is_not_stopped(tmp_path):
    from clean_studio.frozen.music import local_index

    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    with pytest.raises(RuntimeError, match="start|unavailable"):
        with local_index(
            _index_config(tmp_path),
            tmp_path,
            runner=runner,
            connector=_offline,
        ):
            pass
    assert [command[-1] for command in calls] == ["start"]


def test_owned_index_cleanup_failure_is_reported(tmp_path):
    from clean_studio.frozen.music import local_index

    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[-1] == "stop" else 0)

    with pytest.raises(RuntimeError, match="cleanup|stop"):
        with local_index(
            _index_config(tmp_path),
            tmp_path,
            runner=runner,
            connector=_sequence_connector(OSError("down"), None),
        ):
            pass
    assert [command[-1] for command in calls] == ["start", "stop"]


@pytest.mark.parametrize("missing", ["home", "java", "both"])
@pytest.mark.parametrize("online", [False, True])
def test_index_without_complete_startup_config_only_accepts_existing_service(
    tmp_path, missing, online
):
    from clean_studio.frozen.music import local_index

    config = _index_config(tmp_path)
    if missing in {"home", "both"}:
        config.pop("neo4j_home")
    if missing in {"java", "both"}:
        config.pop("java_home")

    def forbidden(*args, **kwargs):
        pytest.fail("missing startup paths must never launch Neo4j")

    if online:
        with local_index(
            config,
            tmp_path,
            runner=forbidden,
            connector=_online,
        ):
            pass
    else:
        with pytest.raises(RuntimeError, match="unavailable|neo4j_home|java_home"):
            with local_index(
                config,
                tmp_path,
                runner=forbidden,
                connector=_offline,
            ):
                pass


def test_remote_index_is_never_started_on_demand(tmp_path):
    from clean_studio.frozen.music import local_index

    config = _index_config(tmp_path)
    config["neo4j_uri"] = "bolt://192.0.2.10:17687"

    def forbidden(*args, **kwargs):
        pytest.fail("a remote index must never be started")

    with pytest.raises(RuntimeError, match="loopback|remote"):
        with local_index(
            config,
            tmp_path,
            runner=forbidden,
            connector=_offline,
        ):
            pass


def test_empty_backend_result_never_becomes_a_music_candidate(tmp_path):
    cue, _, _, _, _, provider, _ = _music_search(tmp_path)
    provider.backend = lambda *args: {
        "success": True,
        "source": "soultuner",
        "recommendations": {"success": True, "data": []},
    }

    assert search_music(cue, provider=provider) == []
    result = json.loads(
        next((tmp_path / "searches").glob("search-*/result.json")).read_text(
            encoding="utf-8"
        )
    )
    assert result["candidate_ids"] == []
    assert result["content_accepted"] is False
    assert result["requires_actual_agent_audition"] is True
