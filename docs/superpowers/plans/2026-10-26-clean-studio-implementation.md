# Clean Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建一个独立最小项目，从 W10 原包完成创作、媒体生产、正式音画验收和私有上传，而不继承旧生产状态机。

**Architecture:** 一个 SQLite 事务状态库、一份作者镜头事实源、一份不可变参考清单和一个固定阶段宿主。创作者/审核/修复 Agent 只处理内容；冻结适配器只负责外部执行，提交、计次、恢复和验收归新宿主。

**Tech Stack:** Python 3.11+、标准库 dataclasses/sqlite3/json/hashlib、httpx、Pillow、pytest、FFmpeg、既有 Codex/H3/ASR/Eureka/ModelScope 服务。

**Spec:** `docs/superpowers/specs/2026-10-26-minimal-production-design.md`

## Global Constraints

- 项目根目录固定为 `/mnt/disk1/zengzhitao/interactive-video-studio-clean`；旧项目全部只读，不修改 `main` 或 `autonomous-production-v4-20261023`。
- 第一版只做 W10 + H3 首帧模式，1080×1920、25 fps、H.264/AAC、JPEG 封面和 3 秒片尾；不做 SD、无首帧、多集并行或旧 edition 兼容。
- 不继承旧稿件、图片、会话、审核、task ID 或计数。历史文件只作只读回归证据。
- 不手改剧本、审核或计数；创作问题由原作者/异常 Agent 通过正式接口修复，宿主不得选择矛盾剧情。
- 标准额度：每镜 3 图、3 takes、2 input revisions；单集 3 次启动；创作单次 1200 秒、累计 5400 秒。
- 网络重试不调用内容 Agent；提交不确定先对账，不盲重发；凭据/配额/未知异常停止。
- 不把 candidate、exit 0、ASR、单镜或小样当成整集 accepted；正式视觉、完整声音和远端核实不可互相替代。
- 冻结适配器代码统一放在 `src/clean_studio/frozen/`，提示词放在 `prompts/`，并记录来源路径、SHA-256 和日期；运行时只能导入本项目模块，不得导入旧 worktree，不复制 `.env`、登录目录或密钥。
- 使用 `/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python` 作为只读兼容解释器运行测试，不向其安装包；命令均设置 `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD"`。
- 每个任务先 RED 后 GREEN，由另一名代理复核；不提交、不推送。真实模型/媒体/上传只能按 Task 9 顺序执行。
- 新增运行目录必须包含 `owner`；媒体和模型不进入 Git。

## Review Focus

- 原包是 `episodes[0].scenes[].flow[]`，60 句对白和固定片尾不能丢；Task 1 用真实 W10 只读回归逐句核对。
- 作者只允许源字段，旧 `opening_frame/visual_prompt/motion_prompt/storyboard` 不得混入；Task 1 结构拒绝、Task 4 验证只由宿主派生。
- 已受理但下载失败与提交不确定必须区别；Task 2 和 Task 5 验证同 task 恢复、第四次付费前停止。
- 正常生产不能因缺首帧而唤醒生产 Agent；Task 8 用真实 SQLite + fake provider 验证自动阶段推进。
- 当前 accepted 与实际上传是不同结果，已有 repo 为 public 时必须零上传；Task 7 覆盖，Task 9 用私有远端回执确认。

---

### Task 1: 项目骨架、原包解析、domain 与不可变参考清单

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `AGENTS.md`
- Create: `src/clean_studio/__init__.py`
- Create: `src/clean_studio/domain.py`
- Create: `src/clean_studio/source.py`
- Create: `configs/w10.json`
- Create: `tests/test_source_contract.py`

**Interfaces:**
- Consumes: 原始 W10 ZIP、系列 material、冻结 travel-live style。
- Produces: `load_source(zip_path: Path) -> SourcePackage`；`build_reference_manifest(source, material_dir, style_dir) -> ReferenceManifest`；`validate_episode(episode, source, references) -> None`。

- [ ] **Step 1: 写原包和作者合同的失败测试**

`tests/test_source_contract.py` 直接只读原 ZIP，断言 9 个 scenes、85 个 flow 项、60 句对白；逐句保留 `C01/C046` 和文字，片尾问题与 A/B/C 目标完全符合 `fixedOptions`。结构测试同时拒绝：

```python
bad = replace(shot, visual_prompt='旧派生提示')
with pytest.raises(ValueError, match='author-owned fields'):
    validate_episode(episode, source, references)
```

再覆盖：改一句对白、把 C046 改成 C01、跨场景 source segment、空泛 `end_state='对白完成，准备下一镜'`、非整数或超出 4–15 秒的时长均拒绝；总正文加 3 秒片尾必须落在 180–300 秒。

- [ ] **Step 2: 运行测试，确认骨架尚不存在**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_source_contract.py
```

Expected: 导入 `clean_studio.domain/source` 失败；不得用跳过原包测试代替。

- [ ] **Step 3: 实现最小骨架和解析**

`pyproject.toml` 只声明 `httpx>=0.27,<1`、`Pillow>=10,<13` 和 dev `pytest>=8,<10`。使用 dataclasses，不引入 ORM、pydantic 或框架。`domain.py` 定义 `Shot/EpisodeSpec/SourcePackage/ReferenceManifest/Review/Operation`；当前可变状态留给 Task 2 SQLite。

`source.py` 读取 `episodes[0]`，用 `sceneId/flow/{index}` 形成稳定 segment ID；动作项不补 speaker。`configs/w10.json` 只登记输入级事实：`C01=知遥、C046=潮叔`、两张角色图的 identity/wardrobe 用途和 travel-live style ID，不包含任何逐镜补丁。场景名从 `01-完整剧本.md` 对齐，不用角色顺序猜说话人。

参考清单只包含真实存在文件：知遥身份、综合衣装卡、冻结 travel-live 的两个 style examples。系列 `material.json` 只补充文字 profile/bible；其不存在的规划目标 `.jpeg`、`style.json`、地点或道具资产不得登记为已存在附件。所有复制项记录来源路径、SHA-256 和日期，输入与媒体目录写入 `.gitignore`。

- [ ] **Step 4: 运行测试并验证不可变性**

Run Step 2 命令。Expected: 全部 PASS；重复构建清单字节一致，改变源图片字节会改变 manifest，未绑定主体与无图片附件分开表示。

- [ ] **Step 5: 独立复核**

检查 domain 没有派生 opening/visual/motion/storyboard 字段，原包逐句与片尾证据完整；复核者运行同一测试并审查来源哈希，不提交。

### Task 2: SQLite 事务状态、幂等、计次与回执

**Files:**
- Create: `src/clean_studio/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: Task 1 的 domain 值合同和不可变 artifacts。
- Produces: `Store.create_episode`、`load`、`advance`、`begin_operation`、`mark_submitted`、`complete_operation`、`fail_operation`、`usage`、`find_valid_review`。

- [ ] **Step 1: 写幂等和额度失败测试**

覆盖：

```python
op = store.begin_operation('img-1', 'image', 'input-hash', 'shot-1')
assert store.begin_operation('img-1', 'image', 'input-hash', 'shot-1').id == op.id
with pytest.raises(ValueError, match='idempotency'):
    store.begin_operation('img-1', 'image', 'different-hash', 'shot-1')
```

3 个 pending/confirmed 图片操作后第四次付费前停止；查询、轮询和下载不增加计数。`submission_uncertain` 占 pending slot，明确未受理才释放。input apply 以补丁幂等键计一次，最多 2 次。review 服务错误不能写入有效 verdict，输入 hash 变化后旧 review 不可命中。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_store.py
```

Expected: `clean_studio.store` 不存在。

- [ ] **Step 3: 实现四表 SQLite**

只建 `episodes / shots / operations / artifacts` 四表。`episodes` 保存冻结配置、stage、当前 script/export、blocked、launches 和 recovery 标记；`shots` 保存当前 source/compile hash 和当前 frame/video 指针；`operations` 保存幂等键、类型、输入 hash、外部 task ID、状态、有效 verdict 和回执；`artifacts` 保存相对路径、SHA-256、输入 hash 和元数据。

计数只从 operations 推导，不另存可清零 counters。提交后、下载前崩溃时，`mark_submitted` 已持久化外部 task ID，恢复返回同 Operation。artifact、回执和当前指针在同一事务更新；`status` 用只读连接，不推进阶段或增加 launches。

- [ ] **Step 4: 重跑并做崩溃恢复测试**

Run Step 2 命令。Expected: PASS；同 key 同输入不重复，pending 也占额度，accepted/candidate/complete/blocked 由当前产物与回执投影，不互相覆盖。

- [ ] **Step 5: 独立复核**

检查没有队列表、事件溯源、通用恢复框架或第二状态文件；重点模拟提交后崩溃与混合 pending/confirmed 计数，不提交。

### Task 3: 原作者、独立源审和证据闭环

**Files:**
- Create: `src/clean_studio/agents/__init__.py`
- Create: `src/clean_studio/agents/codex.py`
- Create: `src/clean_studio/agents/authoring.py`
- Create: `src/clean_studio/agents/review.py`
- Create: `prompts/author.md`
- Create: `prompts/source-review.md`
- Create: `tests/test_authoring_review.py`

**Interfaces:**
- Consumes: Task 1 source/domain 和冻结 Codex 适配器。
- Produces: `Author.start() -> EpisodeSpec`；`Author.revise(findings) -> EpisodeSpec`；`Reviewer.review(episode, sources) -> Review`；`ground_finding(finding, episode, sources) -> None`。

- [ ] **Step 1: 写作者额度、证据和误报回归**

fake clock 下验证单次 1200 秒、累计 5400 秒，修订使用同一 session，不能新建作者绕过。结构不合格不调用语义审核。服务异常既不是 verdict 也不触发作者改稿。

将 A5 形状转为 Clean 字段：场景错贴应定位 `camera`；两个 t=0 字段均“已经亮起”不得报互相矛盾，但 camera 问题保留；start 只有 A/B、action 加入 C 合法；相邻镜重复红灯/离开必须给双方字段证据。finding 必须有 `evidence_origin=original_text|authored_field`、字段路径、逐字原句和时间；原文问题另需 source segment。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_authoring_review.py
```

Expected: authoring/review 接口不存在。

- [ ] **Step 3: 实现有限 Agent 协议**

冻结复用 `llm completion/ask_json/image_part` 所需最小闭包，不复制 episode Skill 或生产调度。Author 的输入同时包含原包文本和实际身份、衣装、风格图片附件，不能只传路径；返回完整结构化 EpisodeSpec，写回后再由 Task 1 合同校验。长 JSON 截断是协议失败，不是内容候选。Author.revise 只接收确认 findings 和允许修订范围。

Reviewer 先做结构检查，再调用独立模型，二次确认只撤销具体误报。`ground_finding` 校验字段路径和逐字原句，`original_text` 强制原文证据，`authored_field` 必须显式 relation；最终只消费 confirmed contradiction。内容/来源/政策指纹用于复用昂贵审核，非法或服务失败结果不得缓存。提示词不包含 opening/visual/motion/storyboard 作者任务。

- [ ] **Step 4: 重跑并检查协议**

Run Step 2 命令。Expected: PASS；二次确认撤销一项不影响同镜另一项，同指纹不重复调用，内容变化才重审。

- [ ] **Step 5: 独立复核**

检查 mock 仅证明接线，不把 fake reviewer 当语义准确率；真实 A5 历史案例只读，不修改旧稿，不提交。

### Task 4: 纯编译、参考展开和 typed check/apply

**Files:**
- Create: `src/clean_studio/compiler.py`
- Create: `src/clean_studio/agents/repair.py`
- Create: `prompts/h3-translation.md`
- Create: `tests/test_compiler.py`
- Create: `tests/test_repair.py`

**Interfaces:**
- Consumes: 已验收 EpisodeSpec、ReferenceManifest、冻结 H3 翻译适配器。
- Produces: `compile_episode(...) -> Compilation`；`compile_shot(...) -> CompiledShot`；`check_patch(episode, patch, grant) -> PatchPlan`；`apply_patch(episode, plan) -> tuple[EpisodeSpec, set[str]]`；`video_request(compiled, accepted_frame) -> VideoRequest`。

- [ ] **Step 1: 写派生边界和参考错误回归**

start 只有 A/B、action 加入 C 时，opening 不含 C，H3 必须包含加入过程。修改 start_state 后 opening/H3 都更新，无关镜头的产物、task ID 和计数不变。视频请求还绑定 accepted frame hash，禁止旧帧配新 H3。

两个镜头同时含未注册引用时聚合全部错误且图片调用为 0；区分作者引用错误与注册/缺文件/哈希错误。附件严格按身份→衣装→地点→风格，生成、图审、视频和缓存共用同一展开结果。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_compiler.py tests/test_repair.py
```

Expected: compiler/repair 接口不存在。

- [ ] **Step 3: 实现宿主结构编译和有限模型翻译**

opening 只读取 location/start_state/camera/shot_scale/visible_characters/references；motion/H3 才读取 action/end_state/dialogue/duration。宿主拥有结构和引文绑定；H3 英文只通过一次 typed 翻译调用生成，逐字保留中文对白、source segment 和动作，不允许模型新增事件或修改镜头。翻译缓存绑定源镜头和编译器版本。

`check_patch` 只接受 grant 中的 shot IDs 和字段，必要相邻镜只读；`apply_patch` 返回新 EpisodeSpec 和受影响 shot IDs，不表示审核通过。只有受影响输出重编译。所有编译错误在付费前聚合，未注册名字不做 basename/别名猜测。

- [ ] **Step 4: 重跑编译与修复测试**

Run Step 2 命令。Expected: PASS；越镜号/字段补丁拒绝，`apply ok` 后仍需重审，源修改不会保留旧派生提示。

- [ ] **Step 5: 独立复核**

检查没有重新引入 storyboard 或第二份作者状态，真实请求与参考附件顺序一致，不提交。

### Task 5: 冻结图片/H3、图审、视频和单镜质量门

**Files:**
- Create: `src/clean_studio/providers/__init__.py`
- Create: `src/clean_studio/providers/image.py`
- Create: `src/clean_studio/providers/h3.py`
- Create: `src/clean_studio/providers/asr.py`
- Create: `src/clean_studio/media.py`
- Create: `provenance/adapters.json`
- Create: `tests/test_media_execution.py`

**Interfaces:**
- Consumes: Task 4 请求、Task 2 Operations、冻结 image/H3/ASR 适配器。
- Produces: `ensure_first_frame(shot_id) -> Artifact`；`ensure_video(shot_id) -> Artifact`；provider `submit/query/download`；`review_frame`、`probe_video`、`transcribe` 回执。

- [ ] **Step 1: 写付费、恢复和质量门回归**

图审与生成收到完全相同的附件顺序；H3 首帧同时是第一个普通参考和 `keyframe frame_index=0`，短边 768。部分首帧完成后 503，恢复只处理缺失/失效镜头。两个候选拒绝、第三个通过后图片计数为 3，之后不得再生成；视频 takes 同理。

提交不确定不重新 POST、不调用内容 Agent；轮询失败保留 task ID；结果确已清除后的重新提交是新 Operation 并占额度。每个有效视频独立完整解码、ASR 和技术检查，ASR 不代替最终声音。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_media_execution.py
```

Expected: providers/media 接口不存在。

- [ ] **Step 3: 冻结并收窄适配器**

复制必要适配器及来源哈希到 `provenance/adapters.json`，不得整文件照搬旧恢复行为。图片复用 PhanRouter 有序附件、task 查询和下载；H3 复用 payload/query/download，但固定短边 768、时长 4–15 整数秒，禁止静默 ceil 改时长。

旧 `local_h3._submit` 的通用 retry、池切换丢弃旧 task、图片 purged 后自行重新提交不得带入。所有提交、外部 task ID、重新提交授权和计次由 Clean Store/Operations 决定；`SubmissionUncertain` 只用于对账。ASR 继续使用独立 SenseVoice Python 和模型目录，不向主解释器安装依赖。

- [ ] **Step 4: 重跑执行与协议测试**

Run Step 2 命令，并运行冻结适配器对应的只读旧协议测试（不调用真实服务）。Expected: PASS；图审与生成附件一致、首个参考/keyframe 原图一致、恢复不重复付费。

- [ ] **Step 5: 独立复核**

检查没有旧 episode flow、generation/cache/recovery 状态机混入；provider 不自行决定重试次数或内容修复，不提交。

### Task 6: 音乐证据、FFmpeg 合成和技术导出

**Files:**
- Create: `src/clean_studio/providers/music.py`
- Create: `src/clean_studio/providers/ffmpeg.py`
- Create: `src/clean_studio/assembly.py`
- Create: `tests/test_assembly.py`

**Interfaces:**
- Consumes: 有效视频、Episode 音乐 cue、原包片尾、冻结音乐库和 FFmpeg。
- Produces: `search_music(cue) -> list[MusicCandidate]`；`assemble(episode, valid_videos, accepted_music) -> ExportArtifact`；`probe_export(export) -> TechnicalReport`。

- [ ] **Step 1: 写音乐证据和真实小媒体测试**

音乐候选必须保存实际音频证据和有效内容 accepted；搜索成功、选择成功或 ASR 成功都不能代替试听验收。未审音乐、已选未混音乐、音频 bytes 已变化的旧审核不得进入 accepted export。

用 FFmpeg 生成无模型小视频，合成后断言 1080×1920、25 fps、H.264/AAC、JPEG 封面和片尾；片尾严格 3 秒，问题和选项文字逐字来自原包。完整导出可解码、时长正确、N/N 视频镜齐全，此时仍只是 candidate。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_assembly.py
```

Expected: music/ffmpeg/assembly 接口不存在。

- [ ] **Step 3: 实现音乐检索和单一后期时间线**

冻结复用现有 SoulTuner 检索与音频证据接口，不要求旧 episode music 状态机。候选采用必须包含 cue、音频 hash、实际试听 accepted 和限制说明；内容选择交给异常 Agent，宿主不伪造判断。

合成复用 `media/renderer.py` 的必要 FFmpeg 算法或按其合同写窄调用，不复制旧 runner/report。原生视频声音保留，音乐按 cue 混合；字幕来自当前 ASR/对白，片尾由宿主编排。技术门复用 `media/quality.py`，不把技术通过当内容通过。

- [ ] **Step 4: 重跑合成和技术门**

Run Step 2 命令。Expected: PASS；无黑片尾、重复编码或覆盖原生声音，缺 N/N 视频立即失败。

- [ ] **Step 5: 独立复核**

检查实际导出流、时长、字幕和音频，而不是只看文件存在；不提交。

### Task 7: 当前导出的正式音画验收与私有发布

**Files:**
- Create: `src/clean_studio/acceptance.py`
- Create: `src/clean_studio/providers/modelscope.py`
- Create: `prompts/final-visual-review.md`
- Create: `tests/test_acceptance_publish.py`

**Interfaces:**
- Consumes: 当前 ExportArtifact、冻结视觉/完整声音审核、ModelScope 私有目标。
- Produces: `accept_export(export, visual, audio, technical) -> AcceptedExport`；`publish(accepted) -> UploadReceipt`；`verify_upload(receipt) -> RemoteEvidence`。

- [ ] **Step 1: 写验收和私有性失败测试**

visual、完整 audio 分别绑定当前 export hash；visual accepted + ASR accepted + audio rejected 仍为 candidate。旧视觉/声音、缺完整音频窗口、视频或封面 hash 改变均不能接受。candidate 不上传，上传意图和不可核实的上传都不是 complete。

模拟远端已提交但连接丢失，先查清单/索引不重复上传。目标 repo 已存在但为 public 时，在任何上传前停止；`accepted` 未上传不能投影为 `complete`。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_acceptance_publish.py
```

Expected: acceptance/modelscope 接口不存在。

- [ ] **Step 3: 实现正式验收和窄发布适配**

视觉审核复用旧 prompt、抽帧、`validate_group/blocking_issues` 合同，不复制旧 episode review closure。完整声音复用 `review_audio` 的完整解码、30 秒窗口和 2 秒重叠、Eureka worker 与文本判断；缺窗、服务错误或 malformed judgment 都不是通过。新宿主只生成其需要的纯 JSON 事实视图，视觉和音频均绑定当前 export hash。

ModelScope 复用 `modelscope_hub.HubApi` 的上传/清单/索引回读和单写者锁，保留源 SHA-256。发布前由 Clean `accept_export` 提供当前验收查询，不复制旧 `acceptance_status` 状态机；必须验证账号、repo 私有性、当前 accepted export、目标 receipt 和远端索引。

- [ ] **Step 4: 重跑验收与假远端测试**

Run Step 2 命令。Expected: PASS；缺证据、错导出、public repo 和 candidate 都零上传。

- [ ] **Step 5: 独立复核**

检查没有 dry-run 冒充真实上传，也没有因已有 repo 而跳过私有性检查；不提交。

### Task 8: 唯一宿主循环、produce/status 与冷恢复

**Files:**
- Create: `src/clean_studio/pipeline.py`
- Create: `src/clean_studio/cli.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–7 的正式接口。
- Produces: `Pipeline.run(episode_id, *, stop_after=None, only_shot=None) -> RunResult`；`produce(config_path, *, services, stop_after=None, only_shot=None) -> RunResult`；`status(episode_id) -> dict`。两个范围参数仅用于显式小验证，完整生产默认均为 None，且范围结果不得投影为整集 COMPLETE。

- [ ] **Step 1: 写固定阶段与异常分流集成测试**

使用真实 SQLite、compiler 和 fake agents/providers：源审通过但缺首帧时自动执行 FIRST_FRAMES，无生产 Agent 回合；阶段严格按 AUTHORING→SOURCE_REVIEW→COMPILING→FIRST_FRAMES→VIDEOS→MUSIC→ASSEMBLY→FINAL_REVIEW→PUBLISHING→COMPLETE。Agent 没有 finish 或选阶段接口。

blocked 保留真实阶段和合法下一步，已有 assembly/candidate 不得覆盖。`apply ok` 后重新编译和审核；临时故障只做同 Operation 有界重试，凭据/配额/未知异常停止。冷恢复保留 edition、task ID、计数和余额，并标 `recovery_validation=True`，最多 3 次启动；代码变化记录恢复证据，模型/输入/通道/发布目标变化直接拒绝。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m pytest -q tests/test_pipeline.py tests/test_cli.py
```

Expected: pipeline/cli 接口不存在。

- [ ] **Step 3: 实现一个 advance 循环**

只保留一个 `STAGES` 元组和 `Pipeline.advance()`；每个 stage handler 返回 `completed/blocked`，内容修复留在当前 stage，不增加 `next_action()`、恢复 planner 或 Agent finish。正常路径由宿主直接调用 provider；只有内容 findings 才调用异常 Agent。

`produce` 在首次启动写入冻结配置和代码哈希；后续启动从 SQLite 继续并标记恢复验证。`stop_after` 只停在指定阶段，`only_shot` 只执行显式小验证范围；二者写入 validation scope，不创建第二生产模式，也不得把范围结果当成整集完成。`status` 只读计算有效视频/计划数、accepted 成片数、实际上传数、当前卡点、是否生产验证和验证性质，不修改 launches。

- [ ] **Step 4: 重跑集成与 CLI 测试**

Run Step 2 命令。Expected: PASS；candidate 未上传、accepted 未上传尚非 complete，只有当前 accepted + 可核实上传才 COMPLETE。

- [ ] **Step 5: 独立复核与整分支审查**

复核所有任务接口和跨模块组合，尤其付费计次、无 Agent 驾驶、blocked 终态、当前证据、上传私有性。运行项目完整测试一次并保存 pytest exit code，不提交。

### Task 9: 真实分层验证与冻结 W10

**Files:**
- Create: `.runtime/clean-w10-20261026/owner`
- Create: `.runtime/clean-w10-20261026/validation-result.json`
- Create: `.runtime/clean-w10-20261026/frozen-config.json`
- Create: `.runtime/clean-w10-20261026/author-validation.json`
- Create: `.runtime/clean-w10-20261026/media-validation.json`

**Interfaces:**
- Consumes: Tasks 1–8 已复核实现、原包、原参考、正式模型和私有发布目标。
- Produces: 真实作者/审核证据、代表范围媒体链回执、冻结 W10 最终结果。

- [ ] **Step 1: 全仓测试和适配器只读健康检查**

运行项目完整测试并保存真实退出码。核对 Codex、GPT Image、GPU16 H3、SenseVoice、Eureka、FFmpeg 和 ModelScope 配置存在及最小健康状态；只读检查不算真实生成或内容通过。GPU16 不可用时只能在 edition 创建前选择云端 H3 并冻结。

- [ ] **Step 2: 真实作者与独立审核**

先把 A5 的只读失败形状转换为 Clean 字段并调用真实 Reviewer，确认场景错贴能定位、起始状态误报可撤销但 camera 问题保留、镜内加入 C 不误报、相邻重复能指出双方字段；不得修改旧稿，也不得反复审同一内容直到通过。随后从原包和原始参考调用正式 `produce` 的创作/源审阶段，保存完整读回 episode、session、实际模型回复、grounding、二次确认、反馈和计数。作者无法在标准额度内交付一致稿则停止，不进入付费媒体。

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m clean_studio.cli produce \
  /mnt/disk1/zengzhitao/interactive-video-studio-clean/.runtime/clean-w10-20261026/author-validation.json \
  --stop-after SOURCE_REVIEW
```

- [ ] **Step 3: 代表范围完整媒体链**

对已通过制作稿的 `shot_001` 完成真实参考展开、首帧图审、视频、完整解码、ASR、音乐、合成、正式视觉/完整声音审核和私有上传。保存实际 prompt、附件顺序/hash、task ID、生成计数、解码、审核和远端回执。candidate 或审核服务错误都停止；代表范围成功不计作 W10 整集成功。

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m clean_studio.cli produce \
  /mnt/disk1/zengzhitao/interactive-video-studio-clean/.runtime/clean-w10-20261026/media-validation.json \
  --only-shot shot_001
```

- [ ] **Step 4: 冻结并运行 W10**

只有 Steps 1–3 全通过，才创建 edition `clean-w10-20261026`。冻结代码、模型、提示词、输入、编译器、每镜 3 图/3 takes/2 input revisions、3 次启动、作者 1200/5400 秒和私有 repo `Flame4pd/zhiyao-clean-validation-20261026`。

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m clean_studio.cli produce \
  /mnt/disk1/zengzhitao/interactive-video-studio-clean/.runtime/clean-w10-20261026/frozen-config.json
```

等待最终结果，不逐镜人工改稿或热换。最终只认当前稿有效视频 N/N、当前完整导出视觉和完整声音 accepted、正式 accepted 成片 1 个、私有远端可核实上传 1 个。

- [ ] **Step 5: 保存结果并如实报告**

`validation-result.json` 记录有效视频/计划数、accepted 成片数、实际上传数、当前卡点、最新修复是否真实生产验证、恢复还是从头无人验证。若真实失败，停止对应阶段；修复后的续跑只算恢复验证，不能靠重建同一任务、追加额度或放宽验收制造成功。不提交、不推送。

## 实施与复核顺序

Tasks 1→2→3→4→5→6→7→8 默认串行，因为接口逐层依赖；每个任务由独立实现代理完成、另一名代理复核。Task 9 必须等整分支复核和全部离线测试通过。并行只允许只读调查、历史夹具准备或不重叠测试审查，不允许多个代理同时修改同一文件。
