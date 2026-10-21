# 后室55包 EP02-A 零介入诊断跑报告(2026-10-22)

目标:当前 main(0725daa)全链,从包到成片一条命令零人工介入跑通,记录每个停点。
现场:codex/backrooms55-ep02-diagnostic-20261022(自 main 新开);官方 CLI + official-ds41 路由;
完整台账 `.runtime/ep02a-diagnostic/event-ledger.log`(45 行,逐时戳)。
并行事实:心动会话生产全程并行运行(共享 H3 池/沙箱 runs 目录),无互相干扰。

## 结论一句话

**"新管线"的原创路径(produce)是一条命令;改编路径(create→生产)没有自动化桥,
创作端在官方路由上无法收尾,生产端能真实产出但会被 no-progress 误杀**。全程 11 个停点,
其中真正的管线缺陷 8 个,人工接驳成本集中在"创作 schema ↔ 编译契约"的断层上。

## 停点清单(按发生顺序)

| # | 层 | 现象 | 定性 |
|---|---|---|---|
| 0 | 环境 | task 相对 source_archive 按 task 文件目录解析,路径翻倍启动即崩 | 配置易错点 |
| 1 | 环境 | 新 worktree 缺 gitignored 画风库 assets/style-library,选风硬失败 FileNotFound | 新环境不可自举 |
| 2 | 健壮性 | create 失败后重跑不幂等,残留目录致 FileExistsError,需手清 | 真缺陷 |
| 3 | — | (诊断者失误)pkill -f 自匹配杀死重启命令 | 记录在案 |
| 4 | 创作端 | 创作者沙箱超时 exit 124(1200s 片长不够跑完 21 镜稿+notes),稿已写完但按进程退出判 agent_failed,无自动续跑 | 真缺陷 |
| 5 | 创作端 | official-ds41 模型名不被沙箱 claude CLI 识别(unrecognized_model,192s 即死) | 路由不兼容 |
| 6 | 创作端 | **校验全过(必读 26/26、零 issue)的候选因进程 exit≠0 被丢弃**,creative_candidate 无法达成 | 设计缺陷 |
| 7 | 创作端 | 切 codex 后端被会话保护拒("先普通重试归档"),而普通重试必死于 #5——路由死锁 | 设计缺陷 |
| 8 | 接驳 | **改编无自动桥**:produce 拒 adaptation(film.py:16)、write_material 也拒;手工 material 需补 segment_id、16 个 shot 字段、source.md、location 严格名、sfx 类型、profile h3 字段——创作输出与编译契约之间约 7 处断缝,EP01 时代是 300 行手写 builder | 根本性缺口 |
| 9 | 生产端 | 启动即崩:荒野采纳并集的 session↔recovery 签名错配(已在 main 修复 074f9aa,并暴露采纳轮 exit=0 误报) | 已修 |
| 10 | 生产端 | **agent 真实工作被 no-progress 误杀**:最后 attempt 提交并完成 job-7(六镜室外光照修正),但 no_progress=2 判"两轮无进展",剩余 1985s 预算作废,8/18 视频,apply 阶段中断 | 核心缺陷 |

另:心动会话全程并行生产(资源共存无冲突,可作为"多会话共享池可行"的正面证据)。

## 生产段实测(修复 #9 后,03:25→04:22)

- prepare/assets:通过(1 角色卡复用 + 7 新场景卡,共享素材库工作正常)。
- 首帧→H3→审修循环:**真实产出 8/18 视频**(单条 15s 视频约 90s 生成),agent 自主完成了
  至少 7 个作业(job-7 为光照域修正,内容判断正确:六镜室外"头灯为主光源"错误描述被改正)。
- 中断点:apply 阶段 no-progress 误杀(见 #10)。

## 建议(按价值排序)

1. **修 no-progress 判定**(#10):把"已完成的 host 作业"计入进展;两 attempt 间隔极短且上一
   attempt 产出了 submission 时不应累计 no_progress。这是生产段能否无人值守的关键。
2. **补改编自动桥**(#8):write_material 已具备 90% 能力,缺的只是 adaptation 分支的
   shot 字段映射(location 归一、sfx 类型、segment_id、h3 profile 注入)。补上后 create→produce
   一条命令可通,EP01/EP02 两代手写 builder 即可退役。
3. **创作端收尾**(#4-#7):校验通过的候选不应因进程退出被丢(先落 creative_candidate 再谈重试);
   官方路由需一个沙箱 CLI 认识的模型名;后端切换的会话归档应允许"上一路由已死"时直切。
4. 幂等重试(#2)与新环境自举(#1)是小修:失败清理 + 画风库入仓或首跑引导。

## 证据

- 台账:`.runtime/ep02a-diagnostic/event-ledger.log`
- 创作候选(已验证):`.runtime/ep02a-diagnostic/creator/sandbox/output/episode.json`
- agent 运行区:`outputs/episode-agent-firstframes/suspense-backrooms55-ep02a-20261022/`
- 本诊断不占用生产预算修复任何画面;未提交推送。


## 修复状态(2026-10-22,main 88704d7,全量 4039 过/exit=0)

| 堵点 | 修复 | 方式 |
|---|---|---|
| #10 no-progress 误杀 | ✅ | `complete_started_repairs` 默认开启;已完成的 apply 修复计入进展;新增不带开关的回归测试 |
| #8 改编无自动桥 | ✅ | 新 `write_adaptation_material`(系列基座合并+候选归一),`produce` 放行带 `series_material`+`source_script` 的改编任务;7 处手工断缝全部产品化 |
| #4/#6 首轮超时弃稿 | ✅ | 首轮完整过验候选容忍沙箱 exit 124;修订轮维持"超时≠改完"严契约(现有契约测试保留) |
| #2 重跑不幂等 | ✅ | create 重跑自动清残留 package 导入 |
| #0 路径解析 | ✅ | source_archive 任务目录→项目根双解析 |
| #1 画风库自举 | ✅ | 库目录整体缺失时容错(文字规则仍绑定);库在样例丢仍按原契约硬失败 |
| #5 模型名不识别 | ⚠️ 配置层 | 代码不改;官方路由需配沙箱 CLI 认识的模型名(agent_phanthy.json 已是可用样例) |
| #7 后端切换死锁 | ⚠️ 未修 | 依赖"普通重试归档旧会话";#4/#6 修复后首轮不再进入该死锁,修订轮场景罕见,暂不动 |
| #3/#9 | 已闭环 | #3 诊断者操作失误(已入档);#9 荒野采纳错配(已在 074f9aa 修) |
