# Fast 首帧与 API 互动尾卡

新 Fast edition 可使用：

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py run \
  --project romance --edition romance-new-edition \
  --lane sd20-fast --sd-input-mode first-frame
```

每个既有生成镜组使用创作者的 `opening_frame.image_prompt`、
`visible_characters` 和可选的 `reference_subjects` 绘制开场图。宿主沿用已有图片 API、
所选画风和图片审核，审核通过后才提交 Fast。所有镜组先做源输入检查；每组须为
4–15 秒，使用 `no_reference_performance_first`。不会按对白逐句拆分现有镜组。

Fast 请求只发送一张完整开场图，角色为 `first_frame`，声音由模型原生生成。
源素材仍留在计划中用于追溯和审核，旧的逐张参考图片编号不会进入 Fast 的最终提示。
采用正常 SD 制作、原有生成次数及修复/终审路径，不进入 H3 episode Agent。
`h3_input_mode` 继续属于 H3；不能通过改 lane 把已冻结 H3 任务切成 Fast。
已存在视频任务的 edition 也不能临时改成首帧模式。

首帧候选、供应商回执、审核和源开场状态保存在 edition 的 `first_frames/`。
恢复时复用相同请求；源开场或素材变化后旧图失效。每镜最多三个图片候选，
拒绝结果会保留并停下等待创作者修正，不自动以相同输入重复花费。
《心动待定》pilot-v2 已补入此前由 Agent 设计的两个开场提示。
full 的 93 镜仍需完成逐镜首帧设计和整集实跑，代码接入不代表整集验收。

互动素材可给出 Agent 编写的 `interaction.art_direction`。
正常导出会调用项目现有图片 API 绘制尾卡，附上原题及选项，审核节目名、中文、人名、
等权呈现及可读性。只把审核通过的图片选入 `interaction.artwork`；
图片和审核保存在 edition 的 `interaction_cards/`，后续导出复用。
题目、选项或选中状态改变后不能复用旧图。尾卡继续为 3 秒、75 帧。
《心动待定》full 与 pilot-v2 的未来导入已启用本次暖色编辑设计；旧 edition 不改写。

也可以在拍摄前独立生成并审核尾卡：

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py interaction-card \
  --project romance --edition romance-new-edition --prompt-file approved-card-design.txt
```

此次没有把实际尾帧续接设为默认。它适用于同场连续动作，换景、反打和采访使用各自开场。
目前实际尾帧实验和新旧对照片仍在原实验目录；首帧接入不会自动串行接上一段尾帧。
