# W10 Deterministic Production Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在冻结的八类问题范围内，完成创作审核闭环、受影响请求编译和宿主自动执行，经真实分层验证后再从原包冻结运行 W10。

**Architecture:** 复用现有创作会话、独立源审、`rebuild_clips`、`CompiledOpening`、episode loop 和发布门，不新增队列、生产模式、世界状态或恢复框架。作者负责内容，宿主负责确定性编译和阶段执行；只有需要内容判断的异常才调用 Agent。

**Tech Stack:** Python、pytest、现有 interactive/episode APIs、既有 LLM/图像/H3/ModelScope 正式入口。

**Spec:** `docs/w10-autonomous-production-status-20261026.md`

## Global Constraints

- 工作树固定为 `/mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-v4-20261023`；保留其他会话 WIP，不 reset、不整体覆盖、不提交、不推送。
- A1–A5 和旧 v1/v2 只读作证据；不恢复、不改稿、不改审核、不清计数、不追加额度。
- 每项先写历史失败回归，再写最小实现；只改本任务文件，测试通过不等于内容或整集通过。
- 不修改旧 `build_episode.py`，不禁止作者用代码生成 JSON；验收最终输出，不为 clip_03/04/19 写生产特例。
- 真实内容修复只能由原作者或生产 Agent 通过正式反馈、check/apply 完成；宿主不得选择矛盾剧情。
- 不重复审核同一内容指纹直到通过；服务错误、无新证据和非法 findings 不得缓存成有效 verdict。
- 只重编译受影响输出，保留无关镜头、有效素材、task ID、回执和原额度。重新查询不算生成，重新提交必须受生成额度约束。
- “正常生产零 Agent”指无需生产 Agent 驾驶；作者、语义审核和现有模型编译仍可调用 LLM。
- 首帧、视频、ASR、声音自然度、整片视觉/声音终审和上传各自保留，不互相替代。
- 生产只有一个 `produce-batch` 入口；代码、模型、输入、标准额度和私有发布目标启动前冻结，不热换。
- 测试命令统一使用 `PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest`；最终全仓只跑一次，失败修复后针对失败复跑。

## Review Focus

- `clip_04` 两个开场字段均“已经亮起”不得被报成互相矛盾，但认领区三灯 `camera` 仍须报场景错贴：Task 1 的 grounding/确认回归和 Task 8 的真实审核。
- `clip_19` 镜内加入 C 合法，首帧提示内部及 storyboard 的零点三卡与 C 未加入冲突：Task 1 的语义证据回归、Task 3 的 freshness 回归和 Task 8 的真实审核。
- 源修订只改 `start_state/event` 时，真实编译请求不得继续读取旧 `visual_prompt/motion_prompt`：Task 3 的真实 `rebuild_clips` 回归。
- 普通首帧没有 Agent session 时，宿主必须写合法输入文件并能正式 finish；失败动作不得无变化重放：Task 5 的 loop 回归。
- 网络中断、提交不确定和审核服务错误不得变成内容改稿或盲目重新提交：Task 6 的结构化故障回归。

---

### Task 1: 制作稿审核证据、时间语义与相邻镜覆盖

**Files:**
- Modify: `src/novel_manga/application/preparation/audit.py`
- Modify: `src/novel_manga/planning/preparation.py`
- Test: `tests/test_h3_book_preparation.py`
- Test: `tests/test_first_frame_source_preflight.py`

**Interfaces:**
- Consumes: 原始作者 `episode.json`、原包文本、现有 `ask_json` 和二次确认。
- Produces: `creative_audit(episode: dict, sources: dict[str, str], *, cache_path: Path, settings=None) -> dict`；`creative_fingerprint(episode: dict, sources: dict[str, str]) -> str`；`ground_creative_finding(issue: dict, episode: dict) -> None`。

- [ ] **Step 1: 写字段原句、时间和 storyboard 的失败回归**

在 `tests/test_h3_book_preparation.py` 增加最小制作稿，覆盖两条独立问题，而不是只检查问题数量：

```python
finding = {
    'kind': 'action', 'stage': 19, 'relation': 'contradiction',
    'basis': ['chapter_script.shots[18].opening_frame.image_prompt',
              'chapter_script.shots[18].storyboard.frame'],
    'observed': 'C卡尚未加入', 'expected': 'A、B、C三张目的地卡并排',
    'observed_time': 't=0', 'expected_time': 't=0',
    'source_segment': '', 'source_quote': '',
    'problem': '首帧不能同时只有A/B和已有A/B/C',
    'correction': '统一零点描述，保留镜内加入C',
}
ground_creative_finding(finding, episode)
finding['basis'][0] = 'chapter_script.shots[18].missing.field'
with pytest.raises(ValueError, match='evidence path'):
    ground_creative_finding(finding, episode)
```

另加 `clip_04` 形状：确认输入同时保留“两个起始字段均已亮”的证据和 `camera=认领区三盏灯`；测试只允许二次确认撤销起始状态项，不能撤销摄像机项。`event` 中“镜内加入C”不得生成 t=0 冲突。

- [ ] **Step 2: 运行回归，确认旧接口缺少制作字段证据**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_h3_book_preparation.py tests/test_first_frame_source_preflight.py
```

Expected: 新用例 FAIL，原因是 `ground_creative_finding` 不存在或 storyboard/字段证据未校验；既有小说原文 grounding 不得失败。

- [ ] **Step 3: 在现有审核中实现制作字段 grounding 和缓存指纹**

扩展现有 issue schema 的可选字段，不另建 findings 格式：

```python
{
  'basis': list[str], 'observed': str, 'expected': str,
  'observed_time': 't=0|during|t=end|whole_shot',
  'expected_time': 't=0|during|t=end|whole_shot',
  'relation': 'contradiction|underspecified|compatible',
}
```

`ground_creative_finding` 用现有 JSON 路径解析读取真实字段，要求 `stage == shots[n]['index']`、引用原句逐字包含在对应字段中；内部矛盾必须有两个真实字段证据，原文问题继续走 `grounded_issues()`。grounding 失败沿用现有“一次带错误纠正”，仍失败则不产生有效 verdict。

`creative_fingerprint` 对以下内容规范化 JSON 后 SHA-256，不包含绝对路径、mtime 或派生 `clip_plan`：

```python
{'policy': AUTHORED_REVIEW_POLICY, 'episode': episode, 'sources': sources}
```

`creative_audit` 复用 `audit()` 的 `ask_json`、证据纠正和二次确认顺序，提示中明确：`start_state/opening_frame/storyboard.frame=t=0`、`event/action_beats=during`、`end_state=t=end`；相邻镜只比较实际文本，允许跳切，不建立世界状态表。返回：

```python
{'fingerprint': creative_fingerprint(episode, sources),
 'answer': {'source_readable': True, 'source_problem': '', 'issues': []},
 'raw': raw_answer, 'confirmation': confirmation}
```

更新 `audit.py` 顶部说明，使其描述制作字段与相邻镜职责；不改变 `OPENING_FIELDS` 的首帧提示范围。

- [ ] **Step 4: 重跑定向回归**

Run Step 2 命令。Expected: 新旧用例全部 PASS；storyboard 不进入实际生图 prompt，非法 finding 不缓存。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/application/preparation/audit.py src/novel_manga/planning/preparation.py tests/test_h3_book_preparation.py tests/test_first_frame_source_preflight.py`

Expected: 只有审核、证据和测试增量；不提交。

### Task 2: 创作拒绝回到同一作者并复用有效审核

**Files:**
- Modify: `src/novel_manga/interactive/film.py`
- Modify: `src/novel_manga/application/preparation/actions.py`
- Modify: `src/novel_manga/application/agents/episode/inputs.py`
- Modify: `src/novel_manga/application/agents/episode/local_preparation.py`
- Test: `tests/test_original_film.py`
- Test: `tests/test_film_lane_handoff.py`
- Test: `tests/test_episode_agent_inputs.py`
- Test: `tests/test_local_preparation.py`

**Interfaces:**
- Consumes: Task 1 的 `creative_audit/creative_fingerprint`；现有 `creation.run(task_path, run_dir, *, config=None, resume=False, feedback=None)`。
- Produces: `state['pending_feedback']`、`state['pending_review_fingerprint']`、`episode/pre_render_story_audit.json`；同指纹生产复用。

- [ ] **Step 1: 写语义拒绝不进入素材/生产的回归**

mock `creative_audit` 返回带真实字段证据的拒绝，第一次调用 `film.run` 后断言：

```python
assert state['status'] == 'blocked'
assert state['retryable'] is True
assert state['pending_review_fingerprint'] == review['fingerprint']
assert not (run_dir / 'material' / 'material.json').exists()
assert build_assets.call_count == 0
assert run_episode.call_count == 0
```

第二次让作者改稿并改变 fingerprint，断言 `creation.run(..., resume=True, feedback=<已保存反馈>)`、同一 session 和累计时间继续使用；同稿无变化时 `retryable=False`，不得循环。

- [ ] **Step 2: 验证新回归失败**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_original_film.py tests/test_film_lane_handoff.py \
  tests/test_episode_agent_inputs.py tests/test_local_preparation.py
```

Expected: 新用例因 film 未调用创作审核、未传 pending feedback 而 FAIL。

- [ ] **Step 3: 在现有 film 状态中接审核与反馈**

在 `film.py:74` 读取原始 draft 后、任何 `write_material` 前调用 `creative_audit`。拒绝时把结构化 findings 写入 `run_dir/creative-feedback/<fingerprint>.json`，保存 pending 指纹并返回；不物化 material。下次仅当 pending 指纹仍对应当前 draft 时调用：

```python
result = creation.run(
    frozen, creator_dir, config=config,
    resume=True, feedback=Path(state['pending_feedback']),
)
```

审核通过后才写 material；`pack_authored` 后、资产前发布同一回执，不再次调用模型。`actions.py` 新回执优先比较内容 fingerprint，旧无指纹回执继续用 `inputs()` 兼容。`inputs.py` 的 `SCRIPT_FILES` 增加 `pre_render_story_audit.json`，`local_preparation` 的本地 stamp 包含该指纹。`source_readable=False` 和审核服务异常不得生成作者修复任务。

- [ ] **Step 4: 重跑创作交接回归**

Run Step 2 命令。Expected: PASS；不同绝对路径、相同内容只审核一次，单独修改 storyboard 会使指纹失效。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/interactive/film.py src/novel_manga/application/preparation/actions.py src/novel_manga/application/agents/episode/inputs.py src/novel_manga/application/agents/episode/local_preparation.py tests/test_original_film.py tests/test_film_lane_handoff.py tests/test_episode_agent_inputs.py tests/test_local_preparation.py`

Expected: 无第二套反馈状态机、无新队列；不提交。

### Task 3: 权威源字段、受影响派生提示与零点 freshness

**Files:**
- Modify: `src/novel_manga/interactive/creative_material.py`
- Modify: `src/novel_manga/application/agents/episode/input_candidate.py`
- Modify: `src/novel_manga/application/agents/episode/input_contracts.py`
- Modify: `src/novel_manga/application/agents/episode/input_compilation.py`
- Modify: `src/novel_manga/media/first_frames.py`
- Modify: `src/novel_manga/application/agents/episode/opening_source.py`
- Test: `tests/test_creative_material.py`
- Test: `tests/test_adaptation_material_bridge.py`
- Test: `tests/test_episode_agent_input_repair.py`
- Test: `tests/test_first_frame_source_preflight.py`

**Interfaces:**
- Consumes: 作者 `start_state/event/storyboard` 和正式 input updates。
- Produces: 同步的 `visual_prompt/motion_prompt`；含 `storyboard.frame` 的 opening 审核/freshness 合同；局部 `rebuild_clips` 输出。

- [ ] **Step 1: 写旧派生提示不得进入真实请求的回归**

在两个 material 测试的作者稿中放入与 `start_state/event` 相反的旧 `visual_prompt/motion_prompt`，断言打包后：

```python
assert packed_shot['visual_prompt'] == packed_shot['start_state']
assert packed_shot['motion_prompt'] == packed_shot['event']
```

在 `test_episode_agent_input_repair.py` 使用现有 `real_rebuild_clips`，只修一个 clip 的 `start_state/event/storyboard.frame`，断言新中文进入真实 `clip['prompt']`，其他 clip、媒体、时长、take 和预算不变。

- [ ] **Step 2: 验证旧 setdefault 和 apply 留下 stale prompt**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_creative_material.py tests/test_adaptation_material_bridge.py \
  tests/test_episode_agent_input_repair.py tests/test_first_frame_source_preflight.py
```

Expected: 新用例 FAIL，显示旧派生值仍被保留或 storyboard 修改未到达 opening freshness。

- [ ] **Step 3: 最小同步权威字段并扩展零点合同**

`creative_material.py` 两个规范化入口均使用确定赋值：

```python
shot['visual_prompt'] = copy.deepcopy(shot['start_state'])
shot['motion_prompt'] = copy.deepcopy(shot['event'])
```

`input_candidate.prepare_candidate()` 只在 updates 含权威字段时同步对应派生字段。旧接口直接修 `visual_prompt/motion_prompt` 的兼容行为保留。

`media/first_frames.py` 只把 `storyboard.frame` 加入 opening 审核和 freshness：

```python
board = opening.get('storyboard') or {}
if 'frame' in board:
    compiled_opening['storyboard'] = {'frame': copy.deepcopy(board['frame'])}
```

不得加入 `action_beats`，也不得把 frame 再拼进实际生图指令。`SHOT_FIELDS/opening_grant` 允许完整 storyboard 对象；`input_compilation` 将 frame 变化视为 opening 变化，在新首帧审核前不重译 H3。更新相关注释，删除“storyboard 不参与 freshness”的旧描述。

- [ ] **Step 4: 重跑编译回归**

Run Step 2 命令。Expected: PASS；单独 frame 改变会使首帧和源审缓存失效，无关 clip 不变。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/interactive/creative_material.py src/novel_manga/application/agents/episode/input_candidate.py src/novel_manga/application/agents/episode/input_contracts.py src/novel_manga/application/agents/episode/input_compilation.py src/novel_manga/media/first_frames.py src/novel_manga/application/agents/episode/opening_source.py tests/test_creative_material.py tests/test_adaptation_material_bridge.py tests/test_episode_agent_input_repair.py tests/test_first_frame_source_preflight.py`

Expected: 没有整集重建、没有宿主改写 storyboard；不提交。

### Task 4: 主体/风格参考展开与付费前聚合错误

**Files:**
- Modify: `src/novel_manga/media/first_frames.py`
- Modify: `src/novel_manga/media/reference_views.py`
- Modify: `src/novel_manga/interactive/creative_contract.py`
- Modify: `src/novel_manga/interactive/creative_material.py`
- Modify: `src/novel_manga/interactive/authored.py`
- Modify: `src/novel_manga/interactive/style_packs.py`
- Modify: `src/novel_manga/application/agents/episode/first_frames.py`
- Modify: `src/novel_manga/application/agents/episode/inputs.py`
- Modify: `src/novel_manga/application/agents/episode/current_task.py`
- Test: `tests/test_first_frame_argument_validation.py`
- Test: `tests/test_first_frame_source_route.py`
- Test: `tests/test_first_frame_prompt.py`
- Test: `tests/test_first_frame_production.py`
- Test: `tests/test_style_packs.py`
- Test: `tests/test_episode_current_task.py`

**Interfaces:**
- Consumes: `opening_frame.reference_subjects`、clip 注册主体、冻结 style examples、`compile_references()`。
- Produces: 主体→衣装→风格的同一 `CompiledOpening.references`；逐 clip `invalid_fields/argument_errors/repair_origin/repair_grant`；作者引用与 registration 分类。

- [ ] **Step 1: 写未注册名、风格附件和错误聚合回归**

两个 clip 同时包含未知主体，断言 builder 调用为 0、图片候选分配为 0，且：

```python
assert result['invalid_fields']['clip_01'] == ['opening_frame.reference_subjects']
assert result['invalid_fields']['clip_02'] == ['opening_frame.reference_subjects']
assert result['required_action'] == 'apply'
assert result['image_candidates_allocated'] == 0
```

作者未知注册名返回仅限 `opening_frame` 的 input grant；catalog 已注册但未绑定、缺文件或哈希失败标 `registration`，不得发作者 grant。风格图测试核对实际存在、顺序、64 位哈希、图号和 `style_only_not_character_identity` scope。

- [ ] **Step 2: 验证直接 ValueError 遮住同批错误**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_first_frame_argument_validation.py tests/test_first_frame_source_route.py \
  tests/test_first_frame_prompt.py tests/test_first_frame_production.py \
  tests/test_style_packs.py tests/test_episode_current_task.py
```

Expected: 新用例 FAIL；保留既有真实附件和图审测试。

- [ ] **Step 3: 复用现有参考编译完成展开和错误收集**

`compile_opening()` 计算全部 unknown 和当前合法注册名；`field_errors is None` 时仍抛 ValueError，episode 边界传入时记录 `opening_frame.reference_subjects` 并返回不生成媒体的 draft。`FirstFrameOperations.prepare()` 将错误加入现有 `argument_errors/invalid_fields`，在 reserve/build 前停止。

风格样图复制从 `creative_contract.export()` 抽成 `export_style(style_source, destination)` 并复用到 material；`interactive/authored.py` 和 episode inputs 复制 `style-examples/`。`style_packs` 以冻结 examples 顺序提供只读参考描述，`opening_references()` 在主体和衣装后追加风格图，最终仍只调用：

```python
compile_references(ctx.novel_dir, expanded, require_files=require_assets)
```

生成、图审、缓存和哈希继续使用同一 `CompiledOpening`。风格只约束色光、材质和渲染，不复制人物、衣装、剧情、文字、布局或 POV。源字段错误提示 check/apply；普通行参数错误仍重试 first_frames。

- [ ] **Step 4: 重跑参考与提示回归**

Run Step 2 命令。Expected: PASS；同路径风格图内容变化会使旧首帧/图审失效，源修复后 status 重算恢复普通 first_frames。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/media/first_frames.py src/novel_manga/media/reference_views.py src/novel_manga/interactive/creative_contract.py src/novel_manga/interactive/creative_material.py src/novel_manga/interactive/authored.py src/novel_manga/interactive/style_packs.py src/novel_manga/application/agents/episode/first_frames.py src/novel_manga/application/agents/episode/inputs.py src/novel_manga/application/agents/episode/current_task.py tests/test_first_frame_argument_validation.py tests/test_first_frame_source_route.py tests/test_first_frame_prompt.py tests/test_first_frame_production.py tests/test_style_packs.py tests/test_episode_current_task.py`

Expected: 无 basename/别名猜身份、无第二套附件哈希；不提交。

### Task 5: 宿主普通首帧、统一 finish 与无 Agent session 完成

**Files:**
- Modify: `src/novel_manga/application/agents/episode/readiness.py`
- Modify: `src/novel_manga/application/agents/episode/flow.py`
- Modify: `src/novel_manga/application/agents/episode/completion.py`
- Modify: `src/novel_manga/application/agents/episode/lifecycle.py`
- Test: `tests/test_autonomous_phase_handoff.py`
- Test: `tests/test_final_audio_delivery_gate.py`
- Test: `tests/test_episode_agent_scheduling.py`
- Test: `tests/test_episode_agent_cli.py`

**Interfaces:**
- Consumes: 最新 `next_action()`、当前 `clip_plan`、现有 `bridge.handle()` 和正式验收。
- Produces: `automatic_request(ctx, status, *, first_pass_only=False) -> dict | None`；合法 `output/first-frames.json`；`current_finish(..., allow_host=False)` 的 host 兼容。

- [ ] **Step 1: 写宿主首帧 payload 和完成回归**

构造缺两个首帧且无 Agent session 的 adopted episode，断言宿主不写 Agent prompt，生成：

```python
{
  'render_ready': False,
  'frames': [
    {'clip_id': 'clip_01', 'extra_references': []},
    {'clip_id': 'clip_02', 'extra_references': []},
  ],
}
```

随后调用 `bridge.handle('first_frames', {'file': 'output/first-frames.json'})`，再按最新路由 render/review/finish。另测 `finish → missing_video` 必须返回最新 route 的 action/reason/blocked；相同 completed/failed action 不重放；提前 `passed=false` finish 不得跳过异常。

- [ ] **Step 2: 验证白名单、payload 和 host session 缺口**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_autonomous_phase_handoff.py tests/test_final_audio_delivery_gate.py \
  tests/test_episode_agent_scheduling.py tests/test_episode_agent_cli.py
```

Expected: 新用例 FAIL；现有 watchdog、旧 manifest 和终态缓存测试保持可复现。

- [ ] **Step 3: 在现有 loop 内实现请求派生**

`automatic_request` 在首轮允许 `first_frames/render`，后续允许 `first_frames/render/review/finish`，复用相同 action guard；首轮缺首帧也不能退回 Agent 驾驶。首帧 target 取 plan 视频顺序与最新 `next_action()['blocked']` 的交集，空目标不调用。`flow.py` 普通分发和 input apply 后分发共用，并删除“first_frames 必须由 saved session 推进”的旧注释：

```python
request = automatic_request(ctx, status, first_pass_only=first_pass_only)
if request is not None:
    if request.get('document') is not None:
        atomic_write_json(ctx.work_dir / request['payload']['file'], request['document'])
    bridge.update_state(phase='tool_running', next_action=request['route'])
    bridge.handle(request['action'], request['payload'])
    continue
```

不把 `adopt/select_music` 加入该白名单：前者由既有 startup/adopt 交接负责，后者需要真实候选与内容审核。

`completion.finish()` 缺视频早退复用最新 `next_action()`，后续正式验收不变。`current_finish(..., allow_host=False)` 仅在 `production_mode == 'agent'` 的宿主 finish 调用中传 true；无 session 时仍要求新 stamp、当前 job 和 passed 一致，旧 manifest 不得复用。更新 readiness 顶部“Agent 决定下一步”的旧注释为宿主路由职责。

- [ ] **Step 4: 重跑宿主执行回归**

Run Step 2 命令。Expected: PASS；源审/action=agent 时不生图，host finish 仍需当前视觉/完整声音门。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/application/agents/episode/readiness.py src/novel_manga/application/agents/episode/flow.py src/novel_manga/application/agents/episode/completion.py src/novel_manga/application/agents/episode/lifecycle.py tests/test_autonomous_phase_handoff.py tests/test_final_audio_delivery_gate.py tests/test_episode_agent_scheduling.py tests/test_episode_agent_cli.py`

Expected: 没有第二调度器、没有自动选曲或自动重拍；不提交。

### Task 6: 技术故障分流与最小跨镜异常上下文

**Files:**
- Modify: `src/novel_manga/application/agents/episode/tools.py`
- Modify: `src/novel_manga/application/agents/episode/readiness.py`
- Modify: `src/novel_manga/application/agents/episode/flow.py`
- Modify: `src/novel_manga/application/agents/episode/current_task.py`
- Modify: `src/novel_manga/application/agents/episode/prompts.py`
- Modify: `src/novel_manga/interactive/first_frame_entry.py`
- Modify: `src/novel_manga/interactive/film.py`
- Test: `tests/test_episode_agent_scheduling.py`
- Test: `tests/test_episode_current_task.py`
- Test: `tests/test_first_frame_image_grants.py`
- Test: `tests/test_episode_review_readiness.py`
- Test: `tests/test_music_selection.py`

**Interfaces:**
- Consumes: 现有 `service_interruption()`、`service_blocker()`、`SubmissionUncertain` guard、`RepairGrant/InputScope`。
- Produces: 结构化 `service_interrupted/retryable` 回执；`action=agent + required_action=select_music`；仅问题及必要依赖的 Agent 投影。

- [ ] **Step 1: 写技术故障不送内容 Agent 的回归**

分别模拟可恢复 503、凭据失效、配额耗尽和 `SubmissionUncertain`：

```python
assert result['service_interrupted']['http_status'] == 503
assert state['retryable'] is True
assert agent.call_count == 0
```

凭据/配额为 `retryable=False`；提交不确定只查询原 task，不重新 POST。`review_error` 不得当内容拒绝。跨镜问题投影包含问题 clip 和必要共享/相邻镜，后者标 `context_only`，不扩大可改字段。

音乐需要内容选择时返回 `action=agent, required_action=select_music`，保留 search/apply、候选音频证据和原额度；宿主不伪造 accepted review。

- [ ] **Step 2: 验证当前异常被字符串化或误路由**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_episode_agent_scheduling.py tests/test_episode_current_task.py \
  tests/test_first_frame_image_grants.py tests/test_episode_review_readiness.py \
  tests/test_music_selection.py
```

Expected: 新用例 FAIL；现有安全重试和 candidate resume 用例继续有效。

- [ ] **Step 3: 保留故障结构并缩小异常投影**

`tools.py` 两个异常边界在 result 中保留 `service_interrupted` 和保守的 `retryable`；未知宿主异常不自动重试。`flow.py` 在构造 Agent prompt 前分流：安全临时故障有界恢复，凭据/配额停止，只有当前内容 findings/grant 才调用 Agent。`SubmissionUncertain` 继续走原 task 对账，结果过期后的重新提交仍占生成额度。

`current_task.prepare()` 从 blocked/source conflicts/invalid fields 取种子 clip，加入现有 InputScope 声明的必要共享或相邻镜；无显式 clip 时保留现有回退。只投影当前 issue、证据、grant、余额和所需协议，移除正常 clip 的完整 opening 及无关音乐/混音规则。`last_operation.result` 保留 `required_action/message/invalid_fields/service_interrupted/source_input_conflicts`。`first_frame_entry` 和 `film` 传递既有 retryable，内容 candidate 不得因此重开。

- [ ] **Step 4: 重跑故障与上下文回归**

Run Step 2 命令。Expected: PASS；网络恢复不增加生成计数，异常上下文不变成“永远只给一个镜头”。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/application/agents/episode/tools.py src/novel_manga/application/agents/episode/readiness.py src/novel_manga/application/agents/episode/flow.py src/novel_manga/application/agents/episode/current_task.py src/novel_manga/application/agents/episode/prompts.py src/novel_manga/interactive/first_frame_entry.py src/novel_manga/interactive/film.py tests/test_episode_agent_scheduling.py tests/test_episode_current_task.py tests/test_first_frame_image_grants.py tests/test_episode_review_readiness.py tests/test_music_selection.py`

Expected: 无新故障框架、无技术问题改剧情；不提交。

### Task 7: 保留并核实当前导出验收与上传绑定

**Files:**
- Modify: `src/novel_manga/application/agents/episode/media_operations.py`
- Modify: `src/novel_manga/interactive/first_frame_entry.py`
- Modify: `src/novel_manga/review/acceptance.py`
- Test: `tests/test_current_export_acceptance.py`
- Test: `tests/test_modelscope_publication.py`
- Test: `tests/test_interactive_delivery.py`

**Interfaces:**
- Consumes: 现有 `finish_reviewed_delivery()`、`acceptance_status()`、`publish_deliveries()`。
- Produces: 正式 `run_dir` 绑定和版本化 `cover.jpeg` 定位；不重写上传器。

- [ ] **Step 1: 写真实版本目录的验收回归**

使用 `delivery/.../versions/export-001/episode01.mp4` 和同目录 `cover.jpeg`，不 mock `acceptance_status`，断言 receipt 的 `run_dir` 能定位当前 `final-candidate`；旧视觉/声音、缺 run_dir、视频或封面 hash 改变均拒绝。candidate 继续不上传。

- [ ] **Step 2: 验证旧封面路径或空 run_dir**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_current_export_acceptance.py tests/test_modelscope_publication.py \
  tests/test_interactive_delivery.py
```

Expected: 新版本目录用例 FAIL；现有远端对账测试不回归。

- [ ] **Step 3: 只修现有绑定**

`MediaOperations.runner()` 和 first-frame entry 的 runner 设置 `runner.context.run_dir = ctx.run_dir`；`acceptance.py` 使用：

```python
cover = Path(movie).parent / 'cover.jpeg'
```

保留当前 accepted_export、视觉/完整声音、视频/封面 SHA-256、远端文件清单和 episodes 索引核实，不改变发布协议。

- [ ] **Step 4: 重跑验收与发布回归**

Run Step 2 命令。Expected: PASS；上传仍只接受当前正式 accepted 导出。

- [ ] **Step 5: 审查本任务差异**

Run: `git diff -- src/novel_manga/application/agents/episode/media_operations.py src/novel_manga/interactive/first_frame_entry.py src/novel_manga/review/acceptance.py tests/test_current_export_acceptance.py tests/test_modelscope_publication.py tests/test_interactive_delivery.py`

Expected: 无新上传实现、无验收放宽；不提交。

### Task 8: 定向/全仓验证、真实小闭环与冻结 W10

**Files:**
- Modify: `docs/active-sessions.md`
- Modify: `docs/w10-autonomous-production-status-20261026.md`
- Create: `.runtime/w10-closure-20261026/owner`
- Create: `.runtime/w10-closure-20261026/validation-result.json`

**Interfaces:**
- Consumes: Tasks 1–7 的正式接口、原始 W10 zip 和系列 material、现有 A2 不受影响证据。
- Produces: 真实作者/审核回执、受影响媒体小链回执、全仓结果、冻结配置和最终整集/上传结果。

- [ ] **Step 1: 运行全部定向回归与独立只读复核**

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" .venv/bin/python -m pytest -q \
  tests/test_h3_book_preparation.py tests/test_original_film.py \
  tests/test_film_lane_handoff.py tests/test_episode_agent_inputs.py \
  tests/test_local_preparation.py tests/test_creative_material.py \
  tests/test_adaptation_material_bridge.py tests/test_episode_agent_input_repair.py \
  tests/test_first_frame_source_preflight.py tests/test_first_frame_argument_validation.py \
  tests/test_first_frame_source_route.py tests/test_first_frame_prompt.py \
  tests/test_first_frame_production.py tests/test_style_packs.py \
  tests/test_episode_current_task.py tests/test_autonomous_phase_handoff.py \
  tests/test_final_audio_delivery_gate.py tests/test_episode_agent_scheduling.py \
  tests/test_episode_agent_cli.py tests/test_first_frame_image_grants.py \
  tests/test_episode_review_readiness.py tests/test_music_selection.py \
  tests/test_current_export_acceptance.py tests/test_modelscope_publication.py \
  tests/test_interactive_delivery.py
```

Expected: exit 0。独立复核只读检查差异、字段归属、计数和旧任务未被修改；发现 Important 问题先回到所属 Task，不进入真实调用。

- [ ] **Step 2: 执行一次全仓测试并保存真实退出码**

Run:

```bash
set -o pipefail
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  .venv/bin/python -m pytest -q 2>&1 | tee .runtime/w10-closure-20261026/full-suite.log
status=${PIPESTATUS[0]}
printf '%s\n' "$status" > .runtime/w10-closure-20261026/full-suite.exit
exit "$status"
```

Expected: exit 0 和 pytest 汇总同时保留。若工具 300 秒限制中断，后台继续原命令，不把超时当 pytest 失败或成功。

- [ ] **Step 3: 真实作者与审核闭环**

先将 A5 作者原稿作为只读回归输入调用真实 `creative_audit()`，不修改旧目录、不采用旧稿作为新任务起点。核对至少以下判断：场景错贴能定位到 camera；clip_04 起始状态误报可撤销但 camera 问题保留；clip_19 首帧内部/storyboard 冲突保留，镜内加入 C 不报；相邻镜重复红灯/离开能指出双方字段。审核判断不准确时停止并回到 Task 1，不能靠反复审核同一稿件直到通过。

历史判断验证后，在新的 `.runtime/w10-closure-20261026/author-review/` 下，从原包和原 material 使用正式 `creation.run()` 创作，不继承 A5 稿件、会话或计数。确认 findings 通过 `creation.run(resume=True, feedback=...)` 回原作者；保存前后稿、真实审核 raw/confirmation、session、余额和 apply 回执。相同指纹不得再次审核，服务错误不得变成作者改稿。真实作者无法在原额度内交付一致制作稿时停止，不进入付费媒体。

- [ ] **Step 4: 真实小规模完整媒体链**

在同一正式宿主入口下，从已通过的真实制作稿使用现有 `clip_01` 代表范围，验证：编译请求与参考附件 → 首帧图审 → 视频解码/ASR → 该代表范围的合成导出 → 当前导出的正式视觉/完整声音终审 → 私有数据集上传回执。只使用标准额度和现有通用工具，不手写单镜内容，不把代表范围的验收计作 W10 整集验收。

A2 已证明且本次修改不影响的能力可只核对原证据；凡本次改动涉及的派生 prompt、参考展开、宿主首帧和验收绑定必须用新运行验证。保存实际 prompt、附件顺序/hash、task ID、生成计数、解码、审核和上传回执。candidate 不算 accepted，ASR 不算声音自然度，单镜不算整集。

- [ ] **Step 5: 冻结并运行新的 W10**

只有 Steps 1–4 全通过才创建新 edition `zhiyao-auto-b1-EP04C_W10_失物海`。输入固定为：

```text
/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022/.runtime/zhiyao-batch/mini-EP04C_W10_失物海.zip
/mnt/disk1/zengzhitao/interactive-video-worktrees/zhiyao-batch-20261022/.runtime/zhiyao-base/material
```

启动前保存完整代码树快照、源包 SHA-256、正式 agent/model 配置、H3 通道、标准每镜 3 图/3 takes/2 input revisions、最多 3 次启动和私有发布目标。运行中不热换任何项，不继承旧稿、图片、会话、审核或计数。

Run:

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  .venv/bin/python scripts/interactive_video.py produce-batch \
  --batch /mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-v4-20261023/.runtime/w10-closure-20261026/frozen/batch.json \
  --output-dir /mnt/disk1/zengzhitao/interactive-video-worktrees/autonomous-production-v4-20261023/.runtime/w10-closure-20261026/frozen/run
```

等待最终结果，不逐镜人工修片。最终只认有效视频 N/N、正式 accepted 成片 1 个、远端可核实上传 1 个。若发生软件修复或冷恢复，该轮标恢复验证；需要从头成功时，只有在通用修复和分层验证完成后另开新冻结 edition，不能反复重建同一失败任务。

- [ ] **Step 6: 更新事实台账并报告**

在 `docs/active-sessions.md` 和状态文档记录实际完成/未完成、日志和证据路径，明确：有效视频/计划数、accepted 成片数、上传数、当前卡点、最新修复是否真实生产验证、从头还是恢复验证。不提交、不推送。

## 实施边界

Tasks 1–7 默认按编号串行实施，因为 Task 2/6 共享 `film.py`、Task 2/4 共享 episode inputs、Task 4/6 共享 `current_task.py`、Task 5/6/7 共享执行入口；同一文件只能有一个实现者。只读诊断、独立审查和不重叠的历史夹具准备可以并行。Task 8 必须等全部实现和复核完成。任何真实新失败先归入八类清单；只有证明现有清单未覆盖时才增加修复项。
