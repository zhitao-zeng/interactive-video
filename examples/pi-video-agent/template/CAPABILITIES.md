# 本任务已备好的能力

准确环境路径见 CAPABILITIES.json；无需搜索整台机器、读取 .env 或重新寻找模型。

- **Agent**：Pi 原生 read/write/edit/bash，可改当前 tools/ 或另写脚本。已有编排器负责会话/压缩，不需要自己重写 Agent 循环。
- **图片**：GPT Image 2；references 按实际数组顺序附图，purposes 说明用途。可以给新角色/场景建图。场景的时间、光线、空间以本集事实为准。
- **SD2.0 Fast / Mini**：Fast 为 720P，Mini 约 480P（样片 864×496；竖屏预期约 496×864）。原生中文声音；普通 references 先上传素材库。可传 reference_audios（合计不超过15.2秒）；音色参考不是逐字配音。首帧可用，但只接受一张首帧图、使用原生声音，且不能同时带参考音频；只有 `first_frame_policy=none` 才禁用。Mini 已可通过 `media_lane=sd2.0-mini` 选择。
- **H3**：本地池支持中文原生对白、图片、音频参考和可选 t=0 首帧；传 audios 或 reference_audios，首帧也放 references 首项。用已有 H3 Skill 写法；无需另找 TTS 才能开拍。
- **Eureka**：GPU 音频感知，不是 TTS。观察实际声音、说话者、异常与转录；不是精确逐字时间轴或最终质量判定。服务已由任务发起者准备，进度/任务回执由 media.py 保留。
- **SenseVoice**：可选离线转录与 token 时间戳，使用 CAPABILITIES.json 中的专用 Python 和 model_dir；tools/transcribe.py 是现成入口。时间戳/同音字可能有误，不能以一个相似度分数宣布台词100%正确。
- **批处理**：`<python> tools/batch.py requests/*.json --workers 3 --report output/batch-01.json`。一次调用等待本批完成，报告真实返回码与日志。媒体失败按原任务回执查询；不要重新提交已受理任务。
- **合成**：`<python> tools/assemble.py cuts.json output/film-v1.mp4`。支持任意镜头数、裁切、可选 zoom/x/y、ASS 字幕与 music。音频按实际帧数裁切，输出 timeline.json。纯配乐变更用 add_music.py，无需重编码画面。
- **BGM**：可用曲库和数量见 CAPABILITIES.json 的 music_selection。`tools/media.py` 的 `action=music` 接受 query、limit、可选 track_ids 和 output_dir，返回已有曲库的候选及署名信息。先取得实际声音证据，再自主选曲、留白和混音；保留原对白、原片及来源署名，检索排名不等于适合本片。纯配乐变更沿用现成 add_music.py，不重新生成视频。
- **检查与交付**：实际看片/取得声音证据后写自己的 self-review，再运行 report.py 取得解码、规格、轨时长、请求数事实。它不替你做艺术判断，不把片尾音轨长度差称为全片口型误差。

参考素材的公开目录已经配置；需要排障时先看 CAPABILITIES.json 和实际 HTTP 错误。请求会由客户端读取凭据，不要把密钥打印到命令输出或写入制作稿。

耗时命令可给原生 bash 足够的 timeout；不给 timeout 的命令会按本 run 剩余墙钟被限制在一个有限默认值
（本批 900 秒），显式 timeout 也会被压到不超剩余墙钟。所以长操作要自己写明需要多久；
不要为等自己的 ffmpeg 写 pgrep/sleep 轮询。直接等待当前子进程或使用 batch.py。报告文件不表示内容已验收。

**音乐未配置时怎么做。** 若 `music_selection` 明确报 `SoulTuner runtime is not configured`
或本地目录缺失，就**跳过 BGM 继续交付**：用原生对白、现场声和你已有的音频证据完成混音。
不要全盘 `find` 找 music-selection、不要猜环境路径、不要安装任何东西、不要为此重启服务——
一次明确错误就是结论。没有 BGM 不是交付阻塞项，在 self-review 里如实写明"未使用配乐"即可。
若本集能力说明另有说明（例如本批统一不带配乐），以本集说明为准。

音频感知支持question文本，描述实际音乐/混音时可自主提问；纯音乐不要强制转录中文。已有audio_service_url时调用既有服务并沿同job查询，不自己起多个本地音检。未完成的后台任务不算已检查范围。
