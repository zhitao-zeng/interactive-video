import ast
import base64
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import get_type_hints
from zipfile import ZipFile

import pytest
from PIL import Image

from clean_studio.agents.authoring import (
    Author,
    AuthorBudgetExceeded,
    AuthorProtocolError,
)
from clean_studio.agents.codex import CodexClient
from clean_studio.agents.review import (
    ReviewProtocolError,
    ReviewSources,
    Reviewer,
    _FINDING_SCHEMA,
    _RELATION_SCHEMA,
    _REVIEW_SCHEMA,
    ground_finding,
)
from clean_studio.domain import (
    EpisodeSpec,
    FixedOption,
    Reference,
    ReferenceManifest,
    Review,
    Shot,
    SourcePackage,
    SourceScene,
    SourceSegment,
    SubjectRegistration,
)
from clean_studio.frozen.codex import completion
from clean_studio.frozen.llm import (
    JsonEndpoint,
    ModelOutputTruncated,
    ask_json,
    image_part,
)
from clean_studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
FULL_SCRIPT = "pkg/episode/01-完整剧本.md"
STRUCTURED_SCRIPT = "pkg/episode/02-结构化剧本.json"


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@dataclass(frozen=True)
class FakeSession:
    name: str


class FakeClient:
    def __init__(self, responses=(), *, clock=None, elapsed=()):
        self.responses = list(responses)
        self.clock = clock
        self.elapsed = list(elapsed)
        self.calls = []
        self.sessions = []

    def create_session(self, prefix):
        session = FakeSession(f"{prefix}-{len(self.sessions) + 1}")
        self.sessions.append(session)
        return session

    def ask_json(
        self,
        parts,
        schema,
        *,
        name,
        max_tokens,
        timeout,
        session=None,
        **options,
    ):
        call = {
            "parts": deepcopy(parts),
            "schema": deepcopy(schema),
            "name": name,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "session": session,
            "options": options,
        }
        self.calls.append(call)
        if self.elapsed:
            elapsed = self.elapsed.pop(0)
            assert self.clock is not None
            self.clock.advance(elapsed)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response()
        return deepcopy(response)


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _package(tmp_path):
    fixed_option = FixedOption("A", "W11", "EP05A", "静音群岛")
    actions = [f"原包动作{index}" for index in range(20)]
    raw = {
        "episodes": [
            {
                "materialId": "W10",
                "videoId": "EP04C",
                "title": "失物海",
                "targetSeconds": 180,
                "scenes": [
                    {
                        "sceneId": "S1",
                        "characters": ["C01", "C046", "C02"],
                        "flow": [{"action": action} for action in actions],
                    }
                ],
                "fixedQuestion": "下一站？",
                "fixedOptions": [
                    {
                        "label": "A",
                        "targetWorld": "W11",
                        "targetVideo": "EP05A",
                        "title": "静音群岛",
                    }
                ],
                "continuityExit": "继续",
            }
        ]
    }
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(
            FULL_SCRIPT,
            "## 场1 ｜ 失物海码头\n原包完整剧本：红色船灯与离开动作必须逐字核对。\n",
        )
        bundle.writestr(STRUCTURED_SCRIPT, json.dumps(raw, ensure_ascii=False))
    segments = tuple(
        SourceSegment(f"S1/flow/{index}", "S1", index, action, None, None, None)
        for index, action in enumerate(actions)
    )
    source = SourcePackage(
        archive_path=str(archive),
        archive_sha256=_digest(archive),
        material_id="W10",
        video_id="EP04C",
        title="失物海",
        target_seconds=180,
        scenes=(SourceScene("S1", "失物海码头", ("C01", "C046", "C02"), segments),),
        fixed_question="下一站？",
        fixed_options=(fixed_option,),
        continuity_exit="继续",
    )
    roles = ("identity", "wardrobe", "style")
    references = []
    for index, role in enumerate(roles):
        path = tmp_path / f"{role}.png"
        Image.new("RGB", (12 + index, 10 + index), (30 + index * 60, 20, 90)).save(path)
        references.append(
            Reference(
                subject_id="travel-live" if role == "style" else "C01",
                path=f"{'style' if role == 'style' else 'material'}/{path.name}",
                sha256=_digest(path),
                role=role,
                scope=f"{role} scope",
                order=index + 1,
                source_path=str(path),
                recorded_date="2026-10-26",
            )
        )
    manifest = ReferenceManifest(
        source_path=str(archive),
        source_sha256=source.archive_sha256,
        recorded_date="2026-10-26",
        style_id="travel-live",
        subjects=(
            SubjectRegistration("C01", "知遥", "character", "知遥 profile"),
            SubjectRegistration("C046", "潮叔", "character", "潮叔 profile"),
            SubjectRegistration("C02", "阿澈", "character", "阿澈 profile"),
        ),
        references=tuple(references),
    )
    return source, manifest


def _episode(source):
    shots = tuple(
        Shot(
            shot_id=f"shot_{index + 1:03d}",
            scene_id="S1",
            source_segments=(segment.segment_id,),
            location="失物海码头",
            start_state="知遥与潮叔站在码头，船灯熄灭，背景物件就位",
            action=f"知遥与潮叔处理{segment.action}",
            end_state="知遥与潮叔仍站在码头，手中物件握稳，船灯保持熄灭",
            camera="固定机位，码头全景",
            shot_scale="中景",
            visible_characters=("C01", "C046"),
            dialogue=(),
            reference_subjects=("C01",),
            duration_seconds=9,
        )
        for index, segment in enumerate(source.segments)
    )
    return EpisodeSpec(
        episode_id=source.video_id,
        title=source.title,
        target_seconds=source.target_seconds,
        style_id="travel-live",
        shots=shots,
        fixed_question=source.fixed_question,
        fixed_options=source.fixed_options,
        music_cues=(),
    )


def _with_shot(episode, index, **changes):
    shots = list(episode.shots)
    shots[index] = replace(shots[index], **changes)
    return replace(episode, shots=tuple(shots))


def _episode_payload(episode):
    payload = asdict(episode)
    for shot in payload["shots"]:
        shot.pop("extra_fields", None)
    return payload


def _at_path(value, path):
    current = asdict(value) if isinstance(value, EpisodeSpec) else value
    for token in path.strip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if token.isdigit() else current[token]
    return current


def _time_for_path(path):
    if path.endswith("/start_state"):
        return "t=0"
    if path.endswith("/action"):
        return "during"
    if path.endswith("/end_state"):
        return "end"
    if path.startswith("/shots/"):
        return "whole_shot"
    return "whole_episode"


def _finding(
    episode,
    finding_id="issue-1",
    *,
    field_path="/shots/0/camera",
    quote=None,
    time=None,
    relation=None,
):
    return {
        "id": finding_id,
        "category": "contradiction",
        "evidence_origin": "authored_field",
        "field_path": field_path,
        "quote": _at_path(episode, field_path) if quote is None else quote,
        "time": _time_for_path(field_path) if time is None else time,
        "relation": {"type": "scene_mismatch"} if relation is None else relation,
        "message": "需要独立审核确认的矛盾",
    }


def _confirmed(finding):
    return {**finding, "status": "confirmed"}


def _review_sources(source, references):
    return ReviewSources(source=source, references=references)


def _store(tmp_path, episode):
    store = Store(tmp_path / "review.sqlite3")
    store.create_episode(episode, {"model": "frozen"})
    return store


@pytest.fixture()
def context(tmp_path):
    source, references = _package(tmp_path)
    return source, references, _episode(source)


def test_author_start_returns_complete_episode_with_source_text_and_real_images(context):
    source, references, expected = context
    client = FakeClient([_episode_payload(expected)])
    author = Author(source, references, client)

    actual = author.start()

    assert actual == expected
    assert author.current == expected
    call = client.calls[0]
    text = "\n".join(part.get("text", "") for part in call["parts"])
    assert "原包完整剧本：红色船灯与离开动作必须逐字核对" in text
    assert "原包动作19" in text
    images = [part for part in call["parts"] if part.get("type") == "image_url"]
    assert len(images) == 3
    roles = [
        part["text"]
        for part in call["parts"]
        if part.get("type") == "text" and part.get("text", "").startswith("REFERENCE ")
    ]
    assert [role.split()[1] for role in roles] == ["identity", "wardrobe", "style"]
    for index, part in enumerate(images):
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        with Image.open(io.BytesIO(decoded)) as image:
            assert image.size == (12 + index, 10 + index)
    assert call["timeout"] == 1200


def test_style_reference_attachment_is_not_labeled_as_a_subject(context):
    source, references, expected = context
    client = FakeClient([_episode_payload(expected)])
    author = Author(source, references, client)

    author.start()

    call = client.calls[0]
    lines = [
        part["text"]
        for part in call["parts"]
        if part.get("type") == "text" and part.get("text", "").startswith("REFERENCE ")
    ]
    style_lines = [line for line in lines if "travel-live" in line]
    assert len(style_lines) == 1
    assert "subject=travel-live" not in style_lines[0]
    assert "style_id=travel-live" in style_lines[0]
    assert "not a subject_id" in style_lines[0]
    registered = "\n".join(
        part["text"]
        for part in call["parts"]
        if part.get("type") == "text"
        and part.get("text", "").startswith("REGISTERED SUBJECTS")
    )
    assert "travel-live" not in registered
    assert '"bound_reference_images":2' in registered
    assert '"bound_reference_images":0' in registered
    segments = "\n".join(
        part["text"]
        for part in call["parts"]
        if part.get("type") == "text"
        and part.get("text", "").startswith("SOURCE SEGMENTS")
    )
    assert '"segment_id":"S1/flow/0"' in segments
    assert '"scene_id":"S1"' in segments
    assert "verbatim" in segments


def test_rejected_start_feeds_host_rejection_to_next_launch(context, tmp_path):
    source, references, expected = context
    draft = replace(expected, shots=())
    store = Store(tmp_path / "author-resume.sqlite3")
    store.create_episode(draft, {"model": "frozen"})
    invalid = _episode_payload(expected)
    invalid["shots"][0]["reference_subjects"] = ["C01", "travel-live"]
    first = Author(source, references, FakeClient([invalid]), store=store)
    with pytest.raises(AuthorProtocolError, match="unregistered subject"):
        first.start()

    client = FakeClient([_episode_payload(expected)])
    second = Author(source, references, client, store=store)
    second.start()

    text = "\n".join(part.get("text", "") for part in client.calls[0]["parts"])
    assert "unregistered subject: travel-live" in text


def test_author_revise_uses_same_session_and_only_findings_and_scope(context):
    source, references, episode = context
    revised = _with_shot(episode, 0, action="知遥查看船灯，潮叔随后登记")
    client = FakeClient([_episode_payload(episode), _episode_payload(revised)])
    author = Author(source, references, client)
    author.start()
    finding = _confirmed(
        _finding(
            episode,
            field_path="/shots/0/action",
            relation={"type": "state_contradiction"},
        )
    )

    actual = author.revise(
        [finding], allowed_fields={"shot_001": {"action"}}
    )

    assert actual == revised
    assert len(client.sessions) == 1
    assert client.calls[1]["session"] is client.calls[0]["session"]
    second_text = "\n".join(
        part.get("text", "") for part in client.calls[1]["parts"]
    )
    assert finding["quote"] in second_text
    assert "allowed_fields" in second_text
    assert "原包完整剧本：红色船灯与离开动作必须逐字核对" not in second_text
    assert not any(
        part.get("type") == "image_url" for part in client.calls[1]["parts"]
    )


@pytest.mark.parametrize(
    "response",
    [
        {"episode_id": "EP04C", "shots": []},
        ModelOutputTruncated("clean_author_start", 16000),
    ],
)
def test_author_incomplete_or_truncated_output_is_never_a_candidate(context, response):
    source, references, _ = context
    client = FakeClient([response])
    author = Author(source, references, client)

    with pytest.raises((AuthorProtocolError, ModelOutputTruncated)):
        author.start()
    assert author.current is None


def test_author_rejects_derived_or_out_of_scope_revision(context):
    source, references, episode = context
    bad = _with_shot(episode, 0, visual_prompt="旧派生提示")
    client = FakeClient([_episode_payload(episode), asdict(bad)])
    author = Author(source, references, client)
    author.start()
    finding = _confirmed(_finding(episode))

    with pytest.raises(AuthorProtocolError):
        author.revise([finding], allowed_fields={"shot_001": {"camera"}})
    assert author.current == episode

    out_of_scope = _with_shot(episode, 0, camera="另一个机位")
    client.responses.append(_episode_payload(out_of_scope))
    with pytest.raises(AuthorProtocolError, match="scope"):
        author.revise([finding], allowed_fields={"shot_001": {"action"}})
    assert author.current == episode


def test_author_refuses_unconfirmed_or_non_contradiction_feedback(context):
    source, references, episode = context
    client = FakeClient([_episode_payload(episode)])
    author = Author(source, references, client)
    author.start()

    with pytest.raises(ValueError, match="confirmed contradiction"):
        author.revise([_finding(episode)])
    with pytest.raises(ValueError, match="confirmed contradiction"):
        author.revise([{**_confirmed(_finding(episode)), "category": "note"}])
    assert len(client.calls) == 1


def test_author_budget_is_shared_by_start_and_all_revisions(context):
    source, references, episode = context
    clock = FakeClock()
    responses = [_episode_payload(episode)]
    current = episode
    for index in range(4):
        current = _with_shot(current, 0, action=f"合法修订动作{index}")
        responses.append(_episode_payload(current))
    client = FakeClient(
        responses,
        clock=clock,
        elapsed=[1200, 1200, 1200, 1200, 600],
    )
    author = Author(source, references, client, clock=clock)
    author.start()
    for _ in range(4):
        finding = _confirmed(
            _finding(
                author.current,
                field_path="/shots/0/action",
                relation={"type": "state_contradiction"},
            )
        )
        author.revise(finding and [finding], allowed_fields={"shot_001": {"action"}})

    assert clock.value == 5400
    assert [call["timeout"] for call in client.calls] == [1200, 1200, 1200, 1200, 600]
    assert len(client.sessions) == 1
    finding = _confirmed(_finding(author.current))
    with pytest.raises(AuthorBudgetExceeded, match="cumulative"):
        author.revise([finding])
    assert len(client.calls) == 5


def test_author_overlong_turn_does_not_publish_candidate(context):
    source, references, episode = context
    clock = FakeClock()
    client = FakeClient([_episode_payload(episode)], clock=clock, elapsed=[1201])
    author = Author(source, references, client, clock=clock)

    with pytest.raises(AuthorBudgetExceeded, match="single"):
        author.start()
    assert author.current is None


def test_review_schemas_satisfy_strict_required_all_endpoint():
    def check(schema):
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert "object" in types
        assert set(schema["properties"]) == set(schema["required"])
        assert schema["additionalProperties"] is False

    check(_FINDING_SCHEMA)
    check(_RELATION_SCHEMA)
    check(_REVIEW_SCHEMA)
    assert "status" not in _FINDING_SCHEMA["properties"]


def test_review_relation_may_quote_an_array_element(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    finding = {
        "id": "F1",
        "category": "contradiction",
        "evidence_origin": "authored_field",
        "field_path": "/shots/0/camera",
        "quote": _at_path(episode, "/shots/0/camera"),
        "time": "whole_shot",
        "relation": {
            "type": "visual_presence_conflict",
            "other_field_path": "/shots/0/visible_characters",
            "other_quote": episode.shots[0].visible_characters[0],
            "other_time": "whole_shot",
        },
        "message": "镜头描述与在场角色冲突，引用数组元素作为证据",
    }
    ground_finding(finding, episode, sources)
    bad = {**finding["relation"], "other_quote": "不在场角色"}
    try:
        ground_finding({**finding, "relation": bad}, episode, sources)
    except ValueError as error:
        assert "not verbatim present" in str(error)
    else:
        raise AssertionError("array quote absent from elements must not ground")


def test_review_finding_null_optionals_behave_as_absent(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    compact = {
        "id": "F1",
        "category": "contradiction",
        "evidence_origin": "authored_field",
        "field_path": "/shots/0/start_state",
        "quote": _at_path(episode, "/shots/0/start_state"),
        "time": "t=0",
        "relation": {
            "type": "scene_mismatch",
            "other_field_path": None,
            "other_quote": None,
            "other_time": None,
            "source_segment": None,
        },
        "message": "严格端点会输出显式 null 的可选键",
    }
    ground_finding(compact, episode, sources)


def test_ungrounded_finding_is_dropped_but_never_blocks_grounded_evidence(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    grounded = _finding(episode, finding_id="keep-1")
    misindexed = _finding(
        episode,
        finding_id="drop-1",
        field_path="/shots/1/action",
        quote=episode.shots[0].action,
        time="during",
    )
    client = FakeClient(
        [{"findings": [grounded, misindexed]}, {"retract_ids": []}]
    )
    reviewer = Reviewer(client)

    review = reviewer.review(episode, sources)

    assert review.verdict == "rejected"
    assert [finding["id"] for finding in review.findings] == ["keep-1"]
    assert [row["id"] for row in reviewer.last_dropped] == ["drop-1"]


def test_review_fails_when_every_finding_is_ungrounded(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    ungrounded = _finding(episode, finding_id="bad-1", quote="稿件里不存在的话")
    client = FakeClient([{"findings": [ungrounded]}])
    reviewer = Reviewer(client)

    with pytest.raises(ReviewProtocolError, match="all review findings were ungrounded"):
        reviewer.review(episode, sources)


def test_reviewer_structure_failure_never_calls_model(context):
    source, references, episode = context
    invalid = _with_shot(episode, 0, end_state="同上")
    client = FakeClient()
    reviewer = Reviewer(client)

    with pytest.raises(ValueError, match="end_state"):
        reviewer.review(invalid, _review_sources(source, references))
    assert client.calls == []


def test_ground_finding_requires_original_segment_verbatim_quote_and_time(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    finding = _finding(
        episode,
        field_path="/shots/0/source_segments/0",
        quote=source.segments[0].action,
        time="whole_shot",
    )
    finding["evidence_origin"] = "original_text"
    finding["source_segment"] = "S1/flow/0"
    finding.pop("relation")

    assert ground_finding(finding, episode, sources) is None
    for field in ("source_segment", "quote", "time"):
        invalid = dict(finding)
        invalid.pop(field)
        with pytest.raises(ValueError, match=field):
            ground_finding(invalid, episode, sources)
    invalid = dict(finding, quote="并非原包原句")
    with pytest.raises(ValueError, match="verbatim"):
        ground_finding(invalid, episode, sources)
    invalid = dict(finding, source_segment="S1/flow/19")
    with pytest.raises(ValueError, match="source_segment"):
        ground_finding(invalid, episode, sources)


def test_ground_finding_requires_authored_relation_and_real_field_path(context):
    source, references, episode = context
    sources = _review_sources(source, references)
    finding = _finding(episode)

    assert ground_finding(finding, episode, sources) is None
    invalid = dict(finding)
    invalid.pop("relation")
    with pytest.raises(ValueError, match="relation"):
        ground_finding(invalid, episode, sources)
    invalid = dict(finding, quote="稿件里不存在的话")
    with pytest.raises(ValueError, match="verbatim"):
        ground_finding(invalid, episode, sources)
    invalid = dict(finding, field_path="/shots/99/camera")
    with pytest.raises(ValueError, match="field_path"):
        ground_finding(invalid, episode, sources)


def test_second_confirmation_retracts_only_t0_false_positive_and_keeps_camera(context):
    source, references, episode = context
    episode = _with_shot(
        episode,
        0,
        start_state="知遥与潮叔站在码头，码头灯已经亮起，背景物件就位",
        end_state="知遥与潮叔仍站在码头，手中物件握稳，码头灯保持亮起",
        camera="固定机位，码头灯已经亮起；画面却错写为旧灯塔内景",
    )
    before = _episode_payload(episode)
    camera = _finding(episode, "camera-scene", quote="旧灯塔内景")
    false_t0 = _finding(
        episode,
        "false-t0",
        field_path="/shots/0/start_state",
        quote="码头灯已经亮起",
        relation={
            "type": "state_contradiction",
            "other_field_path": "/shots/0/camera",
            "other_quote": "码头灯已经亮起",
            "other_time": "whole_shot",
        },
    )
    client = FakeClient(
        [{"findings": [false_t0, camera]}, {"retract_ids": ["false-t0"]}]
    )

    review = Reviewer(client).review(episode, _review_sources(source, references))

    assert review.verdict == "rejected"
    assert [finding["id"] for finding in review.findings] == ["camera-scene"]
    assert review.findings[0]["status"] == "confirmed"
    assert review.findings[0]["category"] == "contradiction"
    assert review.findings[0]["field_path"] == "/shots/0/camera"
    assert len(client.calls) == 2
    confirmation_text = "\n".join(
        part.get("text", "") for part in client.calls[1]["parts"]
    )
    assert "false-t0" in confirmation_text and "camera-scene" in confirmation_text
    assert _episode_payload(episode) == before


def test_action_may_add_c_after_ab_only_start(context):
    source, references, episode = context
    episode = _with_shot(
        episode,
        0,
        action="知遥与潮叔查看船灯，阿澈随后从栈桥走进画面",
        end_state="知遥、潮叔与阿澈站在码头，船灯保持熄灭",
        visible_characters=("C01", "C046", "C02"),
    )
    false_join = _finding(
        episode,
        "false-join",
        field_path="/shots/0/start_state",
        quote="知遥与潮叔站在码头",
        relation={
            "type": "state_contradiction",
            "other_field_path": "/shots/0/action",
            "other_quote": "阿澈随后从栈桥走进画面",
            "other_time": "during",
        },
    )
    client = FakeClient(
        [{"findings": [false_join]}, {"retract_ids": ["false-join"]}]
    )

    review = Reviewer(client).review(episode, _review_sources(source, references))

    assert review.verdict == "accepted"
    assert review.findings == ()
    assert len(client.calls) == 2


def test_adjacent_repeated_light_and_departure_keep_both_field_evidence(context):
    source, references, episode = context
    episode = _with_shot(
        episode,
        0,
        action="知遥点亮红色船灯，随后离开码头",
    )
    episode = _with_shot(
        episode,
        1,
        action="知遥再次点亮红色船灯，随后再次离开码头",
    )
    repeated_light = _finding(
        episode,
        "repeat-light",
        field_path="/shots/1/action",
        quote="再次点亮红色船灯",
        time="during",
        relation={
            "type": "repeated_action",
            "other_field_path": "/shots/0/action",
            "other_quote": "点亮红色船灯",
            "other_time": "during",
        },
    )
    repeated_exit = _finding(
        episode,
        "repeat-exit",
        field_path="/shots/1/action",
        quote="再次离开码头",
        time="during",
        relation={
            "type": "repeated_action",
            "other_field_path": "/shots/0/action",
            "other_quote": "离开码头",
            "other_time": "during",
        },
    )
    client = FakeClient(
        [
            {"findings": [repeated_light, repeated_exit]},
            {"retract_ids": []},
        ]
    )

    review = Reviewer(client).review(episode, _review_sources(source, references))

    assert review.verdict == "rejected"
    assert len(review.findings) == 2
    for finding in review.findings:
        assert finding["field_path"] == "/shots/1/action"
        assert finding["relation"]["other_field_path"] == "/shots/0/action"
        assert finding["quote"] in _at_path(episode, finding["field_path"])
        relation = finding["relation"]
        assert relation["other_quote"] in _at_path(
            episode, relation["other_field_path"]
        )
        assert finding["time"] and relation["other_time"]


def test_review_reuses_same_content_source_policy_and_reviews_content_change(context):
    source, references, episode = context
    client = FakeClient([{"findings": []}, {"findings": []}])
    reviewer = Reviewer(client)
    sources = _review_sources(source, references)

    first = reviewer.review(episode, sources)
    second = reviewer.review(episode, sources)
    assert first == second
    assert len(client.calls) == 1

    changed = _with_shot(episode, 0, action="内容已经变化的合法动作")
    reviewer.review(changed, sources)
    assert len(client.calls) == 2


def test_review_source_change_invalidates_cache(context):
    source, references, episode = context
    client = FakeClient([{"findings": []}, {"findings": []}])
    reviewer = Reviewer(client)
    reviewer.review(episode, _review_sources(source, references))

    changed_source = replace(source, archive_sha256="b" * 64)
    changed_references = replace(references, source_sha256="b" * 64)
    reviewer.review(
        episode, _review_sources(changed_source, changed_references)
    )
    assert len(client.calls) == 2


def test_store_reuses_valid_review_across_reviewers_but_not_policy_change(context, tmp_path):
    source, references, episode = context
    store = _store(tmp_path, episode)
    sources = _review_sources(source, references)
    first_client = FakeClient([{"findings": []}])
    first = Reviewer(first_client, store=store).review(episode, sources)

    second_client = FakeClient()
    second = Reviewer(second_client, store=store).review(episode, sources)
    assert second == first
    assert second_client.calls == []

    policy = tmp_path / "different-policy.md"
    policy.write_text("不同的审核政策文本", encoding="utf-8")
    third_client = FakeClient([{"findings": []}])
    third = Reviewer(third_client, store=store, prompt_path=policy).review(
        episode, sources
    )
    assert third.content_hash != first.content_hash
    assert len(third_client.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("review unavailable"),
        {"findings": [{"id": "broken"}]},
    ],
)
def test_invalid_or_failed_review_is_not_cached(context, tmp_path, failure):
    source, references, episode = context
    store = _store(tmp_path, episode)
    client = FakeClient([failure, {"findings": []}])
    reviewer = Reviewer(client, store=store)
    sources = _review_sources(source, references)

    with pytest.raises((ConnectionError, ReviewProtocolError)):
        reviewer.review(episode, sources)
    reviewer.review(episode, sources)
    assert len(client.calls) == 2
    reviewer.review(episode, sources)
    assert len(client.calls) == 2


def test_invalid_confirmation_is_not_cached_or_used_to_erase_other_findings(context):
    source, references, episode = context
    camera = _finding(episode, "camera-scene")
    false_t0 = _finding(episode, "false-t0")
    client = FakeClient(
        [
            {"findings": [camera, false_t0]},
            {"retract_ids": ["unknown-id"]},
            {"findings": [camera, false_t0]},
            {"retract_ids": ["false-t0"]},
        ]
    )
    reviewer = Reviewer(client)
    sources = _review_sources(source, references)

    with pytest.raises(ReviewProtocolError, match="retract"):
        reviewer.review(episode, sources)
    review = reviewer.review(episode, sources)
    assert [finding["id"] for finding in review.findings] == ["camera-scene"]
    assert len(client.calls) == 4


def test_review_service_failure_does_not_trigger_author_revision(context):
    source, references, episode = context
    author_client = FakeClient()
    author = Author(source, references, author_client)
    reviewer = Reviewer(FakeClient([ConnectionError("unavailable")]))

    with pytest.raises(ConnectionError):
        reviewer.review(episode, _review_sources(source, references))
    assert author.current is None
    assert author_client.calls == []


@pytest.mark.parametrize("unknown", ["root_visual_prompt", "shot_extra_fields"])
def test_author_strict_json_rejects_every_unknown_root_or_shot_key(context, unknown):
    source, references, episode = context
    payload = _episode_payload(episode)
    if unknown == "root_visual_prompt":
        payload["visual_prompt"] = "旧派生提示"
    else:
        payload["shots"][0]["extra_fields"] = []
    author = Author(source, references, FakeClient([payload]))

    with pytest.raises(AuthorProtocolError, match="schema"):
        author.start()
    assert author.current is None


def test_review_findings_are_structured_and_defensively_copied(context):
    _, _, episode = context
    finding = _confirmed(_finding(episode))
    review = Review("review", "hash", "rejected", (finding,))

    finding["field_path"] = "/shots/9/camera"
    finding["relation"]["type"] = "polluted"

    assert review.findings[0]["field_path"] == "/shots/0/camera"
    assert review.findings[0]["relation"]["type"] == "scene_mismatch"
    expected_type = tuple[dict[str, object], ...]
    assert get_type_hints(Review)["findings"] == expected_type
    assert get_type_hints(Store.complete_operation)["findings"] == expected_type
    with pytest.raises(TypeError, match="structured"):
        Review("bad", "hash", "rejected", ("legacy string",))


@pytest.mark.parametrize("persistent", [False, True])
def test_cache_mutation_cannot_redirect_author_or_skip_regrounding(
    context, tmp_path, persistent
):
    source, references, episode = context
    store = _store(tmp_path, episode) if persistent else None
    reviewer = Reviewer(
        FakeClient(
            [
                {"findings": [_finding(episode, "camera-scene")]},
                {"retract_ids": []},
            ]
        ),
        store=store,
    )
    sources = _review_sources(source, references)
    first = reviewer.review(episode, sources)

    first.findings[0]["field_path"] = "/shots/1/camera"
    first.findings[0]["relation"]["type"] = "polluted"
    second = reviewer.review(episode, sources)

    assert second is not first
    assert second.findings[0]["field_path"] == "/shots/0/camera"
    assert second.findings[0]["relation"]["type"] == "scene_mismatch"
    assert second.findings[0]["relation"] is not first.findings[0]["relation"]

    revised = _with_shot(episode, 0, camera="修正后的码头机位")
    author_client = FakeClient(
        [_episode_payload(episode), _episode_payload(revised)]
    )
    author = Author(source, references, author_client)
    author.start()
    assert author.revise(second) == revised

    invalid = deepcopy(second)
    invalid.findings[0]["quote"] = "当前镜头不存在的引文"
    with pytest.raises(ValueError, match="grounded"):
        author.revise(invalid)
    assert len(author_client.calls) == 2
    assert "宿主授权" in author.prompt


def _complete_cached_review(store, operation, verdict, findings):
    receipt = {
        "receipt_id": f"receipt-{operation.id}",
        "operation_id": operation.id,
        "kind": operation.kind,
        "input_hash": operation.input_hash,
        "verdict": verdict,
    }
    data = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
    relative = f"receipts/{operation.id}.json"
    path = store.path.parent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    store.complete_operation(
        operation.id,
        verdict=verdict,
        findings=findings,
        receipt_path=relative,
        receipt_sha256=sha256(data).hexdigest(),
    )
    return path


@pytest.mark.parametrize(
    "cached_shape",
    ["candidate", "accepted_with_finding", "rejected_empty", "ungrounded"],
)
def test_invalid_completed_cache_is_an_error_and_never_rewritten(
    context, tmp_path, cached_shape
):
    source, references, episode = context
    store = _store(tmp_path, episode)
    client = FakeClient()
    reviewer = Reviewer(client, store=store)
    sources = _review_sources(source, references)
    fingerprint = reviewer._fingerprint(episode, sources)
    operation = store.begin_operation(
        f"source-review-{fingerprint}", "source_review", fingerprint, None
    )
    finding = _confirmed(_finding(episode))
    verdict = "rejected"
    findings = (finding,)
    if cached_shape == "candidate":
        finding["status"] = "candidate"
    elif cached_shape == "accepted_with_finding":
        verdict = "accepted"
    elif cached_shape == "rejected_empty":
        findings = ()
    elif cached_shape == "ungrounded":
        finding["quote"] = "当前稿不存在的引文"
    receipt_path = _complete_cached_review(store, operation, verdict, findings)
    before = receipt_path.read_bytes()

    with pytest.raises(ReviewProtocolError, match="cached"):
        reviewer.review(episode, sources)
    assert client.calls == []
    assert receipt_path.read_bytes() == before


def test_cache_hit_must_match_composite_content_hash(context, tmp_path, monkeypatch):
    source, references, episode = context
    store = _store(tmp_path, episode)
    client = FakeClient()
    reviewer = Reviewer(client, store=store)
    sources = _review_sources(source, references)
    fingerprint = reviewer._fingerprint(episode, sources)
    operation = store.begin_operation(
        f"source-review-{fingerprint}", "source_review", fingerprint, None
    )
    receipt_path = _complete_cached_review(store, operation, "accepted", ())
    before = receipt_path.read_bytes()
    cached = store.find_valid_review(fingerprint, "source_review")
    monkeypatch.setattr(
        store,
        "find_valid_review",
        lambda *args, **kwargs: Review(
            cached.review_id, "0" * 64, cached.verdict, cached.findings
        ),
    )

    with pytest.raises(ReviewProtocolError, match="content_hash"):
        reviewer.review(episode, sources)
    assert client.calls == []
    assert receipt_path.read_bytes() == before


def test_completed_operation_with_sql_invalid_verdict_never_calls_or_overwrites(
    context, tmp_path
):
    source, references, episode = context
    store = _store(tmp_path, episode)
    client = FakeClient([{"findings": []}])
    reviewer = Reviewer(client, store=store)
    sources = _review_sources(source, references)
    fingerprint = reviewer._fingerprint(episode, sources)
    operation = store.begin_operation(
        f"source-review-{fingerprint}", "source_review", fingerprint, None
    )
    finding = _confirmed(_finding(episode))
    receipt_path = _complete_cached_review(store, operation, "rejected", (finding,))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE operations SET verdict = 'service_error' WHERE id = ?",
            (operation.id,),
        )
    before = receipt_path.read_bytes()
    assert store.find_valid_review(fingerprint, "source_review") is None

    with pytest.raises(ReviewProtocolError, match="completed"):
        reviewer.review(episode, sources)
    assert client.calls == []
    assert receipt_path.read_bytes() == before


def test_original_text_cannot_impersonate_source_segment_through_string_field(context):
    source, references, episode = context
    episode = _with_shot(episode, 0, location="S1/flow/0")
    finding = _finding(
        episode,
        field_path="/shots/0/location",
        quote=source.segments[0].action,
        time="whole_shot",
    )
    finding["evidence_origin"] = "original_text"
    finding["source_segment"] = "S1/flow/0"
    finding.pop("relation")

    with pytest.raises(ValueError, match="source_segments"):
        ground_finding(finding, episode, _review_sources(source, references))


@pytest.mark.parametrize(
    "field_path,valid_time,invalid_time",
    [
        ("/shots/0/start_state", "t=0", "during"),
        ("/shots/0/action", "during", "t=0"),
        ("/shots/0/end_state", "end", "during"),
        ("/shots/0/camera", "whole_shot", "t=0"),
        ("/shots/0/location", "whole_shot", "whenever"),
        ("/shots/0/shot_scale", "whole_shot", "end"),
    ],
)
def test_ground_finding_binds_time_to_field_semantics(
    context, field_path, valid_time, invalid_time
):
    source, references, episode = context
    sources = _review_sources(source, references)
    finding = _finding(episode, field_path=field_path, time=valid_time)
    assert ground_finding(finding, episode, sources) is None

    finding["time"] = invalid_time
    with pytest.raises(ValueError, match="time"):
        ground_finding(finding, episode, sources)


@pytest.mark.parametrize(
    "time,valid",
    [
        ("during", True),
        ("whole_shot", True),
        ("t=0", False),
        ("end", False),
        ("whenever", False),
    ],
)
def test_original_source_segment_allows_action_or_whole_shot_time(
    context, time, valid
):
    source, references, episode = context
    finding = _finding(
        episode,
        field_path="/shots/0/source_segments/0",
        quote=source.segments[0].action,
        time=time,
    )
    finding["evidence_origin"] = "original_text"
    finding["source_segment"] = "S1/flow/0"
    finding.pop("relation")
    sources = _review_sources(source, references)

    if valid:
        assert ground_finding(finding, episode, sources) is None
    else:
        with pytest.raises(ValueError, match="time"):
            ground_finding(finding, episode, sources)


def test_relation_other_time_uses_the_same_path_binding(context):
    source, references, episode = context
    finding = _finding(
        episode,
        field_path="/shots/0/start_state",
        time="t=0",
        relation={
            "type": "state_contradiction",
            "other_field_path": "/shots/0/action",
            "other_quote": "知遥与潮叔处理原包动作0",
            "other_time": "t=0",
        },
    )

    with pytest.raises(ValueError, match="other_time"):
        ground_finding(finding, episode, _review_sources(source, references))
    finding["relation"]["other_time"] = "during"
    assert ground_finding(finding, episode, _review_sources(source, references)) is None


def test_frozen_image_part_embeds_jpeg_bytes(tmp_path):
    path = tmp_path / "identity.png"
    Image.new("RGB", (20, 10), "navy").save(path)

    part = image_part(path, 10)

    data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (10, 5)


def test_frozen_completion_preserves_images_and_allows_text_only_resume(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(work, prompt, **options):
        work = Path(work)
        calls.append(
            {
                "resume": options["resume"],
                "request": json.loads(
                    (work / "input/request.json").read_text(encoding="utf-8")
                ),
                "images": json.loads(
                    (work / "input/images.json").read_text(encoding="utf-8")
                ),
            }
        )
        (work / "output/response.json").write_text('{"ok": true}', encoding="utf-8")
        return SimpleNamespace(
            ok=True,
            produced=["response.json"],
            record={"usage": {}, "seconds": 0},
            directory=work / "attempt",
        )

    monkeypatch.setattr("clean_studio.frozen.codex.run_structured_session", fake_run)
    work = tmp_path / "session"
    identity = tmp_path / "identity.png"
    Image.new("RGB", (8, 8), "navy").save(identity)
    image = image_part(identity, 32)
    request = {
        "model": "gpt-5.6-luna",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "source"}, image],
            }
        ],
    }

    assert completion(request, timeout=30, work_dir=work)["choices"]
    attached = calls[0]["images"]
    assert len(attached) == 1
    assert (work / attached[0]).read_bytes() == base64.b64decode(
        image["image_url"]["url"].split(",", 1)[1]
    )
    first_parts = calls[0]["request"]["messages"][0]["content"]
    assert not any(part.get("type") == "image_url" for part in first_parts)
    assert any("Actual attached image 1" in part.get("text", "") for part in first_parts)

    text_only = {
        "model": "gpt-5.6-luna",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "confirmed findings"}]}
        ],
    }
    assert completion(text_only, timeout=30, work_dir=work, resume=True)["choices"]
    assert [call["resume"] for call in calls] == [False, True]
    assert calls[1]["images"] == attached


def test_codex_adapter_resumes_same_session(monkeypatch, tmp_path):
    calls = []

    def fake_completion(request, *, timeout, work_dir=None, resume=False, sandbox=None):
        calls.append(
            {
                "request": request,
                "timeout": timeout,
                "work_dir": work_dir,
                "resume": resume,
                "sandbox": sandbox,
            }
        )
        return {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }

    monkeypatch.setattr("clean_studio.frozen.llm.completion", fake_completion)
    client = CodexClient(root=tmp_path, model="gpt-5.6-luna")
    session = client.create_session("author")
    for _ in range(2):
        result = client.ask_json(
            [{"type": "text", "text": "source"}],
            {"type": "object"},
            name="clean_author",
            max_tokens=100,
            timeout=30,
            session=session,
        )
        assert result == {"ok": True}

    assert calls[0]["work_dir"] == calls[1]["work_dir"]
    assert [call["resume"] for call in calls] == [False, True]
    assert calls[0]["request"]["model"] == "gpt-5.6-luna"


def test_frozen_truncated_json_is_protocol_failure(monkeypatch):
    def fake_completion(request, *, timeout, work_dir=None, resume=False, sandbox=None):
        return {
            "choices": [
                {"message": {"content": '{"episode_id":'}, "finish_reason": "length"}
            ]
        }

    monkeypatch.setattr("clean_studio.frozen.llm.completion", fake_completion)
    settings = JsonEndpoint("gpt-5.6-luna", ("codex://local",))

    with pytest.raises(ModelOutputTruncated):
        ask_json(
            [{"type": "text", "text": "source"}],
            {"type": "object"},
            name="clean_author",
            timeout=30,
            retry_truncated=False,
            settings=settings,
        )


def test_frozen_runtime_has_no_legacy_import_or_episode_skill():
    frozen = ROOT / "src/clean_studio/frozen"
    for path in frozen.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("novel_manga") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("novel_manga")
        assert "episode-production" not in path.read_text(encoding="utf-8")


def _author_operations(store):
    with store._db(readonly=True) as connection:
        rows = connection.execute(
            "SELECT status, metadata FROM operations WHERE kind = 'author' ORDER BY rowid"
        ).fetchall()
    return [{"status": row["status"], **json.loads(row["metadata"])} for row in rows]


def _author_store(tmp_path, episode):
    store = Store(tmp_path / "author.sqlite3")
    store.create_episode(replace(episode, shots=()), {"model": "frozen"})
    return store


def _persist_author_start(store, episode):
    from clean_studio.pipeline import Pipeline

    Pipeline(store, {})._persist_initial_authoring(episode)


def test_author_rehydrates_original_session_current_episode_and_failed_elapsed(context, tmp_path):
    source, references, episode = context
    store = _author_store(tmp_path, episode)
    clock = FakeClock()
    client = FakeClient(
        [_episode_payload(episode), RuntimeError("author service failed")],
        clock=clock,
        elapsed=[100, 200],
    )
    author = Author(source, references, client, clock=clock, store=store)
    author.start()
    _persist_author_start(store, episode)
    finding = _confirmed(_finding(episode, field_path="/shots/0/action"))
    with pytest.raises(RuntimeError, match="author service failed"):
        author.revise([finding], allowed_fields={"shot_001": {"action"}})
    original_session = author.session.name
    assert author.elapsed_seconds == 300

    revised = _with_shot(episode, 0, action="恢复原作者会话后的动作")
    new_clock = FakeClock()
    new_client = FakeClient([_episode_payload(revised)], clock=new_clock, elapsed=[300])
    recovered = Author(source, references, new_client, clock=new_clock, store=store)
    assert recovered.current == episode
    assert recovered.elapsed_seconds == 300
    assert str(recovered.session.work_dir) == original_session
    assert recovered.session.started is True

    assert recovered.revise([finding], allowed_fields={"shot_001": {"action"}}) == revised
    assert recovered.elapsed_seconds == 600
    assert new_client.calls[0]["session"] is recovered.session
    operations = _author_operations(store)
    assert [row["status"] for row in operations] == ["completed", "failed", "completed"]
    assert [row["duration_seconds"] for row in operations] == [100, 200, 300]
    assert {row["session"]["work_dir"] for row in operations} == {original_session}


def test_author_cold_recovery_preserves_cumulative_budget(context, tmp_path):
    source, references, episode = context
    store = _author_store(tmp_path, episode)
    clock = FakeClock()
    current = episode
    responses = [_episode_payload(episode)]
    for index in range(3):
        current = _with_shot(current, 0, action=f"累计耗时修订{index}")
        responses.append(_episode_payload(current))
    client = FakeClient(responses, clock=clock, elapsed=[1200, 1200, 1200, 1200])
    author = Author(source, references, client, clock=clock, store=store)
    author.start()
    _persist_author_start(store, episode)
    for _ in range(3):
        finding = _confirmed(_finding(author.current, field_path="/shots/0/action"))
        author.revise([finding], allowed_fields={"shot_001": {"action"}})
    assert author.elapsed_seconds == 4800

    final = _with_shot(current, 0, action="冷恢复后的最后600秒")
    new_clock = FakeClock()
    new_client = FakeClient([_episode_payload(final)], clock=new_clock, elapsed=[600])
    recovered = Author(source, references, new_client, clock=new_clock, store=store)
    finding = _confirmed(_finding(recovered.current, field_path="/shots/0/action"))
    assert recovered.revise([finding], allowed_fields={"shot_001": {"action"}}) == final
    assert new_client.calls[0]["timeout"] == 600
    assert recovered.elapsed_seconds == 5400

    exhausted_client = FakeClient()
    exhausted = Author(source, references, exhausted_client, store=store)
    finding = _confirmed(_finding(exhausted.current, field_path="/shots/0/action"))
    with pytest.raises(AuthorBudgetExceeded, match="cumulative"):
        exhausted.revise([finding], allowed_fields={"shot_001": {"action"}})
    assert exhausted_client.calls == []
    assert len(_author_operations(store)) == 5


def _crash_author_with_pending_operation(context, tmp_path, monkeypatch):
    from clean_studio.pipeline import produce

    source, references, episode = context
    wall = FakeClock()
    wall.advance(1000)
    monkeypatch.setattr("clean_studio.agents.authoring.time.time", wall)
    calls = []

    def fake_ask_json(parts, schema, **options):
        calls.append(options)
        if len(calls) == 1:
            work_dir = Path(options["work_dir"])
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "codex-session.json").write_text(
                json.dumps({
                    "backend": "codex",
                    "session_id": "12345678-1234-5678-1234-567812345678",
                }),
                encoding="utf-8",
            )
            raise KeyboardInterrupt
        return _episode_payload(episode)

    monkeypatch.setattr("clean_studio.agents.codex.frozen_ask_json", fake_ask_json)
    config = {
        "episode_id": source.video_id,
        "database_path": str(tmp_path / "pending-author.sqlite3"),
        "model": "frozen-model",
        "channel": "h3-first-frame",
        "input_id": source.material_id,
        "publish_target": "owner/private-dataset",
    }
    generation = 0

    def services():
        nonlocal generation
        generation += 1
        client = CodexClient(root=tmp_path / f"new-services-{generation}")
        return {
            "source": source,
            "references": references,
            "author": Author(source, references, client),
        }

    with pytest.raises(KeyboardInterrupt):
        produce(config, services=services(), stop_after="AUTHORING")
    assert len(calls) == 1 and calls[0]["resume"] is False
    return produce, config, services, calls, wall


def test_author_pending_crash_blocks_new_services_then_resumes_original_session(
    context, tmp_path, monkeypatch
):
    produce, config, services, calls, wall = _crash_author_with_pending_operation(
        context, tmp_path, monkeypatch
    )
    wall.advance(300)

    recovered = produce(config, services=services(), stop_after="AUTHORING")

    assert recovered.status == "blocked" and recovered.stage == "AUTHORING"
    assert recovered.launches == 2 and recovered.recovery_validation is True
    assert "pending" in recovered.blocked or "recover" in recovered.blocked
    assert len(calls) == 1
    store = Store.open_readonly(config["database_path"])
    operations = _author_operations(store)
    assert [row["status"] for row in operations] == ["failed"]
    assert operations[0]["duration_seconds"] == 300
    assert operations[0]["elapsed_total_seconds"] == 300
    assert operations[0]["session"]["started"] is True

    resumed = produce(config, services=services(), stop_after="AUTHORING")

    assert resumed.status != "blocked" and resumed.launches == 3
    assert len(calls) == 2 and calls[1]["resume"] is True
    assert calls[1]["work_dir"] == calls[0]["work_dir"]
    assert [row["status"] for row in _author_operations(store)] == [
        "failed",
        "completed",
    ]


def test_author_pending_crash_conservative_budget_exhaustion_allows_no_call(
    context, tmp_path, monkeypatch
):
    produce, config, services, calls, wall = _crash_author_with_pending_operation(
        context, tmp_path, monkeypatch
    )
    wall.advance(5400)

    recovered = produce(config, services=services(), stop_after="AUTHORING")
    exhausted = produce(config, services=services(), stop_after="AUTHORING")

    assert recovered.status == "blocked" and recovered.launches == 2
    assert exhausted.status == "blocked" and exhausted.launches == 3
    assert "cumulative" in exhausted.blocked
    assert len(calls) == 1
    operations = _author_operations(Store.open_readonly(config["database_path"]))
    assert [row["status"] for row in operations] == ["failed"]
    assert operations[0]["duration_seconds"] == 5400
    assert operations[0]["elapsed_total_seconds"] == 5400
