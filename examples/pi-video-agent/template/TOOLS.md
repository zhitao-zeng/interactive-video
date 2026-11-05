# 可用代码，不是阶段协议

Pi 原生 `read/write/edit/bash` 始终可用。修改 tools/ 后通过 Bash 再执行，使用的是修改后的工作副本。`starter.py run` 也启动该目录自己的 agent.mjs/runtime.mjs。共享 SDK、node_modules 和输入原件保留，在当前目录写派生代码或素材。

Python 路径见 local.json。下面的 JSON 只是 helper 示例，不要求你用此格式写制作稿。把请求保存成文件后调用：

```sh
<local.json中的python> tools/media.py request.json
```

图片（附件顺序完全由你指定，没有默认角色或偷偷追加的图）：

```json
{"action":"image","prompt":"Picture 1 是人物身份参考，Picture 2 是空间参考。描绘当前镜头的静态起点……","references":["input/identity.png","input/space.png"],"purposes":["identity only","space only"],"output":"media/opening-v1.png","ratio":"9:16"}
```

SD 普通参考：先逐张 `{"action":"upload","path":"input/identity.png"}`，确认回执为 Active，再提交：

```json
{"action":"video","prompt":"@图片1 是人物身份参考。场景、动作、运镜和原文中文台词……","references":["input/identity.png"],"seconds":10,"output":"media/take-v1.mp4"}
```

SD 首帧：目前复用的 SDK 接受一个开场图片与原生声音，不混传其他参考；同样先上传该图：

```json
{"action":"video","prompt":"从所附首帧的状态开始，人物转身……","first_frame":"media/opening-v1.png","references":["media/opening-v1.png"],"seconds":10,"output":"media/take-framed-v1.mp4"}
```

H3：`action="h3"`，显式给 `endpoint`（现有服务地址或 `pool:/现有池配置路径`），用项目 H3 写法；`references` 为实际图序，指定 `first_frame` 时必须位于首项，也可加其他图。不需要 SD 素材库。

执行前，SDK 从 local.json 的 sdk_root/.env 读取已有凭据。SD 素材库需要 `PHANROUTER_ASSET_GROUP_ID`、`PHANROUTER_ASSET_PUBLIC_BASE`。`NOVEL_ASSET_PUBLISH_DIR` 必须指向这个公开服务实际提供的目录；不能随便建个同名 .published 就认为图能访问。沿用已配置的服务，不修改服务或凭据。

每个输出保留 `.request.json`、`.wire/request.json`、`.wire/response.json`、SDK `.task.json`。实际图片 API 附件保存为 `.wire/attachment-*.image`；SD 保存真实 asset URI。相同输入且已有 task id 时可运行同一请求继续查询/下载；终态失败或未知提交不会自动换池重新付费。需要新 take 时，在原预算内明确用新文件名。不要删除回执以重试。

观察：

```json
{"action":"inspect","path":"media/take-v1.mp4","times":[0.2,4,8]}
```

返回实际抽帧文件，再用原生 read 看图。音频例子为 `{"action":"audio","path":"media/take-v1.mp4","windows":[{"start":0,"end":10}]}`。先自行创建 audio-runtime.json：python、audio_source_root、audio_model_dir 指向现有 Eureka 环境/模型，device 用 cpu；使用空闲 GPU 时明确 device=cuda 和 cuda_visible_devices，不默认占卡。窗口通常每段不超过30秒，需要全片覆盖时自行列出全部区间；重查修改后的片子用新的 output_dir，防止读取旧观察。

合成可使用 `python tools/assemble.py cuts.json output/film-v1.mp4`，也可自行 ffmpeg。示例读取所有实际 cuts、重新编码，不缓存旧成片；新剪辑用新输出名，保留旧版。字幕、配乐、封面等由你按任务加入。没有额外 finish 工具或固定终审字段，实际成片和实际检查决定交付。
