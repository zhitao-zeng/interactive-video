# Interactive Adventure Implementation Plan

> For agentic workers: use executing-plans inline. User explicitly requested implementation in this session.

**Goal:** 独立项目接入三个包，完成探险首集完整生成与验收尝试。
**Architecture:** 外部包导入与栏目资料转换接在现有分镜打包器前；视频、字幕、自动审查复用现有模块；互动尾卡在最终交付时由确定性后期制作。
**Tech Stack:** Python、Pydantic、Pillow、FFmpeg、现有 H3/图片/文本/ASR 服务。
**Spec:** docs/interactive-design.md

## Global Constraints

- 9:16、1080x1920、25fps、H.264/AAC；正文177秒加3秒互动卡。
- 独立运行目录；共享服务继续使用既有资源槽；不复制模型与旧生产任务。
- 已有角色图优先采用；源稿、扩写和生成段保持可追溯关系。
- 完整自动流程，不人工逐镜改稿/追加重拍；失败如实记录。
- 仅本地改动，不提交或推送。

## Review Focus

- macOS ZIP 中文名解码及 __MACOSX 排除，由导入测试覆盖。
- 恋综互斥路线和包内相对引用完整保留，由真实包导入检查覆盖。
- 已有角色参考必须进入实际视频请求，由准备结果检查覆盖。
- 时长、3秒尾卡与交付标记按实际视频测量，由后期测试和 ffprobe 覆盖。
- 未验收或失败不能标成最终版，由交付状态测试覆盖。

## Task 1: 独立项目与包导入

Files: src/novel_manga/interactive/importing.py, scripts/interactive_video.py, tests/test_interactive_import.py, configs/interactive.json.
Interface: import_package(archive: Path, destination: Path) -> dict；原包提取至 inputs/packages/<id>，索引保留相对路径。
- [ ] 测试 UTF8/旧ZIP中文名及重复导入不覆盖已有文件。
- [ ] 实现原样导入、清单与栏目入口；导入真实三个包。
- [ ] 独立环境只读复用现有依赖；配置隔离小说名单和运行目录。

## Task 2: 探险制作准备

Files: projects/adventure/episode01.json, projects/adventure/bible.json, src/novel_manga/interactive/preparation.py.
Interface: prepare_episode(project: Path, output: Path) -> Path；写入现有 chapter_script、story_bible、profile、source trace，调用原 packer。
- [ ] 完成177秒扩写、原镜引用、场景描述、对白与互动节点。
- [ ] 导入主角身份图，使用现有资产生成补场景及声线。
- [ ] 验证所有台词和引用映射到生成段，绑定现有共享资源槽。

## Task 3: 完整生产与交付

Files: src/novel_manga/interactive/delivery.py, tests/test_interactive_delivery.py, scripts/interactive_video.py.
Interface: delivery consumes original runner report and writes candidate/accepted delivery plus interactions.json.
- [ ] 测试3秒片尾卡、节点时间与失败报告禁止accepted。
- [ ] 一条命令完成 prepare→pack→render/review→文字/尾卡/字幕交付。
- [ ] 跑探险完整自动流程，记录实际结果、用量、失败及验收范围。
- [ ] 一次独立代码审查，修复真实问题，更新README与交接。
