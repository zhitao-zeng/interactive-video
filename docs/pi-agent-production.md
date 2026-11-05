# 完全 AGENT 生产入口

新任务默认使用 `scripts/pi_video_agent.py`。一个创作者/导演 Agent 在独立工作副本中自主创作、调用媒体工具、检查、修复和剪辑；不使用旧 host phase/check/apply 路由。

旧 `produce/produce-batch` 只用于既有 edition 的原目录、原计数和原会话恢复。`legacy/host-production-20261102` 保存切换前的提交点。不得热换正在运行的旧任务。

## 初始化

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py init /path/to/new-workspace \
  --sdk-root /mnt/disk1/zengzhitao/interactive-video-worktrees/main \
  --dependencies /path/to/existing/pi/node_modules \
  --input /path/to/original-input \
  --lane MiniMax-H3 --first-frame selective --interaction-page required \
  --viewpoint auto
```

`--asset-cache` 只用于同系列明确允许的参考资产；`--previous` 只复制同一集的上一次尝试为当前工作区私有副本，不能跨集复用。init 不覆盖已有目录，也不生成媒体。

`--viewpoint` 可选 `first-person/mixed/auto`，默认由导演按戏决定。制作说明进入首次请求；resume 不热加载修改后的 TASK 或 PRODUCTION。

首次 run 前必须填写工作副本 `TASK.md`：目标、允许改编范围、时长/画幅、模型、交付目标和授权边界。未填写的模板会被拒绝启动。`--wall-seconds` 与 `--max-model-requests` 在 init 时冻结硬限额；文字说明不能替代这些限制。

## 启动与恢复

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py run /path/to/new-workspace --endpoint official-ds41
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py run /path/to/new-workspace --endpoint official-ds41 --resume
```

使用工作副本自己的代码、原生会话和已冻结预算。恢复不重置请求计数或媒体额度。模型、输入、通道、首帧策略和互动页在启动前确定，不由 Agent 在运行中自行切换。

### Bash 等待上限

原生 bash 工具的 `timeout` 参数是可选的，缺省时**不装任何定时器**。S2E03A 因此付过代价：
一条无 timeout 的 `find /` 单次跑了 11960.6 秒（3 小时 19 分），直接把 4 小时墙钟耗尽，
48 条 take 与 `build/cuts-v1.json` 都在，但没有合成，整路后续停派。

`template/tools/runtime.mjs` 在既有的 `beforeToolCall` 里钳制每次 bash 调用
（`template/tools/bash_limits.mjs` 提供纯函数）：

- 没写 `timeout` → 用 `local.json` 的 `bash_timeout_seconds`，init 时冻结，默认 900 秒。
- 写了但超过本 run 剩余墙钟 → 压到剩余值，永不超过。
- 写了且装得下 → 原样保留，长媒体操作不受影响。
- 只向下钳制，且返回值不小于 1，已过期时仍能让命令报告而不是挂住。

每次钳制都会往 stderr 打一行 `[bash-timeout] <explicit|default> <old>s -> <new>s (<原因>)`。
复用的是 `run-budget.json` 已有的 `deadline_at`，没有第二个截止时间来源。

回归：`template/tools/test_bash_limits.mjs`（纯函数）与
`template/tools/test_bash_timeout_wiring.mjs`（真实原生 Pi 会话 + 假 SSE）。
后者同时支持 Node 与生产运行时 bun：Bun 忽略 `NODE_OPTIONS=--import`，必须显式
`--preload`，脚本按 `process.versions.bun` 自行选择；Node < 22 无法加载 SDK 时打印 skip
而不是失败。两个运行时都已实际跑过同一组三例。

### 窗口续期

墙钟耗尽后 `run` 会在第一次请求前抛 `RUN_BUDGET_EXHAUSTED`，此时需要用户授权的续期文件。
在该 workspace 写：

```json
{"seconds": 3600, "authorized_by": "用户批准修 S2E02A 的 blocking", "reason": "独立审片 rejected"}
```

再 `--resume` 即生效。规则：

- **一次性**：生效后文件写入 `consumed_at`，同一份文件再 resume 不会加时间。
- **只加时间，不加次数**：`request_limit` 不变；请求数用完仍需另批。
- 必须带 `authorized_by`，否则拒绝执行。该字段只是**审计记录**，不是授权来源：
  谁批准、批了多少，由用户在主控会话决定；生产 Agent 不自行写这个文件。
- 续期记录写入 `run-budget.json` 的 `window_grants`，含授权人、原因与旧 deadline。
- 续期不改变请求数、限额、`ended_at` 以外的原结束记录、历史回执与已有的失败记录；
  也不会让旧任务自动获得续跑资格——过期任务仍停在明确未补证状态。

独立审片判 `rejected` 后派回原导演修复时，通常就是靠这个文件把窗口接上。

## 独立审片

`init --role reviewer` 起一个只出判词的审片 Agent：加载 `template/REVIEWER-SYSTEM.md` 与
`skills/episode-review`，`media.py` 只接受 `inspect` 与 `audio`（抽帧、Eureka 音频感知），
生成与上传动作在入口被拒绝。它在 init 时就写完整 `TASK.md`（成片路径、SHA256、剪辑时间表、
基准剧本、授权边界），因此可直接 `run`。

**严重程度与修复方分开**，不要合成一档：

- `severity`（`blocking` / `advisory`）决定 `verdict`。有实际观看阻塞就 `rejected`。
- `owner`（`episode` / `tool` / `series`）决定派给谁修。

一条问题可以既严重又由工具修——那一版仍是 `rejected`，只是修复落在工具维护者身上；
反过来，共享工具的轻微行为也不因"是共用代码"就自动升级成阻塞。按实际观看影响判断。

改工具或改系列约定不会改变已经导出的那份 MP4：判词只判当前文件，修复后是新导出、新哈希、需重审。

判词必须绑定成片 SHA256。声音覆盖要按证据分开写：Eureka 按窗口（写出实际合并区间与
未覆盖秒数）、ASR 是否整轨、电平测量是否整轨——三类不能合并成一句"覆盖完整音轨"。

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py init /path/to/review-workspace \
  --sdk-root /path/to/frozen-sdk --dependencies /path/to/node_modules \
  --role reviewer --review-film /path/to/movie.mp4 \
  --review-timeline /path/to/movie.mp4.timeline.json \
  --review-baseline /path/to/02-script.json --review-brief "受保护内容与授权边界" \
  --wall-seconds 7200 --max-model-requests 96
```

## 批量生产

`batch` 先完整校验整个 manifest，任何错误在任何 init 或生产调用前报错，且不生成 workspace 或 `batch-state.json`。校验范围包括：

- `workers` 必须是正整数，默认等于集数；`0`、负数、非整数或布尔均拒绝。
- `resume` 必须是真正的 JSON 布尔，`"false"` 等字符串拒绝。
- 每集必须提供 `workspace`；非 resume 集必须能从顶层或单集拿到 `sdk_root` 与 `dependencies`。
- workspace 解析后不能重叠（相同、父子目录均拒绝），siblings 允许。
- `lane`、`first_frame`、`interaction_page`、`h3_mode`、`viewpoint`、`wall_seconds`、`max_model_requests` 等 init 选项均须合法。
- `FastH3-Omni` 必须显式提供 `fasth3_endpoint`，且 `first_frame=required` 与其不兼容；endpoint 按 SDK 规则规范化。
- **非 resume 集必须提供 `task` 或 `task_file`。** `init` 复制的是空白 `TASK.md`，`run()` 会拒绝它；
  没有任务文本的批次注定在 init 之后失败，所以在任何 init 副作用前就报错。`resume` 集不检查这一项，
  它们的 TASK 在更早的一轮已经授权过。这一项最后检查，其他具体 manifest 错误保持原有优先顺序。

校验通过后，batch 获取 manifest 同级目录 `batch-state.lock` 的排他文件锁；第二个同目录批次在 init/run 前即被拒绝，锁在批次结束时释放。状态文件写入失败属于批次基础设施错误，不会冒充 accepted 或完整结果。

然后按 manifest 顺序逐集 init，再用 `ThreadPoolExecutor` 并行调用各集 `run`。每集保留独立的 workspace、session 和 budgets；一集失败不会阻塞或取消其他集。

```json
{
  "sdk_root": "/mnt/disk1/zengzhitao/interactive-video-worktrees/main",
  "dependencies": "/path/to/existing/pi/node_modules",
  "workers": 2,
  "episodes": [
    {"workspace": "/path/to/episode-A", "input": "/path/to/input-A", "endpoint": "official-ds41",
     "task": "授权任务文本（写入 TASK.md）"},
    {"workspace": "/path/to/episode-B", "input": "/path/to/input-B", "endpoint": "official-ds41",
     "task_file": "/path/to/episode-B-task.md"}
  ]
}
```

单集 init 也可以直接带上任务文本，省掉手工编辑 `TASK.md`：

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py init /path/to/episode \
  --sdk-root /path/to/sdk --dependencies /path/to/node_modules \
  --input /path/to/input --task-file /path/to/task.md
```

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py batch /path/to/batch.json
```

已有工作区必须显式 `resume=true`，不存在的恢复目标会拒绝。每集完成后立即在 manifest 旁写入 `batch-state.json`（原子写），记录该集 workspace、returncode 与 completed_at；这只是进程完成状态，不是内容 accepted。最终输出保留 `episodes`/`ok` 并新增 `workers`，退出码只反映进程是否全部退出 0，不能当作内容通过。batch 不调度内容阶段，也不伪造 accepted 状态；每集结束后由协调者分别审核、恢复或上传，不按整批同步等待。

使用 `FastH3-Omni` 时，UniServe 服务应在批次级常驻，Agent 只调用其 API；batch 不会为每一集单独启停服务。

## 可选 FastH3-Omni lane

显式选择 `--lane FastH3-Omni --fasth3-endpoint http://127.0.0.1:30027`，媒体请求使用 `action=fasth3`。batch 可在 manifest 顶层或单集设置 `fasth3_endpoint`。endpoint 会冻结到 `local.json`、`PRODUCTION.md` 和 `CAPABILITIES.json`；没有机器默认值，`h3_mode` 不影响 Fast 路由。请求 endpoint 规范化后必须等于冻结值，userinfo/query/fragment 会在写 config/request 前拒绝。此版只接受无需 Bearer 的内部 UniServe 端点，不增加密钥配置。默认 lane 及 image/video/h3 分支不变。

Fast 使用 UniServe 的 `model=FastH3-Omni`、`task=ref2va`，保留原 prompt、参考顺序和图片/音频原字节 data URI，不隐藏缩放或换格式；`target.short_edge/aspect_ratio/duration_seconds` 与显式 seed 原样透传。只接受普通 reference：显式 `first_frame` 会拒绝，`first_frame_policy=required` 不兼容；`selective` 不等于已验证 keyframe。

UniServe 服务必须另行启动，本代码不启停服务。请求保存 request SHA、实际 target、task id、状态历史、附件 SHA、耗时和 HTTP 错误正文；下载先写 `.partial` 再替换。相同 request SHA 且已有 task id 时只轮询原任务，服务重启会清 jobs/content，404 只表示过期并停止，不隐式重新 POST。Agent 若决定新 take，必须使用新输出名并受原任务预算约束。Fast 不代表普遍优于 Turbo；music_selection 不变。

## 2026-11-05 已运行改进的主线入口

- 普通 `init` 会读取同一个 SDK 的现有 `music_selection` 配置，把实际曲库路径、数量和是否配置写入 `CAPABILITIES.json`。导演使用已有 `tools/media.py action=music` 检索并自主选择、试听和混音；没有配置时如实跳过，不搜索全盘、不下载曲库、不自动采用音乐。配置方式见 [music-selection.md](music-selection.md)。曲库和服务配置仍只放本地，不随 GitHub 发布。
- `AUDIO_SERVICE_URL` 在 init 时写入任务；声检支持可选 `question`。常驻服务沿原 job 查询，不因超时再起一个本地模型。CPU 回退限制线程并隔离视频 GPU；所属 Agent 结束后仅清理其遗留音检进程，原请求和已有结果保留。
- 当前已准备批次的唯一调度入口是 `python scripts/pi_episode_lanes.py /path/to/batch-run`。它读取已有 `state.json`，并行调度三路制作和独立审片；不是把 `pi_video_agent.py batch` 的进程退出状态当作审片结果。不要为同一批次同时启动两个生产调度器。
- 调度脚本会优先使用该批次原来的 `frozen-main`、`frozen-tools-v2` 和 reviewer snapshot；没有这些历史目录时，使用本主线的 starter 和模板，不要求复制旧批次目录。运行中的任务仍用自己的工具副本与原会话；合并代码不会热换它们。
- 独立审片只拒绝影响观看的问题。通过必须绑定当前实际导出；轻微问题写建议；不完整证据或审核工具错误不等于内容拒绝或通过。修好后的新导出优先复审，不重新付费审同一份被拒字节；已核实只改声音时可复用未变画面的证据和限制。
- 内容拒绝交原导演 `--resume`，保留原始会话、素材、回执、请求数、take 限额与旧版。LLM 连接中断只做一次有原预算余额的续接；不会用新会话代替原任务。
- 已成片的阻塞回修，只有该批次存在人类明确批准的 `repair-time-policy.json`，且 episode 在 `eligible_episode_ids` 内，才可在原墙钟耗尽后写一次待消费 `window-grant.json`。本批授权是一次 900 秒，不写成全局默认；待实际修复启动才消费，不在排队期间计时。原请求与媒体次数上限不增加；无片旧失败、已用过加时或请求已耗尽都不会被自动续期。

本次整合包含本地回归与真实初始化，不重新提交媒体或重审旧片。之前真实的 BGM 混音与独审、原导演回修后复审通过记录仍保留在原批次，不能把代码合并称为新一轮从头无人验证。

## 验证边界

原生工具测试、MockTransport 和 FFmpeg 只证明接口与本地执行。W24 和后续短片证明自主路线的局部可行性，不代表所有题材、整集质量或运动能力已通过。正式交付仍需实际媒体检查、正式音画终审和可核实发布；会话退出、候选文件或 ASR 都不是整集成功。
