import json
import sys
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.runtime_backends import RuntimeEvidenceBackends


def test_director_review_backend_preserves_inconclusive_output(tmp_path: Path) -> None:
    adapter = tmp_path / "review_adapter.py"
    adapter.write_text(
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--episode-dir')
parser.add_argument('--manifest')
parser.add_argument('--plan')
parser.add_argument('--output')
args = parser.parse_args()
Path(args.output).write_text(json.dumps({
    'reviewer': 'fake-vlm',
    'groups': [{'group_id': 'visual_001', 'review_status': 'inconclusive'}],
}), encoding='utf-8')
raise SystemExit(2)
""".strip(),
        encoding="utf-8",
    )
    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    manifest = episode_dir / "video_quality_manifest.json"
    plan = episode_dir / "production_plan.json"
    manifest.write_text(json.dumps({"groups": []}), encoding="utf-8")
    plan.write_text(json.dumps({"visual_groups": []}), encoding="utf-8")
    output = episode_dir / "director_video_review_vlm.json"
    settings = Settings(
        director_review_command=f"{sys.executable} {adapter}",
    )

    report = RuntimeEvidenceBackends(settings).generate_director_review(
        episode_dir=episode_dir,
        manifest=manifest,
        plan=plan,
        output=output,
    )

    assert report is not None
    assert report["groups"][0]["review_status"] == "inconclusive"
