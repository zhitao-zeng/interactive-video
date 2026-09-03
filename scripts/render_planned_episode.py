#!/usr/bin/env python3
"""Render one already-audited episode while later chapters keep planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import (
    ChapterDiagnosis,
    EpisodeContract,
    EpisodePlan,
    ScriptQualityReport,
    SeriesState,
    StoryBible,
)
from novel_manga.pipeline import NovelPipeline
from novel_manga.script_planning import evaluate_script_quality
from novel_manga.util import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--episode-index", type=int, required=True)
    parser.add_argument("--title")
    parser.add_argument("--provider", choices=("phanrouter", "command"), default="phanrouter")
    parser.add_argument(
        "--admission-mode",
        choices=("preview", "production"),
        default="production",
    )
    args = parser.parse_args()

    root = args.bundle_dir.resolve()
    novel_id = root.name
    novel = read_novel(args.source, novel_id=novel_id, title=args.title)
    if not 1 <= args.episode_index <= len(novel.episodes):
        raise ValueError("episode index is outside the parsed source")
    episode = novel.episodes[args.episode_index - 1]
    prefix = f"episode_{args.episode_index:03d}"
    required = {
        "plan": root / f"{prefix}_plan.json",
        "diagnosis": root / f"{prefix}_diagnosis.json",
        "quality": root / f"{prefix}_script_quality.json",
        "state": root / f"{prefix}_series_state.json",
        "bible": root / "story_bible.json",
        "contract": root / "script_drafts" / prefix / "episode_contract.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise RuntimeError(
            "audited planning bundle is incomplete: " + ", ".join(missing)
        )

    bible = StoryBible.model_validate_json(required["bible"].read_text(encoding="utf-8"))
    if "3D国漫" not in bible.visual_style or "禁止真人照片" not in bible.visual_style:
        raise RuntimeError("story bible is not locked to the approved 3D style")
    plan = EpisodePlan.model_validate_json(required["plan"].read_text(encoding="utf-8"))
    diagnosis = ChapterDiagnosis.model_validate_json(
        required["diagnosis"].read_text(encoding="utf-8")
    )
    stored_quality = ScriptQualityReport.model_validate_json(
        required["quality"].read_text(encoding="utf-8")
    )
    previous_state = None
    if args.episode_index > 1:
        previous_path = root / f"episode_{args.episode_index - 1:03d}_series_state.json"
        if not previous_path.is_file():
            raise RuntimeError("previous episode state is required for sequential validation")
        previous_state = SeriesState.model_validate_json(
            previous_path.read_text(encoding="utf-8")
        )
    quality = evaluate_script_quality(
        plan,
        diagnosis,
        episode,
        qualitative=stored_quality,
        previous_state=previous_state,
    )
    if not quality.passed:
        raise RuntimeError("episode script no longer passes the quality gate")

    settings = Settings.from_env(
        provider=args.provider,
        output_root=root.parent,
        admission_mode=args.admission_mode,
    )
    pipeline = NovelPipeline(settings)
    assets = pipeline.asset_factory.build(root / "series_assets", bible)
    video_id = f"{novel_id}_{episode.index}"
    episode_dir = root / video_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    atomic_write_json(
        episode_dir / "chapter_diagnosis.json", diagnosis.model_dump(mode="json")
    )
    atomic_write_json(
        episode_dir / "script_quality_report.json", quality.model_dump(mode="json")
    )
    atomic_write_json(
        episode_dir / "updated_series_state.json",
        SeriesState.model_validate_json(required["state"].read_text(encoding="utf-8")).model_dump(
            mode="json"
        ),
    )
    atomic_write_json(
        episode_dir / "episode_contract.json",
        EpisodeContract.model_validate_json(
            required["contract"].read_text(encoding="utf-8")
        ).model_dump(mode="json"),
    )
    admission = pipeline.episode_runtime.run(
        novel_dir=root,
        episode_dir=episode_dir,
        episode=episode,
        episode_plan=plan,
        bible=bible,
        series_assets=assets,
        final_video=episode_dir / f"{video_id}.mp4",
        cover=episode_dir / f"{video_id}_cover.jpeg",
        ending=episode_dir / f"{video_id}_ending.jpeg",
        video_id=video_id,
        episode_count=len(novel.episodes),
    )
    print(
        json.dumps(
            {
                "video_id": video_id,
                "passed": bool(admission.get("passed")),
                "video": str(episode_dir / f"{video_id}.mp4"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if admission.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
