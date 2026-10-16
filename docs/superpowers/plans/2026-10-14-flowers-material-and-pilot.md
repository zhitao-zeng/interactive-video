# 未署名的花素材包与H3小样 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 完整交付原创花系恋综创作素材包，四人及温室唯美参考，并通过既有H3链路完成20—30秒小样。

**Architecture:** 在恋综内容worktree内准备新material与独立edition，使用现有连续交流编译、任务台账、Agent审核与有界修复。编译器的两处真人硬编码改为画风中性措辞；画风仍来自已有profile/style，不新增分支或模板。

**Tech Stack:** Python项目现有只读环境、imagegen、MiniMax-H3 Ref2VA Turbo、现有interactive入口与ffmpeg。

**Spec:** projects/romance/anonymous-heart-v1/README.md；用户2026-10-14确认完整包、唯美参考、剧情与20—30秒H3小样。

## Global Constraints

- 本次仅拍20—30秒小样，不启动整集；完整首集素材保持3—5分钟及片尾3秒互动。
- 唯美半写实三维CG；旧认可source_en不改；16:9横屏，H3原生768P，导出25fps H.264/AAC。
- 图片与影片艺术审查通过Agent；协调会话不看图或逐帧复核。
- 保留旧片、原图、每次请求/任务/take与失败历史；不继承旧片QC豁免。
- 现有共享worktree的delivery.py等WIP属于其他会话，不覆盖、不代为提交。
- 只记录本轮局部生产，不推送、部署或改远端服务。

## Review Focus

- 原图可能偏摄影，必须实际审查CG画风后采用。
- 四张人物卡需区分且无额外人；空温室四椅、入口、通道、行李位可用。
- 已在场不等于全部入镜；反打继承世界座位，花与行李归属不断。
- 编译必须绑定每句台词一次，实际H3请求含唯美画风，不残留真人指令。
- 20—30秒实验不冒充满足180—300秒正式交付门；单独核验其时长与实际审核结果。

## Task 1: 画风中性编译

**Files:** src/novel_manga/interactive/conversation.py；tests/test_romance_conversation.py。

**Interfaces:** 保留compile_conversation_clip签名；继续由clip.render_family/h3_style_line定义画风，现有pack_conversation/build_request消费输出。

- [x] 加一个实际请求回归：3d与唯美h3_style_line输入，SD提示词不能强制真人，H3必须保留原风格行；参考与台词保持。
- [x] 运行该回归，预期先因真人硬编码失败。
- [x] 将开头“真人恋综”改为“恋综”，末尾“原参考的真人画风”改为“原参考的画风”；不添加画风猜测或新参数。
- [x] 运行现有恋综连续交流、导入和共享素材接入相关测试；预期通过。
- [x] 留存最小修复补丁与独立复核，保留其他会话WIP；本轮不提交未授权文件。

## Task 2: 完整素材与有界小样

**Files:** projects/romance/anonymous-heart-v1内人物/空间/剧情/分镜/互动/包装/声线说明；outputs/romance-flowers-assets-20261014；outputs/romance-flowers-h3-pilot-20261014；.runtime/romance-flowers-20261014；delivery/romance/flowers-20261014。

**Interfaces:** material.json/source.md通过prepare_authored进入现有pack_romance；production_mode=romance_conversation；每组一个连续交流段、多turns，生成段≤15秒。

- [x] 生成五张参考图；Agent审核，必要的画风编辑最多两轮，原图保留。
- [x] 完成首集剧情、花信原文、四人/场景/道具/声线、完整分镜与ABC后续入口；字幕/花字/卡片/音乐均明确后期归属。
- [x] 新小样截取完整首集两个连续交流段，每段12秒；保留真实源映射，片尾3秒，共约27秒。
- [x] 准备新edition，复用现有声线音色并记录来源；在新角色名下绑定，不能把旧对白转入新剧情。
- [x] 通过现有素材准备与请求检查；核对实际H3请求、引用、768P与预算，再运行既有Agent审核/修复链路。
- [x] Agent实际审核人物、画风、空间、台词/口型、花和行李；失败交既有有界修复，不由主会话定点人工修片。
- [x] 合成并保留原生素材；加入后期字幕、轻量包装、低声音乐与3秒选择；Agent审核新增后期。
- [x] 核验20—30秒、横屏与完整解码；把可用素材包打成ZIP，与小样一并交付，记录实际状态和限制。

## Completion evidence

完整包和27.56秒H3小样已完成；F01两次、F02三次，Agent视觉和后期接受，最终媒体门通过。首集195秒仅准备/编译，没有视频生成。25针对＋3275全量测试与独立代码复核通过；无commit/push/服务变更。音频仅ASR词句和信号检查，没有实际听审或逐音节口型认证。Mac传输结果随交付receipt记录。
