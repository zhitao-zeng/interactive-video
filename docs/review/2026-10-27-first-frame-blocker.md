# 当前卡点 Review：首帧图像通道的衣装保真不稳定（2026-10-27）

> 目的：请 reviewer 判断下述"归因于外部图像模型能力"的结论是否成立，以及两条出路选哪条。
> 结论先说：**管线各工程层已全部打通并有测试/真实运行证据；剩余卡点是 gpt-image-2（经 phanrouter）对参考卡衣装的一致性不稳定——同一份收紧后 prompt，一次首轮通过、一次三连拒。**

## 链路现状（真实运行证据，非测试桩）

Task 9 分层验证在 W10《失物海》上已完成到：

| 阶段 | 状态 | 证据 |
|---|---|---|
| 作者（真实 Codex/Luna） | ✅ 一轮出稿（合同完备后） | validation-5/10/11 各库 `episodes` 表 |
| 独立源审 | ✅ accepted，0 findings | validation-5 receipts |
| 编译（翻译+保真门） | ✅ 含有界重试（fidelity 拒稿回喂，≤3 次） | validation-10/11 `compilation` ops |
| 首帧生图+图审 | ⚠️ 不稳定（见下） | validation-8/10/11 `frame_review` receipts |
| H3 视频 | ✅ 3 take 全生成 | validation-10 `video` ops（external_task_id 真实） |
| ASR | ✅ 修复同音误杀后逻辑就绪 | validation-10 三 take 的拒因分析 |
| 合成/终审/上传 | 未到达（被首帧卡住） | — |

## 卡点：三个实例的对照

同一收紧后的首帧 prompt（t=0 语义 + `opening_characters` 锚定 + 装备外观/携带关系边界句 + 参考卡附件同序）：

| 实例 | 首帧结果 | 拒因（图审原文摘要） |
|---|---|---|
| validation-8 | 3/3 拒 | 男性/短发；运动服 vs 卡上米色外套+灰绿工装裤+黑背包 |
| validation-10 | **第 1 张即过** | — |
| validation-11 | 3/3 拒 | 白衬衫短裤腰部装备 vs 同一张参考卡 |

判定依据：
1. v10 与 v8/v11 用的是**同一份**收紧后 prompt 与同一组附件（frame_request_hash 可比对），结果一过三拒——不是确定性的 prompt 缺陷形态。
2. 请求侧已核对：附件确实附带（identity/wardrobe/style 三/四张，receipts 里有逐张 sha256 与顺序）；payload 格式与旧管线（zhiyao 批产 20 集同通道）逐字段一致。
3. OpenAI 官方文档明确跨次生成的角色一致性可能失稳，且 `input_fidelity` 不可调。phanrouter 的附件映射以请求记录为准（receipts 保留）。
4. 每镜 3 张候选是冻结标准额度，3/3 拒即停（`image quota exhausted`），不做无限重试。

**不能排除的备择解释**（reviewer 请重点看）：
- phanrouter 对 gpt-image-2 的 `base64Files` 传递是否等价于官方多图输入（我们只验证了格式与旧管线一致，未验证路由行为）；
- 竖屏 9:16 + 2K + "thinking high" 组合下参考图权重是否被削；
- 中文长 prompt 中锚定段落在多附件场景的相对权重。

## 已修的工程问题（这些不是卡点，列出供 reviewer 排除）

本轮共 16 项修复，全部带测试（440 passed），详见 `.runtime/clean-w10-20261026/owner` 的逐条记录。要点：
- 作者合同五处盲区（风格附件误标 subject、拒稿理由不回传、segment id 宿主派生未出示、对白逐字映射、title 精确回显）；
- 审核 schema 不满足严格端点（required≠properties、oneOf 禁用）；
- grounding 拒绝数组元素引文、单条不 ground 阻塞全场；
- 首帧 prompt 时间语义（camera 携带镜内动作入首帧）与可见者过滤（`opening_characters` 同时控制文字锚定与角色附件）；
- 图审增加 `missing_content` 类别（要求可见而缺失）；
- ASR 同音专名误杀（拼音等价层，evaluation v2-phonetic，双份证据保留）。

## 复现路径

```bash
PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
  /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
  -m clean_studio.cli produce \
  .runtime/clean-w10-20261026/media-validation-11.json --only-shot SH01
```

证据位置（本机，不在 git 内）：
- 各轮库与回执：`.runtime/clean-w10-20261026/validation-{8,10,11}.sqlite3` + `receipts/`
- 每张候选的实际请求（含附件 sha256/顺序）与拒因：库内 `operations`（kind=image/frame_review）及 `artifacts`
- 模型侧原始交互：`.runtime/clean-codex/`（作者/审核/翻译 attempts 全留）

## 更正（2026-10-27 外部审查后，V01/V03）

外部完整审查（19 组发现）确认本文的下述口径需要收窄：

1. **"剩余卡点归因于图像模型衣装保真不稳定"是当前假设，不是已确立的根因。** v8/v10/v11 比较的是"生图+图审组合"的结果，实际外发请求、原图编码、候选图与完整判词未纳入对照；phanrouter 的 base64Files 路由等价性未验证；审核复用路径此前也未按当前策略重验（E06，已修）。固定输入的对照实验（第三批）完成后才允许归因结论。
2. **"工程层已全部排除"不成立。** 审查确认了 E01–E07 七项可核实缺陷（镜头设计字段丢失、opening_characters 未接入修订链、未解决审核条目可与 accepted 并存、配乐时间轴越界、ASR 修复范围超描述、审核缓存不随策略、请求摘要不等于发包证据）。第一批四项（E01–E04）与 P02–P04 已于本轮修复并带回归测试；其余见第二批。
3. **选项 a 的表述混淆了"诊断结束"与"任务成功"。** 即使日后确认媒体能力限制，也只说明本轮失败原因；原验收（有效视频 N/N、正式音画 accepted、accepted 成片、可核实私有上传、全程无修复/接续/热换）不变，修复后续跑一律记为恢复验证。

## 待决策

- **a) 接受为媒体能力结论**：clean-studio 的设计目标就是把失败归因到明确层；此结论成立则验证本身已达标，后续再议通道。
- **b) 换首帧图像模型对照**：如旧管线支持过的 doubao-seedream 系（单参考图限制需注意），或官方直连 gpt-image-2 验证 phanrouter 路由变量。需要一次明确授权（涉及新的外部付费通道或凭据）。

## 边界说明

- 图审标准未放松：锚定只告诉模型"该画什么"，画错照拒；3/3 拒即停。
- 未做对照实验隔离"文字锚定"的独立贡献（v10/v11 作者稿不同）；如 reviewer 需要因果更强的证据，可固定同一稿+同一附件重跑 N 次采样通过率。
