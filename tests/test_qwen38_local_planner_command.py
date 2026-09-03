import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qwen38_local_planner_command.py"


def _module():
    spec = importlib.util.spec_from_file_location("qwen38_local_planner_command", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen38_planner_extracts_final_json_after_thinking_text() -> None:
    module = _module()

    assert module.extract_json('分析已经完成。\n{"shots": []}') == {"shots": []}


def test_qwen38_planner_uses_bounded_operation_budgets(monkeypatch) -> None:
    module = _module()
    monkeypatch.delenv("QWEN38_LOCAL_MAX_TOKENS", raising=False)

    assert module.token_budget("diagnose_episode") == 16000
    assert module.token_budget("build_bible") == 16000
    assert module.token_budget("plan_episode") == 32000
    assert module.token_budget("review_episode") == 10000
    assert module.token_budget("plan_beat_script") == 16000


def test_qwen38_planner_uses_request_json_schema() -> None:
    module = _module()
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }

    response_format = module.response_format(
        {"schema": schema}, "diagnose_episode"
    )

    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "diagnose_episode",
            "strict": True,
            "schema": schema,
        },
    }


def test_qwen38_repair_prompt_targets_grounding_and_fact_carriers() -> None:
    module = _module()
    system = module.build_system(
        "plan_beat_script",
        {
            "repair": {
                "validation_errors": [
                    "source_quote is not grounded",
                    "concrete facts lack a current-timeline carrier",
                    "beat script did not cover requested events: event_011",
                ]
            }
        },
    )

    assert "逐字复制" in system
    assert "至少两个" in system
    assert "非silent_action" in system
    assert "必须新增后续镜头" in system


def test_qwen38_diagnosis_prefers_semantically_matching_evidence() -> None:
    module = _module()
    payload = {
        "episode": {
            "source_text": "楚焱，战之气：三段！级别：低级！\n楚媚，战之气：七段！级别：高级！"
        },
        "story_bible": {
            "characters": [{"name": "楚焱"}, {"name": "楚媚"}]
        },
    }
    module.build_system("diagnose_episode", payload)
    result = module.normalize_qwen_diagnosis(
        {
            "events": [
                {
                    "description": "楚焱三段低级成绩被公布",
                    "characters": ["楚焱"],
                    "source_quote": "楚媚，战之气：七段！级别：高级！",
                }
            ]
        },
        payload,
    )

    assert result["events"][0]["source_quote"] == (
        "楚焱，战之气：三段！级别：低级！"
    )


def test_qwen38_diagnosis_alignment_preserves_event_order() -> None:
    module = _module()
    evidence = [
        "楚焱测出三段低级。",
        "广场人群爆发嘲讽。",
        "楚媚测出七段高级。",
        "楚烟儿走到楚焱面前弯腰。",
    ]
    events = [
        {"description": "楚焱三段成绩公布", "characters": ["楚焱"]},
        {"description": "众人嘲笑楚焱", "characters": ["楚焱"]},
        {"description": "楚媚七段成绩公布", "characters": ["楚媚"]},
        {"description": "楚烟儿向楚焱弯腰", "characters": ["楚烟儿", "楚焱"]},
    ]

    aligned = module._monotonic_evidence_quotes(
        events, evidence, ["楚焱", "楚媚", "楚烟儿"]
    )

    assert aligned == evidence


def test_qwen38_develop_series_system_prompt_is_independent_of_episode() -> None:
    module = _module()

    system = module.build_system("develop_series", {"chapter_diagnoses": []})

    assert "章节编号递增" in system
    assert "当前章有效长度" not in system


def test_qwen38_diagnosis_uses_low_thinking_and_bounded_schema() -> None:
    module = _module()
    schema = {
        "type": "object",
        "properties": {"events": {"type": "array", "items": {}}},
        "$defs": {
            "ChapterEvent": {
                "properties": {
                    "description": {"type": "string"},
                    "source_quote": {"type": "string"},
                    "characters": {"type": "array", "items": {}},
                    "causes": {"type": "array", "items": {}},
                }
            }
        },
    }

    bounded = module.bounded_schema(schema, "diagnose_episode")

    assert module.reasoning_effort("diagnose_episode") == "low"
    assert module.reasoning_effort("build_bible") == "low"
    assert module.uses_two_stage_generation("diagnose_episode") is True
    assert module.uses_two_stage_generation("build_bible") is True
    assert module.uses_two_stage_generation("plan_beat_script") is True
    assert module.uses_two_stage_generation("plan_beat_direction") is True
    assert module.reasoning_effort("plan_beat_script") == "medium"
    assert bounded["properties"]["events"]["maxItems"] == 16
    assert bounded["$defs"]["ChapterEvent"]["properties"]["source_quote"][
        "maxLength"
    ] == 800


def test_qwen38_direction_schema_requires_explicit_motion_contract() -> None:
    module = _module()
    schema = {
        "type": "object",
        "$defs": {
            "MotionBeat": {
                "type": "object",
                "properties": {
                    key: {}
                    for key in (
                        "phase",
                        "seconds",
                        "actor",
                        "target",
                        "action_type",
                        "action",
                        "end_state",
                    )
                },
                "required": ["phase", "action"],
            }
        },
    }

    bounded = module.bounded_schema(schema, "plan_beat_direction")

    assert set(bounded["$defs"]["MotionBeat"]["required"]) >= {
        "phase",
        "action",
        "seconds",
        "actor",
        "target",
        "action_type",
        "end_state",
    }


def test_qwen38_locks_3d_style_and_final_beat_requirements() -> None:
    module = _module()
    system = module.build_system(
        "plan_beat_script",
        {"retention_beat": {"function": "cliffhanger"}},
    )

    assert "3D国漫" in module.REQUIRED_3D_STYLE
    assert "二维线稿" in module.REQUIRED_3D_STYLE
    assert "title_card只允许表达时间或地点跳转" in system
    assert "不得只让主角独自离场" in system
