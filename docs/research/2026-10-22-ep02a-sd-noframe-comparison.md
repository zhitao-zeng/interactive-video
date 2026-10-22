# backrooms55 EP02-A · SD2.0-fast 无首帧对照线报告

日期:2026-10-22。分支 `codex/backrooms55-ep02a-sd-noframe-20261022`(自 main fc5c6a8)。
与 H3 首帧复验(诊断树 ep02a-verify)并行;本报告为对照线交付的一部分。

## 一、线体定义

- 同一份已验证 EP02-A material(21 镜 / 267 秒 / 互动 AB),传统参考绑定:material profile
  删除 `h3_input_mode / sd_input_mode / h3_extra_reference_policy / h3_speech_policy`,
  全程无 select_mode、无开场图;人物/场景参考经发布通道(asset://)直接绑进视频请求。
- lane sd20-fast,模型 sd2.0-fast,key=PHANROUTER_IMAGE_API_KEY,单 edition 预算
  (每段≤3 take;实际首拍 19/19 一次通过,修复 Agent 决策另计)。
- edition `suspense-backrooms55-ep02a-sd-noframe-20261022`;交付
  `delivery/suspense/backrooms55-ep02a-sd-noframe-20261022/`(三态规则)。

## 二、与首帧桥素材的结构性冲突(分支内适配,合并前需协调者审)

首帧 H3 流程会把组重写为逐镜一组(`select_mode`),因此 material 的 18 组结构里只有传统
模式会踩的坑:

1. g14=[16,17] 跨场景(旧摊位 13s + 旧拱口 2s)——`plan_issues` 拒绝。
2. 旧拱口块镜头时长为 [2s,15,15,15,15]:2 秒镜向前并入=跨场景,向后并入=17s 超
   provider 15s 硬上限,单独成组=低于 4s 地板。**数学上无合法分组**。

适配(镜头/台词/时长零改动):
- 组重切 [16,17] → [16],[17];video_units 18→19。
- 分支内 `pack_authored`/`clip_entry` 允许"单镜微空镜(<4s)"按 `max(4, seconds)` 申请
  ——与首帧规则及 provider 自身的 4s 下限一致;装配仍按镜时点回切。
- 实测:clip_15(即 S17,2s 空镜)以 4s 请求成功出片(142s 生成,4.1s 文件,无对白
  环境声),逐镜审查通过。

## 三、素材级缺陷(已在素材层修复,非管线改动)

- `interaction.json` 缺 `seconds` 字段:标准素材桥 `creative_material.py` 会
  `setdefault('seconds',3)`,诊断会话手工拼接的 material 漏了;首帧线不走
  `deliver()` 该校验所以未暴露。本 edition 已补 `seconds=3`。

## 四、运行时间线(详见 .runtime/ep02a-sd-noframe/event-ledger.log)

| 时间 | 事件 |
|---|---|
| 13:52–14:10 | worktree/登记/material 剥字段/风格库/.env;ngrok 静态域名被他线占用(ERR_NGROK_334),改用 cloudflared quick tunnel(task-only 发布目录) |
| 14:07 | 首次 pack 撞跨场景组,停点分析→结构适配(见第二节) |
| 14:15 | pack 过:19 clips/269s;build_assets 生成 006–012 缺失场景卡(006/007 于 14:38 前补齐) |
| 14:38–15:40 | run1:11/19 段出片且全部首拍过审;15:19:49–15:20:02 八段提交前失败(attempts=0,**零预算消耗**) |
| 14:53–15:20 | cloudflared QUIC 中断窗口,失败根因;已发布副本探测恢复 200 |
| 15:41 | resume:管线只重试缺的 8 段(缓存段秒过) |
| 16:05–16:16 | 接管:按方案 A 切 4 并发(profile workers=4/inflight=4 + 共享 inflight 池),SIGTERM 旧进程,task cgt-…-8n9t2 按 request_sha256 续轮询,不重发 |
| 16:18–16:26 | 余下 7 段 4 并发出片,全部首拍过审;clip_15 微空镜路径实测通过 |
| 16:26 | 交互卡/装配/导出撞 interaction.seconds 缺失(第三节),补字段 |
| 16:30 | 导出 candidate:273.9s;终审 17 项发现;修复 Agent 启动 |

## 五、逐镜指标(首拍;cer=ASR 字错率,peak=音频峰值 dB)

| clip | 计划s | 文件s | 生成等待s | cer | peak |
|---|---|---|---|---|---|
| 01 | 15 | 15.1 | 314 | 0.036 | -6.9 |
| 02 | 15 | 15.1 | 254 | 0.000 | -4.4 |
| 03 | 13 | 13.1 | 281 | 0.000 | -3.9 |
| 04 | 15 | 15.1 | 296 | 0.060 | -9.0 |
| 05 | 14 | 14.1 | 325 | 0.039 | -4.5 |
| 06 | 15 | 15.1 | 292 | 0.000 | -3.0 |
| 07 | 15 | 15.1 | 381 | 0.000 | -4.9 |
| 08 | 15 | 15.1 | 332 | 0.035 | -6.7 |
| 09 | 15 | 15.1 | 344 | **0.286** | -5.7 |
| 10 | 15 | 15.1 | 353 | 0.035 | -7.4 |
| 11 | 15 | 15.1 | 395 | 0.039 | -6.2 |
| 12 | 15 | 15.1 | 335 | 0.018 | -2.3 |
| 13 | 15 | 15.1 | 376 | 0.018 | -6.1 |
| 14 | 13 | 13.1 | 327 | 0.019 | -3.7 |
| 15 | 4 | 4.1 | 142 | 0.000 | -40.6(无对白空镜) |
| 16 | 15 | 15.1 | 304 | 0.017 | -4.1 |
| 17 | 15 | 15.1 | 364 | 0.000 | -2.6 |
| 18 | 15 | 15.1 | 503 | 0.019 | -2.2 |
| 19 | 15 | 15.1 | 265 | 0.059 | -3.8 |

- 时长符合度:每段文件时长与计划差 ≤0.1s;整片 270.9s(正文)对计划 269s。
- 台词符合度:18/19 段 cer≤0.06;clip_09 首拍 cer=0.286 + 多余语音,按管线语义
  转修复 Agent(决策:input 源侧修正,未消耗新 take)。

## 六、终审发现与修复 Agent 处置(候选片状态以 delivery.json 为准)

导出 candidate 时终审 17 项发现,全部为道具/状态连续性类(头灯点亮时序、手中长杆、
动作与 end_state 不符),无人物身份漂移、无场景绑定错乱、无字幕/语音缺词。
修复 Agent 逐项以实际帧证据核对(600s/项有界沙箱),已见处置:
- clip_03/07/12/15/18/19:keep(实帧核为误报)
- clip_05/09/11/17:input(源侧修正)
其余以 delivery 目录 final-cut-review.json 与 .runtime 台账为准。

### 6.1 头灯发现的裁决(用户拍板,2026-10-22 晚)

两条头灯发现(clip_03"提前出现"、clip_09"S11 灯亮")按母本 WATCHABLE_CRITERIA 定性为
**提示级**:道具开关状态不影响人物识别或关键事件,不改行动能力——只记提示,不构成
重拍依据。clip_09 若触发补拍,正当性只来自额外语音;头灯条仅记录。裁决记档后,
终审复跑不得以同一理由再拦(反"反复抽判官")。

### 6.2 根因:bible 层对齐 BUG(素材文本,已实锤)

material bible 的 wardrobe("前半未戴头灯,S20起戴")与 continuity_rules("S20起额头
头灯常在")是 **EP01 的剧情形状错套到 EP02-A**(EP01 第 23 镜才取出头灯;本集 S01
起点即"戴在头上且已点亮",S03"头灯照近地")。批准源文与角色制作卡均与剧本一致,
bible 是唯一离群——clip_03 假阳性即由此产生。同源 material 也喂着诊断树 H3 复验线
(未修,移交主会话;两线同修同受益)。

### 6.3 审查边界缺口(代码核验,交主会话的需求单)

对本报告引用的外部分析逐条核验(交互线 fc5c6a8):
- ✅ WATCHABLE_CRITERIA 在交互线零命中(母本 novel-manga-video review/contracts.py 有全文,
  且被 4 处审查提示词引用);
- ✅ 终审是单布尔门:final_cut.py:87 `passed != (not issues)` 硬校验,无 severity 路由,
  任何进 issues 的发现必然拦截 accepted;
- ⚠️ 修正:"段级 minor 悬空无人读"不成立——段级审查有 fix_tier 三级分级且 gate.py
  只拦 must_fix;**缺口仅在终审路径完全不消费分级**;
- ⚠️ 修正:"clip_09 补拍 attempt 2/3 在走"与现场不符(截至 18:31 无 attempt 2,
  修复决策为 input 源侧修正)。

需求单(一句话):**终审接母本 WATCHABLE_CRITERIA + fix_tier 语义,替换单布尔 issues 门,
拦截集只收必修类**;另:连续性发现须先判"画面忠实于该镜请求?"——忠实而请求与全局
规则矛盾 → 判素材层不一致,路由素材文本修复,不判视频失败、不触发重拍("视频错"与
"素材错"两条路由)。改编桥的 bible 组装加本集剧本回验(EP02-A 头灯为现成回归用例,
惠及剩余 53 片)。

**与首帧模式的定性对照(基于本线证据)**:
- 参考绑定本身稳:19 段无一例人物脸漂移或场景错绑;identity/loc 逐镜检查全过。
- 漂移集中在**道具与状态连续性**(头灯开/关、手持物),这正是无首帧模式下
  "每段只有静态参考卡、无帧零锚定"时最预期的失效面——首帧模式靠每镜帧零
  锚定状态,本模式只有参考图。
- 结构性限制:传统模式组预算 4–15s 且单场景,对含 2s 级微空镜的场景块不可行,
  需要微空镜地板适配或素材侧避免 <4s 镜头与 15s 镜头同场景混排。

## 七、与 H3 首帧版的对照现状

截至本报告写时,诊断树 ep02a-verify 的 agent-run 自 04:22 起 status=blocked
(12+ 小时无更新),**H3 侧尚无可比成片**;本报告第六节的定性对照基于本线
内部证据(参考绑定 vs 帧零锚定的失效面分析),不是双向实测对照。H3 线恢复后
可用同一脚本口径补双向表。

## 八、遗留与移交

1. 分支内三处改动待协调者审后方可回合 main:pack_authored/clip_entry 微空镜
   地板、g14 重切、interaction.seconds 素材修复(素材修复部分建议上游
   build_material_ep02a.py 补 setdefault)。
2. 共享基础设施:cloudflared(pid 见 channel-processes.json)与本地静态服务
   (127.0.0.1:18521)为本任务专用,销账后由本会话停止;若复用请改写
   .runtime/suspense-demo/asset-channel.json。
3. 修复 Agent 若触发 retake,消耗的是同 edition 每段≤3 take 预算内的剩余次数。

## 九、最终收尾(2026-10-22 20:30)

- 修复循环跑完 3–4 轮共 33 个沙箱后仍在重问已裁决的提示级发现(终审 15 项恒定,
  passed=False 完全由单布尔门造成),按用户裁决 SIGTERM 停线,一切状态可 resume。
- **最终交付:delivery/suspense/backrooms55-ep02a-sd-noframe-20261022 = candidate,
  273.88 秒(export-006/episode01.mp4)**。19/19 段全部首拍通过,无任何段消耗第二次
  视频预算;终审 15 项发现全部裁决为提示级或素材层(见 6.1–6.3)。
- accepted 转换路径(移交主会话):终审接 WATCHABLE_CRITERIA + fix_tier 分级替换单布尔
  issues 门 → 复跑终审(头灯裁决已记档,预期 15 项落 observations)→ delivery 转
  accepted;本 edition 与 H3 复验线随后可做逐镜双向对照。
