from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qwen38_vlm_service.sh"


def test_qwen38_vlm_service_script_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_qwen38_vlm_service_enables_images_and_is_project_isolated() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"image":{"count":8' in text
    assert '"video":0' in text
    assert "--limit-mm-per-prompt" in text
    assert "--language-model-only" not in text
    assert "QWEN38_VLM_GPUS" in text
    assert "novel-manga-qwen38-vlm" in text
    assert "Qwen3.8-27B-Project" in text
    assert "--tensor-parallel-size" in text
    assert "127.0.0.1" in text
    assert "--no-enable-log-requests" in text
    assert '"enable_thinking":true' in text
    assert '"reasoning_effort":"medium"' in text
    assert "io.codex.service" in text
