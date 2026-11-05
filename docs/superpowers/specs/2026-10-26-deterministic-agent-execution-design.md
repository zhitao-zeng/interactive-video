# 确定性生产执行与 Agent 异常修复设计

## 背景与已确认方向

目标是从原始素材包完成一集无人值守生产，正式视觉/声音终审 accepted 并上传，同时保留现有验收、额度和全部历史证据。已确认采用“宿主执行正常路径，Agent 只处理内容和媒体异常”的方向；不继续堆提示词，不增加 Agent 层级，不让宿主自动改剧情。

A3–A5 仅作失败证据，不恢复、不改稿、不清零。A5 的最后记录显示源审已转为 `issues=[]`，但普通 `first_frames` 没有由宿主自动执行；Agent 调用 `finish` 得到 `missing_video` 后无进展退出。这是正常阶段续跑断点，与此前创作稿中的场景、动作和时序问题分开处理。

## 当前问题

1. 创作合同通过主要证明结构和来源约束合格，独立语义问题直到生产才暴露，占用生产修复额度。
2. `readiness.automatic_action()` 只自动处理 `render/review/finish`，普通首帧仍依赖 Agent 推阶段。
3. `finish` 的缺失视频分支自行推断 `required_action`，与 readiness 的 `next_action()` 可能不一致。
4. Agent 可主动请求结束，但工具 job `completed` 只表示调用执行完，不表示整集 accepted；正常路径不应依赖 Agent 是否愿意继续。
5. 恢复提示混入正常镜头、完整 opening、无关能力和规则。A5 的恢复提示约 47 KB，`current-task` 约 68 KB，异常定位被稀释。
6. 首帧编译中的源字段异常可能只形成顶层错误，未稳定映射到对应 clip、可修字段和正式 `check/apply` 路由。

## 方案选择

采用现有单状态机内的确定性执行：复用 `next_action()`、现有 episode host tools、创作会话和语义审核，不新增队列、生产模式或另一套状态文件。

不采用两个替代方向：

- 只强化提示词和 `finish` 说明：改动较小，但仍把正常阶段推进依赖在模型行为上。
- 拆成创作、首帧、渲染等多个 Agent：职责看似更细，实际增加会话、交接和恢复路径，不符合简化目标。

## 一、创作交接先过一次独立语义门

在 `interactive/film.py` 的作者候选打包后、资产构建和生产启动前，复用 `application/preparation/audit.py` 的独立审核及现有缓存策略。

- `creative_contract` 继续只负责结构和创作合同，不把语义审核塞入格式校验。
- 语义审核只判断，不改稿。输入包含原始包证据和当前完整作者稿，覆盖场景、动作、状态、时序和来源承诺。
- 每个剧本/来源/政策指纹只采用一次有效独立语义审核；生产准备遇到相同指纹时复用该回执，指纹变化才重审。
- opening 的确定性一致性检查、实际附件检查、图审、视频审和终审仍保留。语义审核通过不代表媒体通过。
- 审核拒绝时，不构建新资产、不启动生产 Agent；把逐镜头 findings 作为 pending feedback 交回同一个创作 Agent。
- 创作者只能在原额度和合法字段内修订。修订产生新指纹并重新审核；已有 `creative_candidate` 不得因提前返回而跳过 pending feedback。
- 原额度耗尽、没有内容进展或来源不可读时如实停止，不自动新建作者、不追加次数。

## 二、宿主自动执行普通首帧和后续正常动作

在 `agents/episode/readiness.py` 和现有 `flow.py` 分发点接入普通 `first_frames`，不另建循环。

- `next_action()` 是当前阶段的唯一路由事实源。
- 当最新路由为普通 `first_frames` 时，宿主根据缺失首帧的当前计划派生 payload，只包含普通 clip 和空额外引用；不由 Agent 重写 opening、H3 或引文。
- 普通首帧完成后，重新计算路由，继续现有 render、review、合成和 finish 流程。
- `reuse_candidate`、`retake_candidate`、源输入冲突、图审拒绝和其他异常仍进入 Agent，不自动重画或重拍。
- 复用现有“相同动作、相同状态不得无变化重放”的保护；自动执行不增加图片、视频、Agent 时间或启动额度。
- 实际生成前仍强制校验真实附件、引用注册和哈希。编译失败不调用付费生图。

调度器不因 `blocked`、剩余时间或失败 `finish` 笼统重开任务。只有现有明确恢复语义可以续跑，watchdog 对真正 Agent 无进展仍然有效。

## 三、统一结束和失败回执路由

- `completion.py` 的缺失视频早退分支复用最新 `next_action()` 的 action、reason 和 blocked，不再独立猜测 `first_frames/render`。
- 每次工具操作后都基于最新状态重新计算路由，不能拿旧 `required_action` 覆盖新的源审结果。
- 正式 `produce-batch` 路径的正常 `finish` 由宿主发起；Agent 不再需要以 `finish` 推进或结束异常修复。
- 保留 `EpisodeCompletion.finish()` 的全部验收检查及旧任务兼容入口。失败 finish 不得生成 accepted manifest，不得复用旧 manifest 冒充本次通过。
- 正常路径中，Agent 提前 finish 且仍有合法异常工作时，不得借 `finished_with_issues` 跳过工作。正式完成仍只由当前导出、当前视觉/声音终审和发布条件决定。
- `current_task` 保留失败操作的结构化结果，包括 `required_action`、具体 blocker 和 message；明确区分“工具调用完成”和“整集完成”。

## 四、只向 Agent 提供当前异常

只有最新 `next_action().action == agent` 时构造生产 Agent 修复任务。

投影仅包含：

- 当前 blocked/source conflict/retry clip 的交集；
- 审核问题、原始证据和最近拒绝；
- 本错误允许修改的字段及 RepairGrant/InputScope；
- 当前剩余额度和正式 `check/apply` 所需协议。

普通首帧目标不携带全量修复能力。首帧异常不重复注入宿主持有的完整 `authored_opening_frame`；需要原文时从正式源文件读取。没有对应异常时，不注入 input、scoped-review、音乐或混音的长规则。

完整状态和证据仍保存在磁盘；裁剪只针对模型输入，不删除历史，也不改变计数。

## 五、把首帧源错误送回正确修复层

`media/first_frames.py` 的引用注册等源字段错误，应在 episode first-frame 边界按 clip 形成结构化错误，包含：

- `invalid_fields`，例如 `opening_frame.reference_subjects`；
- 原始编译错误和当前 clip；
- 对源拥有字段使用正式 input `check/apply` 的路由；
- 未发生生图、未消耗图片候选的明确事实。

这些字段不能伪装成普通首帧行的自由参数让 Agent 改写，也不能由宿主自动忽略、重命名或补登记。

## 六、异常与停止语义

- 创作语义拒绝：回原创作 Agent，未过门前不进入生产。
- 生产 `next_action=agent`：只处理当前异常；修复后由宿主继续。
- Agent 对真实异常连续无有效进展：沿用现有 watchdog 停止，不转为无限 deferred。
- 相同失败动作没有输入或状态变化：不自动重放。
- 合法额度耗尽：保留 blocked 和全部回执，不新建同任务规避额度。
- 软件修复后的续跑标为恢复验证；从头无人成功只能来自新的冻结 edition。

## 回归与真实验证

先以 A3–A5 的只读证据和历史失败夹具做定向回归，不修改旧运行目录。

必须覆盖：

1. 结构合格但时序冲突的作者稿：不建资产、不启动生产，反馈回到同一创作者；修订后重审。
2. 相同语义审核指纹只审核一次，内容变化后重审；失败 verdict 不进入 normal。
3. 源审转绿且缺首帧：宿主执行一次普通 `first_frames`，无需 Agent 回合；相同失败不盲重试。
4. `finish → missing_video` 但最新路由为首帧：走首帧；最新路由仍为 agent：不得生图或渲染。
5. Agent 提前 finish：不能提前 accepted、上传或跳过合法异常工作。
6. 未注册 `reference_subjects`：付费生成前返回逐 clip 源错误和正式修复路由。
7. 恢复投影只含受影响 clip 和必要权限，不含正常 20 镜的完整 opening。
8. 原 watchdog、终态缓存、余额、回执和恢复测试保持通过；失败 finish 不复用旧 manifest。

定向回归通过后执行一次全仓回归。真实小验证按“作者语义门 → 正式 input 修复 → 代表首帧”顺序推进，前一阶段失败不进入下一阶段，不烧整集。小验证通过不代表整集成功。

只有上述验证通过，才从原包新建独立冻结 edition，固定代码、模型、输入、标准额度和私有发布目标，经单一 `produce-batch` 完整运行。最终成功仍要求有效视频 N/N、正式 accepted 成片 1 个、可核实上传 1 个；candidate、exit 0 和局部小样均不计成功。

## 预计文件归属

实施时先核对工作区现有 WIP，只改本设计所需增量，不整体覆盖文件：

- `src/novel_manga/interactive/film.py`、`creation.py`：创作语义交接与原作者反馈。
- `src/novel_manga/application/preparation/`：现有审核回执复用，不新增第二套审核。
- `src/novel_manga/application/agents/episode/readiness.py`、`flow.py`、`completion.py`：正常执行和统一路由。
- `current_task.py`、`prompts.py`、`first_frames.py`：异常投影和结构化错误。
- `skills/episode-production/`：替换旧的 Agent 推阶段说明，与真实工具协议一致。
- 对应现有测试文件：先补失败回归，再实施最小修复。

不修改 A3–A5 的稿件、图片、请求、审核、计数或状态，不提交、不推送，不热换任何运行任务。
