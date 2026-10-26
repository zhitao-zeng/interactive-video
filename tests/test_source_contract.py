import json
import shutil
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

from clean_studio.domain import Dialogue, EpisodeSpec, FixedOption, Shot
from clean_studio.source import build_reference_manifest, load_source, validate_episode

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_BASE = Path(
    "/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022"
)
ORIGINAL_W10_ZIP = ORIGINAL_BASE / ".runtime/zhiyao-batch/mini-EP04C_W10_失物海.zip"
SNAPSHOT_ROOT = ROOT / "inputs/w10"
MATERIAL_DIR = SNAPSHOT_ROOT / "material"
STYLE_DIR = SNAPSHOT_ROOT / "style"
CONFIG_PATH = ROOT / "configs/w10.json"


def _raw_episode(zip_path: Path = ORIGINAL_W10_ZIP) -> dict:
    with ZipFile(zip_path) as archive:
        return json.loads(
            archive.read("pkg/episode/02-结构化剧本.json").decode("utf-8")
        )["episodes"][0]


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _partition(items: tuple, parts: int) -> list[tuple]:
    small, large = divmod(len(items), parts)
    groups = []
    offset = 0
    for index in range(parts):
        size = small + (index < large)
        groups.append(items[offset : offset + size])
        offset += size
    return groups


def _valid_episode(source) -> EpisodeSpec:
    shots = []
    for scene, part_count in zip(source.scenes, [2] * 8 + [4]):
        for segments in _partition(scene.segments, part_count):
            number = len(shots) + 1
            actions = [segment.action for segment in segments if segment.action]
            shots.append(
                Shot(
                    shot_id=f"shot_{number:03d}",
                    scene_id=scene.scene_id,
                    source_segments=tuple(
                        segment.segment_id for segment in segments
                    ),
                    location=scene.name,
                    start_state=(
                        f"{scene.name}的门窗和现场光线可见，知遥与物件处于本段动作前位置"
                    ),
                    action="；".join(actions)
                    or "知遥面向镜头拿稳三张目的地卡，保持当前位置完成本段表达",
                    end_state=(
                        f"{scene.name}中知遥双脚站定，手中物件握稳，背景门窗与现场光线不变"
                    ),
                    camera="固定机位，人物与关键物件同时入画",
                    shot_scale="中景",
                    visible_characters=scene.characters,
                    dialogue=tuple(
                        Dialogue(speaker=segment.speaker, line=segment.line)
                        for segment in segments
                        if segment.speaker is not None
                    ),
                    reference_subjects=("C01",),
                    duration_seconds=9,
                )
            )
    return EpisodeSpec(
        episode_id=source.video_id,
        title=source.title,
        target_seconds=source.target_seconds,
        style_id="travel-live",
        shots=tuple(shots),
        fixed_question=source.fixed_question,
        fixed_options=source.fixed_options,
        music_cues=(),
    )


def _with_shot(episode: EpisodeSpec, index: int, **changes) -> EpisodeSpec:
    shots = list(episode.shots)
    shots[index] = replace(shots[index], **changes)
    return replace(episode, shots=tuple(shots))


@pytest.fixture(scope="session")
def source():
    return load_source(ORIGINAL_W10_ZIP)


@pytest.fixture(scope="session")
def references(source):
    return build_reference_manifest(source, MATERIAL_DIR, STYLE_DIR)


@pytest.fixture()
def episode(source):
    return _valid_episode(source)


def test_real_w10_zip_preserves_every_flow_and_dialogue(source):
    raw = _raw_episode()
    assert len(raw["scenes"]) == 9
    assert sum(len(scene["flow"]) for scene in raw["scenes"]) == 85
    assert sum(
        "speaker" in item
        for scene in raw["scenes"]
        for item in scene["flow"]
    ) == 60

    assert len(source.scenes) == 9
    assert len(source.segments) == 85
    assert len(source.dialogue) == 60
    assert source.material_id == "W10"
    assert source.video_id == "EP04C"
    assert source.title == "失物海"

    expected = []
    for scene in raw["scenes"]:
        for index, item in enumerate(scene["flow"]):
            expected.append(
                (
                    f"{scene['sceneId']}/flow/{index}",
                    scene["sceneId"],
                    index,
                    item.get("action"),
                    item.get("speaker"),
                    item.get("line"),
                    item.get("delivery"),
                )
            )
    actual = [
        (
            segment.segment_id,
            segment.scene_id,
            segment.flow_index,
            segment.action,
            segment.speaker,
            segment.line,
            segment.delivery,
        )
        for segment in source.segments
    ]
    assert actual == expected
    assert all(
        segment.speaker is None and segment.line is None
        for segment in source.segments
        if segment.action is not None
    )


def test_scene_names_come_from_full_script_not_character_order(source):
    assert tuple(scene.name for scene in source.scenes) == (
        "失物海码头",
        "打捞台",
        "失物登记棚",
        "海边认领区",
        "旧灯塔门外",
        "海边认领区",
        "灯塔工作台",
        "离岸栈桥",
        "固定片尾投票",
    )
    scene = source.scenes[3]
    assert scene.characters == ("C046", "C01")
    assert scene.segments[1].speaker == "C046"
    assert scene.segments[2].speaker == "C01"


def test_fixed_question_and_targets_are_exact(source):
    raw = _raw_episode()
    assert source.fixed_question == raw["fixedQuestion"]
    assert source.fixed_options == tuple(
        FixedOption(
            label=option["label"],
            target_world=option["targetWorld"],
            target_video=option["targetVideo"],
            title=option["title"],
        )
        for option in raw["fixedOptions"]
    )
    assert [
        (option.label, option.target_world, option.target_video, option.title)
        for option in source.fixed_options
    ] == [
        ("A", "W11", "EP05A", "静音群岛"),
        ("B", "W12", "EP05B", "瓶中四季"),
        ("C", "W13", "EP05C", "重力集市"),
    ]


def test_snapshot_provenance_matches_original_and_copies():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["input_id"] == "W10"
    assert config["style"]["style_id"] == "travel-live"
    assert [(item["subject_id"], item["name"]) for item in config["subjects"]] == [
        ("C01", "知遥"),
        ("C046", "潮叔"),
    ]
    assert "shots" not in config
    assert "shot_patches" not in config
    assert len(config["snapshots"]) == 7
    for item in config["snapshots"]:
        original = Path(item["source_path"])
        snapshot = ROOT / item["path"]
        assert original.is_file(), original
        assert snapshot.is_file(), snapshot
        assert item["recorded_date"] == "2026-10-26"
        assert len(item["sha256"]) == 64
        assert _digest(original) == item["sha256"]
        assert _digest(snapshot) == item["sha256"]


def test_reference_manifest_is_real_deterministic_and_separates_registration(
    source, references
):
    rebuilt = build_reference_manifest(source, MATERIAL_DIR, STYLE_DIR)
    assert references.to_bytes() == rebuilt.to_bytes()
    assert references.source_sha256 == source.archive_sha256
    assert references.recorded_date == "2026-10-26"
    assert references.style_id == "travel-live"
    assert [(subject.subject_id, subject.name) for subject in references.subjects] == [
        ("C01", "知遥"),
        ("C046", "潮叔"),
    ]
    assert "失物海" in references.subjects[1].profile

    assert [reference.role for reference in references.references] == [
        "identity",
        "wardrobe",
        "style",
        "style",
    ]
    assert [reference.order for reference in references.references] == [1, 2, 3, 4]
    assert [reference.path for reference in references.references] == [
        "material/知遥-身份基准-v1.1.png",
        "material/知遥-综合视觉卡-v1.1.png",
        "style/board.png",
        "style/historical-frame.jpg",
    ]
    assert len(references.references_for("C01")) == 2
    assert references.references_for("C046") == ()
    assert all(
        reference.role not in {"location", "prop"}
        for reference in references.references
    )
    for reference in references.references:
        assert (SNAPSHOT_ROOT / reference.path).is_file()
        assert _digest(SNAPSHOT_ROOT / reference.path) == reference.sha256
        assert reference.recorded_date == "2026-10-26"
        assert reference.path not in {"style.json", "material/style.json"}
        assert not reference.path.endswith(".jpeg")
    for reference in references.references[2:]:
        assert "不复制人物" in reference.scope
        assert "文字" in reference.scope
        assert "POV" in reference.scope


def test_reference_manifest_detects_source_image_change(source, tmp_path):
    material = tmp_path / "material"
    style = tmp_path / "style"
    shutil.copytree(MATERIAL_DIR, material)
    shutil.copytree(STYLE_DIR, style)
    baseline = build_reference_manifest(source, material, style)
    assert build_reference_manifest(source, material, style).to_bytes() == baseline.to_bytes()

    identity_path = material / "知遥-身份基准-v1.1.png"
    with identity_path.open("ab") as handle:
        handle.write(b"immutable-input-change")
    changed = build_reference_manifest(source, material, style)
    assert changed.references[0].sha256 != baseline.references[0].sha256
    assert changed.to_bytes() != baseline.to_bytes()


def test_reference_manifest_refuses_missing_example(source, tmp_path):
    material = tmp_path / "material"
    style = tmp_path / "style"
    shutil.copytree(MATERIAL_DIR, material)
    shutil.copytree(STYLE_DIR, style)
    (style / "board.png").unlink()
    with pytest.raises(FileNotFoundError, match="board.png"):
        build_reference_manifest(source, material, style)


def test_source_content_change_is_detectable(tmp_path, source):
    changed_path = tmp_path / "changed-w10.zip"
    with ZipFile(ORIGINAL_W10_ZIP) as original, ZipFile(changed_path, "w") as changed:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "pkg/episode/02-结构化剧本.json":
                payload = json.loads(data)
                payload["episodes"][0]["scenes"][0]["flow"][1]["line"] += "（改动）"
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            changed.writestr(info, data)
    changed_source = load_source(changed_path)
    assert changed_source.archive_sha256 != source.archive_sha256
    assert changed_source.dialogue[0].line != source.dialogue[0].line


def test_domain_is_immutable_and_has_no_derived_shot_fields(episode):
    forbidden = {"opening_frame", "visual_prompt", "motion_prompt", "storyboard"}
    assert forbidden.isdisjoint(field.name for field in fields(Shot))
    assert isinstance(episode.shots, tuple)
    assert isinstance(episode.shots[0].source_segments, tuple)
    assert isinstance(episode.shots[0].dialogue, tuple)
    with pytest.raises(FrozenInstanceError):
        episode.shots[0].duration_seconds = 12


def test_valid_episode_passes(episode, source, references):
    assert len(episode.shots) == 20
    assert sum(shot.duration_seconds for shot in episode.shots) + 3 == 183
    validate_episode(episode, source, references)


@pytest.mark.parametrize(
    "field", ["opening_frame", "visual_prompt", "motion_prompt", "storyboard"]
)
def test_author_rejects_derived_fields(episode, source, references, field):
    bad = _with_shot(episode, 0, **{field: "旧派生提示"})
    with pytest.raises(ValueError, match="author-owned fields"):
        validate_episode(bad, source, references)


def test_dialogue_line_change_is_rejected(episode, source, references):
    dialogue = list(episode.shots[0].dialogue)
    dialogue[0] = replace(dialogue[0], line="被改写的对白")
    bad = _with_shot(episode, 0, dialogue=tuple(dialogue))
    with pytest.raises(ValueError, match="dialogue"):
        validate_episode(bad, source, references)


def test_c046_cannot_be_relabelled_c01(episode, source, references):
    for shot_index, shot in enumerate(episode.shots):
        for line_index, line in enumerate(shot.dialogue):
            if line.speaker == "C046":
                dialogue = list(shot.dialogue)
                dialogue[line_index] = replace(line, speaker="C01")
                bad = _with_shot(episode, shot_index, dialogue=tuple(dialogue))
                with pytest.raises(ValueError, match="dialogue"):
                    validate_episode(bad, source, references)
                return
    pytest.fail("W10 fixture has no C046 dialogue")


def test_cross_scene_source_segment_is_rejected(episode, source, references):
    shot = episode.shots[0]
    other_scene_segment = episode.shots[2].source_segments[0]
    bad = _with_shot(
        episode,
        0,
        source_segments=(other_scene_segment, *shot.source_segments[1:]),
    )
    with pytest.raises(ValueError, match="scene"):
        validate_episode(bad, source, references)


def test_missing_source_segment_is_rejected(episode, source, references):
    shot = episode.shots[0]
    bad = _with_shot(episode, 0, source_segments=shot.source_segments[:-1])
    with pytest.raises(ValueError, match="source coverage"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize(
    "end_state",
    ["对白完成，准备下一镜", "准备下一镜", "镜头结束", "同上", "状态不变"],
)
def test_vague_end_state_is_rejected(episode, source, references, end_state):
    bad = _with_shot(episode, 0, end_state=end_state)
    with pytest.raises(ValueError, match="end_state"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize(
    "end_state",
    [
        "对白完成，准备下一镜头",
        "对白说完了，准备下一镜",
        "下一镜继续",
        "结束拍摄",
        "x",
    ],
)
def test_pure_production_end_state_variants_are_rejected(
    episode, source, references, end_state
):
    bad = _with_shot(episode, 0, end_state=end_state)
    with pytest.raises(ValueError, match="end_state"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize(
    "end_state",
    [
        "对白完成，准备转场",
        "对白完成，镜头结束",
        "准备转场，拍摄完成",
        "同上，准备下一镜",
    ],
)
def test_combined_non_state_atoms_are_rejected(
    episode, source, references, end_state
):
    bad = _with_shot(episode, 0, end_state=end_state)
    with pytest.raises(ValueError, match="end_state"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize(
    "end_state",
    [
        "对白完成后，知遥把背包放在桌上，码头灯仍亮着",
        "知遥结束拍摄后收好手机，灯塔暖光仍亮着",
        "潮叔握稳旧船灯，木支架已装好，工作台灯亮着",
    ],
)
def test_concrete_end_state_with_process_words_is_allowed(
    episode, source, references, end_state
):
    candidate = _with_shot(episode, 0, end_state=end_state)
    validate_episode(candidate, source, references)


def test_character_reference_must_be_visible(episode, source, references):
    bad = _with_shot(
        episode,
        0,
        visible_characters=("C046",),
        reference_subjects=("C01",),
    )
    with pytest.raises(ValueError, match="character reference.*visible_characters"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize("duration", [3, 16, 4.5, "9", True])
def test_duration_must_be_integer_from_4_to_15(
    episode, source, references, duration
):
    bad = _with_shot(episode, 0, duration_seconds=duration)
    with pytest.raises(ValueError, match="duration_seconds"):
        validate_episode(bad, source, references)


@pytest.mark.parametrize("duration", [4, 15])
def test_total_body_plus_three_seconds_must_be_180_to_300(
    episode, source, references, duration
):
    bad = replace(
        episode,
        shots=tuple(replace(shot, duration_seconds=duration) for shot in episode.shots),
    )
    with pytest.raises(ValueError, match="total duration"):
        validate_episode(bad, source, references)


def test_total_duration_boundaries_are_inclusive(episode, source, references):
    lower = [9] * len(episode.shots)
    lower[0] = 6
    upper = [15] * len(episode.shots)
    upper[0] = 12
    for durations in (lower, upper):
        candidate = replace(
            episode,
            shots=tuple(
                replace(shot, duration_seconds=duration)
                for shot, duration in zip(episode.shots, durations)
            ),
        )
        validate_episode(candidate, source, references)


def test_fixed_tail_cannot_be_rewritten(episode, source, references):
    with pytest.raises(ValueError, match="fixedQuestion"):
        validate_episode(
            replace(episode, fixed_question="另一个问题"), source, references
        )
    options = list(episode.fixed_options)
    options[0] = replace(options[0], target_video="WRONG")
    with pytest.raises(ValueError, match="fixedOptions"):
        validate_episode(
            replace(episode, fixed_options=tuple(options)), source, references
        )


def test_unregistered_and_registered_but_unbound_subjects_are_distinct(
    episode, source, references
):
    unregistered = _with_shot(
        episode, 0, visible_characters=("C01", "C999")
    )
    with pytest.raises(ValueError, match="unregistered subject"):
        validate_episode(unregistered, source, references)

    unbound = _with_shot(episode, 0, reference_subjects=("C046",))
    with pytest.raises(ValueError, match="registered subject.*no bound image"):
        validate_episode(unbound, source, references)
