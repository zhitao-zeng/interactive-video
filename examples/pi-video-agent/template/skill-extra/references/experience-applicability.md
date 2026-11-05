# 历史经验在 v2 的适用性

先判断一条经验属于“判断原则”还是“旧宿主操作”。v2 是 Pi 单 agent + 可编辑 tools，没有旧宿主的 phase/check/apply、binding/phase 资产系统、publication 路由、DS fallback、repair.json/decision.json。旧资料可以查证，但不要把旧命令当成今天的接口。

案例编号按 `case-lessons.md` 表格顺序：前表 1–13，后表 14–18。

## 快速分档

| 档位 | 数量 | 条目 |
|---|---:|---|
| 现在可直接照用 | 22 | H3 12 条 + 导演 5 条 + 案例 4、5、7、16、18 |
| 原则仍对，解法要换 | 6 | 案例 1、2、3、6、11、17 |
| 旧宿主专用，具体解法不要照搬 | 7 | 案例 8、9、10、12、13、14、15 |

## 一、现在可直接照用

- **H3 12 条全部适用**：`summary` 不写可朗读英文/角色名/牌面文字；中文台词只进 `<d>[Chinese] …</d>`；无对白写 `No spoken words / 无任何人声`；一镜一说话人一主要动作；两人同框不等于可复制同一角色；`first_frame` 是本镜 t=0；云 H3 无 keyframe，本地 H3 才按 t=0；附件成功不等于主体存在；先看 contact sheet；重点查念 summary/场号、句间乱码、首尾多余语音、复制人物、服装漂移、参考卡侵入；ASR/音色/音高只是线索。
- **导演 5 条全部适用**：意图要变成观众看得到的变化；参考/首帧服务于具体问题；看实际采用段落，允许证据纠正判断；修改前确认它能改变结果；后期检查对应的最终输出，声画必须来自同一原片时间区间。
- **案例 4/5/7/16/18**：完整场景图会把不该出现的人带进近景；改 prompt 不一定修好；ASR 同音字不等于漏词；754 的 raw/final 时序与实际身体实例检查；722/752 的抖动、固定背景+局部动作、新 take 要查新缺陷。

## 二、原则仍对，解法要换

| 案例 | 旧解法 | v2 现在怎么做 |
|---|---|---|
| 1 绑定前后状态矛盾 | 查宿主 binding | 查 `output/script.md`、`requests/*.json`、图片顺序和镜头状态；先找源文/请求矛盾再重拍 |
| 2 盔甲穿着关系丢失 | `wears` 字段 | 在 subject definitions、prompt 和参考图里写清穿着者/穿着关系/是否有独立空壳，看实际帧核对 |
| 3 官方旁白仍动嘴 | 宿主 single-thinker postmix 契约 | v2 没有 postmix/TTS；原则仍成立，真做画外音只能静默画面+单独音频，且要确认工具链支持，不能假装有契约 |
| 6 15 秒塞 217–344 字 | 宿主 source compression/splitting | 自己拆镜头、拆句、改台词；4–5 个汉字/秒仍是有效经验值 |
| 11 局部修改影响相邻镜头 | 宿主返回 dependent clip/shot 范围 | 自己查 `cuts.json` 和源 clip；改共享源文件会影响所有引用它的 cut，parts cache 会失效并只重编相关片段 |
| 17 先交付后审 | 旧批次交付策略 | v2 要求先完成镜头级重拍/音频修补，再做 review-cut，final + 最多一次针对性修复；`delivery.json` 不是内容验收 |

## 三、旧宿主专用，具体解法不要照搬

| 案例 | 旧概念 | v2 处理 |
|---|---|---|
| 8 用旧男性底模审当前女性阶段图 | phase/阶段图系统 | 没有 phase API；以当前源文和实际参考图为准，需要新 appearance 就生成/指定新图 |
| 9 发布列表和普通修复列表不一致 | publication routing / per-clip 权限 | 没有宿主发布路由；用 `delivery*.json`、`report.py` 和自审清单，自己列交付阻塞 |
| 10 缺角色卡 vs 已有角色换阶段 | 角色卡、phase、`reuse_asset_id` | 没有卡/phase；`--asset-cache` 只复制 cache 顶层图片/JSON，`--previous` 链上一版 workspace；新 appearance 用新图 |
| 12 DS 拒绝后走 fallback | 宿主配置的自动 fallback | 没有自动 fallback；改写请求、换表达/换模型，或停下说明，不重复同一轮 |
| 13 400 章批量准备 | 旧书稿流水线 | 当前是 40 集正片 + 3 集季间桥段；`batch.py` 是本地进程批处理，不是读书/合并/故事台账流水线 |
| 14 391/456/560 缓存校验、非数字计数、存储语音失败 | 旧宿主缓存/parser | 看 `run-budget.json`、媒体 task receipt、`report.py` 和实际文件；保留独立失败，不把未知判通过 |
| 15 473 idle-recovery 标记 | 旧宿主 idle marker | 用 Pi `--resume` + workspace 文件；`run-budget.json` 保留预算，不重放过期 fallback 历史 |

## 四、当前硬修正

- **SD 首帧**：v2 允许 `action=video` + `first_frame`，不是禁用；但 `references` 必须只有首帧这一张，且不能同时带 `reference_audios`。`--first-frame none` 才全局禁用，`required` 会强制每个视频请求带首帧。
- **H3 首帧**：本地 H3 的 `first_frame` 按 t=0 keyframe 使用；云 H3 不支持 keyframe，只作为第一张参考图。
- **没有宿主 `check/apply/phase`**：`media.py` 只做路线、首帧策略、SD 参考音频总时长、输出冲突等运行校验；`assemble.py` 只做裁切范围、配乐 review、parts 复用等校验。画面和声音的语义验收仍由 agent 自己看/听。
- **没有 `repair.json`/`decision.json`/`source_patch.json`/`speaker_map`/`reuse_asset_id`**：需要修复时直接改请求/镜头 JSON、参考图或 `cuts.json`，把判断写进 `output/script.md`/`self-review.md`。

## 五、使用规则

1. 先问：这是判断原则，还是旧宿主操作？
2. 当前 v2 有没有对应字段/工具？没有就只保留原则，不照搬命令。
3. 按这条改，实际请求、帧或声音会变吗？不会变就不要做。
4. 旧 `repair-playbook`、`review-and-repair` 仍可查证诊断思路；其中的 check/apply/phase 命令和固定交付协议不适用于 v2。
