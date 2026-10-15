# Codex Luna 临时 Agent 入口

本分支的单镜头修复和独立成片复审可显式使用本机 Codex 登录的 `gpt-6-luna`。在所属任务进程将本目录加入Python路径，调用 `luna_sandbox.install()`，然后继续原 `interactive.agent.run`。不修改普通入口、模型配置、GPU判官或H3。

复用既有 `run_agent` 输入副本、技能、当前产出和600秒调用窗；宿主继续决定候选采用、拍摄次数和验收。旧输出、每次原生events及实际模型/用量均留存。直接使用现有登录，不复制凭据。

仅在明确启用 `CODEX_LUNA_ADAPTER=1` 并保留本目录的Python路径时，子Python进程同样安装此备用。小说工程还保留原有整集Episode Agent的Luna入口；本互动分支沿现有逐请求宿主执行。
