# W10《失物海》无人值守生产：进度、失败原因与收缩计划

日期：2026-10-26  
范围：通用生产流程、A2 分层小样、A3–A5 恢复验证；旧 v1/v2 仅作历史证据。

## 一、当前结论

**整集没有跑通，也没有从头无人干预成功。当前没有生产任务在运行。**

| 指标 | 当前有效结果 |
|---|---|
| A5 有效视频镜头 | **0/20** |
| 正式 accepted 完整成片 | **0** |
| 可核实上传 | **0** |
| A3/A4/A5 的性质 | 恢复验证，不是从头无人干预成功 |
| 最新通用修复 | 有回归和 A/B/C 小样证据，未通过从头整集验证 |

A5 最终为 `blocked / passed=false / exit_code=2`，`finish` 返回 20 镜 `missing_video`；发布为 `waiting_for_acceptance`，`uploads={}`、`verified_episodes=0`。不能把候选、小样、进程退出或测试通过计作整集成功。

最终失败也不能只概括为“创作有时序矛盾”：

- A5 最后一次自动源审为 `issues=[]`；
- 最新首帧调用仍报 `clip_01: opening reference_subjects must name registered source cards`；
- 普通 `first_frames` 没有由宿主自动推进；
- Agent 在失败 `finish` 后连续两次无有效进展，宿主停止。

此外，对最终稿的只读复核发现，自动源审的空问题列表存在漏报，不能据此宣称所有源字段已经实质一致。

## 二、一路遇到的问题

### 1. 旧 v2：首帧生成了，但源输入和修复协议没有闭环

旧交接记录为 15/26 张合格首帧、0/26 视频、0 成片、0 上传。最后两个镜头分别是：

- `clip_25`：自拍中景与 `wide establishing` 景别冲突；
- `clip_26`：独自自拍、只有一个可见人物，却仍写 `medium two-shot`。

生产 Agent 改过 `opening_frame` 和 `visual_prompt`，却没有解决 `shot_scale`，消耗额度后仍失败。它还错误断言工具不支持 `opening_frame`，说明文档、恢复上下文和真实工具协议没有一致交接。

v13–v16 是补丁批次，不是 Git commit，分别暴露了：

1. 首个字段错误遮住其他错误，真实字段修正被 watchdog 误判为无进展；
2. 源输入冲突被误当图片问题，进入重画队列；
3. 明细要求修 input，总指令却仍要求 first_frames；
4. 可修改字段、精确冲突和原始证据在恢复上下文中丢失。

这些批次修复了对应协议和路由问题，但没有证明完整整集成功。图像层也确有构图、场景和物件复制偏差，不能全部归因于软件。

### 2. Tasks 1–7：通用简化已完成一大批，但仍只是局部能力

已完成身份 judge-only、opening/参考统一编译、宿主编译 H3、最小异常授权、恢复与路由冻结、后期共享编排，以及 accepted/上传绑定当前导出、图片哈希和重拍反馈等修正。

全仓验证的配套结果回执记录 `pytest exit 0`，保留日志进度到 100%。准确说法是“结果回执记录退出零，日志到 100%”，不是日志文本本身保存了操作系统退出码。测试通过不等于真实整集通过。

### 3. A2：分层小样有真实进展，但不是完整小链路或整集

- 首次 opening 准备失败：`reference_subjects` 未注册，未生图。
- 后续正式 input check/apply 成功，`apply.ok=true`，修复 `clip_07、clip_12`，opening blockers 清空。
- 代表首帧前两个候选被图审拒绝，第三个候选通过并采用；不能省略前两次真实失败。
- 代表视频 `clip_01` 为约 12.282 秒、768×1344、H.264/AAC，完整解码成功，ASR CER 0，语音技术检查通过。

这些属于 **A/B/C 分层恢复小样**。D 阶段完整合成、正式视觉/声音终审和上传没有因此得到验证；A2 自己也明确记录“不是从头无人干预整集”。

### 4. A3/A4：恢复协议和 Agent 推进仍不稳定

- A3：`repair.json` 缺少 `decisions`，`opening_frame` 不完整，推动修正 current-task 协议。
- A4：仍出现未注册引用、错误删除身份/衣装引用，以及源修复后 Agent 提前 `finish`。
- 这些运行证明协议修复有必要，也证明仅修协议不足以让宿主完成整集。

### 5. A5：创作、审查、字段同步和生产执行四类问题叠加

A5 作者候选结构验收通过，但生产源审报告 clip_03–08、19 的场景和状态问题。生产 Agent 进行过多轮修订，最后自动源审转绿，却仍缺首帧并提前结束。

因此不能把 A5 归因为单一“模型不行”“审查太严”或“生产 Agent 不会调用工具”。

## 三、创作矛盾真的是问题吗

**多数有真实文本依据，但不是全部审查结论都正确。**

本次直接对照了 A5 作者原稿、原包结构化剧本和生产最终稿。七项中，六项能找到实际字段冲突；`clip_04` 原审查所称的起始状态冲突属于误报，但该镜另有真实的摄像机场景错贴。`clip_07、clip_19` 的审查描述还扩大或错置了问题范围。

| 镜头 | 原稿是否有真实问题 | 核对结果与当前边界 |
|---|---|---|
| clip_03 | **有** | 本镜在打捞台，`camera` 却写登记棚、登记簿和三张照片，把下一场动作提前。生产稿已改回打捞台，但 clip_03/04 又都安排红牌亮起和灯离开，产生跨镜重复。 |
| clip_04 | **有；但起始状态指控是误报** | 原稿 `start_state` 与 `opening_frame` 都写红牌“已经亮起”，并不存在一处已亮、一处未亮；`event` 再写亮起是不够严谨，但不是两个起始状态冲突。另一条真实问题是 `camera` 已写到认领区三盏灯和新灯退开，当前却仍是打捞台。生产稿改成开场未亮后，`visual_prompt` 仍保留“已经亮起”，又留下派生字段冲突。撤销误报不等于整镜原稿正确。 |
| clip_05 | **有** | 登记棚场景错用旧灯塔门、木料和工具箱的摄像机说明；生产稿已修回登记簿和照片。 |
| clip_06 | **有** | 登记棚回忆铜片时，`camera` 提前写旧灯靠岸、小门打开和认灯；生产稿已修回登记棚。 |
| clip_07 | **有，但审查范围过宽** | `camera` 确实提前写工作台装支架和点灯；`event` 原本是三灯辨认，并非审查所称也已改成工作台。生产稿已修正场景跳跃。 |
| clip_08 | **有** | 当前是认领区规则牌，`camera` 却提前写离岸送行和归还打捞杆；生产稿已修回规则牌。 |
| clip_19 | **有，且不止 storyboard** | 开场只有 A/B、镜内加入 C 是合理的；但 `opening_frame.image_prompt` 的场景描述先写 A/B/C 三卡并排，随后静态构图又写 C 尚未加入，同一首帧提示内部已经冲突。`storyboard.frame` 也在“时间零点”写 A/B/C 已并排。生产稿已修 `start_state/event/opening_frame`，但 storyboard 仍保留三卡零点并排，尚未闭环。 |

所以准确结论是：

1. **作者原稿确有制作级冲突**，主要是把后续场景的摄像机说明贴到当前镜头，不是合理视觉补充。
2. **审查也会误报和漏报**。不能把所有 findings 当事实，也不能因 `issues=[]` 就认定全部修好；撤销一条错误指控不等于撤销同镜其他真实问题。
3. **局部修复没有同步所有字段**。源字段、storyboard、opening 和派生提示词之间仍会漂移。
4. 即使文本全部修好，A5 还独立存在首帧引用错误和宿主不自动续跑的问题。

## 四、新的收缩计划

采用与小说生产相同的原则：**宿主工作流驱动，Agent 负责创作和需要内容判断的异常；正常生产不依赖 Agent 推阶段。**不照搬小说代码，保留视频参考、付费媒体、ASR、音画终审和上传门。

最优先的两个独立目标是：先交付一份真实一致的制作稿，再证明宿主能自动执行它。两项分别验证后，才新建冻结 W10。

### 阶段 1：先把历史问题变成回归，不再烧整集

只读使用 A3–A5 证据，至少固定：

- clip_03/04 的真实场景错贴必须拒绝；
- clip_04 原稿不得被误报为两个起始状态；
- clip_19 要区分“镜内加入 C”和首帧提示、storyboard 中的“零点已有 C”；
- 修改起始状态后，所有派生字段必须重新编译，不得保留旧 `visual_prompt`；
- 源审通过且缺首帧时，宿主自动执行普通首帧；
- 未注册引用在付费生图前返回结构化错误，并区分作者引用错误与注册、编译遗漏。

旧任务不恢复、不改稿、不清计数。

### 阶段 2：先让创作交付真实一致

复用并校准现有独立审核，不新增第二套审查系统，也不能只把现有源审机械前移：

```text
作者写稿 → 结构检查 → 独立语义/一致性审核
                       ├─ 确认问题：交回同一原作者修订
                       └─ 通过：才进入生产
```

同时满足四条：

- findings 必须指出具体字段、原句、时间位置和冲突关系；允许复核误报，不强迫作者修不存在的问题。
- 明确起始状态、镜内事件和结束状态的时间语义，不能把正常的镜内变化误报成开场冲突。
- 每份内容指纹只采用一次有效独立审核；opening 确定性检查和实际媒体验收仍保留。
- 必须用真实作者和真实审核调用验证准确性；模拟审核结果的单元测试只能证明接线，不能证明审核判对。

创作者使用原会话和原额度，宿主不自动改剧情。

### 阶段 3：明确作者内容与编译结果，修复后全部重编译

- 作者负责场景、起始状态、动作、运镜和 storyboard 等内容；宿主负责把通过验收的内容编译成实际请求。
- 一个事实只由一个源字段拥有；opening、visual/motion prompt 等派生结果由宿主编译。
- input apply 后使相关派生缓存失效并全部重编译，不能只更新 `start_state/opening_frame` 而保留旧提示词。
- `start_state/opening_frame/storyboard.frame` 的零点一致性在付费生成前检查；作者 storyboard 本身矛盾时交回作者，宿主不得通过重编译擅自选择一种说法。
- 参考清单、注册名和哈希在实际请求前校验，错误定位到具体 clip 和字段；先归因，再决定由宿主修注册/编译还是由 Agent 修作者引用。

### 阶段 4：宿主独立推进正常生产，只有内容异常才调用 Agent

```text
宿主直接执行 first_frames → render → review → 合成 → finish
                     ↓ next_action=agent 且确需内容判断
              临时调用 Agent 修当前异常
                     ↓ 修复落盘、Agent 退出
                  宿主重新计算并继续
```

- `next_action()` 是唯一阶段路由；`finish` 不再另猜下一步，但仍执行全部正式验收，路由到 finish 不等于成功。
- 普通首帧、渲染、审核、合成和正式结束检查由宿主执行。
- 只有需要内容判断的异常才调用 Agent；网络重试由宿主处理，额度耗尽明确停止。
- 异常 Agent 只接收当前问题、证据、可改字段和余额，不接收整集生产计划。
- Agent 不再靠 `finish` 推进或结束异常修复；只有宿主正式验收才能完成。
- 不新增队列、生产模式、恢复类型或状态文件；真实异常无进展和额度耗尽仍停止。

### 阶段 5：定向回归、一次全仓回归、分层真实小验证

真实小验证按顺序进行：

1. 作者语义验收和误报复核；
2. 正式 input check/apply 后全部派生字段一致；
3. 源审通过后宿主自动生成代表首帧，核对真实 prompt、附件和图审；
4. 代表视频解码、ASR 和技术门；
5. 正式合成、视觉/声音终审和私有上传出口分别验证。

前一阶段失败不进入下一阶段，不烧整集。小样通过仍不冒充整集成功。

### 阶段 6：新建冻结 W10，完整从头验证

以上通过后，从原始包和原始参考图新建独立 edition，冻结代码、模型、输入、标准额度和私有发布目标，只经单一 `produce-batch` 等待最终结果。

成功必须同时满足：

- 有效视频镜头 N/N；
- 正式视觉和声音终审 accepted；
- 完整正式成片 1 个；
- 可核实上传 1 个；
- 运行中没有软件修复、人工接续、热换模型或代码。

若暴露新通用问题，保存证据并停止；修复后的续跑只算恢复验证。不得通过重建同一失败任务、追加额度或放宽验收制造成功。

## 五、主要证据

以下路径均相对于工作区 `/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-v4-20261023`：

- A5 批次状态：`.runtime/deterministic-simplification-validation/a5-frozen-run/state.json`
- A5 作者原稿：`.runtime/deterministic-simplification-validation/a5-frozen-run/zhiyao-auto-a5-EP04C_W10_失物海/creator/sandbox/output/episode.json`
- A5 原包结构化剧本：同 creator 目录下 `sandbox/input/package/pkg/episode/02-结构化剧本.json`
- A5 生产回执：`.runtime/deterministic-simplification-validation/a4-frozen-code/outputs/episode-agent-firstframes/zhiyao-auto-a5-EP04C_W10_失物海/zhiyao-auto-a5-EP04C_W10_失物海_1-script/agent-run.json`
- A5 最终生产稿：同生产目录下 `zhiyao-auto-a5-EP04C_W10_失物海/zhiyao-auto-a5-EP04C_W10_失物海_1/chapter_script.json`
- A5 最后自动源审：同生产目录下 `local-source-check.json`
- A5 最新首帧错误：同生产目录下 `sandbox/input/latest-first-frames.json`
- A2 input：`.runtime/deterministic-simplification-validation/a2-input-agent-result.json`
- A2 首帧：`.runtime/deterministic-simplification-validation/a2-first-frame-agent-result.json`
- A2 单镜：`.runtime/deterministic-simplification-validation/a2-one-video-result.json`
- 全仓结果回执：`.runtime/deterministic-simplification-validation/validation-result.json`
- 旧 v2 交接：`/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-20261023/.runtime/autonomous-clean-v2-20261024/HANDOFF.md`

本文档只是进度和方案记录，不表示上述新计划已经实施或通过生产验证。


## 本会话接管后的实际进展

- Tasks 1–7 及已证实的组合问题已三方整合到 main；主线原有资源优先顺序和独立审核端点保留，未提交、未推送。
- 一次全仓验证只暴露循环依赖与跨测试导入；已修正并通过失败项及相关回归。主线整合遗漏的 transport 字段已由实际回归抓出并补齐。
- 正式 produce 入口的服务初始化已前移到创作/审核前；清空旧环境的真实配置回归证明源审不会误用旧18120默认端点。
- 真实创作审核发现并修复：严格JSON schema不合法、首个坏引文遮住其他错误、证据纠正可清空问题绕过、整篇原文重复塞入纠正上下文、初审/复核时间语义不同、汇总保留编号与理由错配。均保留原始请求和响应，未改旧稿或旧审核。
- Luna 5.6/6 的真实审核存在漏报/证据不稳，K3 在原请求预算内返回正文前截断；独立源审正在用已登记的 Sol 6 验证。作者配置仍保留 Luna。
- 历史基准已能正确拦03–08场景错贴和19零点冲突、撤04假起始冲突；独立复核又发现动作顺序、跳切和名词/位置推断误报，已补通用规则，当前等待最后一轮真实判断。不能把执行成功或 issues=[] 当作准确性通过。
- 新作者、新媒体小链、新冻结W10仍未启动；A5仍0/20有效视频，0 accepted成片，0上传。所有旧任务、额度、回执与媒体保留。

最新执行台账及实证：`.runtime/w10-closure-20261026/CONTINUE.md`、`validation-result.json`；主线三方集成记录在其`takeover-1433/integration/`，真实审核每轮单独目录。

## 2026-10-26 19:32 真实小媒体链续跑

此项是独立原创小样的恢复验证，不是W10从头无人成功。真实身份审核已通过：宿主把作者既有角色/道具分类传给同一独立judge，保留拒绝门，没有把普通道具补为演员。三个首帧均current；clip01/03各一份H3原始视频、回执及ASR检查通过。clip02在POST前因作者手部特写与闭嘴表情要求冲突被请求审查拒绝，视频次数未消耗。生产将确定内容拒绝错分为media_execution_failed而停止；该通用分流正在定点修复。剩余4485.960秒、Agent两次；无新授权、无计次修改、无人工改稿。小样成片/accepted/上传均0，W10 B1仍未启动。

## 2026-10-26T21:51:46 本会话最终核实

独立小样恢复验证：3/3首帧current，3/3视频基础与镜头视觉通过，完整合成候选18.64秒；正式整片视觉通过，完整声音审核已完成但拒绝。最新问题为clip_01 2–4秒计划外金属撞击、clip_02 8–10秒开锁/关门声与未开门意图不符。每镜3次、共9次标准视频次数全部用满；accepted成片0、私有上传0。剩余时间3232.326秒不补视频额度。原始/全部修复takes和回执保留。

通用身份分类、请求内容拒绝分流、审核输出下限与同模型恢复、review列表交接、精简声音反馈及权限边界，均已本地集成MAIN并冷切F；正常Agent已实际完成源修订和两轮声音请求修复。真实输入审计确认音效要求实际传H3、宿主未删声、正确波形进入声音模型；不据此冒称声音判定准确或某模型必然无能力。

当前无法在原视频额度内完成小样验收；新冻结W10 B1尚未启动，未取得从头无人成功。没有手改本集内容/审核/计数、没有追加次数或放宽门、没有提交推送。详细回执索引：.runtime/w10-closure-20261026/validation-result.json、CONTINUE.md。


2026-10-27 B7/B8实证更新：B7源审通过、35组，10张首帧保存但0视频，发现已登记prop归属未进入图审且Kimi alias视觉漏判双包；新增派发暂停后全部在途收齐、退出2，原历史不手改。通用修复投影本镜prop事实到生成/审图同一契约，v7明确无源新增剧情道具实例；正常HTTP重试reset真实16k→16k→8k。原生Sol6图审两张B7坏图均识别双包；v6新单帧仍发生双包并被underspecified放过，保留该假阳性，不宣称成功。v7真实对照：原B5单包正例passed、新B7双包和手持包内手机反例rejected。相关主线回归及独立审查通过，代码已冻结；新B8从原包首次produce-batch无resume实际启动，原writer/预算/四并发/私有发布目标，显式SECOND_REVIEW_JUDGE=codex-sol-6，实际root已核。整集从头无人结果尚未完成，当前权威现场记录：/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-v4-20261023/.runtime/w10-closure-20261026/CONTINUE.md。

2026-10-27 B8/B9：B8原作者267/5400秒完成结构，源审自由路径漏掉chapter_script层级而终止（reason字段存在）；0媒体，保留失败。v15改为模型选择完整实际文本叶目录整数编号，宿主确定映射原路径后保留严格阶段、时间、原稿与独立确认。两份完整新native源审B8/B3均exit0且无需协议纠错，分别4→2、6→6；独立重放实际映射一致。另核实shot.audio_plan.music_cue为描述意图而非timeline cue ID，旧24项名字比较不足以判错，已补真实混音语义；最终B8完整复核也exit0，保留2项真实t0冲突。代码主线集成、相关回归/独立复核通过，B9已冻结并首次正常produce-batch从原包启动，guard及实际root已核，原额度、四并发、显式Sol6审核。仍需整集媒体/正式accepted/远端上传证明，当前原包无人成功数仍0。现场真相见.runtime/w10-closure-20261026/CONTINUE.md和fresh-b9/run。

2026-10-27 B9/B10：B9源审passed(438/5400s)，27首帧中19/23通过，01正常重拍candidate02也通过；后H3 native json_object未传schema、实际坏JSON多]被read_json吞成None触发Assertion，首帧未采用，0视频且退出2，历史/原额度保留不恢复。修复native schema传递、坏JSON共用原最多3轮/总deadline并窄typed、H3原异常与candidate保留、冷复用retake child02与图审；H3 items:false真实被native400拒绝，换等价通用string item(保留prefix/min=max及后验marker/count/English)后真实9s正常。Main185相关回归+交叉审查通过；新监督合成组合3例验证自动--resume到H3而不制造accepted。真实隔离正常EpisodeTools复用01_02、0新图/0重审、1实际翻译+1请求检查passed；随后1实际H3请求生成10.157s视频，技术/语音pass、CER0、旧B9未变(仅资格，非完整无人成功)。B10已用冻结修复代码从原ZIP+原双图首次normalproduce-batch启动，actualroot已核，原writer/预算/4workers/private publisher；须继续完整成片终审及上传，当前原包完整freshaccepted仍0。现场权威记录.runtime/w10-closure-20261026/CONTINUE.md与fresh-b10/run。

2026-10-27 B10/B11：B10创作185秒/validate0issues、源审17项均撤销，仍在交接pack触发TypeError(0media)。真实stack camera dict进入ClipCompiler.prompt_text regex；30镜camera均dict、scene_objects均str，validator str(value)非空漏形状。新通用前置类型检查精确返回60条，保留已有purpose/sfx/extras string/list、props/sceneobjects null/[]、空镜无人语义；Skill明确camera完整string，不禁PythonJSON。原B8/B9实际稿仍validate0issues，独立完整material→prepare→selectmode→pack30/27clips成功，三份旧稿字节不变，0API。Main相关回归/crossreview通过；冻结B11首次normalproduce-batch原包启动，actualroot核实，原预算与4workers，0视频/accepted/upload。全部旧任务未手工救，当前完整原包fresh无人成功数仍0。现场truth见.runtime/w10-closure-20261026/CONTINUE.md、fresh-b11/run。


2026-10-27T12:13:44 B13 本次收口记录

B13 从原包、原双参考和冻结代码开始，经历两次正常源审协议冷恢复，原作者修订、素材生成、宿主首轮28张和两轮生产Agent修复均自动推进。最终有效首帧6/28、图片53张，视频、完整accepted成片和上传均为0。第三轮Agent把要重拍的已有父候选candidate_02误写成尚不存在的candidate_03；宿主reserve阶段未输出结构化参数反馈而终止。未消耗第三候选，剩余时间2336秒；不能称为图片额度耗尽，也未从头无人成功。旧任务保留且未恢复，未开B14。

通用修复已在主线：有字段证据的源审时间必须使用已有合法枚举，固定时间仍由宿主覆盖；候选选择格式、互斥、存在性前移至整批参数反馈。相关回归与独立审查通过，v17在真实保留错误稿的完整native源审中无协议纠错、7项候选经内容确认保留4项；B13最后22行原请求隔离回放返回22个精确参数错误，图片分配/API调用/旧数据改动均0。这些是通用修复的资格，不是新的整集生产成功。

拒绝原因经先盲看实际图片、再解盲原QA核实：抽查01/04/07均有真实像素缺陷（提前打捞、双包、红光代红牌及造型漂移）；部分QA位置/接触时点/裤色理由过强，也漏报发型/配饰。07的包不背回连续性最早在作者本镜省略，各编译层逐字保留现有字段，没有删除已写状态。整镜camera进入静帧prompt是歧义风险，尚不能证明其导致错误；没有证据证明服务端忽略六参考。诊断未额外生图、重审或改生产判定。证据统一见 .runtime/w10-closure-20261026/validation-result.json、b13-selector-argument-qualification/result.json 和 b13-prompt-vs-review/三份JSON。代码未提交或推送，新整集生产尚未验证。


2026-10-27T13:17:31 首帧连续性和审核对照结果

本轮依用户要求，对 B13 留存镜头做真实源审和同图审核对照，没有恢复旧任务、追加生图次数或启动新集。实验性的源审连续性遗漏反馈和图审独立确认均已实现并通过相关回归，但真实小范围对照未全部通过：完整28镜源审漏掉07的“不背回肩上”，并在24声称肩带可见而该事实不在引用的源字段中；同一实际图审 Sol 保留双包和红牌，但仍将无法排除光照影响的裤色判为确定冲突，Luna 对裤色不确定却错误撤销双包，不能靠切模型或挑结果制造通过。对照期间原视频/图片/历史未改，新增图片/视频/上传0。

因此 B13 仍是6/28有效首帧、53图、0视频/accepted/upload；新的一集无人验证未启动。主线已仅撤出这次未通过实际验证的源码补丁，保留上一轮已验证的v17源审、v7图审和selector参数反馈修复；实验补丁和证据保存在 .runtime/continuity-source-review-20261027、.runtime/first-frame-review-confirmation-20261027、.runtime/first-frame-colour-confirm-20261027 及 .runtime/w10-closure-20261026/validation-result.json。未提交或推送。

2026-10-27T13:21:51 追加模型可用性实证：当前交互代理对真实相邻镜的盲判能找出07穿戴状态遗漏；但同一证据通过生产正常Codex原生接口调用 gpt-6.1-terra 时返回 HTTP 400 model_not_found（该登录账号不支持该模型）。这是交互代理能力与生产模型可用性的区别，不作为生产验证通过。Sol6/Luna6在实测源审对照均未稳定找对，图审颜色/真双包仍有分歧，故小范围条件未过，未启动新整集，也未修改旧任务或新增媒体次数。


## 2026-10-27 外部审查后续收口

已按当前主线核对外部九项审查：冷恢复唤回 Agent 的第4项已有后续修复；其余确定性缺口已补齐并精确整合 main。主线相关回归通过，真实保留初审12项经过实际纠错后仍为12项，再由独立确认撤1项；旧第4镜真实新接口仍检出重复背包，未确认景别没有变成重拍。

冻结旧码任务 zhiyao-w10-fresh-20261027 已保留创作/源素材结果，在既有生产暂停门正常exit2，批次只启动1次，0视频／accepted／上传。没有热换代码、手改旧稿或追加额度，也没有在这次审查后再启动整集。H3新异常闭环目前是离线正常入口验证，从头无人成功仍未证明。

逐项处理与证据见维护主线 `/mnt/disk1/zengzhitao/interactive-video-worktrees/main/docs/w10-external-review-closure-20261027.md`；开发证据与补丁在 `/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-review-closure-20261027/.runtime/review-closure-20261027/`。未提交、未推送。
