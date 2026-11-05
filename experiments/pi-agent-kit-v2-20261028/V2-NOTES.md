# Pi agent kit v2（2026-10-28）

来源：`pi-editable-starter-20261028/examples/pi-video-agent`（评审过的可编辑版，7 项离线测试）。
在其上加了两处本项目需要的能力，其余保持源码形态。

## 合并进来的

- `template/tools/media.py`：显式参考图与用途、`wire/request.json` 实际附件留证、
  `wire/response.json` 独立 task id、同一输出第二次 POST 直接拒绝、输出已存在不静默返回缓存。
- `template/tools/assemble.py`：取消固定镜头数；拒绝覆盖已有成片；改剪辑必须新文件名。
- `starter.py` / `tools/agent.mjs` / `tools/runtime.mjs`：可编辑工作副本、原生 read/write/edit/bash、
  原生会话 `--resume`（`session-path.txt`）、init 不覆盖已有目录。
- `template/SYSTEM.md` / `SKILL.md`：去掉固定制作流程；会话结束不等于成片通过。
- `test_starter.py`：9 项离线验证（原生 edit/bash/resume、图片附件、终态失败不重发、
  未知提交不重发、SD 素材库 + first_frame、H3 keyframe、assemble 旧成片保护、
  `--previous` 同集复用清单、资产缓存只补缺）。
- 本项目追加：GPU Eureka 默认 `audio-runtime.json`（`device=cuda`、显式 `cuda_visible_devices`）；
  `skill-extra/references/interactive-leaderboard.md` 并链接进 SKILL；
  `starter.run` 剥离会话代理（与 pi-agent-kit 启动器一致）；
  `starter.init` 复制 skill-extra 与 audio-runtime。

## 验证

- 9 项测试：`PI_STARTER_SDK_ROOT=.../main PI_STARTER_NODE_MODULES=.../pi-agent-kit-20261028/code/node_modules pytest test_starter.py -q` → 9 passed。
- 单段 smoke（EP04C）：agent 用原生工具 + `tools/media.py`；两张卡 Active；s01a 生成后发现
  背包重复/语速问题，主动用新文件名 s01a-v2、s01a-v3 重拍，没有覆盖旧片或复用旧成片；
  audio 走 GPU。smoke 尚在收尾。

## 复用

- `starter.py init --previous <dir>`：把同一集上一次尝试只读链接到 `input/previous/`，并写
  `input/PREVIOUS.md` + `input/PREVIOUS-MEDIA.tsv`（路径/字节/修改时间）。只用于同一集；
  agent 需逐条看帧、听声音、确认与本次参考一致后采用，并在工作记录里注明来源。不同集/不同 W 不用。
- `series-assets/<系列>/`：按系列分开的跨集稳定资产；当前 `zhiyao/` 是知遥身份基准 + 综合视觉卡及
  `.asset.json`。`init` **不默认注入**，需要时显式 `--asset-cache series-assets/zhiyao`；
  同名已存在则不覆盖。sidecar 让同系列新一集直接复用 `asset://`，不重复 CreateAsset。
  本集新角色/新场景仍放该集 `input/`；其他项目/系列建自己的 `series-assets/<name>/`。
- `template/tools/plan.example.py`：可选的镜头表→请求 JSON 构建示例，只写请求不提交；
  会把 SD 参考音频总时长 >15.2 秒直接拦下。不是必须遵循的格式。
- `template/TOOLS.md` 增加“已验证的硬限制”：SD r2v 参考音频合计 ≤15.2s、SD 首帧独占、
  H3 的 references/purposes/audios/first_frame 顺序、真实 H3 最小请求示例。
- `starter.py init` 会从 `configs/pipeline.json`（SDK worktree 或 canonical novel-manga-video）读取
  `asset_publish_dir` 写入 `local.json`，避免新工作区再踩公开参考图 404。
- 当前 EP09A/W22 在途 workspace 未做任何热替换；新能力只对下一次 init/resume 生效。

## 配乐（可选）

- `template/tools/media.py` 新增 `action:"music"`：走 SDK 的 `music_selection.search`，只返回本地曲库候选，
  不自动采用；候选带 license/attribution/audio_evidence/review_status。
- `template/tools/assemble.py` 支持 `music`：必须 `review.status="accepted"` + `reason`，支持 gain/loop/fades，
  写 `.music.json`；未审核会拒绝。
- 真实检索 61 首库：EP09A 的“安静邮局/温暖记忆/稀疏钢琴”只返回悬疑、失恋、明亮合成器；
  W22 的“no vocals”查询返回两首有人声的未分类曲目。结论：61 首能跑流程，不够稳定选配；
  详见 `MUSIC-NOTES.md`。当前两集未加 BGM。

## 不合并/保留

- 音频默认 CPU：不采用；v2 默认 GPU，CPU 仅作回退。
- 自定义工具注册：v2 不注册媒体工具，统一走 `tools/media.py` 脚本；与在跑的 kit 不同。
- 未做新整集真实验证；不要热替换 EP08B/W22 在途任务。

## 音频服务

- `template/tools/audio_service.py`：常驻 Eureka 模型、FIFO 队列、`/audio` 提交、`/job/<id>` 查询、
  `/health` 队列深度、`--allow-root` 路径白名单。
- `template/tools/media.py audio`：设置 `AUDIO_SERVICE_URL` 或 `local.json.audio_service_url` 时走服务；
  提交不可达才回退本地 worker；已提交成功后只轮询，不重复提交。
- `starter.py init` 写入空的 `audio_service_url`；`TOOLS.md` 有启动命令。

## 请求体预算 guard

- `template/tools/request_budget.mjs`：在 `runtime.mjs` 的 `onPayload` 里估算 JSON 字节数；
  超过 `max_request_bytes`（默认 10,485,760，即 10 MiB）时，保留最后 `keep_recent_images` 张图片，
  把更老的 `image`/`image_url` 块替换成文本占位；仍超时再截断老消息里的长文本。
  只影响单次 provider 请求，session JSONL 原文件不变。
- 单元测试 `template/tools/test_request_budget.mjs`（bun）：超限时 10 张图中保留最后 2 张、
  前 8 张替换为占位，文本截断；`under-budget` 不改动。
- `starter.py init` 新工作区写入 `max_request_bytes=10485760`、`keep_recent_images=4`。
- EP09A 工作区已同步 guard/local.json；当前运行中的 session 是旧加载，若需要生效需重启/resume。

## 网关请求体上限（实测）

`tools/probe_gateway_limit.py` 用无效模型名 + 纯文本 body 二分探测 chat completions 网关：
1/2/4/8MB 返回 503 model_not_found（未过 nginx 限制），16MB 返回 413；二分结果
**11.75MB 通过、12.0MB 被 413**，即 nginx 上限约 **12 MiB**。没有触发真实生成。
guard 默认因此设为 **10 MiB**（`10,485,760`），留 2MB 余量。

## 声线形容词（2026-10-31）

用他的糖果短片 P00 做同镜对照时发现：我们的构造里没有“声线形容词”，模型默认给了一个
更成熟、更快的女声。同一句「本鱼带你吃个够！」——写了 `high-pitched, bubbly, childish
cute girl voice, eager and delighted` 的 take 是 286 Hz / 3.65 字每秒；没写的 take 是
254 Hz / 5.07 字每秒。两条的句子数量不同，只作方向性证据。

改动：
- `skills/episode-production/references/sound-direction.md` 新增 `Per-character voice definition`：
  每个说话角色在 `subject_definitions` 写一次固定英文声线（音高/音色/年龄感/说话方式），
  全剧保持一致；每句的 `emotion` 只写这句话在做什么，两者不互相替代。没有 `<Audio>` 锚时
  这句是唯一的声音身份；有锚时它防止换 take 重新选角。
- `references/h3-format.md` 的 Timeline and voices 示例后加了同一条指针。
- `template/TOOLS.md` 的 H3 硬约束加了一条；`template/SKILL.md` 的声音入口补了一句。

受控验证（同日，同一请求只加一句声线，其他一字不改）：254 Hz / 5.07 字每秒 →
296 Hz / 4.67 字每秒，起伏 8.5 → 8.0 半音，SD 2.8 → 2.5。他的参考 take 是 286 / 3.65，
句子数量不同，只作方向。台词位置没有因此提前（仍在 5.7 秒后），说明声线只改音色和节奏，
不改语音分布。
