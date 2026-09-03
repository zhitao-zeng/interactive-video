import json
from pathlib import Path

from PIL import Image

from novel_manga.task_ledger import build_generation_task_ledger


def test_task_ledger_deduplicates_copied_task_metadata_and_omits_secrets(
    tmp_path: Path,
) -> None:
    novel = tmp_path / "novel"
    episode = novel / "novel_1"
    canonical = episode / "work" / "visual_group_keyframes" / "visual_001.jpeg"
    attempt = (
        episode
        / "work"
        / "visual_attempts"
        / "visual_001"
        / "identity"
        / "attempt_01"
        / "keyframe.jpeg"
    )
    for path in (canonical, attempt):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 24), "blue").save(path)
        path.with_suffix(path.suffix + ".task.json").write_text(
            json.dumps(
                {
                    "task_id": "task-one",
                    "kind": "image",
                    "model": "image-model",
                    "request_sha256": "request-one",
                    "authenticated_url": "must-not-leak",
                    "api_key": "must-not-leak",
                }
            ),
            encoding="utf-8",
        )
    (episode / "production_plan.json").write_text(
        json.dumps(
            {
                "units": [],
                "visual_groups": [
                    {
                        "keyframe_path": "work/visual_group_keyframes/visual_001.jpeg",
                        "raw_video_path": "work/visual_group_video/visual_001.mp4",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    ledger = build_generation_task_ledger(novel)

    assert ledger["summary"]["task_metadata_files"] == 2
    assert ledger["summary"]["unique_remote_tasks"] == 1
    assert len(ledger["tasks"]) == 1
    assert ledger["tasks"][0]["canonical"] is True
    serialized = json.dumps(ledger)
    assert "must-not-leak" not in serialized
