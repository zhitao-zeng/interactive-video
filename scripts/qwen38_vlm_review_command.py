#!/usr/bin/env python3
"""Generate production director-review evidence with the project Qwen3.8 VLM."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from novel_manga.util import atomic_write_json, media_duration


POLICY_REVISION = "qwen38-vlm-director-review-v3-style-blocking"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group-id")
    parser.add_argument(
        "--base-url",
        default=os.getenv("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("QWEN38_VLM_REVIEW_TIMEOUT", "300")),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(os.getenv("QWEN38_VLM_MIN_CONFIDENCE", "0.72")),
    )
    return parser.parse_args()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("VLM review response must be a JSON object")
    return value


def _extract_frame(video: Path, second: float, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{second:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _asset_index(series_root: Path) -> dict[str, dict]:
    manifest_path = series_root / "series_assets" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        str(row["asset_id"]): row
        for row in [*manifest.get("characters", []), *manifest.get("locations", [])]
    }


def _resolved_asset_image(series_root: Path, asset: dict) -> Path | None:
    value = asset.get("primary_image")
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = series_root / path
    return path if path.is_file() else None


def build_contact_sheet(
    *,
    video: Path,
    group: dict,
    assets: dict[str, dict],
    series_root: Path,
    output: Path,
) -> list[str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    seconds = media_duration(video)
    ratios = (0.12, 0.50, 0.88)
    with tempfile.TemporaryDirectory(prefix="qwen-vlm-frames-") as directory:
        frame_paths: list[Path] = []
        for index, ratio in enumerate(ratios, start=1):
            frame = Path(directory) / f"frame_{index}.jpeg"
            _extract_frame(video, max(0.05, seconds * ratio), frame)
            frame_paths.append(frame)
        frames = [
            Image.open(path).convert("RGB").resize((480, 853))
            for path in frame_paths
        ]
        asset_ids = [
            *[str(value) for value in group.get("character_asset_ids", [])],
            str(group.get("location_asset_id", "")),
        ]
        asset_ids = [value for value in dict.fromkeys(asset_ids) if value]
        references: list[tuple[str, Image.Image]] = []
        for asset_id in asset_ids:
            asset = assets.get(asset_id, {})
            image_path = _resolved_asset_image(series_root, asset)
            if image_path is None:
                continue
            references.append(
                (
                    f"{asset_id} {asset.get('name', '')}".strip(),
                    Image.open(image_path).convert("RGB").resize((270, 480)),
                )
            )
        reference_rows = max(1, (len(references) + 4) // 5)
        width = 1500
        height = 935 + reference_rows * 540
        sheet = Image.new("RGB", (width, height), "#0b0d12")
        draw = ImageDraw.Draw(sheet)
        title_font = _font(30)
        label_font = _font(22)
        draw.text((20, 12), f"{group['group_id']} 视频帧：12% / 50% / 88%", font=title_font, fill="white")
        for index, frame in enumerate(frames):
            sheet.paste(frame, (20 + index * 493, 64))
        draw.text((20, 880), "锁定角色与场景参考（只用于身份、服装、地点核对）", font=title_font, fill="#ffd166")
        for index, (label, reference) in enumerate(references):
            column = index % 5
            row = index // 5
            x = 20 + column * 295
            y = 935 + row * 540
            sheet.paste(reference, (x, y))
            draw.text((x, y + 486), label[:22], font=label_font, fill="white")
        sheet.save(output, "JPEG", quality=92, subsampling=0)
    return asset_ids


def _expected_contract(
    group: dict,
    assets: dict[str, dict],
    style_contract: dict,
) -> dict:
    expected_characters = []
    for asset_id in group.get("character_asset_ids", []):
        asset = assets.get(str(asset_id), {})
        expected_characters.append(
            {
                "asset_id": str(asset_id),
                "name": asset.get("name"),
                "identity_invariants": asset.get("identity_invariants", []),
                "costume": asset.get("state_variables", {}).get("costume"),
            }
        )
    location_id = str(group.get("location_asset_id", ""))
    location = assets.get(location_id, {})
    return {
        "group_id": group["group_id"],
        "planned_action": group.get("motion_prompt"),
        "combined_text": group.get("combined_text"),
        "spatial_anchor": group.get("spatial_anchor"),
        "expected_characters": expected_characters,
        "expected_location": {
            "asset_id": location_id,
            "name": location.get("name"),
            "identity_invariants": location.get("identity_invariants", []),
        },
        "style_contract": style_contract,
    }


def normalize_review(
    raw: dict,
    *,
    group_id: str,
    expected_ids: set[str],
    known_asset_ids: set[str] | None = None,
    min_confidence: float,
    evidence: str,
    request_hash: str,
) -> dict:
    confidence = raw.get("confidence", 0.0)
    confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    visible_ids = [
        str(value)
        for value in raw.get("visible_asset_ids", [])
        if str(value) in expected_ids
    ]
    known_asset_ids = known_asset_ids or expected_ids
    unknown_ids = [
        str(value)
        for value in raw.get("visible_asset_ids", [])
        if str(value) not in known_asset_ids
    ]
    unexpected_characters = [
        str(value) for value in raw.get("unexpected_named_characters", [])
    ]
    unexpected_characters.extend(unknown_ids)
    conclusive = confidence >= min_confidence
    status = "passed" if conclusive else "inconclusive"
    style_score = raw.get("style_consistency_score")
    style_score = (
        float(style_score) if isinstance(style_score, (int, float)) else 0.0
    )
    return {
        "group_id": group_id,
        "review_status": status,
        "action_observed": bool(raw.get("action_observed")) if conclusive else False,
        "visible_asset_ids": list(dict.fromkeys(visible_ids)),
        "unexpected_named_characters": list(dict.fromkeys(unexpected_characters)),
        "unexpected_objects": [
            str(value) for value in raw.get("unexpected_objects", [])
        ],
        "screen_direction_ok": bool(raw.get("screen_direction_ok")) if conclusive else False,
        "identity_consistency_score": (
            float(raw["identity_consistency_score"])
            if isinstance(raw.get("identity_consistency_score"), (int, float))
            else None
        ),
        "style_match": bool(raw.get("style_match")) if conclusive else False,
        "style_consistency_score": round(style_score, 6),
        "render_medium": str(raw.get("render_medium", "unknown")),
        "style_issues": [str(value) for value in raw.get("style_issues", [])],
        "confidence": round(confidence, 6),
        "evidence": [evidence],
        "request_identity_sha256": request_hash,
        "review_notes": str(raw.get("review_notes", ""))[:1000],
    }


def _inconclusive_review(
    *, group_id: str, evidence: str, request_hash: str, error: Exception
) -> dict:
    return {
        "group_id": group_id,
        "review_status": "inconclusive",
        "action_observed": False,
        "visible_asset_ids": [],
        "unexpected_named_characters": ["VLM_REVIEW_INCONCLUSIVE"],
        "unexpected_objects": [],
        "screen_direction_ok": False,
        "identity_consistency_score": None,
        "style_match": False,
        "style_consistency_score": 0.0,
        "render_medium": "unknown",
        "style_issues": ["VLM_REVIEW_INCONCLUSIVE"],
        "confidence": 0.0,
        "evidence": [evidence],
        "request_identity_sha256": request_hash,
        "review_notes": f"{type(error).__name__}: visual review failed",
    }


def _review_prompt(contract: dict) -> str:
    return (
        "你是竖屏3D国漫的视觉质检导演。联系表上半部是同一视频镜头按时间顺序抽取的三帧，"
        "下半部是锁定角色和场景参考。只根据像素证据与下列合同判断，不得因为合同写了某角色就假定画面中存在。"
        "检查：计划动作是否至少在一帧可见；实际可见角色是否与参考身份和服装一致；是否出现额外具名人物、"
        "多余肢体、刑具、文字、水印或不应出现的物体；运动/视线是否明显违反行动轴。"
        "还必须把上半部视频帧与下半部锁定参考及style_contract比较：皮肤材质、五官雕塑方式、发丝、"
        "布料/木石金属、光照和整体写实程度必须属于同一套3D国漫渲染。真人照片感、塑料玩偶感、二维线稿、"
        "欧美卡通或同一集中写实度明显跳变都判style_match=false。render_medium只能填写"
        "3d_cg_animation、photoreal、2d_illustration、plastic_doll_cg或mixed。"
        "看不清或无法确定时必须降低confidence，不得猜测。只返回一个JSON对象，字段严格为："
        "action_observed:boolean, visible_asset_ids:string[], unexpected_named_characters:string[], "
        "unexpected_objects:string[], screen_direction_ok:boolean, identity_consistency_score:number|null, "
        "style_match:boolean, style_consistency_score:number(0到1), render_medium:string, style_issues:string[], "
        "confidence:number(0到1), review_notes:string。visible_asset_ids只能填写合同里的角色asset_id，"
        "地点asset_id不得填入visible_asset_ids。\n"
        f"镜头合同：{json.dumps(contract, ensure_ascii=False)}"
    )


def _call_vlm(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    image: Path,
    prompt: str,
    api_key: str | None,
) -> tuple[dict, bool]:
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "temperature": 0.1,
            "max_tokens": 3200,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
                "reasoning_effort": "medium",
            },
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    return _json_object(str(message["content"])), bool(
        message.get("reasoning") or message.get("reasoning_content")
    )


def main() -> int:
    args = parse_args()
    if not 0.0 < args.min_confidence <= 1.0:
        raise ValueError("--min-confidence must be in (0, 1]")
    episode_dir = args.episode_dir.resolve()
    manifest_path = (args.manifest or episode_dir / "video_quality_manifest.json").resolve()
    plan_path = (args.plan or episode_dir / "production_plan.json").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    groups = {str(row["group_id"]): row for row in plan.get("visual_groups", [])}
    selected = [
        row
        for row in manifest.get("groups", [])
        if args.group_id is None or str(row.get("group_id")) == args.group_id
    ]
    if args.group_id and not selected:
        raise ValueError(f"unknown group id: {args.group_id}")
    series_root = episode_dir.parent
    assets = _asset_index(series_root)
    story_bible_path = series_root / "story_bible.json"
    story_bible = (
        json.loads(story_bible_path.read_text(encoding="utf-8"))
        if story_bible_path.is_file()
        else {}
    )
    style_contract = {
        "style_fingerprint": story_bible.get("style_fingerprint"),
        "visual_style": story_bible.get("visual_style"),
        "palette": story_bible.get("palette"),
        "typography": story_bible.get("typography"),
        "required_render_medium": "3d_cg_animation",
    }
    review_root = episode_dir / "work" / "vlm_review"
    contacts = review_root / "contacts"
    results = review_root / "results"
    contacts.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    rows = []
    api_key = os.getenv("QWEN38_LOCAL_API_KEY")
    reasoning_seen = False
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for manifest_group in selected:
            group_id = str(manifest_group["group_id"])
            group = groups[group_id]
            raw_video = episode_dir / str(group["raw_video_path"])
            contact = contacts / f"{group_id}.jpeg"
            build_contact_sheet(
                video=raw_video,
                group=group,
                assets=assets,
                series_root=series_root,
                output=contact,
            )
            contract = _expected_contract(group, assets, style_contract)
            request_hash = hashlib.sha256(
                json.dumps(
                    {
                        "policy": POLICY_REVISION,
                        "model": args.model,
                        "contract": contract,
                        "contact_sha256": _sha256_file(contact),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            cache = results / f"{group_id}.json"
            if cache.is_file():
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if cached.get("request_identity_sha256") == request_hash:
                    rows.append(cached)
                    continue
            evidence = str(contact.relative_to(episode_dir))
            expected_ids = {
                str(reference["asset_id"])
                for reference in manifest_group.get("references", [])
                if reference.get("role") == "character_identity_costume"
            }
            known_asset_ids = {
                str(reference["asset_id"])
                for reference in manifest_group.get("references", [])
            }
            try:
                raw, had_reasoning = _call_vlm(
                    client,
                    base_url=args.base_url,
                    model=args.model,
                    image=contact,
                    prompt=_review_prompt(contract),
                    api_key=api_key,
                )
                reasoning_seen = reasoning_seen or had_reasoning
                row = normalize_review(
                    raw,
                    group_id=group_id,
                    expected_ids=expected_ids,
                    known_asset_ids=known_asset_ids,
                    min_confidence=args.min_confidence,
                    evidence=evidence,
                    request_hash=request_hash,
                )
            except (OSError, ValueError, KeyError, httpx.HTTPError) as error:
                row = _inconclusive_review(
                    group_id=group_id,
                    evidence=evidence,
                    request_hash=request_hash,
                    error=error,
                )
            atomic_write_json(cache, row)
            rows.append(row)
    report = {
        "schema_version": 1,
        "reviewer": "qwen3.8-27b-vlm",
        "review_policy_revision": POLICY_REVISION,
        "model": args.model,
        "thinking_enabled": True,
        "reasoning_returned_separately": reasoning_seen,
        "min_confidence": args.min_confidence,
        "groups": rows,
    }
    atomic_write_json(args.output.resolve(), report)
    if any(row.get("review_status") == "inconclusive" for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
