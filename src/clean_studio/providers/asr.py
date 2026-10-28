from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import unicodedata

from clean_studio.frozen.media import extract_native_audio, media_duration


def _speech_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, heard in enumerate(hypothesis, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected != heard),
                )
            )
        previous = current
    return previous[-1]


def _phonemes(text: str):
    from pypinyin import lazy_pinyin

    return "".join(
        token
        for part in lazy_pinyin(text, errors=lambda value: list(value))
        for token in part
    )


def evaluate_speech(
    reference: str,
    transcript: Mapping[str, object],
    *,
    video_sha256: str,
    max_cer: float = 0.5,
) -> dict:
    if not isinstance(transcript, Mapping) or transcript.get("status") != "completed":
        raise ValueError("ASR transcription must complete before speech evaluation")
    if transcript.get("video_sha256") != video_sha256:
        raise ValueError("ASR video_sha256 does not match the current video bytes")
    hypothesis = transcript.get("hypothesis")
    if not isinstance(hypothesis, str):
        raise ValueError("ASR hypothesis must be a string before speech evaluation")
    reference_key = _speech_key(reference)
    hypothesis_key = _speech_key(hypothesis)
    errors = _edit_distance(reference_key, hypothesis_key)
    phonetic_errors = _edit_distance(_phonemes(reference_key), _phonemes(hypothesis_key))
    if reference_key:
        cer = round(errors / len(reference_key), 6)
        phonetic_cer = round(phonetic_errors / max(1, len(_phonemes(reference_key))), 6)
        passed = cer <= max_cer or phonetic_cer <= max_cer
        phonetic_equivalence = bool(cer > max_cer >= phonetic_cer)
    else:
        # A shot that owes no line has no spoken-content contract: there is no
        # reference to score against, so transcribing ambient audio (or the ASR
        # model hallucinating on it) cannot prove a defect. The hypothesis stays
        # in the receipt for diagnosis.
        cer = None
        phonetic_cer = None
        passed = True
        phonetic_equivalence = False
    return {
        **dict(transcript),
        "reference": reference,
        "reference_normalized": reference_key,
        "hypothesis_normalized": hypothesis_key,
        "video_sha256": video_sha256,
        "errors": errors,
        "cer": cer,
        "phonetic_errors": phonetic_errors,
        "phonetic_cer": phonetic_cer,
        "phonetic_equivalence": phonetic_equivalence,
        "max_cer": max_cer,
        "passed": passed,
        "evaluation": "clean-current-shot-dialogue-cer-v2-phonetic",
        "evidence_scope": "asr_only",
        "content_accepted": False,
        "final_audio_accepted": False,
    }


class SenseVoiceASRProvider:
    def __init__(
        self,
        python: str | Path,
        model_dir: str | Path,
        *,
        helper_path: str | Path | None = None,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        threads: int = 4,
        runner=None,
    ) -> None:
        if Path(python).resolve() == Path(sys.executable).resolve():
            raise ValueError("SenseVoice requires a Python independent of the main interpreter")
        self.python = str(python)
        self.model_dir = Path(model_dir)
        self.helper_path = Path(
            helper_path or Path(__file__).resolve().parents[1] / "frozen/asr_worker.py"
        )
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.threads = threads
        self.runner = runner or subprocess.run

    def transcribe(
        self,
        video: str | Path,
        *,
        output: str | Path | None = None,
        reference: str = "",
        duration: float | None = None,
    ) -> dict:
        video = Path(video)
        output = Path(output or video.with_suffix(video.suffix + ".asr.json"))
        seconds = (
            media_duration(video, ffprobe=self.ffprobe, runner=self.runner)
            if duration is None
            else float(duration)
        )
        if seconds <= 0:
            raise ValueError("ASR requires a positive complete audio duration")
        output.parent.mkdir(parents=True, exist_ok=True)
        audio = output.with_suffix(output.suffix + ".native.wav")
        segments_path = output.with_suffix(output.suffix + ".segments.json")
        raw_path = output.with_suffix(output.suffix + ".raw.json")
        extract_native_audio(
            video,
            audio,
            ffmpeg=self.ffmpeg,
            runner=self.runner,
        )
        segments_path.write_text(
            json.dumps([[0.0, seconds]]), encoding="utf-8"
        )
        self.runner(
            [
                self.python,
                str(self.helper_path),
                "--audio",
                str(audio),
                "--segments",
                str(segments_path),
                "--output",
                str(raw_path),
                "--model-dir",
                str(self.model_dir),
                "--threads",
                str(self.threads),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if not raw_path.is_file():
            raise RuntimeError("SenseVoice worker did not create its JSON output")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
            raise ValueError("SenseVoice worker returned an invalid result")
        segments = []
        for row in raw["segments"]:
            if not isinstance(row, dict) or not isinstance(row.get("hypothesis"), str):
                raise ValueError("SenseVoice worker returned an invalid segment")
            segments.append(row)
        hypothesis = "".join(row["hypothesis"] for row in segments)
        receipt = {
            "status": "completed",
            "backend": str(raw.get("backend", "sherpa-onnx-sensevoice")),
            "reference": reference,
            "hypothesis": hypothesis,
            "segments": segments,
            "duration_seconds": seconds,
            "video_sha256": sha256(video.read_bytes()).hexdigest(),
            "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
            "evidence_scope": "asr_only",
            "content_accepted": False,
            "final_audio_accepted": False,
        }
        output.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return receipt


ASRProvider = SenseVoiceASRProvider
