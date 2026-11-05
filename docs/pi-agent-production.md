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

## 批量生产

`batch` 只逐集执行 init → run 并同步等待，不调度内容或生产阶段。manifest 示例：

```json
{
  "sdk_root": "/mnt/disk1/zengzhitao/interactive-video-worktrees/main",
  "dependencies": "/path/to/existing/pi/node_modules",
  "episodes": [
    {"workspace": "/path/to/episode-A", "input": "/path/to/input-A", "endpoint": "official-ds41"},
    {"workspace": "/path/to/episode-B", "input": "/path/to/input-B", "endpoint": "official-ds41"}
  ]
}
```

```bash
PYTHONPATH=src .venv/bin/python scripts/pi_video_agent.py batch /path/to/batch.json
```

已有工作区必须显式 `resume=true`，不存在的恢复目标会拒绝。退出码只表示会话执行结果，不等于整集 accepted。

## 验证边界

原生工具测试、MockTransport 和 FFmpeg 只证明接口与本地执行。W24 和后续短片证明自主路线的局部可行性，不代表所有题材、整集质量或运动能力已通过。正式交付仍需实际媒体检查、正式音画终审和可核实发布；会话退出、候选文件或 ASR 都不是整集成功。
