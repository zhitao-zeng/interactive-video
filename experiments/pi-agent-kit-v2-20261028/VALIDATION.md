# 初版验证（2026-10-28）

来源：只读复用 pi-experiments-20261028 的 pi-agent-kit；代码在 codex/pi-editable-starter-20261028。没有覆盖并行 worktree、已完成实验或主线生产代码。

离线命令：

```bash
PI_STARTER_SDK_ROOT=/mnt/disk1/zengzhitao/interactive-video-worktrees/main \
PI_STARTER_NODE_MODULES=/mnt/disk1/zengzhitao/interactive-video-worktrees/pi-experiments-20261028/.runtime/pi-agent-kit-20261028/code/node_modules \
/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python -m pytest examples/pi-video-agent/test_starter.py -q
```

已通过的验证范围：

- 真正启动 Pi SDK，使用模拟 SSE 返回 edit、bash：修改任务副本的 Python 文件，实际执行得到修改后的结果。恢复同一原生会话后历史和文件仍在；init 不覆盖已有目录。请求开放 read/write/edit/bash，输出上限为128000。
- 实际项目图片 SDK + MockTransport：两张不同颜色的图片确实进入 POST，保存的上传副本颜色与顺序吻合；不绑定不存在的 Picture 3。
- 实际图片 SDK 的终态失败及提交超时：再次运行不新增 POST，原始请求和已知 task id 保留。
- 实际素材库 SDK 的上传/查询及 SD 首帧 SDK：Active 资产进入请求，真实 content 角色为 first_frame，回执标记为 true。
- 实际 H3 SDK：两张普通参考和第一张图的 t=0 keyframe 同时进入请求，模型字段为 MiniMax-H3。
- 实际 ffmpeg 编码本地红、蓝视频：改剪辑必须新输出，新成片确为蓝色，不返回旧红片；采用清单保留。
- Python 编译、Git whitespace 检查通过。

这些验证没有真实 API/LLM/GPU 请求，没有上传成片或修改任何旧生产任务，也没有安装依赖。SD/H3 下载在接口测试中为模拟字节，不是生成质量验证；ffmpeg 测试使用真正可解码的本地媒体。音频例子复用并行 kit 的 Eureka 写法，本版只做编译检查，未重新占用 GPU 或声明听审完成。

源码未提交、未合主线、未推送。这里只证明可修改的起步代码和请求边界；新整集尚未无人验证。原生会话持久化/--resume 已验，没有另加常驻进程监督或自动重启机制。

已初始化的工作副本：`.runtime/pi-editable-v1/`，带此前《失物海》原输入的副本及完整项目 Skill/六方法。TASK.md 仍为未授权付费的起点，未调用 LLM；该目录可直接继续编辑，不需要重新生成模板。
