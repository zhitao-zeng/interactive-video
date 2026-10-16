# 《笑之国》首集制作

用户已选择附件并要求直接排制作。本会话在原隔离worktree内执行；原林野版本、生成历史及恋综均保留，不提交、不推送。SD本轮不补额度，不调用SD视频。

## 采用稿与交付

附件55镜、291秒，原文台词顺序不变，警报标注不朗读。知遥v1.1身份基准图直接导入，综合卡只用于服装和动作核对。原故事完整结束后才介绍三个下一站；最后另留3秒完整选择卡，预计294秒。模型实际时长和语音若有偏差如实记录。

## Task 1：导入与排镜

将附件逐镜文字、时间、声源和动作保存到projects/adventure/laughing-country/material.json，原稿保存source.md。关键短镜合组，超过15秒拆开；坍塌与返回、下井与通道分为明确切点，所有原句只出现一次。记录装置佩戴/摘下、同一把钥匙及手机取证状态。测试原句拼接、警报不朗读、三声源、全部镜头恰好覆盖、组时长4–15秒、人物状态及三项完整选项。

## Task 2：接现有生产契约

新增interactive/authored.py提供prepare_authored、pack_authored、build_authored_voices。production.pack仅对显式authored_family=travel的稿分流，恋综原路不变。prepare输出独立master，拒绝覆盖既有目录；pack仍调用现有prepared_shots/clip_entry/plan_issues和H3英文准备流程。三个声源分别保存样本视频、请求、声音库和次数。环境及道具按现有资产库生成并保留失败/重试；不复用林野脸、声线和乡村场景。道具上的确切文字走既有观察平面后期，定位失败保留问题。

## Task 3：完整H3生产与评估

冻结独立adventure-laughing-country-h3-768，现有run_episode完整运行，原max_attempts=2与每单元最大3次不变。不边生成边人工看片、改稿或追加重拍。全部结束后检查成片、实际请求/ASR/音频数据和完整选择卡；对“完整合格”与“继续观看欲望”分别判断，未试听声音不声称听审通过。新图、样本、正片各自记录数量和预算，旧版全部次数保留。

## Review focus

- 原稿670汉字含警报注释，不能误把注释念出来；原文另文件未提供，校验以附件为依据。
- 系统/守卫是画外声，不借用知遥脸，不把无声衔接变旁白。
- 井口/里世界/矫正中心切景与钥匙、装置状态保持来源；复杂动作不重复。
- 三项投票承接完整本集，不伪造计票、发布或已选下一国。
- 新episode拒绝覆盖，旧edition与SD额度停止标记不清除。

## Execution ledger

- Ruling：用户“直接排这个”授权按附件完整制作，覆盖技能再次询问方案/执行方式要求；沿用已选择的本会话直接执行。
- Ruling：291秒正文后追加3秒选择卡，原文不删、不倍速；预计294秒，实际超时报告，不牺牲台词完整。
- Pre-flight：工作区/项目规则、在途板、制作经验及现有未提交实现已读；新增小模块及项目数据，不改恋综文件或配置。

- Task 1: complete. 55原镜拆为58制作镜、31单元；原句拼接完全一致。离城独立3秒镜因后端最短单元补1秒，正文292秒＋卡3秒＝295秒。
- Task 2: RED→GREEN，6项新回归通过；文字多行适配。实产定位扁平8点遮挡返回与编码异常管道未关闭造成死锁；支持该实际几何形状并在异常时关闭管道、有界退出，不重拍。
- Ruling：新稿昼夜与架空城市使用独立travel-live风格，避免旧自然荒野母版强制阴天午后；旧风格与恋综不改。

- Final review: 2 Important，单次修正。L09拆成现场观察＋知遥自拍“好可怕”，L20/L25/L42补真实入镜名单；原句和总时长不变，现59制作镜/31单元。声音预算改用provider既有before_video_submit，新提交扣次，纯轮询/下载不扣；逐次请求与task回执保留，2次上限仍在。两项均RED→GREEN。尚无新视频，准备master同步修正并重新pack。

- Validation: 3210/3210全量测试通过，73.64秒；最终源句/镜头归属/有界声音续接回归通过。
- Ruling：已结束的林野后期评估实际看到旧sign透明字层叠在模型乱码上，未解决；本集已有text_panel改为遮盖原字的实体牌内平面，7处确切文字统一采用，目标文字和实际拍摄请求不变。只修后期，不重拍原集。
- Full production: 新稿pipeline已启动，素材阶段在途；不做人工逐段改稿或额外重拍。

- Native metadata fix: 旧H3已出片的实际768×1344却标480p trial，原因FramedLocalH3将sampler放在local而非顶层short_edge。读adapter既有sampling_parameters，H3标768P/非SD试制；测试RED→GREEN。旧错误元数据留备份，只改尺寸标注，不新生成。

- Preparation outcome: 13地点、3道具、3声音样本完成，声音各1次；H3英语准备29/31，B/C投票预览的后期字幕被误当生成画面文字，返回CJK违反既有英语格式门。正文native视频0次；保留first-pass计划/错误/日志。
- Generic repair: request_context显式传递本镜已有postproduction_overlays，规则说明后期字幕不由视频模型画，语音与视觉动作不改。测试RED→GREEN，原29有效请求缓存保留；同edition完整续跑补齐2个失败准备，不重拍、不改稿、不清计数。

- Post-overlay regression: 全量检查捕获旧翻译contract被全局规则改动影响；改为只有本镜存在后期层时追加POST_OVERLAY_RULES，旧contract逐字不变。48项相关检查及完整3212/3212通过，72.61秒。实际两项准备恢复成功，31/31已进入同edition完整拍摄；初始请求失败记录保留，未改台词/镜头/素材/种子或次数。

- Full pipeline outcome: 首轮运行中断后按原记录续跑，无次数清空；自动修正clip05，自动重拍clip17/24各一次。最终30/31单元，32次正片，clip12输入修正报source speaker could not be established，正式交付incomplete。16新图＋3声音样本保留，声音各1次；另保留失败服务探针记录。
- Unified evaluation: 完整流程退出后核对全部30段120张分段抽帧、实际资产/请求/ASR。装置佩戴输入与连续性、不可见钥匙仍给参考、全局里世界状态误施到预览场景卡、历史回放漂移等未解决。无音轨听审。详见delivery/adventure/laughing-country-h3-768/README.md。
- Review artifact: 305.175秒incomplete-review.mp4，1080x1920/25fps/H264AAC，原生配音＋识别字幕，缺段以8秒卡明确标记，末尾完整三选项3秒。只做本地拼接，无额外生成或重拍；不是最终合格片。验证媒体规格和末帧选项完整。

## 用户追加：缺了就补（2026-10-13）

- 明确授权补缺并重新合成；按novel-video-repair在沙盒准备既有RepairProposal，再以原history发布/续跑，不建新edition规避上限。
- 保留clip12全部台词、说话人、8秒及镜序。补回原稿守卫/知遥声源标签；使用clip11已采用视频6.8秒的单人穿戴状态帧，承接可见头带固定耳侧装置。该帧按character引用，撤掉本单元无佩戴状态的独立道具设计表；不重画整库、不修改其余30段。

- Missing unit fill complete: 输入检查通过后首次生成虽有片段但未明确跑动、ASR疑少“糟了”；在原3次上限内追加一次明确追兵/连续奔跑/完整短句的动作修正，第二次出现守卫与奔跑，ASR CER0/missing0。其他30段逐项保持。
- Accounting: targeted renderer运行的trial最初未标managed，实际生成事实已在renders内；据原事实补回managed归属，未新增生成/预算，最终clip12=2、全片34。前快照保留。
- Delivery: 31/31、305.44秒完整候选，25fps/H264AAC/1080x1920、末75帧完整选项；已检查最终文件补段位置与末帧。其它原有画面/媒体质检问题不伪装通过。旧不完整审阅版和第一补齐候选保留，未提交推送。
