import json
from dataclasses import replace
from hashlib import sha256

import pytest

from clean_studio.agents.repair import apply_patch, check_patch
from clean_studio.domain import EpisodeSpec, FixedOption, Shot
from clean_studio.store import Store


def _shot(shot_id, segment):
    return Shot(
        shot_id=shot_id,
        scene_id="S1",
        source_segments=(segment,),
        location="失物海码头",
        start_state="知遥与潮叔站在码头，船灯熄灭",
        action="知遥点亮船灯",
        end_state="知遥手持点亮的船灯站在码头",
        camera="固定机位",
        shot_scale="中景",
        visible_characters=("C01",),
        dialogue=(),
        reference_subjects=("C01",),
        duration_seconds=9,
    )


def _episode():
    return EpisodeSpec(
        episode_id="episode-1",
        title="失物海",
        target_seconds=180,
        style_id="travel-live",
        shots=(_shot("shot-1", "S1/flow/0"), _shot("shot-2", "S1/flow/1")),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


def _complete_unrelated_frame(store):
    before = store.load()["shots"][1]
    store.compile_shot(
        "shot-2",
        expected_source_hash=before["source_hash"],
        source_compile_hash="source-compile-2",
    )
    store.set_frame_request(
        "shot-2",
        expected_source_compile_hash="source-compile-2",
        frame_request_hash="frame-request-2",
    )
    operation = store.begin_operation("image-2", "image", "frame-request-2", "shot-2")
    store.mark_submitted(operation.id, "external-task-2")
    receipt = {
        "receipt_id": f"receipt-{operation.id}",
        "operation_id": operation.id,
        "kind": operation.kind,
        "input_hash": operation.input_hash,
        "verdict": None,
    }
    data = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
    relative = f"receipts/{operation.id}.json"
    path = store.path.parent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    store.complete_operation(
        operation.id,
        path="media/shot-2-frame.jpg",
        sha256=sha256(b"shot-2-frame").hexdigest(),
        receipt_path=relative,
        receipt_sha256=sha256(data).hexdigest(),
        expected_current_hash="frame-request-2",
    )
    return operation


def test_check_apply_composes_with_store_hash_quota_and_preserves_unrelated_outputs(tmp_path):
    episode = _episode()
    store = Store(tmp_path / "studio.sqlite3")
    store.create_episode(episode, {"model": "frozen"})
    unrelated_operation = _complete_unrelated_frame(store)
    before = store.load()
    plan = check_patch(
        episode,
        {"shot-1": {"start_state": "知遥与潮叔坐在码头，船灯已经点亮"}},
        {"shot-1": {"start_state"}},
    )

    revised, affected = apply_patch(episode, plan)

    assert affected == {"shot-1"}
    assert plan.expected_script_hash == before["current_script_hash"]
    assert len(plan.input_hash) == 64
    tampered = replace(
        revised,
        shots=(revised.shots[0], replace(revised.shots[1], action="越权修改 shot-2")),
    )
    with pytest.raises(ValueError, match="plan|affected"):
        store.apply_script_revision(
            "repair-1",
            plan.input_hash,
            tampered,
            expected_script_hash=plan.expected_script_hash,
            plan=plan,
        )
    forged_plan = replace(
        plan, affected_shot_ids=frozenset({"shot-1", "shot-2"})
    )
    with pytest.raises(ValueError, match="affected"):
        store.apply_script_revision(
            "repair-forged",
            plan.input_hash,
            revised,
            expected_script_hash=plan.expected_script_hash,
            plan=forged_plan,
        )
    assert store.load()["spec"] == episode
    assert store.usage("shot-1", "input") == 0
    assert store.usage("shot-2", "input") == 0

    operation = store.apply_script_revision(
        "repair-1",
        plan.input_hash,
        revised,
        expected_script_hash=plan.expected_script_hash,
        plan=plan,
    )
    for invalid_plan in (None, replace(plan, input_hash="0" * 64)):
        with pytest.raises(ValueError, match="plan|input_hash"):
            store.apply_script_revision(
                "repair-1",
                plan.input_hash,
                revised,
                expected_script_hash=plan.expected_script_hash,
                plan=invalid_plan,
            )
    replayed = store.apply_script_revision(
        "repair-1",
        plan.input_hash,
        revised,
        expected_script_hash=plan.expected_script_hash,
        plan=plan,
    )
    assert replayed.id == operation.id
    after = store.load()
    assert operation.status == "completed"
    assert after["spec"] == revised
    assert after["shots"][1] == before["shots"][1]
    assert after["artifacts"] == before["artifacts"]
    assert store.usage("shot-1", "input") == 1
    assert store.usage("shot-2", "input") == 0
    assert store.usage("shot-2", "image") == 1
    recovered = store.begin_operation("image-2", "image", "frame-request-2", "shot-2")
    assert recovered.id == unrelated_operation.id
    assert recovered.external_task_id == "external-task-2"


def test_apply_returns_new_spec_and_only_actual_affected_ids():
    episode = _episode()
    plan = check_patch(
        episode,
        {
            "shot-1": {"start_state": episode.shots[0].start_state},
            "shot-2": {"action": "潮叔接过船灯"},
        },
        {"shot-1": {"start_state"}, "shot-2": {"action"}},
    )

    revised, affected = apply_patch(episode, plan)

    assert revised is not episode
    assert affected == {"shot-2"}
    assert revised.shots[0] is episode.shots[0]
    assert revised.shots[1] != episode.shots[1]
    assert episode.shots[1].action == "知遥点亮船灯"
    assert not hasattr(plan, "verdict")


@pytest.mark.parametrize(
    "patch,grant,message",
    [
        ({"shot-2": {"action": "越镜修订"}}, {"shot-1": {"action"}}, "grant|shot"),
        ({"shot-1": {"action": "越字段修订"}}, {"shot-1": {"start_state"}}, "grant|field"),
        ({"shot-1": {"visual_prompt": "旧派生提示"}}, {"shot-1": {"visual_prompt"}}, "author|field|unsupported"),
        ({"shot-1": {"shot_id": "replacement"}}, {"shot-1": {"shot_id"}}, "author|field|unsupported"),
        ({"shot-3": {"action": "不存在"}}, {"shot-3": {"action"}}, "unknown|shot"),
        ({"title": "越根字段"}, {"shot-1": {"action"}}, "shot|patch"),
    ],
)
def test_check_patch_rejects_out_of_grant_or_derived_changes(patch, grant, message):
    with pytest.raises(ValueError, match=message):
        check_patch(_episode(), patch, grant)


def test_apply_rechecks_expected_script_hash_and_noop_is_not_a_revision():
    episode = _episode()
    plan = check_patch(
        episode,
        {"shot-1": {"start_state": "新的起始状态"}},
        {"shot-1": {"start_state"}},
    )
    revised, _ = apply_patch(episode, plan)
    with pytest.raises(ValueError, match="expected_script_hash"):
        apply_patch(revised, plan)

    with pytest.raises(ValueError, match="no changes|no-op"):
        check_patch(
            episode,
            {"shot-1": {"start_state": episode.shots[0].start_state}},
            {"shot-1": {"start_state"}},
        )


def test_store_rejects_stale_expected_hash_without_adding_usage(tmp_path):
    episode = _episode()
    store = Store(tmp_path / "stale.sqlite3")
    store.create_episode(episode, {"model": "frozen"})
    first_plan = check_patch(
        episode,
        {"shot-1": {"start_state": "第一版起始状态"}},
        {"shot-1": {"start_state"}},
    )
    revised, _ = apply_patch(episode, first_plan)
    current_hash = store.load()["current_script_hash"]
    store.apply_script_revision(
        "repair-1",
        first_plan.input_hash,
        revised,
        expected_script_hash=current_hash,
        plan=first_plan,
    )
    second_plan = check_patch(
        revised,
        {"shot-1": {"start_state": "第二版起始状态"}},
        {"shot-1": {"start_state"}},
    )
    final, _ = apply_patch(revised, second_plan)

    with pytest.raises(ValueError, match="expected_script_hash"):
        store.apply_script_revision(
            "repair-stale",
            second_plan.input_hash,
            final,
            expected_script_hash=current_hash,
            plan=second_plan,
        )
    assert store.load()["spec"] == revised
    assert store.usage("shot-1", "input") == 1
