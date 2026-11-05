# 可用代码，不是阶段协议

Pi 原生 `read/write/edit/bash` 始终可用。修改 tools/ 后通过 Bash 再执行，使用的是修改后的工作副本。`starter.py run` 也启动该目录自己的 agent.mjs/runtime.mjs。共享 SDK、node_modules 和输入原件保留，在当前目录写派生代码或素材。

视频路线和首帧策略由 `PRODUCTION.md`/`local.json` 的 `media_lane`、`first_frame_policy` 固定；`media.py` 会拒绝切换路线或不符合首帧策略的请求。不要为了绕过限制改 local.json，除非任务明确授权。

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

H3：`action="h3"`，显式给 `endpoint`（现有池名/地址，如 `"pool"`），用项目 H3 写法；`references` 为实际图序，指定 `first_frame` 时必须位于首项，也可加其他图。不需要 SD 素材库。已验证的最小形态：

```json
{"action":"h3","endpoint":"pool","prompt":"subject_definitions … <Picture 1> … <Audio 1> … summary … detailed_description … Planned duration: 7 seconds.","references":["input/知遥-身份基准-v1.1.png","input/知遥-综合视觉卡-v1.1.png"],"purposes":["face identity","body and clothing"],"audios":["media/voices/zhiyao-anchor-a.wav"],"seconds":7,"ratio":"9:16","output":"media/test-h3-v1.mp4"}
```

## 已验证的硬限制

- SD r2v 的 `reference_audios` 限制是**所有参考音频的总时长**：doubao-seedance-2-0-fast 当前要求合计 ≤ 15.2 秒（不是每个文件）。只截本镜头实际用到的台词/音色；把整段角色音色或整场对白塞进去会直接 400 `InvalidParameter`。先用 ffprobe 量总长再提交。
- SD 首帧只带一个开场图与原生声音，不混传其他参考（见上）。
- H3 的 `audios` 是音色参考，尽量短；`first_frame` 若用必须在 `references` 首位。7 秒音色参考和 10 秒对白参考已在 2026-10-28 实测通过。
- `input/PREVIOUS.md` / `input/previous/` 是同一集上一次尝试的可复用素材；只复用于同一集，逐条核验并记录来源。

执行前，SDK 从 local.json 的 sdk_root/.env 读取已有凭据。SD 素材库需要 `PHANROUTER_ASSET_GROUP_ID`、`PHANROUTER_ASSET_PUBLIC_BASE`。`NOVEL_ASSET_PUBLISH_DIR` 必须指向这个公开服务实际提供的目录；不能随便建个同名 .published 就认为图能访问。沿用已配置的服务，不修改服务或凭据。

每个输出保留 `.request.json`、`.wire/request.json`、`.wire/response.json`、SDK `.task.json`。实际图片 API 附件保存为 `.wire/attachment-*.image`；SD 保存真实 asset URI。相同输入且已有 task id 时可运行同一请求继续查询/下载；终态失败或未知提交不会自动换池重新付费。需要新 take 时，在原预算内明确用新文件名。不要删除回执以重试。

观察：

```json
{"action":"inspect","path":"media/take-v1.mp4","times":[0.2,4,8]}
```

返回实际抽帧文件，再用原生 read 看图。音频例子为 `{"action":"audio","path":"media/take-v1.mp4","windows":[{"start":0,"end":10}]}`。先自行创建 audio-runtime.json：python、audio_source_root、audio_model_dir 指向现有 Eureka 环境/模型，默认 audio-runtime.json 已配 device=cuda 和显式 cuda_visible_devices；只有在 GPU 不可用时才退回 cpu。窗口通常每段不超过30秒，需要全片覆盖时自行列出全部区间；重查修改后的片子用新的 output_dir，防止读取旧观察。

合成可使用 `python tools/assemble.py cuts.json output/film-v1.mp4`，也可自行 ffmpeg。示例读取所有实际 cuts、重新编码，不缓存旧成片；新剪辑用新输出名，保留旧版。字幕、配乐、封面等由你按任务加入。没有额外 finish 工具或固定终审字段，实际成片和实际检查决定交付。

可选的镜头表/请求构建示例见 `tools/plan.example.py`：改 `SHOTS` 后运行，它只写 `requests/*.json`，不提交；不用它也可以。它会把 SD 参考音频总时长 >15.2 秒直接拦下。

## 配乐（可选，本地已授权曲库）

搜索本地曲库（返回候选，不等于采用）：

```json
{"action":"music","query":"quiet old post office, warm memory, sparse piano, no prominent vocals","limit":3,"output_dir":"music-searches/ep09a"}
```

候选包含 `id/title/artist/path/license_id/license_url/attribution/audio_evidence/review_status/fit_status/scene/subscene`。先读 `audio_evidence`，必要时用 `{"action":"audio","path":"<候选 path>","windows":[...]}` 实际听；记录不确定项。

选中后在 `cuts.json` 加：

```json
{"music":{"path":"<候选 path>","track_id":"<候选 id>","title":"...","artist":"...",
 "license_id":"...","attribution":"...","gain_db":-24,"loop":true,"fade_in":1,"fade_out":1.5,
 "review":{"status":"accepted","reason":"为什么适合这个场景；引用实际听到的证据"}}}
```

`assemble.py` 会把 BGM 混到成片并写 `.music.json`；没有 `review.status=accepted` + `reason` 会拒绝。已有成片补 BGM 用：

```json
{"film":"output/film.mp4","output":"output/film-with-music-v1.mp4","music":{"path":"...","track_id":"...","license_id":"...","attribution":"...","gain_db":-28,"loop":true,"fade_in":1,"fade_out":2,"review":{"status":"accepted","reason":"..."}}}
```

`python tools/add_music.py request.json` 只重编码音频、视频流 copy，保留原片并写新的 `.music.json`。

当前本地库 61 首：异世界旅行探险/悬疑后室/恋综各 8 首，24 个 subscene 基本每类 1 首；不是抖音热门库，采用前核对 license/attribution 与 `catalogue_scope`（popularity not verified）。

## 片尾 3 秒选项页（系列默认）

`interaction_page=required` 时，成片最后必须追加一个正好 3 秒的 A/B/C 页：

```json
{"film":"output/film-v1.mp4","output":"output/film-v2.mp4","title":"林野探秘档案",
 "question":"下一站，你想和我去哪里探险？",
 "options":[{"id":"A","label":"A，空椅剧场","reason":"观众可自愿签角色单，正式开场前可以拒演。"},
 {"id":"B","label":"B，风筝浮城","reason":"房屋靠风筝升降，公共连接索维持相对位置。"},
 {"id":"C","label":"C，预言车站","reason":"预言只展示当前路线的一种可能，不保证结果。"}],
 "footer":"评论区留下你的选择","seconds":3}
```

`python tools/interaction.py request.json` 只追加页面、不覆盖原片，并写 `<output>.interaction.json`（question/options/start/end）。必须是三张选项 A/B/C、正好 3 秒，页面保持到最后一帧，不要淡出到空镜。

## 共享音频服务（多 agent 排队）

多个 agent 共用一个 GPU 时，不要让每个请求都拉起 worker。用 audio-runtime.json 的 Python 启动常驻服务：

```sh
<audio-runtime.json 的 python> tools/audio_service.py --host 127.0.0.1 --port 18790 \
  --allow-root <当前 workspace> --device cuda --cuda-visible-devices 5 \
  --audio-source-root <Eureka-Audio> --audio-model-dir <eureka-adapter>
```

模型只加载一次，请求按 FIFO 排队；`GET /health` 可看队列深度。客户端设置 `AUDIO_SERVICE_URL=http://127.0.0.1:18790` 或写入 local.json 的 `audio_service_url`，`tools/media.py audio` 会自动提交并轮询；服务在提交时不可达才回退本地 worker，已经提交成功后只轮询、不重复提交。服务只接受 `--allow-root` 下的音频/输出路径。


> `inspect` 默认把帧缩到 512 短边并生成一张 `contact-sheet.jpg`：先读 contact sheet，必要时只看 1–2 张单帧；不要每镜把 6 张全尺寸图读进上下文，否则后续模型请求可能被网关 413 拒绝。