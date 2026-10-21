# WIP 合并顺序方案(2026-10-21)

目标:把 2026-10-16 主线切换(34f8c34)之后积累的全部未提交成果,按归属分片提交进 main,
不打扰仍在途的会话,不改任何生产产物,不推送(本项目无远程,by design)。

依据:worktrees/main 集成区现状(128 个已跟踪修改 + 87 个新文件)、各 worktree 与集成区的
内容级 diff(2026-10-21 实测)、docs/active-sessions.md 最新登记、各任务 .runtime 证据文件。

## 已核实的关键事实

1. main 自 34f8c34(10-16)后零提交。所有成果以未提交 WIP 存在。
2. 各功能 worktree 的基线是"main 当前脏区的拷贝 + 本会话 owned 增量",因此文件路径与 main
   几乎 100% 重叠;真正的差异是内容级的(实测每树 4–63 个文件内容不同,含各自板书、
   配置漂移和尚未回传的 owned 增量)。
3. worktrees/main 的脏区是**权威集成态**:各会话的"最小差分接 main"都打在这里,
   最近一次全量回归(在途板 10-21 登记)397 项通过。
4. codex/adventure-demo(fcd6787,主仓库 checkout)**不在 main 祖先内**。其上约 15 个提交
   (Phanthy 网关、判官默认 ds41、LLM 超时缩放等)内容大概率已随 shared-pipeline 进入 main,
   但提交层面未合,需按内容对账。
5. 荒野分支 e435b57 已在 main 内;其 worktree
   (/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm)仍有 72 改 + 37 新的 WIP 未进 main。
6. 4 个 worktree 完全干净(audio-model-eval / laughing-country-voice / romance-voice /
   reference-watch):纯实验,记录都在 .runtime(gitignored),无需合并。
7. interactive-video-worktrees/tmp 属于父工作区仓库,与本仓库无关,不处理。

## 冲突面(不要按 worktree 逐个合并这些文件)

以下文件被几乎所有会话改过,逐树合并必然冲突。**处理原则:以 worktrees/main 当前内容为
准,最后一次性提交**,不引入任何 worktree 的版本:

- docs/active-sessions.md(每会话各自追加了板书)
- docs/production-lessons.md(追加式经验)
- configs/interactive.json、configs/pipeline.json、configs/agent_sandbox.json
- scripts/interactive_video.py、README.md
- skills/episode-production/SKILL.md 及 references/ 下多人共改的文件
- projects/romance/xindongdaiding-v1/(864K 纯 JSON/MD 生产状态,单写者=xindong 会话)

delivery/ 已被 .gitignore 覆盖(第 13 行),产物不会误入。

## 阶段 0:冻结与登记(先决条件)

1. 在在途板宣布开始合并,请三个在途会话收工销账:**firstframe-review-fix-20261021、
   source-scope-luna-20261021、xindong-ep01-fast-20261021**(以板上最新状态为准;
   xindong 生产回修与 workflow-closure 五镜已按 10-21 末次登记结束,开工前再核一次
   `ps aux | grep -E "interactive_video|episode_agent"`,确认无生产进程)。
2. 归并在途板:worktrees/main 的板为正本;把主仓库(interactive-video-studio)未提交的
   荒野"撤离误报/524 本轮停止"登记并入正本(该条目尚未进入 main 板)。
3. 在途会话未收工前,阶段 1 不开工。

## 阶段 1:分片提交 main 集成区(核心)

在 worktrees/main(分支 main)上,按下列顺序逐片 `git add <归属文件> && git commit`。
归属依据=各任务的 owned 清单/patch(列在"证据"列),消息引用分支名与证据路径。
无法归属的文件留到最后 S16/S17 兜底,逐个人工看一眼再提交。

| 序 | 片 | 归属分支 | 证据(owned 清单) |
|---|---|---|---|
| S1 | DS1017 切换(configs/llm/transport/测试,7 文件) | codex/ds1017-api-20261019 | .runtime/ds1017-api-20261019/result.json 的 paths |
| S2 | 镜头语言 skill(5 类导演拍法+读取观测修正) | codex/director-camera-skills-20261016 + camera-use + camera-corridor-film | 三树 result/板书;残留差异仅 5–17 文件,以 main 为准 |
| S3 | 风格包(7 包+creation 接线) | codex/style-packs-20261019 | .runtime/style-packs-20261019/result.json |
| S4 | SD2.0 Fast/Mini 通道(10 文件) | codex/sd20-channels-20261020 | .runtime/sd20-channels-20261020/integration.patch |
| S5 | 心动系列:common 输入交接/首帧/提示词修复/尾帧/尾卡/Fast 入口 | codex/xindong-pilot→firstframe→prompt-fix→tailframe→card-fast(按日期序) | 各树 .runtime/*/result.json;card-fast 为 15 文件最小差分 |
| S6 | 音乐:曲库+选曲装配 | codex/music-cues-library-20261020 + music-selection-integration-20261020 | 板书"32 文件最小差分";scripts/music_library_worker.py、soultuner_recommend.py |
| S7 | Prompt/Skill 收拢(24 文件) | codex/prompt-skill-unify-20261020 | 板书;两处 RED→GREEN 复核记录 |
| S8 | 原创从零交接 CLI | codex/original-paper-pilot-20261020 | 板书"交接 82 项+SDK 57 项通过" |
| S9 | 后室:hybrid 声音策略 + 55 包授权入口 | codex/backrooms-hybrid-full-20261020 + backrooms55-ep01-20261020 | 两树 .runtime;板书"最小差分接 main" |
| S10 | 首帧逐镜流水+并行+图片授权 | codex/first-frame-parallel-20261020 + first-frame-image-grant-20261021 | parallel:result.json 的 files 清单(9 文件);grant:owned.patch(3 文件+测试) |
| S11 | workflow 闭环四件套 | codex/workflow-offline-acceptance→official→final-audio→integration(按日期序) | official 为 48 文件 owned 增量,板书记录;final-audio 仅 5 新文件 |
| S12 | 管线耗时优化+修复(14 文件 owned) | codex/pipeline-latency-review/media + pipeline-repair | main/.runtime/pipeline-latency-20261021/final-result.json |
| S13 | 首帧官方指南接入(8 文件) | codex/firstframe-prompt-guide-20261021 | 板书 19:04 条目 |
| S14 | 首帧误放行修复+源审范围修复 | codex/firstframe-review-fix-20261021 + source-scope-luna-20261021 | **等两会在途会话收工后再切此片** |
| S15 | xindong-ep01-fast 生产代码(Fast8/Luna 切换/B 有界恢复) | codex/xindong-ep01-fast-20261021 | 板书 16:35 条目;等生产收尾 |
| S16 | 共享收口单笔:上面没人认领的 configs/styles/*.json、scripts、src 残差 | 集成残留(逐文件过目) | — |
| S17 | 生产项目数据:projects/romance/xindongdaiding-v1/(864K) | xindong 会话 | 纯 JSON/MD,无媒体 |
| S18 | 文档收尾:production-lessons、两板归并后的 active-sessions、music-selection.md、romance-firstframes-and-cards.md、docs/superpowers/*、本方案 | 全体 | — |

**每完成 S4、S11、S16、S18 各跑一次全量回归:**
`PYTHONPATH=src .venv/bin/python -m pytest tests -q`(在 worktrees/main)。
回归基线以板上最近绿数(397 项)为准;红了停下定位,不强行继续提交。

## 阶段 2:回传各树未集成增量

对每个仍有内容差异的 worktree(实测差异 4–63 文件,见下),在其**收工后**执行:

```
for f in <该树差异文件>; do diff <worktree>/$f main/$f; done
```

- 差异属于"该树板书/配置旧版本"→丢弃(以 main 为准);
- 差异属于 owned 增量且 main 缺失→补进 main,追加对应片提交。

实测差异基线(2026-10-21,含板书噪音,仅作核对起点):
prompt-skill-unify 59、xindong-prompt-fix 63、sd20-channels 58、xindong-pilot/card-fast 59、
music-selection 58、hybrid 55、parallel/grant/original 55、cues 54、backrooms55 53、
firstframe 53、repair 53、latency 50、tailframe 49、review-fix 45、ep01-fast 44、
luna 41、offline 28、romance-sd25 25、official 15、integration 16、corridor 17、
ds1017 20、camera-use 10、其余 ≤7。

## 阶段 3:adventure-demo 游离提交对账

`git -C worktrees/main diff main fcd6787 -- src configs`(内容级)。shared-pipeline 整合时
内容应已带入;逐文件确认后,在板书记录"adventure-demo 内容已并入 main,提交层面不追合",
该分支此后仅作历史保留。若发现 main 缺失的实质内容,单独 cherry-pick 对应提交。

## 阶段 4:荒野 worktree WIP 对账

worktrees/interactive-adventure-rhythm(72 改+37 新)与 main 逐文件 diff,同阶段 2 规则。
注意其中 .codex/skills/ 与 projects/adventure/ 部分是生产侧文件,先问清归属再动。

## 阶段 5:清理

1. 零内容差异/纯实验的 4 个 CLEAN worktree(audio-model-eval、laughing-country-voice、
   romance-voice、reference-watch)确认 .runtime 记录已按项目惯例归档后
   `git worktree remove`(分支保留)。
2. 阶段 2 完成且差异归零的 worktree 逐个 remove,分支保留。
3. interactive-video-studio 主 checkout 恢复到干净(仅保留板书正本迁移后的状态),
   或按用户意愿切回 main。
4. 不 push、不加远程(仓库规矩)。

## 阶段 6:本地备份(建议,可选)

合并完成后打一个本地 bundle 作为单盘故障保险(不违反"无远程"约定):
`git -C worktrees/main bundle create /mnt/disk2/zengzhitao/backups/interactive-video-studio-$(date +%Y%m%d).bundle --all`

## 禁止事项(全程)

- 不 reset/checkout -- 任何 worktree 的 WIP;别人的未提交改动始终是别人的工作。
- 不清空 outputs/.runtime/delivery;生成媒体与运行记录一律不进 Git。
- 在途会话未销账前,不提交它拥有的片。
- 回归红了不提交;测试与"成片验收"分开表述,不用代码绿冒充内容通过。
