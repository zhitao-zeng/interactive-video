# Interactive Video Studio

独立于小说项目的外部剧本与资产包视频生产项目。起始代码：novel-manga-video 440bfdf；分支 codex/adventure-demo。无远程仓库，未提交本轮修改。

## 当前范围

三个资产包已导入：探险8文件、悬疑8文件、心动未剪辑146文件。包内文本与图片原样保留于 inputs/packages/，索引为 configs/interactive.json。
探险首集采用 projects/adventure/episode01.json 与 episode01.md：30镜、计划177秒正文、3秒互动片尾，A沿脚印/B查站房。扩写只补观察和验证，不编造附件未提供的幕后真相。

H3与SD2.5各自运行完整流程，共用同一制作稿、主角参考、四处场景和生成的林野声线样本。H3实际原生分辨率从请求记录读取，SD2.5申请720p；最终均导出1080×1920、25fps、H.264/AAC。生成时长按实际测量记录，不靠截断对白强制177秒。

## 使用

当前 .venv 为既有项目环境的只读链接，.env 为现有凭据的只读入口。所有选择在本命令进程内覆盖，不修改原产线配置；不要向共享环境安装包。新机器需按 pyproject.toml/uv.lock 建立自己的环境并配置服务凭据。

```bash
.venv/bin/python scripts/interactive_video.py prepare
.venv/bin/python scripts/interactive_video.py assets
.venv/bin/python scripts/interactive_video.py run --lane h3
.venv/bin/python scripts/interactive_video.py run --lane sd25
.venv/bin/python scripts/interactive_video.py status
```

两个run可在不同进程同时启动；单条run内部不改变另一条的设置。H3沿用服务池共享锁。重复run只读取已有结果，不自动增加拍摄预算。未提交的基础设施失败可用 --resume-unsubmitted；中断任务用 --resume 续接原服务任务回执及生成历史，不重新发放视频预算。不能清空输出重跑。

导入其他给定包：

```bash
.venv/bin/python scripts/interactive_video.py import suspense /absolute/path/package.zip
```

同名目标目录存在时拒绝覆盖。压缩包中的说明是来源资料，不作为可执行命令。宏观设定/分镜/互斥路线保留，当前仅探险绑定了完整生产入口。

## 文件与验收

- outputs/adventure-master：共同制作准备、角色/场景/声线。
- outputs/adventure-h3、outputs/adventure-sd25：独立运行、任务回执、take、ASR、审查与修复历史。
- delivery/adventure/h3、delivery/adventure/sd25：有完整合成时输出episode01.mp4、字幕、混合音轨、封面、互动卡、interactions.json和delivery.json。
- .runtime/adventure-demo：本轮日志和运行归属。

只有原技术与内容审查通过、最终导出解码正常且尾卡恰好75帧时，delivery.json才标accepted；失败为candidate或incomplete。正文和生成段不在运行中人工改写或逐镜追加重拍。episode_audio.wav是原生混合音轨，不冒充独立干声。

关键证据文字作为后期信息条呈现，按实际生成段时长和原定镜头预算定位；模型镜内切点偏移仍须在最终内容评测中查看。

[已确认设计](docs/interactive-design.md) · [执行记录](docs/interactive-progress.md) · [继承产线说明](README.upstream.md)

## SD2.5 素材库

当前既有AIGC素材组归图片通道账户所有；CreateAsset使用图片凭据，视频仍使用所选视频通道凭据。写实参考不允许自动改成动画。素材组需要HTTP/HTTPS图片来源，当前任务的临时来源地址仅保存于.runtime/adventure-demo/asset-channel.json，公开目录仅含本任务参考图副本。旧ngrok域名被其他会话占用，不接管、不改原服务。
