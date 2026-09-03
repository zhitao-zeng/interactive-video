import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qwen38_vlm_review_command.py"


def _module():
    spec = importlib.util.spec_from_file_location("qwen38_vlm_review_command", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_low_confidence_visual_review_is_inconclusive() -> None:
    module = _module()
    row = module.normalize_review(
        {
            "action_observed": True,
            "visible_asset_ids": ["character_001"],
            "screen_direction_ok": True,
            "confidence": 0.5,
            "style_match": True,
            "style_consistency_score": 0.9,
            "render_medium": "3d_cg_animation",
        },
        group_id="visual_001",
        expected_ids={"character_001"},
        known_asset_ids={"character_001"},
        min_confidence=0.72,
        evidence="work/contact.jpeg",
        request_hash="hash",
    )

    assert row["review_status"] == "inconclusive"
    assert row["action_observed"] is False
    assert row["screen_direction_ok"] is False


def test_visual_review_rejects_unregistered_asset_ids() -> None:
    module = _module()
    row = module.normalize_review(
        {
            "action_observed": True,
            "visible_asset_ids": [
                "character_001",
                "location_001",
                "character_unknown",
            ],
            "unexpected_named_characters": [],
            "unexpected_objects": [],
            "screen_direction_ok": True,
            "identity_consistency_score": 0.9,
            "style_match": True,
            "style_consistency_score": 0.88,
            "render_medium": "3d_cg_animation",
            "style_issues": [],
            "confidence": 0.9,
        },
        group_id="visual_001",
        expected_ids={"character_001"},
        known_asset_ids={"character_001", "location_001"},
        min_confidence=0.72,
        evidence="work/contact.jpeg",
        request_hash="hash",
    )

    assert row["visible_asset_ids"] == ["character_001"]
    assert row["unexpected_named_characters"] == ["character_unknown"]
    assert row["style_match"] is True
    assert row["style_consistency_score"] == 0.88
