# 当前工作

|会话|任务|分支|范围|状态|
|---|---|---|---|---|
|Codex|三个包接入、H3与SD2.5探险完整候选片|codex/adventure-demo|interactive模块、配置、验收修复与本项目产物|已收尾；未提交/推送。两版候选可看，质检问题见delivery/adventure/README.md；后续按原预算继续，不清空历史。|

|Codex|心动未剪辑共用首集，H3与SD2.5两版|codex/adventure-demo|romance导入、横屏交付、声线/素材库、本项目新输出|首轮结果已记录；当前SD2.5通过、H3完整候选已导出并传Mac，H3续修结论见下行及delivery/romance/README.md|

|Codex|H3恋综完整首集恢复，对比SD2.5|codex/adventure-demo|作者分镜的H3请求修复、原预算续跑与交付|H3完整188.40秒候选已导出，未全过质检；SD2.5不变。3180测试通过，未提交/推送，两版Mac传输均成功，详见delivery/romance/README.md|

|Codex|H3原生768P小段A/B/C，两种种子共6条|codex/adventure-demo|本项目H3分辨率/显式种子接线、独立实验与对照交付|6条已完成、原生1344×768；语音检查6/6，C相对稳但衣装仍有坏例。3181测试通过，未提交/推送，对照ZIP已成功传Mac；见delivery/romance/h3-768-ab|

|Codex|重新生成H3恋综完整首集，原生768P v2|codex/adventure-demo|单视图参考选择、单镜头请求、原素材说明冲突修复与独立新版本|297.32秒完整候选已生成；未全过质检，3185测试通过、未提交/推送；本地检查替代耗尽的API配额，完整候选MP4已成功传Mac；详见delivery/romance/h3-768-v2|

|Codex|H3沿用已通过SD2.5的16段节奏，768P完整首集|codex/adventure-demo|独立输出romance-h3-768-sd-rhythm、输入复核图片证据、源动作请求修复、记录与交付|后续授权重启已补齐16段，188.40秒候选及90.55秒对照均成功传Mac；必修10与静音未过。四卡UUID绑定已修，原0/3仍未识别；详见下一行和delivery/romance/h3-768-sd-rhythm。无生产/传输进程在跑，未提交/推送。|

|Codex|用户授权再次重启GPU52并续拍H3|codex/adventure-demo|GPU52重启/原服务恢复、H3四卡UUID固定、同edition续跑和Mac交付|本轮结束；H3可用、原四卡UUID固定，原0/3仍未识别。完整188.40秒候选已成功传Mac，格式通过，必修10重复人物及52.83%静音未过；所有旧计数保留。模型/环境未改、未提交/推送，无本任务生产/传输进程在跑。|

|Codex|SD2.5恋综人工反馈诊断，查阅Blender白模worktree|codex/adventure-demo|最终SD成片、原分镜/实际请求与旧实验只读核查；本地证据及制作记录|已收尾：确认英文空格被编译删除、可见性与房间在场状态混淆、座位绑定/行李持续状态丢失；最终成片1:32缺已落座人物、2:37五椅。旧白模实验可借几何/机位/placed角色区分，但曾有替身泄漏、转真人图忽略机位，未证明SD有效。未改生产提示词/代码、未新生成、未动旧worktree或远端、未提交/推送。证据见.runtime/romance-sd25-feedback-20261013/diagnosis.json。|

|Codex|恋综26秒SD2.5结构＋白模对照|codex/romance-sd25-continuity|本worktree；continuity源字段修复、prompt_text保留英文词界、白模与四条有界样本|生成/评估完成：A/B各26.144秒，四次提交，无艺术性重拍。B保留人物/行李有改善，但宋乔坐错椅、跨段机位/座位未锁，表/灯新增、语音未过。3191测试通过，旧生产/次数不变，临时参考HTTP/隧道已关；两份Mac发送均exit0，无本任务生产/传输进程在跑。无commit/push。见delivery/romance/sd25-continuity-ab/review.json。|

|Codex|按romance-reality-show优化恋综首集|codex/romance-sd25-continuity|projects/romance/skill-v1 独立改编稿、来源差异与导入校验|改稿完成：11处对白，63来源镜号、16组，正文183秒＋3秒投票；script/storyboard/chapter_script同步，全部参考路径与本地导入检查通过。未编译最终视频请求、未生成、未提交/推送；旧A及生产历史不变。全文见projects/romance/skill-v1/episode01.md，下一阶段为按新稿重新编译和成片验证。|

|Codex|skill-v1完整SD2.5生成并传Mac|codex/romance-sd25-continuity|outputs/romance-sd25-skill-v1、delivery/romance/sd25-skill-v1及独立运行记录|已完成用户改选480P：16段首拍，187.16秒完整候选，854×480播放版22,571,683字节，Taildrop传Mac exit0。媒体与交付技术检查通过；6段内容通过、10段审查接口502，未标正式通过、无追加重拍。原720P四段仅收回留档。无commit/push，记录见.runtime/romance-sd25-skill-v1/mac-transfer-480p.json。|

|Codex|skill-v1新旧SD成片对照诊断|codex/romance-sd25-continuity|只读源稿/实际请求/成片；.runtime/romance-skill-v1-review证据|已完成：新旧16段共128抽帧及实际请求/对白核对；总体未改善，问后坐局部修正，座位/缺人/行李仍坏，结尾卡片退步。63空end_state、47切镜指令未变，源剪辑note未进SD请求。证据.runtime/romance-skill-v1-review/review.md；无生成/原片修改/commit/push。|

|Codex|恋综连续交流编译接线＋H3三人26秒A/B|codex/romance-sd25-continuity|interactive/conversation.py、romance分派、测试、独立短样|完成：新编译入口与实际SD/H3请求测试接通，独立审阅修复H3表演cue遗漏，3195全量通过。H3四条完成；B第一段复制宋乔，跨段换位，短样未通过，不扩整集。已拍为cue修复前冻结快照，修后仅离线核验。AB54.828秒与B27.4秒480P均传Mac exit0；无commit/push。详见delivery/romance/conversation-h3/review.md。|

|Codex|H3连续交流修复版短样|codex/romance-sd25-continuity|outputs/romance-conversation-h3-v2、对应runtime及delivery|完成：V2两条＋用户追加Blender两条，各版27.4秒；逐秒抽样均保持三人/四椅/三箱，无明显跨段换位；不能证明白模额外收益。Blender整段ASR覆盖台词，自然度/换机位未验，未扩整集。两轮本片与对照均传Mac exit0；空间参考接线3196测试通过、独立审阅通过，无commit/push。详见delivery/romance/conversation-h3-blender/review.md。|

|Codex|H3近景反打起身26秒短样|codex/romance-sd25-continuity|独立outputs/romance-h3-coverage-motion及runtime/delivery|完成：3条首拍，交付26.91秒/854×480，Mac发送exit0。近景/反打/起身有出现，抽样人数与三箱稳定；但第三段回到坐姿后重复起身，连续性未通过。局部ASR找回好，未冒充自然度通过。无追加重拍、无整集、无commit/push。详见delivery/romance/h3-coverage-motion/review.md。|

|Codex|H3竖屏恋综首集＋审片|codex/romance-sd25-continuity|独立romance-h3-vertical-v1、conversation画外广播支持、当前H3/审片配置|修复续拍中：首版196.88秒人工审片失败，用户明确要求不通过即修复重拍；失败片Mac传输已终止（exit -15）。同edition采用19段参考/英文状态修复，旧20次生成记录保留；全流程重拍后复审，通过才传Mac。未提交/推送。|

|Codex|恋综同步既有Agent修复交接|codex/romance-sd25-continuity|repair/agent、帧查阅工具、修复Skill、同edition profile；荒野/小说主线只读|接线与独立复核完成：42针对＋3202全量通过，当前edition启用既有sandbox_agent。真实GLM读4张同take帧，601秒到时未产候选；输入/计数不变。R2源参考已修复并复核，同edition H3整轮续拍中；3203全量通过，语音失败take交接也已修；无commit/push。|

|Codex|按用户停止人工审片并同步另外两会话|codex/romance-sd25-continuity|共用Agent路由、三会话交接、当前影片Mac传输|当前版189.8秒已成功传Mac，文件心动未剪辑-H3竖屏-当前版-20261014.mp4；不再人工复查/改片/重拍。入口增量同步完成，3217全量通过及独立代码复核完成；未再人工审片、未再生成，无commit/push。|

|Codex|恋综现版Agent审片→失败自动H3重拍→验收后Mac|codex/romance-sd25-continuity|现有19镜/原take历史；共用viewer-first审核、既有sandbox_agent；.runtime/romance-agent-review-20261014|已完成审片修复与合成：Agent发现6镜失败，共9次新H3生成，总take61→70；6镜新take均独立复审通过，07局部ASR疑点已核实。189.8秒新片已发Mac（sender_exit_code=0）；主会话未看图。保留静音原始失败值，按批准无BGM/自然停顿稿用本edition非阻塞诊断；speech与其他媒体门保留。未commit/push。|

|Codex integration|共用链路已合入main并同步本分支|romance|保留本分支内容、运行配置与后续增量|main=0f49c49；共用3263测试和独立复核通过；无生成/推送。|
