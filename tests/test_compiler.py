import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from clean_studio.compiler import (
    COMPILER_VERSION,
    CompilationError,
    TranslationError,
    compile_episode,
    compile_shot,
    video_request,
)
from clean_studio.domain import (
    Dialogue,
    EpisodeSpec,
    FixedOption,
    Reference,
    ReferenceManifest,
    Shot,
    SubjectRegistration,
)


FIDELITY_CHECKS = (
    "events_preserved",
    "participants_preserved",
    "temporal_order_preserved",
    "opening_preserved",
    "end_state_preserved",
    "no_extra_speech",
    "no_production_instruction_changes",
)


def _fidelity_result(accepted=True):
    return {
        **{field: accepted for field in FIDELITY_CHECKS},
        "violations": [] if accepted else ["translation fidelity mismatch"],
    }


class FakeTranslator:
    def __init__(
        self,
        response=None,
        *,
        review_response=None,
        model="fake-model",
        effort="low",
    ):
        self.response = response
        self.review_response = review_response
        self.model = model
        self.effort = effort
        self.calls = []
        self.fidelity_calls = []

    def independent_reviewer(self):
        reviewer = FakeTranslator(
            review_response=self.review_response,
            model=self.model,
            effort=self.effort,
        )
        reviewer.fidelity_calls = self.fidelity_calls
        return reviewer

    def ask_json(self, parts, schema, **options):
        payload = json.loads(parts[1]["text"])
        call = {
            "parts": parts,
            "payload": payload,
            "schema": schema,
            "options": options,
        }
        if options["name"] == "clean_h3_translation_fidelity":
            self.fidelity_calls.append(call)
            response = self.review_response
            if isinstance(response, BaseException):
                raise response
            if callable(response):
                response = response(payload)
            return dict(response or _fidelity_result())

        self.calls.append(call)
        if self.response is not None:
            return dict(self.response)
        action = payload["action"]
        action_en = (
            "C02 enters from the pier after C01 and C046."
            if "阿澈随后从栈桥走进画面" in action
            else "action-" + sha256(action.encode("utf-8")).hexdigest()[:12]
        )
        return {
            "opening_en": "opening-"
            + sha256(payload["opening"].encode("utf-8")).hexdigest()[:12],
            "action_en": action_en,
            "end_state_en": "end-"
            + sha256(payload["end_state"].encode("utf-8")).hexdigest()[:12],
        }


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _reference(tmp_path, filename, subject_id, role, order):
    path = tmp_path / "assets" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{subject_id}:{role}:{order}".encode("utf-8"))
    return Reference(
        subject_id=subject_id,
        path=f"registered/{filename}",
        sha256=_digest(path),
        role=role,
        scope=f"{role} only; initial presence comes from start_state",
        order=order,
        source_path=str(path),
        recorded_date="2026-10-26",
    )


@pytest.fixture()
def references(tmp_path):
    style = _reference(tmp_path, "style.png", "travel-live", "style", 4)
    location = _reference(tmp_path, "pier.png", "PIER", "location", 3)
    wardrobe = _reference(tmp_path, "wardrobe.png", "C01", "wardrobe", 2)
    identity = _reference(tmp_path, "identity.png", "C01", "identity", 1)
    return ReferenceManifest(
        source_path="source.zip",
        source_sha256="a" * 64,
        recorded_date="2026-10-26",
        style_id="travel-live",
        subjects=(
            SubjectRegistration("C01", "知遥", "character", "profile"),
            SubjectRegistration("C046", "潮叔", "character", "profile"),
            SubjectRegistration("C02", "阿澈", "character", "profile"),
            SubjectRegistration("PIER", "失物海码头", "location", "profile"),
        ),
        references=(style, location, wardrobe, identity),
    )


def _shot(shot_id="shot-1", segment="S1/flow/0"):
    return Shot(
        shot_id=shot_id,
        scene_id="S1",
        source_segments=(segment,),
        location="失物海码头",
        start_state="知遥与潮叔站在码头，船灯熄灭",
        action="知遥与潮叔查看船灯，阿澈随后从栈桥走进画面",
        end_state="知遥、潮叔与阿澈站在码头，船灯保持熄灭",
        camera="固定机位，缓慢推近",
        shot_scale="中景",
        visible_characters=("C01", "C046", "C02"),
        opening_characters=("C01", "C046"),
        dialogue=(Dialogue("C01", "把船灯递给我，别松手。"),),
        reference_subjects=("C01", "PIER"),
        duration_seconds=9,
    )


@pytest.fixture()
def episode():
    return EpisodeSpec(
        episode_id="EP04C",
        title="失物海",
        target_seconds=180,
        style_id="travel-live",
        shots=(_shot(), _shot("shot-2", "S1/flow/1")),
        fixed_question="下一站？",
        fixed_options=(FixedOption("A", "W11", "EP05A", "静音群岛"),),
        music_cues=(),
    )


def _accepted_frame(compiled, content=b"accepted-frame"):
    return SimpleNamespace(
        id="frame-artifact-1",
        shot_id=compiled.shot_id,
        sha256=sha256(content).hexdigest(),
        input_hash=compiled.frame_request_hash,
    )


def test_opening_and_h3_preserve_temporal_boundary_dialogue_and_trace(episode, references):
    translator = FakeTranslator()

    compiled = compile_shot(
        episode.shots[0],
        references,
        translator,
        translation_reviewer=translator.independent_reviewer(),
    )

    assert "知遥与潮叔站在码头" in compiled.opening
    assert "阿澈" not in compiled.opening
    assert "C02" not in compiled.opening
    assert episode.shots[0].action not in compiled.opening
    assert "失物海码头" in compiled.opening
    assert episode.shots[0].camera not in compiled.opening
    assert "缓慢推近" not in compiled.opening
    assert "景别：中景（本镜起始景别" in compiled.opening
    assert "潮叔（C046）" in compiled.opening
    assert "默认沿用对应角色的衣装参考卡" in compiled.opening
    assert "附件1（identity" in compiled.opening
    assert "附件3（location" in compiled.opening
    assert "CAMERA & FRAMING" in compiled.h3_prompt
    assert episode.shots[0].camera in compiled.h3_prompt
    assert episode.shots[0].shot_scale in compiled.h3_prompt
    assert "CHARACTERS: C01 = 知遥" in compiled.h3_prompt
    assert "image 1 = accepted first frame" in compiled.h3_prompt
    assert "exactly once" in compiled.h3_prompt
    assert "C02 enters from the pier" in compiled.h3_prompt
    assert "把船灯递给我，别松手。" in compiled.h3_prompt
    assert "S1/flow/0" in compiled.h3_prompt
    assert "9 seconds" in compiled.h3_prompt
    assert episode.shots[0].action in compiled.h3_prompt
    assert episode.shots[0].end_state in compiled.h3_prompt
    assert compiled.source_segments == ("S1/flow/0",)
    assert compiled.dialogue == episode.shots[0].dialogue
    assert len(translator.fidelity_calls) == 1
    fidelity = translator.fidelity_calls[0]
    assert fidelity["payload"]["source"]["action"] == episode.shots[0].action
    assert fidelity["payload"]["source"]["end_state"] == episode.shots[0].end_state
    assert fidelity["payload"]["translation"]["action_en"] == (
        "C02 enters from the pier after C01 and C046."
    )
    assert set(fidelity["schema"]["required"]) == {*FIDELITY_CHECKS, "violations"}
    assert translator.calls[0]["payload"]["action"] == episode.shots[0].action
    assert translator.calls[0]["payload"]["source_segments"] == ["S1/flow/0"]
    assert translator.calls[0]["payload"]["dialogue"] == [
        {"speaker": "C01", "line": "把船灯递给我，别松手。"}
    ]


def test_start_change_updates_requests_without_retranslating_unrelated_shot(
    episode, references
):
    cache = {}
    translator = FakeTranslator()
    baseline = compile_episode(
        episode,
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )
    repeated = compile_episode(
        episode,
        references,
        translator,
        cache=cache,
        previous=baseline,
        translation_reviewer=translator.independent_reviewer(),
    )
    assert repeated.shots[0] == baseline.shots[0]
    assert repeated.shots[0] is not baseline.shots[0]
    assert repeated.shots[1] == baseline.shots[1]
    assert repeated.shots[1] is not baseline.shots[1]
    assert len(translator.calls) == 2
    assert len(translator.fidelity_calls) == 4

    changed_shot = replace(
        episode.shots[0], start_state="知遥与潮叔坐在码头，船灯已经点亮"
    )
    changed_episode = replace(
        episode, shots=(changed_shot, episode.shots[1])
    )
    changed = compile_episode(
        changed_episode,
        references,
        translator,
        cache=cache,
        previous=baseline,
        translation_reviewer=translator.independent_reviewer(),
    )

    assert changed.shots[0] is not baseline.shots[0]
    assert changed.shots[0].opening != baseline.shots[0].opening
    assert changed.shots[0].h3_prompt != baseline.shots[0].h3_prompt
    assert changed.shots[0].frame_request.input_hash != baseline.shots[0].frame_request.input_hash
    assert changed.shots[0].frame_request.prompt == changed.shots[0].opening
    assert changed.shots[1] == baseline.shots[1]
    assert changed.shots[1] is not baseline.shots[1]
    assert len(translator.calls) == 3
    assert len(translator.fidelity_calls) == 6


def test_video_request_binds_accepted_frame_and_rejects_old_frame_for_new_h3(
    episode, references
):
    translator = FakeTranslator()
    compiled = compile_shot(
        episode.shots[0],
        references,
        translator,
        translation_reviewer=translator.independent_reviewer(),
    )
    accepted = _accepted_frame(compiled)

    request = video_request(compiled, accepted)

    assert request.accepted_frame_hash == accepted.sha256
    assert request.accepted_frame_id == accepted.id
    assert request.frame_request_hash == compiled.frame_request_hash
    assert request.input_hash == request.request_hash
    assert request.input_hash != compiled.frame_request_hash
    changed = compile_shot(
        replace(episode.shots[0], start_state="知遥与潮叔站在新位置"),
        references,
        translator,
        translation_reviewer=translator.independent_reviewer(),
    )
    with pytest.raises(ValueError, match="accepted frame|stale|frame request"):
        video_request(changed, accepted)


def test_video_request_requires_binding_evidence_and_current_shot(episode, references):
    compiled = compile_shot(
        episode.shots[0], references, FakeTranslator(), translation_reviewer=FakeTranslator()
    )
    with pytest.raises(ValueError, match="input_hash|bound"):
        video_request(compiled, SimpleNamespace(sha256="b" * 64))
    wrong_shot = SimpleNamespace(**{**vars(_accepted_frame(compiled)), "shot_id": "shot-2"})
    with pytest.raises(ValueError, match="shot"):
        video_request(compiled, wrong_shot)
    missing_shot = vars(_accepted_frame(compiled)).copy()
    del missing_shot["shot_id"]
    with pytest.raises(ValueError, match="shot_id"):
        video_request(compiled, SimpleNamespace(**missing_shot))


def test_translation_cache_binds_source_shot_and_compiler_version(
    episode, references, monkeypatch
):
    cache = {}
    translator = FakeTranslator()
    first = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )
    second = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )
    assert first.h3_prompt == second.h3_prompt
    assert len(translator.calls) == 1
    assert len(translator.fidelity_calls) == 2

    compile_shot(
        replace(episode.shots[0], action="知遥收起船灯"),
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )
    assert len(translator.calls) == 2
    assert len(translator.fidelity_calls) == 3

    monkeypatch.setattr("clean_studio.compiler.COMPILER_VERSION", COMPILER_VERSION + "-next")
    changed_version = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )
    assert changed_version.translation_cache_key != first.translation_cache_key
    assert len(translator.calls) == 3
    assert len(translator.fidelity_calls) == 4


@pytest.mark.parametrize(
    "action_en",
    [
        "C02 joins even though the source action has no such participant.",
        'C01 says "Invented dialogue."',
        "C01 looks at the lamp; the later registration is omitted.",
        "DURATION: 99 seconds.",
        "Nothing happens.",
    ],
)
def test_independent_fidelity_gate_blocks_added_or_deleted_content(
    episode, references, action_en
):
    cache = {}
    translator = FakeTranslator(
        {
            "opening_en": "opening",
            "action_en": action_en,
            "end_state_en": "end",
        },
        review_response=_fidelity_result(False),
    )

    with pytest.raises(TranslationError, match="bounded attempts"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            cache=cache,
            translation_reviewer=translator.independent_reviewer(),
        )

    assert cache == {}
    assert len(translator.calls) == 3
    assert len(translator.fidelity_calls) == 3
    feedback = translator.calls[1]["payload"][
        "previous_attempt_rejected_by_fidelity_review"
    ]
    assert isinstance(feedback, list) and feedback
    assert feedback[0].startswith("translation fidelity review failed:")
    review = translator.fidelity_calls[0]
    assert review["payload"]["source"]["action"] == episode.shots[0].action
    assert review["payload"]["source"]["duration_seconds"] == 9
    assert review["payload"]["translation"]["action_en"] == action_en


def test_fidelity_failure_retranslates_with_feedback_then_succeeds(
    episode, references
):
    cache = {}
    reviews = iter([_fidelity_result(False), _fidelity_result(True)])
    translator = FakeTranslator(
        review_response=lambda payload: next(reviews)
    )

    compiled = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
    )

    assert len(translator.calls) == 2
    assert len(translator.fidelity_calls) == 2
    assert "previous_attempt_rejected_by_fidelity_review" in translator.calls[1]["payload"]
    assert cache
    assert compiled.translation_cache_key


def test_fidelity_service_failure_is_not_cached_or_returned(episode, references):
    cache = {}
    translator = FakeTranslator(review_response=ConnectionError("fidelity unavailable"))
    with pytest.raises(ConnectionError, match="fidelity unavailable"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            cache=cache,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert cache == {}
    assert len(translator.calls) == 1
    assert len(translator.fidelity_calls) == 1


def test_polluted_cached_translation_is_independently_reviewed_and_rejected(
    episode, references
):
    cache = {}
    original = compile_shot(
        episode.shots[0],
        references,
        FakeTranslator(),
        cache=cache,
        translation_reviewer=FakeTranslator(),
    )
    cache[original.translation_cache_key]["action_en"] = "Nothing happens. DURATION: 99."
    translator = FakeTranslator(review_response=_fidelity_result(False))

    with pytest.raises(TranslationError, match="fidelity"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            cache=cache,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert translator.calls == []
    assert len(translator.fidelity_calls) == 1


def test_cached_translation_still_requires_available_fidelity_review(
    episode, references
):
    cache = {}
    compile_shot(
        episode.shots[0],
        references,
        FakeTranslator(),
        cache=cache,
        translation_reviewer=FakeTranslator(),
    )
    translator = FakeTranslator(review_response=ConnectionError("review offline"))
    with pytest.raises(ConnectionError, match="review offline"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            cache=cache,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert translator.calls == []
    assert len(translator.fidelity_calls) == 1


def test_translation_cache_isolates_stable_config_and_model_identity(
    episode, references
):
    cache = {}
    translator = FakeTranslator(model="model-a")
    first = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
        translation_config={"model": "model-a", "effort": "low", "temperature": 0},
    )
    repeated = compile_shot(
        episode.shots[0],
        references,
        translator,
        cache=cache,
        translation_reviewer=translator.independent_reviewer(),
        translation_config={"temperature": 0, "effort": "low", "model": "model-a"},
    )
    assert repeated.translation_cache_key == first.translation_cache_key
    assert len(translator.calls) == 1
    assert len(translator.fidelity_calls) == 2

    other = FakeTranslator(model="model-b")
    changed = compile_shot(
        episode.shots[0],
        references,
        other,
        cache=cache,
        translation_reviewer=other.independent_reviewer(),
        translation_config={"model": "model-b", "effort": "low", "temperature": 0},
    )
    assert changed.translation_cache_key != first.translation_cache_key
    assert changed.translation_config_hash != first.translation_config_hash
    assert len(other.calls) == 1


def test_translation_is_one_typed_call_and_host_rejects_extra_or_missing_structure(
    episode, references
):
    translator = FakeTranslator(
        {
            "opening_en": "opening",
            "action_en": "action",
            "end_state_en": "end",
            "dialogue": [{"speaker": "C01", "line": "被改写"}],
        }
    )
    with pytest.raises(TranslationError, match="response|fields|structure"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert len(translator.calls) == 1
    call = translator.calls[0]
    assert call["schema"]["additionalProperties"] is False
    assert set(call["schema"]["required"]) == {
        "opening_en",
        "action_en",
        "end_state_en",
    }
    assert call["options"]["name"] == "clean_h3_translation"
    assert call["options"]["retry_truncated"] is False


def test_reference_order_is_shared_by_frame_review_video_and_cache(episode, references):
    translator = FakeTranslator()
    compiled = compile_shot(
        episode.shots[0],
        references,
        translator,
        translation_reviewer=translator.independent_reviewer(),
    )

    assert [reference.role for reference in compiled.references] == [
        "identity",
        "wardrobe",
        "location",
        "style",
    ]
    assert compiled.frame_request.references is not compiled.references
    assert [
        (reference.role, reference.subject_id)
        for reference in compiled.frame_request.references
    ] == [
        ("identity", "C01"),
        ("wardrobe", "C01"),
        ("location", "PIER"),
        ("style", "travel-live"),
    ]
    assert compiled.review_references is compiled.references
    request = video_request(compiled, _accepted_frame(compiled))
    assert request.references is compiled.references
    assert request.source_segments == compiled.source_segments
    assert request.source_action == episode.shots[0].action
    assert request.source_end_state == episode.shots[0].end_state
    assert compiled.translation_cache_key in compiled.compile_hash or len(compiled.compile_hash) == 64


def test_mid_shot_joiner_reference_cards_stay_out_of_frame_request(
    episode, references, tmp_path
):
    from clean_studio.compiler import compile_shot as _compile

    joiner_card = _reference(tmp_path, "ache-identity.png", "C02", "identity", 5)
    manifest = ReferenceManifest(
        source_path=references.source_path,
        source_sha256=references.source_sha256,
        recorded_date=references.recorded_date,
        style_id=references.style_id,
        subjects=references.subjects,
        references=references.references + (joiner_card,),
    )
    shot = replace(
        episode.shots[0],
        reference_subjects=("C01", "C02", "PIER"),
    )
    assert "C02" in shot.visible_characters
    assert "C02" not in shot.opening_characters
    translator = FakeTranslator()
    compiled = _compile(
        shot,
        manifest,
        translator,
        translation_reviewer=translator.independent_reviewer(),
    )
    frame_subjects = {
        reference.subject_id
        for reference in compiled.frame_request.references
        if reference.role not in ("style", "location")
    }
    assert "C02" not in frame_subjects
    assert "C01" in frame_subjects
    assert any(r.role == "location" for r in compiled.frame_request.references)
    assert "阿澈" not in compiled.opening


def test_episode_extra_fields_aggregate_before_later_shot_translation(
    episode, references
):
    invalid = replace(
        episode,
        shots=(
            episode.shots[0],
            replace(episode.shots[1], visual_prompt="旧派生提示"),
            replace(_shot("shot-3", "S1/flow/2"), storyboard="旧分镜"),
        ),
    )
    translator = FakeTranslator()

    with pytest.raises(CompilationError) as caught:
        compile_episode(
            invalid,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )

    assert [(issue.shot_id, issue.code) for issue in caught.value.issues] == [
        ("shot-2", "author_owned_fields"),
        ("shot-3", "author_owned_fields"),
    ]
    assert all(issue.owner == "author" for issue in caught.value.issues)
    assert translator.calls == []
    assert translator.fidelity_calls == []


def test_compile_shot_rejects_extra_fields_before_reference_file_work(
    episode, references, monkeypatch
):
    invalid = replace(episode.shots[0], motion_prompt="旧运动提示")

    def forbidden_expansion(*args, **kwargs):
        raise AssertionError("reference expansion must not run")

    monkeypatch.setattr("clean_studio.compiler.expand_references", forbidden_expansion)
    with pytest.raises(CompilationError) as caught:
        compile_shot(
            invalid,
            references,
            FakeTranslator(),
            translation_reviewer=FakeTranslator(),
        )
    assert caught.value.issues[0].code == "author_owned_fields"
    assert "motion_prompt" in caught.value.issues[0].message


def test_two_shot_author_reference_errors_aggregate_before_any_model_call(
    episode, references
):
    invalid = replace(
        episode,
        shots=(
            replace(episode.shots[0], reference_subjects=("C999",)),
            replace(episode.shots[1], reference_subjects=("C998",)),
        ),
    )
    translator = FakeTranslator()

    with pytest.raises(CompilationError) as caught:
        compile_episode(
            invalid,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )

    assert [(issue.shot_id, issue.subject_id) for issue in caught.value.issues] == [
        ("shot-1", "C999"),
        ("shot-2", "C998"),
    ]
    assert all(issue.owner == "author" for issue in caught.value.issues)
    assert all(issue.code == "unregistered_subject" for issue in caught.value.issues)
    assert "shot-1" in str(caught.value) and "shot-2" in str(caught.value)
    assert translator.calls == []


def test_registered_but_unbound_subject_is_host_registration_error(episode, references):
    invalid = replace(
        episode,
        shots=(replace(episode.shots[0], reference_subjects=("C046",)), episode.shots[1]),
    )
    translator = FakeTranslator()
    with pytest.raises(CompilationError) as caught:
        compile_episode(
            invalid,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    issue = caught.value.issues[0]
    assert issue.code == "unbound_subject"
    assert issue.owner == "host"
    assert issue.subject_id == "C046"
    assert translator.calls == []


def test_current_file_sha_and_existence_are_rechecked_after_manifest_build(
    episode, references
):
    identity = next(ref for ref in references.references if ref.role == "identity")
    path = Path(identity.source_path)
    translator = FakeTranslator()
    path.write_bytes(b"changed-after-manifest")

    with pytest.raises(CompilationError) as changed:
        compile_episode(
            episode,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert changed.value.issues[0].code == "hash_mismatch"
    assert changed.value.issues[0].owner == "host"
    assert changed.value.issues[0].actual_sha256 == sha256(b"changed-after-manifest").hexdigest()

    path.unlink()
    with pytest.raises(CompilationError) as missing:
        compile_episode(
            episode,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert missing.value.issues[0].code == "missing_file"
    assert translator.calls == []


def test_missing_reference_never_falls_back_to_basename_or_subject_alias(
    episode, references, tmp_path
):
    identity = next(ref for ref in references.references if ref.role == "identity")
    original = Path(identity.source_path)
    elsewhere = tmp_path / "elsewhere" / original.name
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(original.read_bytes())
    original.unlink()
    translator = FakeTranslator()
    with pytest.raises(CompilationError) as caught:
        compile_shot(
            episode.shots[0],
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert caught.value.issues[0].code == "missing_file"

    alias = replace(episode.shots[0], reference_subjects=("知遥",))
    with pytest.raises(CompilationError) as aliased:
        compile_shot(
            alias,
            references,
            translator,
            translation_reviewer=translator.independent_reviewer(),
        )
    assert aliased.value.issues[0].code == "unregistered_subject"
    assert aliased.value.issues[0].owner == "author"
    assert translator.calls == []


def test_translation_requires_an_explicit_distinct_reviewer_before_any_call(
    episode, references
):
    translator = FakeTranslator()
    with pytest.raises(ValueError, match="independent|translation_reviewer"):
        compile_shot(episode.shots[0], references, translator)
    assert translator.calls == []

    with pytest.raises(ValueError, match="independent|same object|distinct"):
        compile_shot(
            episode.shots[0],
            references,
            translator,
            translation_reviewer=translator,
        )
    assert translator.calls == []

    with pytest.raises(ValueError, match="independent|translation_reviewer"):
        compile_episode(episode, references, translator)
    assert translator.calls == []


def test_previous_h3_is_rebuilt_from_reviewed_typed_receipt_not_old_prompt(
    episode, references
):
    baseline = compile_episode(
        episode,
        references,
        FakeTranslator(),
        translation_reviewer=FakeTranslator(),
    )
    polluted = replace(
        baseline,
        shots=(
            replace(
                baseline.shots[0],
                h3_prompt="Nothing happens. DURATION: 99 seconds.",
                compile_hash="0" * 64,
            ),
            baseline.shots[1],
        ),
    )
    translator = FakeTranslator()
    reviewer = FakeTranslator()

    rebuilt = compile_episode(
        episode,
        references,
        translator,
        previous=polluted,
        translation_reviewer=reviewer,
    )

    assert rebuilt.shots[0].h3_prompt == baseline.shots[0].h3_prompt
    assert rebuilt.shots[0].compile_hash == baseline.shots[0].compile_hash
    assert rebuilt.shots[0] is not baseline.shots[0]
    assert len(reviewer.fidelity_calls) == 2
    assert translator.calls == []


def test_polluted_previous_typed_receipt_must_pass_independent_review(
    episode, references
):
    baseline = compile_episode(
        episode,
        references,
        FakeTranslator(),
        translation_reviewer=FakeTranslator(),
    )
    receipt = baseline.shots[0].translation_receipt
    polluted_receipt = replace(
        receipt,
        translation=replace(receipt.translation, action_en="Nothing happens."),
    )
    polluted = replace(
        baseline,
        shots=(
            replace(baseline.shots[0], translation_receipt=polluted_receipt),
            baseline.shots[1],
        ),
    )
    translator = FakeTranslator()
    reviewer = FakeTranslator(review_response=_fidelity_result(False))

    with pytest.raises(TranslationError, match="fidelity"):
        compile_episode(
            episode,
            references,
            translator,
            previous=polluted,
            translation_reviewer=reviewer,
        )
    assert len(reviewer.fidelity_calls) == 1
    assert translator.calls == []
