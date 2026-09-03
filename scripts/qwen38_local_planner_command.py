#!/usr/bin/env python3
"""Command-planner adapter for the project-owned Qwen3.8 multimodal service."""

from __future__ import annotations

import difflib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

import httpx

try:
    from scripts.deepseek_local_planner_command import (
        CONTENT_DIRECTION,
        OPERATION_GUIDANCE,
        normalize_diagnosis,
        normalize_episode_plan,
        parse_args,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/, not its parent.
    from deepseek_local_planner_command import (
        CONTENT_DIRECTION,
        OPERATION_GUIDANCE,
        normalize_diagnosis,
        normalize_episode_plan,
        parse_args,
    )
from novel_manga.script_planning import source_evidence_units


REQUIRED_3D_STYLE = (
    "高品质中国3D国漫动画，简化雕塑式东方面部结构，自然人体比例，"
    "哑光无毛孔皮肤，束状设计发丝，克制Toon-PBR布料木石金属，"
    "电影化但清楚的体积光；禁止真人照片、塑料玩偶、欧美卡通、"
    "二维线稿、平面赛璐璐和游戏角色创建界面"
)


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Qwen3.8 local planner must return one JSON object")
    return value


def _compact_text(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", value))


def _ngrams(value: str, width: int) -> Counter[str]:
    value = _compact_text(value)
    return Counter(
        value[index : index + width]
        for index in range(max(0, len(value) - width + 1))
    )


def _monotonic_evidence_quotes(
    events: list[dict],
    evidence: list[str],
    character_names: list[str],
) -> list[str]:
    if not events or not evidence:
        return [str(event.get("source_quote", "")) for event in events]
    widths = (1, 2, 3)
    evidence_sets = {
        width: [set(_ngrams(row, width)) for row in evidence]
        for width in widths
    }
    inverse_frequency: dict[int, dict[str, float]] = {}
    for width in widths:
        document_frequency = Counter(
            gram for grams in evidence_sets[width] for gram in grams
        )
        inverse_frequency[width] = {
            gram: math.log((len(evidence) + 1) / (count + 1)) + 1
            for gram, count in document_frequency.items()
        }

    scores: list[list[float]] = []
    event_count = len(events)
    evidence_count = len(evidence)
    for event_index, event in enumerate(events):
        target = " ".join(
            [
                str(event.get("description", "")),
                *[str(value) for value in event.get("characters", [])],
            ]
        )
        target_grams = {width: _ngrams(target, width) for width in widths}
        concrete_tokens = set(
            re.findall(
                r"(?:[零〇一二三四五六七八九十百千万两\d]+(?:岁|段|星)|"
                r"战之气旋|黑色古戒|一夜之间|越来越少|百年|第一人|第二人)",
                target,
            )
        )
        row_scores = []
        for evidence_index, candidate in enumerate(evidence):
            score = 0.15 * difflib.SequenceMatcher(
                None, _compact_text(target), _compact_text(candidate)
            ).ratio()
            for width, weight in ((1, 0.10), (2, 0.55), (3, 0.35)):
                denominator = sum(
                    count * inverse_frequency[width].get(gram, 1.0)
                    for gram, count in target_grams[width].items()
                ) or 1.0
                overlap = sum(
                    count * inverse_frequency[width].get(gram, 1.0)
                    for gram, count in target_grams[width].items()
                    if gram in evidence_sets[width][evidence_index]
                )
                score += weight * overlap / denominator
            score += sum(
                0.12
                for name in character_names
                if name in target and name in candidate
            )
            score += sum(
                0.45 if token in candidate else -0.08
                for token in concrete_tokens
            )
            event_position = event_index / max(1, event_count - 1)
            evidence_position = evidence_index / max(1, evidence_count - 1)
            score -= 0.8 * abs(event_position - evidence_position)
            row_scores.append(score)
        scores.append(row_scores)

    totals = [[float("-inf")] * evidence_count for _ in events]
    previous = [[-1] * evidence_count for _ in events]
    totals[0] = list(scores[0])
    require_distinct_rows = evidence_count >= event_count
    for event_index in range(1, event_count):
        best_total = float("-inf")
        best_index = -1
        for evidence_index in range(evidence_count):
            candidate_index = (
                evidence_index - 1 if require_distinct_rows else evidence_index
            )
            if (
                candidate_index >= 0
                and totals[event_index - 1][candidate_index] > best_total
            ):
                best_total = totals[event_index - 1][candidate_index]
                best_index = candidate_index
            totals[event_index][evidence_index] = (
                best_total + scores[event_index][evidence_index]
            )
            previous[event_index][evidence_index] = best_index
    evidence_index = max(
        range(evidence_count), key=lambda index: totals[-1][index]
    )
    indexes = [evidence_index]
    for event_index in range(event_count - 1, 0, -1):
        evidence_index = previous[event_index][evidence_index]
        indexes.append(evidence_index)
    indexes.reverse()
    return [evidence[index] for index in indexes]


def normalize_qwen_diagnosis(result: dict, payload: dict) -> dict:
    result = normalize_diagnosis(result, payload)
    evidence = list(payload.get("source_evidence", []))
    character_names = [
        str(row.get("name", ""))
        for row in payload.get("story_bible", {}).get("characters", [])
        if row.get("name")
    ]
    events = [row for row in result.get("events", []) if isinstance(row, dict)]
    aligned_quotes = _monotonic_evidence_quotes(
        events, evidence, character_names
    )
    for event, quote in zip(events, aligned_quotes, strict=True):
        event["source_quote"] = quote
    return result


def build_system(operation: str, payload: dict) -> str:
    system = CONTENT_DIRECTION + "\n\n当前操作：" + OPERATION_GUIDANCE.get(
        operation,
        "严格按请求中的requirements和schema完成当前规划操作。",
    )
    if operation == "diagnose_episode":
        evidence = source_evidence_units(
            str(payload.get("episode", {}).get("source_text", ""))
        )
        payload["source_evidence"] = evidence
        system += (
            "\n诊断中的hook_source_quote和每个event.source_quote必须从请求的"
            "source_evidence数组中选择一整行逐字复制。characters只能使用"
            "story_bible.characters中的name；匿名群众只能写在description。"
            "只保留8到14个不可合并的关键事件；description不超过80个汉字，"
            "状态字段不超过100个汉字，causes最多3项。禁止复述整章或生成逐句事件。"
        )
    if operation == "plan_episode":
        source_chars = len(
            re.sub(
                r"\s+",
                "",
                str(payload.get("episode", {}).get("source_text", "")),
            )
        )
        system += (
            f"\n当前章有效长度约{source_chars}字。成片只允许14到20个shot；"
            "turn总数至少12个，所有turn.text合计至少260个汉字；不足时补充一个"
            "有原文依据的短对白、画外声或必要旁白，不得新增事件。"
            "匿名少年、族人和围观群众不得作为可见说话者，代表性嘲讽压缩为"
            "画外群声或楚焱的无声反应，不能逐人生成近景。"
        )
    if operation == "develop_series":
        system += (
            "\npressure_loop中的箭头顺序必须严格按chapter_projections的章节编号递增；"
            "不得把后文章节事件写在前文章节事件之前。strategy_creates_problem与setup/payoff义务"
            "只能使用chapter_diagnoses中明确出现的事实，不得补充未建立的能力或动机。"
        )
    if operation == "plan_beat_script":
        system += (
            "\ntitle_card只允许表达时间或地点跳转；原文中的测验成绩、宣布、问答和引号对白"
            "必须由具名角色可见对白或无名角色画外对白说出，不得改成title_card或旁白。"
        )
        retention_beat = payload.get("retention_beat", {})
        if retention_beat.get("function") == "cliffhanger":
            system += (
                "这是章末cliffhanger节拍。最后一镜必须实际表现retention_beat.promise中的"
                "问题、异象、决定或关系动作，并用可见动作或可听声音留下未闭合后果；"
                "不得只让主角独自离场，也不得只在shot_intent注释里写悬念。"
            )
    if payload.get("repair"):
        feedback = json.dumps(
            payload["repair"].get("validation_errors", []),
            ensure_ascii=False,
        )
        system += (
            "\n上一稿未通过校验。必须逐项修复repair.validation_errors，不得重复"
            "被点名的未知角色、改写引用、字段缺失或Schema错误。"
        )
        if "source_quote is not grounded" in feedback:
            system += (
                "每个turn.source_quote必须从episode.source_text逐字复制一段完整连续原文；"
                "不得概括、拼接、省略号改写或自行补标点。无法找到独立引文时，复制所属shot.source_quote。"
            )
        if "concrete facts lack a current-timeline carrier" in feedback:
            system += (
                "把校验反馈冒号后列出的具体词中至少两个逐字写入当前时间线的非silent_action turn.text；"
                "优先用具名角色可见对白、无名族人画外对白或证据宣读，且用derived+serves绑定对应event。"
            )
        if "beat script did not cover requested events" in feedback:
            system += (
                "retention_beat.event_ids中的每个event_id都必须至少出现在一个shot.event_ids中，"
                "而且该shot.change、blocking、source_quote与turn必须实际表现chapter_diagnosis中该事件；"
                "缺失的是后续事件时必须新增后续镜头并推进close_state，禁止只给已有镜头补标签。"
            )
    return system


def token_budget(operation: str) -> int:
    defaults = {
        "build_bible": 16000,
        "diagnose_episode": 16000,
        "plan_episode": 32000,
        "develop_series": 24000,
        "plan_showrunner": 20000,
        "plan_beat_script": 16000,
        "plan_beat_direction": 16000,
        "review_episode": 10000,
        "blind_compare": 8000,
        "review_series_development": 8000,
        "update_series_state": 14000,
    }
    configured = os.getenv("QWEN38_LOCAL_MAX_TOKENS")
    return int(configured) if configured else defaults.get(operation, 16000)


def reasoning_effort(operation: str) -> str:
    if operation in {
        "build_bible",
        "diagnose_episode",
        "review_series_development",
        "review_episode",
        "blind_compare",
        "update_series_state",
    }:
        return "low"
    return "medium"


def bounded_schema(schema: dict, operation: str) -> dict:
    schema = json.loads(json.dumps(schema, ensure_ascii=False))
    if operation == "plan_beat_direction":
        motion_beat = schema.get("$defs", {}).get("MotionBeat", {})
        required = list(motion_beat.get("required", []))
        motion_beat["required"] = list(
            dict.fromkeys(
                [
                    *required,
                    "seconds",
                    "actor",
                    "target",
                    "action_type",
                    "end_state",
                ]
            )
        )
        return schema
    if operation == "build_bible":
        properties = schema.get("properties", {})
        for field, minimum, maximum in (
            ("characters", 1, 12),
            ("locations", 1, 10),
            ("continuity_rules", 1, 10),
        ):
            value = properties.get(field)
            if isinstance(value, dict):
                value["minItems"] = minimum
                value["maxItems"] = maximum
        for field, maximum in (
            ("novel_title", 80),
            ("genre", 120),
            ("visual_style", 500),
            ("palette", 300),
            ("typography", 240),
        ):
            value = properties.get(field)
            if isinstance(value, dict):
                value["maxLength"] = maximum
        character_properties = (
            schema.get("$defs", {}).get("Character", {}).get("properties", {})
        )
        for field, value in character_properties.items():
            if not isinstance(value, dict):
                continue
            if field in {"face_anchors", "episode_costumes"}:
                value["maxItems"] = 8
            else:
                value["maxLength"] = 320
        return schema
    if operation != "diagnose_episode":
        return schema
    events = schema.get("properties", {}).get("events")
    if isinstance(events, dict):
        events["minItems"] = 4
        events["maxItems"] = 16
    event_properties = (
        schema.get("$defs", {}).get("ChapterEvent", {}).get("properties", {})
    )
    for field, maximum in (
        ("description", 180),
        ("source_quote", 800),
        ("state_change", 180),
        ("potential_foreshadowing", 180),
    ):
        value = event_properties.get(field)
        if isinstance(value, dict):
            value["maxLength"] = maximum
    for field, maximum in (("characters", 6), ("causes", 4)):
        value = event_properties.get(field)
        if isinstance(value, dict):
            value["maxItems"] = maximum
    for field in (
        "core_event",
        "chapter_start_state",
        "chapter_end_state",
        "episode_state_change",
        "strongest_hook_candidate",
        "potential_foreshadowing",
    ):
        value = schema.get("properties", {}).get(field)
        if isinstance(value, dict):
            value["maxLength"] = 240
    return schema


def response_format(payload: dict, operation: str) -> dict:
    schema = payload.get("schema")
    if not isinstance(schema, dict):
        return {"type": "json_object"}
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", operation)[:64] or "planner_result"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": bounded_schema(schema, operation),
        },
    }


def uses_two_stage_generation(operation: str) -> bool:
    return operation in {
        "build_bible",
        "diagnose_episode",
        "plan_beat_script",
        "plan_beat_direction",
    }


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    operation = args.operation
    base_url = os.getenv("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1")
    model = os.getenv("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project")
    system = build_system(operation, payload)
    request_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    repair = request_payload.get("repair")
    if isinstance(repair, dict) and repair.get("resample"):
        repair.pop("previous_response", None)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(request_payload, ensure_ascii=False),
        },
    ]
    request = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": token_budget(operation),
        "response_format": response_format(payload, operation),
        "chat_template_kwargs": {
            "enable_thinking": True,
            "preserve_thinking": True,
            "reasoning_effort": reasoning_effort(operation),
        },
        "messages": messages,
    }
    headers: dict[str, str] = {}
    api_key = os.getenv("QWEN38_LOCAL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = float(os.getenv("QWEN38_LOCAL_TIMEOUT", "900"))
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        if uses_two_stage_generation(operation):
            analysis_response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "temperature": 0.2,
                    "max_tokens": 4000,
                    "chat_template_kwargs": {
                        "enable_thinking": True,
                        "preserve_thinking": True,
                        "reasoning_effort": "low",
                    },
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                system
                                + "\n先分析事实、边界和可能冲突，只给出不超过1200汉字的内部规划摘要；"
                                "本阶段不要生成最终JSON。"
                            ),
                        },
                        messages[1],
                    ],
                },
            )
            analysis_response.raise_for_status()
            analysis_message = analysis_response.json()["choices"][0]["message"]
            analysis_notes = str(
                analysis_message.get("content")
                or analysis_message.get("reasoning")
                or ""
            )[-6000:]
            request["chat_template_kwargs"] = {
                "enable_thinking": False,
                "preserve_thinking": False,
            }
            request["messages"] = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "内部分析已经完成。根据下面摘要和原始请求，直接生成符合JSON Schema的"
                        "最终对象；不要继续思考，不要输出Markdown或解释。\n内部摘要："
                        + analysis_notes
                    ),
                },
            ]
        response = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=request,
        )
        response.raise_for_status()
        body = response.json()
    choice = body["choices"][0]
    content_value = choice["message"].get("content")
    if not isinstance(content_value, str) or not content_value.strip():
        raise ValueError(
            "Qwen3.8 planner returned no final JSON; finish_reason="
            + str(choice.get("finish_reason"))
        )
    content = content_value
    try:
        result = extract_json(content)
    except (json.JSONDecodeError, ValueError) as error:
        message = choice.get("message", {})
        usage = body.get("usage", {})
        raise ValueError(
            "Qwen3.8 planner JSON parse failed; "
            f"operation={operation}; finish_reason={choice.get('finish_reason')}; "
            f"content_chars={len(content)}; "
            f"reasoning_chars={len(str(message.get('reasoning') or ''))}; "
            f"completion_tokens={usage.get('completion_tokens')}"
        ) from error
    if operation == "build_bible":
        result["visual_style"] = REQUIRED_3D_STYLE
    elif operation == "diagnose_episode":
        result = normalize_qwen_diagnosis(result, payload)
    elif operation == "plan_episode":
        result = normalize_episode_plan(result)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
