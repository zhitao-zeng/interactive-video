# Deterministic Production Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让正常生产由宿主一次派生事实，独立审核只判断，Agent 仅处理确认异常；在不降低任何验收的前提下，完成 W10 从原包到 accepted 与上传。

**Architecture:** 保留现有 episode host、媒体、审核和发布接口，不新建第二套生产格式。先修缓存与来源选择，再把身份/opening/参考/H3 编译与异常授权分开；恢复、路由和状态投影复用现有入口。旧 raw-novel 路径保留为显式 legacy，不把互动改编继续当成需要重复抽取的文本。

**Tech Stack:** Python、pytest、既有 Codex/HTTP 模型适配、H3/SD 媒体接口、FFmpeg、ModelScope SDK。

**Spec:** `docs/superpowers/specs/2026-10-23-autonomous-production-design.md`（目标与约束）；本计划补充 2026-10-25 架构审查结论。

## Global Constraints

- 验收一项不删：源审、opening 预审、实际图审、视频技术/内容审核、正式视觉与完整声音终审、accepted 后上传全部保留。
- 正常事实只由宿主派生一次；审核不得重新创作、改名、改编号或替 Agent 决定修复。
- 不清零输入/图片/视频/审核计数，不追加时间或次数，不降低验收，不为单镜写特例，不热换运行代码、模型或通道。
- 旧任务、稿件、图片、请求、回执、审核和计数全部保留。恢复验证与从头无人验证分别报告。
- main 和其他 worktree 的其他会话 WIP 不得 reset、整体覆盖或代为提交。只整合本任务已验证增量。
- 生产只走一个 `produce-batch`。诊断、回归、独立审查可并行，但文件归属必须先登记。
- 只有有效视频 N/N、正式 accepted 成片 1 个、可核实私有上传 1 个才算 W10 成功；candidate、exit0、文件存在和上传意图均不算成功。

## Review Focus

- 无 `authored_source.md` 的 raw-novel 仍走原抽取路径，不能被互动改编的 `structured_cast()` 吞掉（Task 2 测试）。
- 相同输入不得重复付费审核或生成；输入改变必须使旧证据失效（Tasks 1、3、4、7 测试）。
- 旧候选即使曾发布，只要当前源审失败就不得复用或返回 `ok=true`（Task 5 测试）。
- 恢复只能消费已有回执和原余额，不得新增时间、take、模型切换或隐式重试（Task 6 测试）。
- 完整 candidate 与 accepted 必须分开；上传只对当前 accepted 回执执行且可核实（Tasks 7、8 测试）。
- `characters/in_frame` 的含义不同：角色在场与 t=0 可见性不能强行改成同一字段（Task 3 测试）。

---

### Task 1: 先修缓存与 opening 来源一致性

**Files:**
- Modify: `src/novel_manga/application/identity/store.py:34-50`
- Modify: `src/novel_manga/application/agents/episode/local_preparation.py:118-154`
- Modify: `src/novel_manga/media/first_frames.py:49-60`
- Modify: `src/novel_manga/application/agents/episode/first_frames.py:30-36,388-391`
- Test: `tests/test_identity_auto_closure.py`
- Test: `tests/test_local_preparation.py`
- Test: `tests/test_first_frame_source_preflight.py`

**Interfaces:**
- Consumes: `structured_cast()` 的 `characters`、`chapter_script.turns`、`first_frame_opening/shots` 旧快照。
- Produces: 完整身份输入指纹、审计后缓存指纹、单一 opening shot 选择顺序，供 Tasks 3/4 使用。

- [ ] **Step 1: 写三个失败用例**

```python
def test_character_change_invalidates_identity_cache(ep):
    flow.resolve_chapter(ep)
    script = read_json(ep/'chapter_script.json')
    script['shots'][0]['characters'] = ['另一个人']
    atomic_write_json(ep/'chapter_script.json', script)
    assert load_chapter(ep).context == {}


def test_source_cache_uses_post_audit_fingerprint(ctx, monkeypatch):
    monkeypatch.setattr(local_preparation, 'audit', lambda directory: (
        (directory/'entity/entities.json').write_text('[]') or
        {'source_readable': True, 'issues': []}))
    local_preparation.source_gate(ctx)
    saved = read_json(ctx.run_dir/'local-source-check.json')
    bible = ctx.novel_dir/'story_bible.json'
    original = ctx.episode_dir/'authored_source.md'
    expected = local_preparation.inputs(ctx.episode_dir)['files'] | {
        str(bible): [bible.stat().st_mtime_ns, bible.stat().st_size],
        str(original): [original.stat().st_mtime_ns, original.stat().st_size]}
    assert saved['inputs'] == expected


def test_opening_fallback_has_one_authority(clip):
    assert opening_shot(clip) == clip['first_frame_opening'][0]
```

第三个用例同时构造不同 `shots` 与 `first_frame_opening`，要求 prompt、contract 和 freshness 全部选 `first_frame_opening or shots`。缓存指纹断言应以实际保存的 `story_bible/authored_source` 为准，不能只检查文件存在。

- [ ] **Step 2: 运行定向测试，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_identity_auto_closure.py tests/test_local_preparation.py tests/test_first_frame_source_preflight.py`
Expected: characters 未使缓存失效、源审保存审计前指纹、opening 回退优先级不一致。

- [ ] **Step 3: 最小实现**

- `chapter_inputs()` 的 evidence 每行加入 `characters`，其他键不变，避免无关缓存大面积失效。
- `source_gate()` 把 stamp 计算提成局部函数；`audit()` 返回后重新计算再写 `local-source-check.json`。
- 在 `media/first_frames.py` 提供 `opening_shot(clip)`，顺序为当前剧本匹配镜头优先，否则 `first_frame_opening or shots`；`source_opening/opening_contract/unchanged_opening_scale` 共用。
- 删除 `FirstFrameOperations.prepare()` 外层第二次 `remember_opening_sources()`；生产缓存只由 `review_batch()` 写，测试替身须显式保存伪审核，不能把 mock 当生产持久化。

- [ ] **Step 4: 运行相关测试**

Run: 同 Step 2，再加 `tests/test_first_frame_production.py tests/test_first_frame_review_facts.py`
Expected: 全部 PASS；真实源冲突仍 fail-closed。

- [ ] **Step 5: 保存独立变更快照与结果，不提交其他 WIP**

记录本任务文件、测试输出和 `git diff --check`。旧快照因来源统一而 stale 的首帧必须按现有规则复审，不能迁移旧 pass。

### Task 2: 身份与剧情审核只判断，修复归异常路径

**Files:**
- Modify: `src/novel_manga/application/identity/flow.py`
- Modify: `src/novel_manga/application/identity/semantic_review.py`
- Modify: `src/novel_manga/application/identity/ledger_resolution.py`
- Modify: `src/novel_manga/application/planning/cast_inputs.py`
- Modify: `src/novel_manga/application/preparation/audit.py`
- Modify: `src/novel_manga/application/preparation/actions.py`
- Test: `tests/test_identity_auto_closure.py`
- Test: `tests/test_story_identity.py`
- Test: `tests/test_h3_book_preparation.py`

**Interfaces:**
- Consumes: Task 1 的完整缓存指纹；已校验 `chapter_script/authored_source`。
- Produces: `structured_cast()` 的确定性 candidate；`IdentityReviewRejected(candidate, findings, inputs)`；只有显式异常路径才可修改 candidate/台账。

- [ ] **Step 1: 写 reviewer 不得二次创作的失败用例**

```python
def test_reviewer_rejection_does_not_rewrite_identity_or_ledger(ep, monkeypatch):
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: {
        'approved': False, 'reason': '绑定错误',
        'correction': {'actors': [], 'bindings': [], 'removed_source_ids': []}})
    before = (ep.parent/'entity/entities.json').read_bytes()
    with pytest.raises(IdentityReviewRejected):
        flow.resolve_chapter(ep)
    assert not (ep/'identity_context.json').exists()
    assert (ep.parent/'entity/entities.json').read_bytes() == before
```

另加：无 `authored_source.md` 时仍调用 `chapter_identity_source`；同一拒绝输入不得重复问模型；`named_missing` 只能在显式采用后重新绑定；旧 correction trace 不得冒充新 judge-only 通过。

- [ ] **Step 2: 运行身份与准备回归，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_identity_auto_closure.py tests/test_story_identity.py tests/test_h3_book_preparation.py`
Expected: reviewer correction 当前能改 candidate/台账，非 Agent 环境可能继续自动改写。

- [ ] **Step 3: 实现 judge-only 与异常路由**

- 互动改编继续使用 `structured_cast()`，不从动作段重新抽取人物。
- `bindings()` 先跑现有 `validate()` 和身体碰撞检查；reviewer 只返回 `approved/reason/findings/inputs/candidate`。拒绝不写成功身份上下文，不合并 correction。
- 相同输入的拒绝缓存直接抛 `IdentityReviewRejected`；输入改变才允许新审核。升级身份缓存 policy，旧 `semantic_review.approved` 不直接等于新协议通过。
- 正常 `resolve_chapter()` 不再调用 `register_names()` 或自动合并同名主体；这些仅进入显式异常修复。`named_missing` 由 Agent/书级台账采用后重新 `load_chapter()` 与 `map_source_reading()`，不重编号旧实体。
- `prepare_cast()` 中 `judge_unknown()/grow_unbound_roster()` 的正常自动增长循环也移到显式异常路径，避免章级与书级仍各自决定创建谁。
- raw-novel 保留原生产者。`preparation/actions.py` 的确认问题统一返回 `needs_repair/needs_replan` 和原始 audit，由 Agent 决定；旧无 Agent 自动修复若保留，必须是显式 legacy 开关。

- [ ] **Step 4: 跑身份、源审及准备测试**

Run: 同 Step 2，再加 `tests/test_parallel_cast_binding.py tests/test_repeated_source_actors.py`
Expected: 全部 PASS；修复后仍需独立 reviewer，不因 reviewer 曾给 correction 就自动通过。

- [ ] **Step 5: 保存协议迁移证据，不提交其他 WIP**

记录新旧 policy、拒绝缓存形状、raw-novel 兼容结果和台账字节对比。

### Task 3: 统一 opening 与不可变参考编译结果

**Files:**
- Modify: `src/novel_manga/media/first_frames.py`
- Modify: `src/novel_manga/media/reference_views.py`
- Modify: `src/novel_manga/application/agents/episode/first_frames.py`
- Modify: `src/novel_manga/interactive/sd_first_frames.py`
- Test: `tests/test_first_frame_prompt.py`
- Test: `tests/test_supplied_wardrobe_reference.py`
- Test: `tests/test_first_frame_production.py`

**Interfaces:**
- Consumes: Task 1 的 `opening_shot()`、Task 2 的已审身份与剧情。
- Produces: 单一 `CompiledOpening(row, contract, assets, draft_frame)` 与不可变参考清单（path、hash、role、scope、order），供生图、图审、视频和缓存共同使用。

- [ ] **Step 1: 写 H3/SD 参考一致性和正常行编译失败用例**

```python
def test_h3_and_sd_share_compiled_references(ctx, clip):
    left = compile_opening(ctx, clip, lane='h3')
    right = compile_opening(ctx, clip, lane='sd20-fast')
    assert [(r.path, r.reference_scope, r.order) for r in left.references] == \
           [(r.path, r.reference_scope, r.order) for r in right.references]
    assert left.row['image_references'] == [r.path for r in left.references]
    assert left.draft_frame['visible_characters'] == clip['shots'][0]['opening_frame']['visible_characters']
```

另加：`{'clip_id': ...}` 正常行不再要求 `image_prompt/H3/source_quote/image_references`；`extra_references` 仍是唯一可选补充参考创作字段；`in_frame` 与 t=0 `visible_characters` 不强行相等。

- [ ] **Step 2: 运行参考与 opening 回归，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_first_frame_prompt.py tests/test_supplied_wardrobe_reference.py tests/test_first_frame_production.py`
Expected: H3 仍要求 Agent 重复提供创作字段，H3/SD 参考选择不一致。

- [ ] **Step 3: 实现单一编译产物**

- 复用 SD 已存在的 `reference_subjects` 过滤规则，经 `canonical_paths()` 和 `opening_references()` 展开一次；记录最终 path/hash/role/scope/order，不重新推断附件顺序。
- `authored_row()` 只从源 opening 写 `image_prompt/visible_characters`；正常行默认 `extra_references=[]`。
- 删除首帧 `source_quote` 的接收与校验。它当前不进入 prompt、候选 identity 或图审；input repair 的逐字 `source_quote` 仍保留。
- `request_prompt/build/check/review_candidate/reserve/status` 全消费同一 `CompiledOpening`，参考哈希与现有 `source_asset_state` 合并，不新建资产数据库。
- 图审和生成必须收到同一份实际有序附件；opening/source/asset 变化仍使旧像素 stale。

- [ ] **Step 4: 跑 opening、参考、图审测试**

Run: 同 Step 2，再加 `tests/test_first_frame_source_preflight.py tests/test_first_frame_source_origin.py tests/test_first_frame_review_facts.py`
Expected: 全部 PASS；真实图片缺陷仍区分 source/image，参考 scope 不错位。

- [ ] **Step 5: 保存编译前后等价性证据，不提交其他 WIP**

记录附件列表、顺序、scope、哈希、prompt 和源合同；不生成媒体。

### Task 4: H3 与首帧正常路径由宿主编译，Agent 只处理异常

**Files:**
- Modify: `src/novel_manga/application/agents/episode/first_frames.py`
- Modify: `src/novel_manga/application/agents/episode/requests.py`
- Modify: `src/novel_manga/application/agents/episode/prompts.py`
- Modify: `skills/episode-production/references/output-contract.md`
- Modify: `skills/episode-production/references/production-workflow.md`
- Test: `tests/test_first_frame_argument_validation.py`
- Test: `tests/test_first_frame_production.py`

**Interfaces:**
- Consumes: Task 3 的 `CompiledOpening` 和 opening 源审结果。
- Produces: 可复用 `prompt_h3` 或宿主重新编译的合法请求；Agent 正常协议只需 `clip_id` 与可选 `extra_references`。

- [ ] **Step 1: 写缺失 H3 由宿主编译的失败用例**

```python
def test_missing_h3_is_compiled_after_opening_review(ctx, monkeypatch):
    monkeypatch.setattr(opening_source, 'review_batch', lambda ctx, prepared: {})
    result = FirstFrameOperations(ctx, builder=fake_png,
        checker=lambda *a: {'consistent': True, 'problems': []}).prepare(
            {'frames': [{'clip_id': 'clip_01', 'extra_references': []}]})
    assert result['ok']
    assert result['clips']['clip_01']['first_frame']['prompt_h3']
```

最终断言只检查合法 H3、dialogue slots、Picture 映射和 `h3_stamp`；不断言存在首帧 `source_quote`。opening 有冲突时必须仍阻断且不调用翻译或生图。

- [ ] **Step 2: 运行首帧参数与生产测试，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_first_frame_argument_validation.py tests/test_first_frame_production.py`
Expected: 缺失 H3 当前要求 Agent 提供。

- [ ] **Step 3: 实现现有请求的 freshness 判断和宿主编译**

- 仅当带 `draft_frame` 的 `h3_stamp` 与 `validate_existing_request()` 均通过才复用 `clip.prompt_h3`；非空不等于可复用。
- 缺失或失效时沿用 `rendering.h3.convert()` 生成，再跑同一验证，不新增另一套 H3 格式。
- opening 源审先于翻译；冲突时不花翻译、图片或视频额度。
- status schema、prompt 和 Skill 不再要求正常 Agent 写 `image_prompt/H3/source_quote/image_references`。旧冗余值仅允许与编译值完全相同；不同值路由源修复。

- [ ] **Step 4: 跑 H3、图审和 Skill 协议测试**

Run: 同 Step 2，再加 `tests/test_actual_take_speech_syntax.py tests/test_native_speech_policy.py tests/test_episode_current_task.py`
Expected: 全部 PASS；请求引文、语音与画幅门不下降。

- [ ] **Step 5: 保存接口与文档一致性证据，不提交其他 WIP**

同步 `first_frames.status`、prompt 与安装 Skill，确保 Agent 看到的协议就是实际工具协议。

### Task 5: 按错误类型最小授权与一次 scope 解析

**Files:**
- Modify: `src/novel_manga/application/agents/episode/input_contracts.py`
- Modify: `src/novel_manga/application/agents/episode/input_repair.py`
- Modify: `src/novel_manga/application/agents/episode/input_candidate.py`
- Modify: `src/novel_manga/application/agents/episode/input_compilation.py`
- Modify: `src/novel_manga/application/agents/episode/current_task.py`
- Modify: `src/novel_manga/application/agents/episode/repair_operations.py`
- Test: `tests/test_episode_agent_input_repair.py`
- Test: `tests/test_episode_current_task.py`

**Interfaces:**
- Consumes: opening/source/image findings 的 `basis`、origin、当前合同与审核指纹。
- Produces: 宿主生成的 `RepairGrant(operation, shot_indexes, shot_fields, evidence)` 和统一 `InputScope`；Agent 不能自报 error_kind 扩权。

- [ ] **Step 1: 写越权字段、越权镜号和旧候选复用失败用例**

```python
def test_opening_grant_rejects_unrelated_field(ctx):
    grant = RepairGrant('input', [3], ['opening_frame'], {'basis': ['opening.opening_frame.image_prompt']})
    with pytest.raises(RepairArgumentError):
        input_repair.repair(ctx, {'clip_id': 'clip_03', 'action': 'input',
            'source_quote': '原文', 'shots': [{'index': 3, 'turns': []}]},
            grant=grant)
```

另加：`device_state` 可被合法修复；共享 shot 的必要依赖仍可展开；其他 shot index 不得夹带；`retake_candidate` 携带源补丁直接拒绝；旧候选在当前源审失败时不得 `ok=true`。

- [ ] **Step 2: 运行 input 与 current-task 回归，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_episode_agent_input_repair.py tests/test_episode_current_task.py`
Expected: 当前完整 `SHOT_FIELDS` 被当作实际权限；`device_state` 没有可执行路径。

- [ ] **Step 3: 实现 grant 与统一 scope**

- `opening.camera→camera`、`opening.shot_scale→shot_scale`、`opening.opening_frame.*→完整 opening_frame`、`opening.device_state→device_state`；把 `device_state` 加入 `SHOT_FIELDS`。
- `origin=image` 只允许 `retake_candidate`；参考恢复/策略变化只允许 `reuse_candidate`；服务中断只允许原候选 `resume`。
- grant 校验放进 `input_repair.repair()` 的前置，位于源审、receipt、建卡之前，使 check/apply 共享。
- `InputScope` 统一 allowed indexes、targets、共享 shot、phase/location 影响和 source review indexes；`repair()` 与 `prepare_candidate()` 使用它，`compile_input()` 只验证实际编译未越界并保留依赖额度检查。
- `current_task.targets[].input` 只展示本次允许字段/镜号/指纹；全局 `repair_capabilities` 仅作为系统词汇表，不再暗示全量授权。

- [ ] **Step 4: 跑 input、current-task、进度与恢复测试**

Run: 同 Step 2，再加 `tests/test_first_frame_field_progress.py tests/test_first_frame_source_route.py tests/test_local_preparation.py`
Expected: 全部 PASS；复用路径重新执行当前源审，重复错误不算进展。

- [ ] **Step 5: 保存异常类型到授权的映射和额度证据，不提交其他 WIP**

记录 grant、scope、修改字段、输入余额和 source findings；Agent 只收到具体异常所需最小协议。

### Task 6: 单一恢复入口、状态投影和有效路由

**Files:**
- Modify: `src/novel_manga/application/agents/episode/recovery.py`
- Modify: `src/novel_manga/application/agents/episode/lifecycle.py`
- Modify: `src/novel_manga/interactive/first_frame_entry.py`
- Modify: `src/novel_manga/application/production/episode_agents.py`
- Modify: `src/novel_manga/application/production/upstream_batch.py`
- Modify: `src/novel_manga/application/resolved_settings.py`
- Modify: `src/novel_manga/interactive/production.py`
- Modify: `src/novel_manga/llm/config.py`
- Test: `tests/test_autonomous_recovery.py`
- Test: `tests/test_episode_agent_route_resume.py`
- Test: `tests/test_resolved_agent_route.py`
- Test: `tests/test_episode_current_task.py`

**Interfaces:**
- Consumes: 现有回执、deadline、额度、fallback 历史和 Task 5 的 grant。
- Produces: 纯 `plan_resume(ctx, saved, config, retry_issues)`；`initial_route/effective_route/route_history`；只读 acceptance/status 投影。

- [ ] **Step 1: 写恢复与路由失败用例**

```python
def test_first_frame_entry_consumes_existing_window_grant(ctx):
    grant = write_window_grant(ctx, seconds=300)
    result = run_first_frame_entry(ctx, resume=True)
    assert result['resume_decision'] == 'existing_window_grant'
    assert result['new_generation_grants'] == 0
    assert result['remaining_seconds'] == 300
```

另加：fallback 后 `effective_route` 更新但 `initial_route` 不变；endpoint role 重新解析不得改变旧 model/base/transport；显式不同配置必须拒绝或产生一次 route-migration receipt；旧记录缺字段标 `legacy/unknown`，不能伪造完整路由。

- [ ] **Step 2: 运行恢复、路由与状态回归，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_autonomous_recovery.py tests/test_episode_agent_route_resume.py tests/test_resolved_agent_route.py tests/test_window_grant.py tests/test_episode_current_task.py`
Expected: 多入口重复恢复、role 重新解析可改变旧路由、状态快捷路径未统一验收。

- [ ] **Step 3: 实现单写入方与路由快照**

- `plan_resume()` 只返回恢复原状态/恢复动作/config，不写文件；仅 `lifecycle.start_run()` 应用并写 `agent-run.json`。`first_frame_entry` 只传 resume 意图，不能提前吞掉 window grant；`upstream_batch` 只提交授权证据。
- `diagnose/remember/recall` 和 `bounded_recovery.evidence/grant` 保持职责，不作为第二启动入口。`switch_for_refusal` 是运行中策略，不再是第三种启动方式。
- 新任务保存已解析非秘密 model/base/transport；fallback 更新 `effective_route` 并追加 `route_history`。默认 resume 严格恢复；旧缺字段标 unknown。
- `edition_status()` 和快捷 passed 返回共用只读 acceptance/status 投影；`current-status/current-task` 只能投影，不能反写。`thin_media_report`、审查和交付记录仍是各自事实源，不强行合并文件。

- [ ] **Step 4: 跑恢复、路由、状态与预算测试**

Run: 同 Step 2，再加 `tests/test_bounded_upstream_recovery.py tests/test_upstream_recovery_handoff.py tests/test_recovery_coverage.py tests/test_episode_state.py`
Expected: 全部 PASS；剩余时间、take、会话和回执不变，无隐式模型迁移。

- [ ] **Step 5: 保存恢复顺序和路由事实证据，不提交其他 WIP**

记录 initial/effective/history、grant 消费次数、deadline 和 take，确保整合没有新增恢复能力。

### Task 7: 后期共享编排，保留正式视觉和声音门

**Files:**
- Modify: `src/novel_manga/application/agents/episode/music.py:208-230`
- Modify: `src/novel_manga/application/agents/episode/audio_mix.py:91-106`
- Modify: `src/novel_manga/application/agents/episode/postproduction.py`
- Modify: `src/novel_manga/application/agents/episode/readiness.py`
- Modify: `src/novel_manga/interactive/delivery.py`
- Test: `tests/test_music_selection.py`
- Test: `tests/test_music_timeline.py`
- Test: `tests/test_episode_ambience_mix.py`
- Test: `tests/test_final_audio_delivery_gate.py`
- Test: `tests/test_modelscope_publication.py`

**Interfaces:**
- Consumes: 已审音乐/环境政策、Task 6 的状态投影和有效路由。
- Produces: 共享“政策→apply_assembly→save_result→工具回执”辅助函数；视觉/声音终审和 accepted 发布仍独立。

- [ ] **Step 1: 写共享编排和验收失败用例**

```python
def test_music_and_ambience_use_same_assembly_helper(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(postproduction, 'persist_audio_policy', lambda *a: calls.append(a) or {'ok': True})
    MusicOperations(ctx).publish(valid_music)
    AudioMixOperations(ctx).prepare(valid_ambience)
    assert len(calls) == 2
    assert all(call.kwargs['context'] for call in calls)
```

另加：相同音乐输入不重复编码；cutaway 不改原生 take；`current_task_summary()` 的固定 finish 提示不得绕过 `next_action()`；视觉或声音未通过只能 candidate，publication 不上传。

- [ ] **Step 2: 跑音乐、后期和发布回归，确认 RED**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q tests/test_music_selection.py tests/test_music_timeline.py tests/test_episode_ambience_mix.py tests/test_final_audio_delivery_gate.py tests/test_modelscope_publication.py`
Expected: 两处发布编排重复；部分状态提示和完成路径未共享。

- [ ] **Step 3: 抽取公共辅助，保持即时反馈与缓存**

- 只抽共享 `persist_audio_policy()`，保持当前立即 `apply_assembly()`、`save_result()` 和工具回执，不延后到 finish。
- 音乐与环境声各自的候选校验、source review、授权政策保持独立；不能合并成一次内容审核。
- 保留 ambience 输入签名、`export_context`、cutaway 派生缓存和原生视频流。
- 最终视觉与完整声音审查仍分别执行；`reviewed_delivery()` 与 `finish_reviewed_delivery()` 仍绑定同一导出文件，publication 继续只对 accepted 执行并保留续传对账。

- [ ] **Step 4: 跑音乐、后期、视觉/声音与发布测试**

Run: 同 Step 2，再加 `tests/test_episode_postproduction.py tests/test_postproduction_current_state.py tests/test_final_cut_review.py tests/test_delivery_single_encode.py`
Expected: 全部 PASS；不重复编码、不叠音、不把 candidate 上传。

- [ ] **Step 5: 保存媒体字节、缓存和验收证据，不提交其他 WIP**

确认原 take、最终导出、审查引用和发布回执一一对应。

### Task 8: 历史回放、分层小验证与 W10 冻结无人验证

**Files:**
- Modify: `tests/test_autonomous_production_recovery.py`
- Modify: `tests/test_production_batch.py`
- Modify: `docs/autonomous-production.md`
- Create: `.runtime/<task-owner>/frozen-inputs.json`（仅运行记录，不进 Git）
- Create: `.runtime/<task-owner>/validation-result.json`（仅运行记录，不进 Git）

**Interfaces:**
- Consumes: Tasks 1-7 的冻结代码、原始包与原始参考、冻结角色路由。
- Produces: 分层实证、有效视频 N/N、正式 accepted 成片、私有上传回执及真实失败分类。

- [ ] **Step 1: 先跑受影响回归和一次全量测试**

Run: `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q`
Expected: pytest 自身 exit0；既有失败必须先在与本任务无关基线复现，不能删除断言或冒称全绿。

- [ ] **Step 2: 做独立代码复核和历史无媒体回放**

核对事实派生只发生一次、审核无二次创作、参考清单一致、恢复不续额、发布只接 accepted。历史失败只读回放，保留所有原始回执，不重启旧耗尽任务。

- [ ] **Step 3: 分层小验证，前一阶段失败就不继续**

- A：原 ZIP CRC、`scenes[].flow[].speaker`、opening 无未解决冲突、完整 episode.json 写入/读回、合法 input check/apply 发布。
- B：一个首帧，核对实际 prompt、附件、图片和同图同参考图审。
- C：一个视频镜头，完整解码、ASR、对白和基本质量通过。
- D：合成后正式视觉与完整声音审查必须 accepted；私有上传必须取得远端文件清单与索引回执。

每阶段保存真实请求/结果；失败只做最小通用修复和定向回归，不进入下一阶段、不烧整集、不反复重试同一失败。

- [ ] **Step 4: 全部小验证通过后启动冻结 W10**

从 `mini-EP04C_W10_失物海.zip` 与原系列 material 新建独立 edition。启动前冻结代码、模型、输入、标准额度和私有发布目标；不继承旧稿、图片、会话、审核或计数。只用一个 `produce-batch`，运行中只读监控，不逐镜人工修片。

- [ ] **Step 5: 核对最终交付并更新真实记录**

必须同时满足：有效视频 N/N、完整正式 accepted 成片 1、私有上传 1。分别报告代码回归、真实生产、人工介入次数、恢复/从头分类、重复提交和额度使用。失败则保留原因，不把恢复成功包装成从头无人成功，不继续盲重建。
