# 可修改的 Pi 视频 Agent 初版

给 Agent 一套起步代码和完整制作资料，由它自主创作、调工具、检查与剪辑。没有固定镜头数、生产阶段、候选次数或 finish 路由。保留原生 read/write/edit/bash、原生会话与 compaction。正常退出仅表示会话结束，不自动宣称成片通过。

复用了并行 worktree `pi-experiments-20261028/.runtime/pi-agent-kit-20261028/code/` 的 Pi 1.4.0 接入、SD 首帧、素材上传、Eureka GPU 听审写法。去掉其固定两张知遥图、固定剧集指令、assemble 缓存和 finish 验收。H3 通过现有媒体 SDK 调用。旧运行、原素材、在途请求均不修改。

**这些是可编辑的起点，不是仅允许 Agent 填参数的工具。** 初始化后 tools/、SYSTEM.md、TASK.md 和 skills/ 都在任务目录中；后续运行只加载这份工作副本。init 拒绝覆盖已有目录，resume 不重新复制模板。共享 SDK 与 node_modules 仅复用，项目 Python 环境不安装新依赖。

本机初始化示例（只复制文本与输入，不启动 LLM 或付费生成）：

```bash
/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python examples/pi-video-agent/starter.py init .runtime/pi-editable-v1 \
  --sdk-root /mnt/disk1/zengzhitao/interactive-video-worktrees/main \
  --dependencies /mnt/disk1/zengzhitao/interactive-video-worktrees/pi-experiments-20261028/.runtime/pi-agent-kit-20261028/code/node_modules \
  --input /实际素材目录
```

默认复制 sdk_root/skills/episode-production 全部资料，并导出完整六方法；可用 `--skills` 选择另一份完整资料。只替换 Skill 导航，旧版导航保留为背景，不强制旧字段和阶段。实际任务写入工作副本 TASK.md，包括改编范围、模型、额度、交付目标；空白模板未授权付费。

选择项目已有端点或设置 PI_PROBE_MODEL、PI_PROBE_BASE、PI_PROBE_API_KEY 后运行：

```bash
<local.json中的python> .runtime/pi-editable-v1/starter.py run .runtime/pi-editable-v1 --endpoint <已有端点名>
<local.json中的python> .runtime/pi-editable-v1/starter.py run .runtime/pi-editable-v1 --endpoint <同一端点名> --resume
```

默认沿用已验证的 DS/Kimi 兼容接入，context_window=1048576、max_output_tokens=128000；可改 local.json，服务实际能力仍以端点为准。密钥只进入环境，不写配置/日志。Bun 直接加载 SDK（本机旧 Node 的 Pi CLI 不兼容，未安装新 Node）。

媒体脚本用法见工作副本 TOOLS.md。保留一次真实提交的边界：实际附件与实际 task id 留证；未知提交不隐式再发。Agent 可以改连接脚本，但仍受用户原预算、原始证据和真实交付要求约束。没有新增状态机、轮询调度器或内容审核服务。

离线验证使用本项目已有 Python 和 Pi 依赖，命令及本次结果见 VALIDATION.md。原生会话恢复允许进程重启后继续；此初版没有额外常驻进程监督器，也没有声称新整集已无人跑通。
