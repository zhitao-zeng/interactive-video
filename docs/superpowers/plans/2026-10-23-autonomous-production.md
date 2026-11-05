# Autonomous Production Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 整合主线自动恢复并通过正式入口执行三集无人干预验证，真实报告工程、内容和发布结果。

**Architecture:** 沿用 film/production/episode host tools 和现有状态文件。把已验证修复合到当前 main 接口，补阶段交接、失败恢复及发布的实际断点。验证运行冻结代码，禁止操作员替某一集补数据或跳阶段。

**Tech Stack:** Python、pytest、既有 Codex/HTTP 模型适配、H3/SD 媒体接口、FFmpeg、ModelScope SDK。

**Spec:** `docs/superpowers/specs/2026-10-23-autonomous-production-design.md`

## Global Constraints

- 旧二十集、素材、视频、take、source、授权与计数不变。
- 保留 main 现有资源策略和其他会话 WIP；不代为提交或覆盖。
- 运行任务代码冻结，不热换 worker。无远端源码 push。
- 重启、切模型、换目录均不续额度；相同失败没有新依据不无限重试。
- candidate 不计为 accepted；正常视觉、语音、音乐和源码质量门保留。
- 不新建队列数据库、兼容层、生产格式或按集名硬编码修法。
- 全部测试显式 `PYTHONPATH=src:tests`，使用现有项目环境；不新建或改动 Python 环境。

## Review Focus

- 主线资源策略已有修改；旧补丁不得把它覆盖成旧路由。
- deferred 首拍进入完整制作时，旧对话不能继续禁止当前合法 review/finish。
- 未确认提交占用原媒体额度；有视频的 attempt02 不得因本轮 max_attempts=1 被排除。
- 音乐与格式错误不得导致整集重新生成或重复编码已完成的媒体。
- 进程/发布重启必须从回执续接；真实测试不能从已有修后影片缓存冒充首跑。

## Execution rules

用户已明确批准方案并要求执行，不再重复请求计划确认。采用 inline 实施；最终使用独立新上下文审查。writing-plans/executing-plans 所引用的 using-git-worktrees、TDD、verification-before-completion、SDD helpers 在当前技能目录缺失；记录这一事实，使用本计划的明确 RED/GREEN 步骤及 `.runtime/autonomous-production-20261023/progress.md` 持久台账，不因此中断已授权工作。

共同命令前缀：

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python -m pytest
```

主线 WIP 基线保存在 `.runtime/autonomous-production-20261023/main-working-baseline.patch` 和 `baseline.json`。只记录/整合本任务的后续增量，不代提交该基线中其他作者的工作。任务要求的本地主线整合已有用户授权，不涉及远端源码 push。

### Task 1: 合并已验证的通用修复

**Files:**
- Integrate: `application/assets/candidates.py`、`media/asset_builder.py`、`application/agents/episode/input_{assets,records,recovery_policy,repair,request_recovery}.py`、`bounded_recovery.py`、`draft_operations.py`、`repair_operations.py`、`resume_inputs.py`。
- Integrate: `application/resolved_settings.py`、`interactive/runtime.py`、`media/{cache,generation,retries}.py`、`application/rendering/flow.py`、`application/repair/{history,source_support}.py`、`providers/phanrouter_references.py`、`story/voice_delivery.py`。
- Integrate: `application/identity/semantic_review.py`、`application/preparation/audit.py`、`application/music_timeline.py`、postmix input contracts/candidate/compilation/effects。
- Tests: 采用原修复的真实回归文件，清单与来源写到 `integration-inventory.json`。

**Interfaces:** 消费 main 当前接口与基线 WIP；输出同一源码树上的既有公开工具和可恢复媒体调用，不增加第二套接口。

- [ ] 保存各来源树的 tracked/untracked 文件差异。来源为 `zcode-w06-identity-20261023`、`zcode-remaining-three-20261023`、`native-four-finish-20261023`、`zcode-w13-music-anchor-20261023`；以各自 HEAD 到工作区差异为输入，不复制整棵旧树。
- [ ] 先合并回归测试并运行：`test_asset_auto_repair.py`、`test_user_approved_input_once.py`、`test_input_request_recovery.py`、`test_unconfirmed_retake_slots.py`、`test_actual_take_speech_syntax.py`、`test_postmix_music_plan_input.py`。Expected: 缺少本轮功能的真实测试失败；已在 main 生效的测试可直接通过并登记去重。
- [ ] 逐文件适配差异，遵循当前资源选择、原 scene scope 与新模型配置；禁止引入旧生产树的 `.rej`、配置退回或删除主线测试。记录每文件采用、已存在、冲突解决原因。
- [ ] 运行上述回归加 `test_asset_candidates_runtime.py test_episode_agent_input_repair.py test_music_timeline.py test_music_selection.py test_native_speech_policy.py test_final_audio_delivery_gate.py test_identity_auto_closure.py`。Expected: 全部目标回归通过，旧数据/预算保持的断言通过。
- [ ] 保存任务专属 patch 与测试结果；遵守 main WIP 所有权，不把他人基线修改代为提交。

### Task 2: 正式入口的阶段与恢复交接

**Files:** `src/novel_manga/interactive/film.py`、`production.py`、`scripts/interactive_video.py`、`application/agents/episode/{prompts,lifecycle,startup,readiness,flow}.py`；tests `test_original_film.py`、`test_episode_current_task.py`、`test_episode_agent_route_resume.py`。

**Interfaces:** 保持 `film.run(root, task_path, run_dir, edition, config=None, resume=False)` 和 `production.run_episode(...)`；新增媒体通道选择作为可选参数/任务配置，默认保持已有行为。使用 `next_action(ctx, status)` 与现有恢复判定，不由操作员猜下一步。

- [ ] 为以下完整状态转换先写失败用例：

```python
def test_resume_prompt_uses_current_review_authority():
    from novel_manga.application.agents.episode.prompts import resume_prompt
    prompt = resume_prompt({
        'mode': 'adopted_repair', 'phase': 'review', 'targets': [],
        'next_action': {'action': 'review', 'reason': 'first takes complete'},
        'execution_rules': ['Review and finish are allowed in the current phase.'],
    })
    assert 'Review and finish are allowed in the current phase.' in prompt
    assert 'keep them in force' not in prompt
```

- [ ] 复用 `test_original_film.py` 已有隔离 workflow 桩，加入 production 首次 deferred、下一次 accepted 的序列；断言同 edition/原预算/原源只准备一次，host 依据真实恢复条件推进。加入显式 Fast produce 不被硬编码 H3 覆盖的用例。Expected: 当前阶段提示/单次入口缺口出现 RED。
- [ ] 在恢复提示中携带当前执行规则，明确历史阶段限制由当前 host scope 更新，保留用户永久约束；在状态判断中区分可续 deferred、可恢复技术失败、真实需作者决策、已耗尽/终态。调用既有恢复函数，不自动新增预算。
- [ ] 将正式 produce 的 lane 选择贯通 configure/assets/production，使用已有 VIDEO_LANES 校验；first-frame 模式分别沿现有 H3/SD 入口，不混协议。
- [ ] 添加三任务批入口或扩展既有可用 batch 入口，复用 `film.run`；单任务故障保持结果并不阻其他任务。只启动官方入口，不生成单集专用补救脚本。批次记录每个正常 run 的路径与结果。
- [ ] 跑上述任务回归与 `test_episode_agent_session.py test_episode_agent_conductor.py test_workflow_sd_entries.py`。Expected: 阶段自动接续、预算不续、已完成产物不重建。

### Task 3: 输入与素材故障在正确层恢复

**Files:** `media/asset_prompts.py`、`application/assets/candidates.py`、`application/identity/context.py`、`application/preparation/audit.py`、`application/agents/episode/{input_candidate,input_contracts,tool_inputs,recovery}.py`。
**Tests:** `test_card_brief_scope.py`、`test_effective_identity_context.py`、现有 input/request/asset 回归。

**Interfaces:** 图像生成仍调用现有 `character_prompt`/asset factory；源审仍调用当前 source audit；失败返回既有结构化错误和合法动作。绝不把技术错误当为源文否决。

- [ ] 增加真实群体缺省 visual_archetype 测试：设计原文含 `A procession of nine-tailed foxes carrying foxfire`，最终提示保留该设计、不得同时强制“只画一个人物/不拿任何剧情道具”。另覆盖普通单人、明确动物与合法设计持物，保持原 scene scope 测试。
- [ ] 增加有效 phase 覆盖旧基础描述的回放：基础为人类服装，当前已通过 phase 为兽形；源审上下文不得把被覆盖的人类外貌当当前事实。未覆盖且仍有效的身份信息保留；文字源审不产生“看到了图片”的假证据。
- [ ] 在已存在的 check 参数校验中报告已知 location/scene_objects/props 被误列 in_frame 的具体镜号、名字和类别；允许真实已登记或此次源支持的新角色。对未知身份使用正常身份流程，不以字符串动物名硬编码归属。
- [ ] 用现有 source/request recovery 消费有效同源英文纠正；源、台词、参考与计数不改，真实 request gate 仍执行；错误片段之外不重编/重拍。
- [ ] 对坏 JSON 给出具体文件/位置和有效动作，要求 Agent 完整覆盖新文件并 parse 后提交；不把字符串拼成“通过”的对象、不自动采用半个内容提案。
- [ ] 运行新增用例及 `test_episode_agent_input_repair.py test_input_request_recovery.py test_identity_auto_closure.py test_asset_candidates_runtime.py test_scene_scope.py`。Expected: 真问题仍拦、技术/上下文误报不再无谓消耗媒体。

### Task 4: 配乐、合成与完成判定

**Files:** `application/agents/episode/{completion,media_operations,music}.py`、`application/{music_timeline,music_selection}.py`、`interactive/delivery.py`。
**Tests:** `test_finish_music_recovery.py`、`test_music_timeline.py`、`test_music_selection.py`、现有 final audio/delivery 回归。

**Interfaces:** 使用真实选中 take 的语法与实际时间线；完成产物通过既有 `thin_media_report`/delivery receipt 交接，后一步出错不丢上一阶段产物。

- [ ] 增加真实 42 秒源音乐不足实际 43.499 秒 cue 的回归：在可获取实际时间线时先返回具体 cue/区间，失败后已有媒体与时间线可复用，下一次选曲不重新生成视频。
- [ ] 增加“正常合成已完成、音乐未混入”的恢复用例：保持音乐门不通过，但已完成 assembly 保存；正常 select_music 混入后 finish 复用视频。禁止仅删除 music_not_applied 检查。
- [ ] 对既有 canonical destination 进行统一绑定与恢复；空 receipt 不算有片，已有真实自定义出口保持可追溯。导出、候选、最终通过分开，统计只用实际文件/回执。
- [ ] 跑 `test_actual_take_speech_syntax.py test_finish_music_recovery.py test_music_selection.py test_music_timeline.py test_final_audio_delivery_gate.py` 和相关 delivery 测试。Expected: 不新增不必要编码/视频，视觉和声音门不因流程修复被豁免。

### Task 5: 发布出口与不中断续传

**Files:** 从 `zhiyao-modelscope-20261023/scripts/ops/zhiyao_modelscope_sync.py` 提取当前真实行为到 `src/novel_manga/interactive/publication.py`；正式批入口调度该出口；test `test_modelscope_publication.py`。

**Interfaces:** `publish_deliveries(root, spec, state_path, api)` 消费当前完整 delivery、公开元数据和已授权的 dataset ID；返回实际上传/核验状态，凭据仍由现有 SDK 取得。不硬编码原二十集或账户 token。

- [ ] 用 fake SDK 写故障序列：媒体上传成功后索引检查暂时不一致；第二轮从远端库存/成功回执恢复，不重复上传大文件。再覆盖重启、单写者锁、已完成媒体替换、错误目录/缺文件不发布。
- [ ] 保留当前已实测的路径/字节对账与匿名可访问抽样流程；参数化 scope/dataset/目录。公开说明仅剧集信息，内部过程保存在本地运行记录。
- [ ] 正式调度自动调用发布并有界重试；鉴权失败明确停止该出口，不影响已完成影片记录。正常网络等待不伪装生产进展。
- [ ] 跑发布回归，Expected: 0真实上传/0真实生成的故障回放全过。真实验证使用独立的验证 dataset，不覆盖原 `Flame4pd/zhiyao-episodes`。

### Task 6: 统一集成与故障回放

**Files:** `tests/test_autonomous_production_recovery.py`、正式批入口、任务台账与 `docs/autonomous-production.md`。

- [ ] 将 spec 中本次真实失败按责任任务组成离线场景；走正式入口/实际 host handlers，供应与生成边界模拟，保留每个故障与恢复事件。
- [ ] 验证三任务并行时一个失败不冻结其余、重启不重复任务/额度、首拍到收口状态连续、错误 reason 能指向具体阶段。
- [ ] 跑相关测试后跑一次全量 pytest，日志写任务 runtime 并直接读取 pytest 汇总/退出码；既有失败先对基线复现，不冒称全绿，也不删除真实断言。
- [ ] 做一次新上下文整体审查；修 Important/Critical 实证问题后跑受影响测试。保留 review/fix 证据。
- [ ] 对 main 当前 WIP 与记录基线比较，只有本任务增量冷接；若他人新增修改，三方合并保留而不是整文件覆盖。接回后执行同样关键回归。

### Task 7: 三集无人干预实跑

**Files:** 正式 batch 任务声明及 `.runtime/autonomous-production-20261023/validation/`；原有二十集不变。

- [ ] 固定 W10/W12/W06 原素材 task/zip/series material 和选择的正式媒体配置；每集独立新 edition/run，记录实际预算/代码版本/路由/输入来源。
- [ ] 从整合后的 main 冻结同一运行版本，三个任务只由正式批入口启动。可只读监控，不运行单集补救脚本、不写候选/审核/状态、不手动换阶段。
- [ ] 引擎按通用规则恢复预期故障；新宿主缺陷保留本轮失败，修通用代码、测试、冷发布后通过同一正式入口恢复。恢复验证不冒充从原包一次首跑无介入。
- [ ] 检查每集 artifact/预算/供应商任务、正常视觉和声音结果及发布回执，分别计算：工程闭环、内容通过、发布完成、人工介入次数、重复提交次数和是否超额。
- [ ] 只有三集达到预先定义的无人干预条件才报告通过。若某集只能在预算内明确停止，报告该边界与真实成功数，不将 candidate 或 exit0 当作验收成功。
- [ ] 更新主线文档、任务台账、源码整合状态与真实验证结果；不覆盖旧二十集，不远端 push 源码。
