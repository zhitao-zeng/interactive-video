# 外来素材共用接入与修复（2026-10-13）

荒野、恋综、悬疑共用接入资料和原有有限修复循环。没有新增制作宿主；本轮撤回了只给 H3 接完整 episode host 的入口变更。

## 接入

已有荒野、恋综适配器仍负责理解各自包格式，准备结果统一调用 `interactive.intake.finalize`：保留原始文档副本、原文到镜头映射、剧本、分组、人物/场景/道具、声源设定、字幕/证据文字与互动选项。原始声源标签和制作声源绑定分别记录，不把派生绑定冒充原文。已有输出不自动重导入。

三种题材也支持 `scripts/interactive_video.py prepare --project <adventure|romance|suspense> --material <材料目录> --edition <新目录名>`。材料目录使用已有的 `material.json`、`source.md` 合约：profile、bible、script、groups、voice_specs、interaction、overlays；可选 evidence_surfaces、source_archive、source_seconds。保留完整 shots 原字段，包括可见人物、穿戴、物品状态和原文引用。groups 必须按顺序覆盖所有镜头，单组仍为 4–15 秒、最多 6 个阶段。

参考图通过 reference_images 的 source/path/scope 显式导入；identity_image 保留旧旅行包用法。声线采用 sample_text/prompt/picture 的既有合约。悬疑包仅解压不等于已生成、已核准的分镜，仍需按原素材整理 material。

`assets --project ... --edition ...` 使用现有素材/声线服务；`run --project ... --edition ... --lane h3|sd25` 使用相同制作入口。以上命令会制作资产或视频，本轮没有执行这些生成命令。

## 修复

两个模型都进入既有 reviewed → managed → repair.agent 循环，沿用原 episode 的实际任务、片段、repair_history 和最多 3 次生成上限。仅在执行该循环期间选择模型和 sandbox_agent 后端，不修改共享配置。H3 的原始翻译准备仍保留。

- 共用：原始文档、相邻分镜、声源/互动资料、实际参考图、当前/历史 take、失败诊断、候选审核与生成预算。
- H3：修改实际英文请求，保留六段结构、角色/音频标签、台词和时长。
- SD2.5：修改实际中文请求，不读取共用素材中残留的 H3 文本作为发送请求。程序锁住非阶段行、图片/音频绑定、阶段标题、台词和声音段，只允许修正阶段画面描述；仍需参考图一致性检查。
- stop 或候选未过，保留阻塞原因；相同输入的已拒候选不会因续跑再启动一轮 Agent。达到原视频上限不再调用修复 Agent。

中文作者分组通过 source_authored_segment 进入既有作者分镜重编译通道，不再依赖英文来源标记。

继承的 episode Agent 输入快照也补齐上述资料，供已有调用者使用；互动 run 本轮没有切换成新的完整制作宿主。

## 验证与边界

本轮完整本地回归 3231 项通过（74.07 秒），验证双模型分流、原文交接、受保护内容、候选发布、旧输入/预算保留。没有启动真实沙箱、生成视频、改远端服务或充值；本地测试不代表成片质量已经改善。SD 额度仍保持用户选择的暂停状态。

这不是“任何问题都会自动修好”：修改对白、穿戴绑定、原始事实等超出最终请求编辑范围的问题会明确停止并交回源输入修复；H3 翻译准备失败仍按原流程报错。后期缺字、静音及已生成画面缺陷没有在本轮重新制作。不要声称已经完整复现长剧所有生产能力。

## 同步

仅同步本轮增量补丁，不覆盖整个工作目录，不带输出、配置、历史或在途板其他行。同步文件见 `delivery/adventure/common-intake-sync.patch`，接入其他会话前先比对它们自己的未提交改动。存在上下文冲突则按改动意图合并，不能强制覆盖。同步本身不授权新的生成、提交或推送。

## 2026-10-14 真实验证更新

首次实测暴露抽帧未交接、Agent超时。本轮已接入同take抽帧与时间、对应请求/ASR，并附标准库帧查阅工具；只支持选择已提供帧，不声称任意时间解码。相同请求的续跑复用原审核。随后Agent在575秒内交出候选，荒野第26段自动修正英文空镜错误、重拍一次并复核通过；仍使用600秒及原视频预算。累计34→35，其余30段不变，SD未跑。整片仍有静音/静帧/后期文字问题。详见 delivery/adventure/laughing-country-h3-768/frame-handoff-evaluation.md。

## 2026-10-14 悬疑工作目录同步

已在 codex/suspense-reference-study-20261013 同步共用 Agent 修复入口、双模型请求适配和同 take 抽帧交接。保留悬疑已有 prepare/声音入口与 suspense_groups 专用打包，同时支持新的 --material/--edition 合约。明确传入 project=suspense，避免把 edition 名误当项目名。已有 runtime 中 run.py 是上轮执行记录；后续从 scripts/interactive_video.py 的 run --project suspense --edition ... --lane h3|sd25 入口执行，--resume 保留原任务与预算。

3228 项全量本地回归通过。使用本集 clip08 实际已有视频验证导出：6 张带时间点审片帧、请求、ASR 与资料可读；仅模拟沙箱返回以验证交接，没有调用模型或启动新生成。原审片范围修正和证据叠字仍保留。整片连续性、声音及后期问题尚未因此自动接回重拍循环。本次未改旧版、历史、预算或共享配置，未提交推送。
