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

|Codex integration|共用链路已合入main并同步本分支|romance|保留本分支内容、运行配置与后续增量|main=018e392；共用3263测试和独立复核通过，本分支3268完整测试通过；保留内容增量，无生成/推送。|

|Codex 恋综业务参考|按用户八项业务标准整理后续版本要求|codex/romance-sd25-continuity|projects/romance/business-reference-20261014.md|完成要求整理：横屏、前5秒关系钩子、档案卡/花字、情绪配乐、连续互动及发布包装；新要求优先于旧竖屏/无配乐方案。短链接未读到原视频，不冒充实看；未改生产配置或启动生成，未提交推送。|

|Codex 新恋综创作|原创素材包点子与既有唯美风格适配|codex/romance-sd25-continuity|projects/romance/anonymous-heart-v1/ 创作草案|已交付《先别认出我》创作草案：四位成人嘉宾、首集5秒误认开场/本集揭晓、3条后续选择、唯美CG美术及横屏包装方向。当前不是可投产完整资产包；未生成美术/视频、未修改旧包/生产输入、未审片，未提交推送。|

|Codex 恋综概念包装|新故事的原创主题包装|codex/romance-sd25-continuity|projects/romance/anonymous-heart-v1/README.md|用户否决MBTI后，撤下类型方案，草案改为《未署名的花》：4种花信邀约、匿名误认、首集揭晓、选择影响下次相处；开场/互动/人物卡/封面同步设计。仍是待定创意，没有美术或视频生成，未提交推送。|

|Codex 花系恋综制作|完整素材包、四人/温室唯美参考、H3 20–30秒小样|codex/romance-sd25-continuity|projects/romance/anonymous-heart-v1、interactive/authored.py/conversation.py/runtime.py及测试、独立outputs/runtime/delivery|完成：4人唯美CG、同温室2机位、4花/声音/8套包装/195秒首集剧本，完整素材ZIP 71文件；仅拍两段，F01第2take/F02第3take经Agent接受，最终27.56秒原生768P横屏小样与包装复审/技术媒体门通过。视频＋ZIP Taildrop传Mac均exit0。全部台词独立ASR检出，无实际听审/逐音节口型认证；未扩整集。25针对＋3275全量测试及独立代码复核通过；旧片/失败take保留，代码未commit/push、无服务变更。证据delivery/romance/flowers-20261014/delivery.json及mac-transfer.json；后续整集需用户选择后启动。|

|Codex 花系恋综整集|H3 768P整集；用户禁止手动修片，现仅标准整集生产Agent审修|codex/romance-sd25-continuity|flowers-full outputs/runtime/delivery、interactive/conversation与公共review/episode/recovery及对应测试|已停在外部权限阻塞：43次实际生成不变；原4失败镜12/13/15/16及自动复审新增07疑点未过，未合成/未发Mac。用户纠正后停止逐镜选图/改提示词/采纳/定点拍摄，15手工候选未提交。标准flow.run_episode(batch_review)接管原集，保全部历史；修复正向证据KeyError、模式断点丢证据/旧verify误计、视觉覆盖语音反馈及超时空转未回退等通用缺陷；3289全量及独立代码复核通过。默认备用Agent按既有配置自动触发一次后返回403 Model isnotallowedforthisplan，无新增影片；无在跑任务。需可用的配置Agent通道才能继续，不能人工补片冒充通过。未commit/push/服务变更；证据.runtime/romance-flowers-full-20261014/automatic-blocker.json。|

|Codex 花系整集续跑|用户明确指定DeepSeek V4.1 Flash、禁用备用|codex/romance-sd25-continuity|仅本任务runtime启动配置、原整集Agent与状态记录|已实际用4.1续跑并自动停止：两次600秒/exit124，均无候选、无host修复操作，触发既有no-progress=2。备用已关闭；43次生成及07/12/13/15/16未过项不变，无合成/发Mac/手工修片，无在跑任务。保留1979秒未用活动时间、完整历史与旧403回执，不改其他会话配置。任务局部配置语法及实际model/禁用备用/计数核对通过；未新增公共代码或提交/推送/服务变更。证据.runtime/romance-flowers-full-20261014/automatic-blocker.json。|

|Codex 花系整集Luna续跑|用户指定先用GPT-6 Luna备用Agent|codex/romance-sd25-continuity|仅flowers-full本任务运行配置/状态、现有通道最小探测|已将本任务model设gpt-6-luna、关闭其他自动切换；实际POST/v1/messages返回402：Both daily quota pools are exhausted。模型列表200未列Luna，但额度先拦截，不能断言该型号获准/实际已调用。未启动耗时Agent或H3，43次计数/审片/原1979秒未用时间保留；无手工修片、合成、发Mac。启动脚本语法/任务配置/计数核验通过，无公共代码修改、commit/push或服务变更。证据.runtime/romance-flowers-full-20261014/gpt6-luna-route-preflight.json。|

|Codex 花系登录Luna接线|用户澄清使用Codex ChatGPT登录的GPT-6 Luna|codex/romance-sd25-continuity|episode/session.py、新Codex调用适配及测试、仅本任务配置|本轮已结束未验收：真实Codex ChatGPT登录GPT-6 Luna/provider=openai，原集标准flow自动采用5镜修复、实际H3重拍6条（07两次），累计43→49，未清历史/补预算。当前12/16段通过，07/12/15/16仍未过，16另有额外发言；当前重试动作耗尽。自动finish仅存诊断候选，silence_ratio也未过；无正式包装/3秒互动/发Mac，无在跑任务。主会话未看生产帧/逐镜改片。登录原生与容器、真实宿主交接及同会话续接通过，模型rollout核验及3296全量通过；无prepared标记误readopt通用入口缺陷已修。源改动未commit/push/merge，无服务重启。证据.runtime/romance-flowers-full-20261014/codex-login-result.json。|

|Codex 花系失败原因核查|回答用户为何仍未通过|codex/romance-sd25-continuity|只读现有请求/评审/限额；Luna Agent只读看当前帧与参考；diagnosis-20261015记录|完成：12/15/16文本已明确空手/桌花且参考状态一致，当前H3仍重置取放动作；07独立样本见首帧先持玫瑰后去取箱，未复现旧下半身残留指控（同take但未确认同抽帧组，未清生产失败）。诊断Agent撤销12参考冲突误判。当前停止是视频重试上限；另原音候选silence_ratio53.5%、音乐包装待办；16额外发声仅ASR标记未实际听审。0新生成/生产修改/预算修改/提交/推送。证据.runtime/romance-flowers-full-20261014/diagnosis-20261015/why-failed.json。|

|Codex 花系经验交接核查|回答修复skill与成功经验是否实际使用|codex/romance-sd25-continuity|只读原生Codex调用日志/prompt快照/recall代码与当前经验目录；skill-experience-audit.json|确认Luna首轮实际读整集SKILL、repair-playbook及review-and-repair；通用经验已读。各次related_history均只有本集unresolved，首轮无可还原修法；recall仅扫当前source_novel_dir/.production-memory（本次仅自身1文件），未交接跨项目具体成功案例。已读skill有替换冲突规则，07仍追加相反时序且check未拦。未改skill/源码/生产/预算，0新生成；49计数与未验收结论不变。|

|Codex 花系经验验证|用户要求验证成功经验召回与Skill执行|codex/romance-sd25-continuity|隔离experience-validation-20261015；真实经验只读、召回/check复现、Luna候选审阅|已按用户纠正停止案例中心的验证与候选改写；旧隔离召回/check对照保留，未改召回代码或生产。候选文件未生成。当前改为Skill前置通用原则验证，详见下一行。|

|Codex 通用修复原则前置|按用户纠正，将经验总结成Skill开头概念|codex/romance-sd25-continuity|skills/episode-production/SKILL.md及隔离principles-only评估|完成：前置替换旧冲突/唯一时序与状态承接/证据定位/区分check与实拍验收四条概念；保留兼容细节可补充。无历史案例的Codex登录Luna首轮把后文覆盖误判成已替换；进一步明确最终文本须删除旧句后，相同任务/输入复测4例均符合预期。真实安装Skill字节与源一致、Skill格式及针对加载/会话回归通过。仅验证这4例规则理解，未证明实拍改善；生产5份文件字节未变、49次不变，0重拍。文档未commit/merge/push，无服务变更。证据.runtime/romance-flowers-full-20261014/experience-validation-20261015/principles-validation-result.json。|

|Codex 新Skill四镜实拍轮|用户明确追加一轮：Codex登录Luna/H3 768P，审过再合成发Mac|codex/romance-sd25-continuity|原集/原SDK；一次性4镜新授权、整集batch_review先审后合成、既有BGM参数接线、针对测试|本轮已结束未验收：Codex登录Luna原SDK重读新版Skill，自动整段改写4镜并各实拍一次；49→53，其他12镜未重拍，旧授权/计数保留，新授权不随重启续发。四条实测1344×768/24fps/12.25s，语音均过，15画面通过；当前13/16段通过，07/12/16仍画面失败。落实batch_review未审过不合成，finish movie=null；0本轮合成/发Mac，无在跑任务。批准BGM已接既有混音，完整片QC因内容失败未进入；3302全量通过。主会话0逐帧看片/提示素材手修；代码未commit/merge/push，服务未重启。证据.runtime/romance-flowers-full-20261014/new-skill-round-20261015/result.json。|

|Codex Luna三镜先诊断后实拍|当前用户要求先查07/12/16实际请求/参考图/画面，再追加一轮H3 768P|codex/romance-sd25-continuity|原集及原Codex登录Luna会话；diagnosis-first-round-20261015诊断/授权/实拍/复核记录|本轮结束未验收：Luna看实际引用图与同take画面，并用2fps连续证据纠正稀疏抽帧误判；追到重复speaker前缀最早出现在Agent候选，普通裸对白槽可修、无阻塞代码缺陷。诊断完成后独立新授权3镜各补拍一次，53→56；实际1344×768/24fps，实交请求重复前缀清零、源台词/声线/时长/脚本及另13镜保留。07行动者已改对但未保持指定连续主机位，穿桌指控未由连续证据证实；12开场玫瑰桌面正确且栀子释放保留、后段仍触碰玫瑰；16开场正确、后续仍拾花。Agent只读动作核验及一次普通review后仍13/16通过，额外核验0生成；finish movie=null，0合成/发Mac、无在跑任务。07 ASR未收回短回复但门检过，未听审，不据此断言漏说。主会话0看片/镜头提示素材手修；本轮仅运行记录/调度/登记、无产品源改动/commit/merge/push/服务重启。结果.runtime/romance-flowers-full-20261014/diagnosis-first-round-20261015/result.json。|

|Codex 用户成片标准复核及16补拍|用户明确07换机位/12轻碰花不自动失败，重点真实连续性；16另追加一轮H3 768P|codex/romance-sd25-continuity|原集/原Codex登录Luna；user-criteria-round-20261015；逐镜用户标准、Agent keep复核裁决记录接线及测试|结束未验收：原SDK Luna按标准复核，另开独立Codex登录Luna只读审片并针对12原花瓶做源证据核对。16经Agent改构图/整段替换提示后本轮留存实拍一次，56→57；实测1344×768/24fps/12.25s，其余15段与原脚本不变。正式keep+review_file裁决已通过宿主校验写入既有confirm/picture_review证据通道，绑定当前session/take/task/原文/用户标准，保留旧自动误判和独立复审；07已接受，12确认同帧独立的额外红玫瑰（非原绿色花器、非轻碰），16约7.12秒拿起栀子/9.09秒放回。当前14/16通过，12/16真实未过，旧首帧持花/强制主机位等错误指控不再覆盖当前结论；只读对账验证裁决保留。无手工看片/提示素材手修，复核阶段0生成，无新合成/发Mac，无在跑任务。新增逐镜标准和Agent裁决通用接线3319全量通过、65项专项/兼容检查通过，语音/技术门保留；全片包装和混音QC因内容未过未执行。代码本地未commit/merge/push，无GPU服务重启。详见.runtime/romance-flowers-full-20261014/user-criteria-round-20261015/result.json。|

|Codex Luna修12/16各加一轮|用户要求修重复玫瑰/实际拿花，其他14镜别动，Agent审过再合成发Mac|codex/romance-sd25-continuity|原集/原Codex登录Luna；two-shot-repair-20261015；仅12/16各1次授权；scoped裁决写回及对账回归|结束未验收：Luna整段替换并调整构图，非只追加限制词；12/16各留存一次H3原生1344×768/24fps/12.25s，57→59，历史保留、另14镜take及原剧本未变。独立Codex登录GPT-6 Luna审当前请求/引用图/连续采样，原Luna正式裁决同为fail：12额外红玫瑰已消除，但白栀子发生接取/转移并落到SE礼物区域；16仍拿起栀子交给闻屿，人物原座/箱序未见乱，机位/轻碰不作失败。当前14/16过；各1次额度用完、后续提案被正常阻止。修复scoped keep已通过快跳和旧resolved证据压住新裁决的通用写回缺陷，现有journal正常对账保留两份完整裁决、失败等级不变，复读稳定；3322全量及此前68专项通过。主会话0看片/镜头提示素材手修，0本轮合成/发Mac，无在跑生产；完整包装/混音QC未进入。代码本地未commit/merge/push，无服务重启。证据.runtime/romance-flowers-full-20261014/two-shot-repair-20261015/result.json。|

|Codex 花系当前版直接交付|用户明确要求直接发当前版本到Mac|codex/romance-sd25-continuity|仅direct-preview运行助手/交付目录及在途登记|完成直接预览交付：当前16段合成199.12秒、87,795,836字节H264/AAC，字幕沿用同take ASR编译、既有批准BGM仅混一次，末尾既有互动卡恰75帧/3秒；1080横屏导出、原生H3 768P。完整解码/格式/时长检查过，16个take及四份生产源/评审/报告字节未变，59次保持，12/16仍fail；0新生成/复审/手动修片/生产放行。2026-10-15 17:26:29 Taildrop发送端exit0到既定Mac100.92.12.100，接收目录与实际播放未核验。预览未加尚未完成的档案卡/姓名/花字包装。交付目录delivery/romance/flowers-direct-preview-20261015；delivery.json仍candidate，mac-transfer.json为sent。无产品源码/commit/merge/push/服务改动，无在跑任务。|

|Codex 花系交付反馈定位|用户反馈当前Mac预览01:11/01:18/03:13画质、震动、位置变化|codex/romance-sd25-continuity|仅delivery-feedback-20261015隔离证据/Agent启动助手及在途登记；原片/交付/请求只读|定位完成，震动仍开放：71/78/193秒分别06/07/16；16原片全1344×768/24fps、实际request/task采样一致，导出统一1080/25fps，无按段降档。中间concat接点56ms、最终40ms，未证明与78秒震动有关。首轮Luna文字自称看图但无实际图像输入/调用，结论未采用；改为Codex登录GPT-6 Luna原生图片附件，同SDK续审24/25fps条带与真实参考，保存rollout核实29个input_image。实际复核：06宽景皮肤/衣发微纹理平滑塑料感、原片已有，突降起点未证；07起身有轻微局部形变，未确认背景震动，保留未解决不判过；16约191–193s真实拿栀子交闻屿，原片与成片一致，未证实换座；未附图的开场引用不采为视觉证据。59次及生产四份记录/所有take不变，0新生成/生产修改/主会话看片/手修/导出/发Mac。无产品源码/commit/merge/push/服务改动，无在跑任务。结果.runtime/romance-flowers-full-20261014/delivery-feedback-20261015/result.json。|

|Codex 花系反馈时点纠正|用户明确01:18位置问题、03:13震动|codex/romance-sd25-continuity|仅delivery-feedback-20261015/corrected-markers-20261015证据及Luna同SDK只读续审|纠正复核完成：实际Codex登录GPT-6 Luna同SDK新增22张图片，rollout累计51个input_image，v3所有图像引用均已实际附入。78秒确认06→07跨切三人换座：阮夏NW→SE、程越NE→NW、许棠SE→NE，闻屿SW不变；房间地标基本同轴，原请求座位未改，源07开头即错，成片保留。之前按起身动作/可换机位判断空间无错漏检，相关结论撤销。193秒按191.5–194.5s原生帧率条带专查震动，见升镜/侧移和局部变化但未定位明确快速震动，保留未解决不判过，拿花为另一问题。诊断报告未擅改07生产grade或代原producer采用修复；59次/所有take/生产四份记录不变，0手工看片/修片/生成/导出/发Mac。无产品源码/commit/merge/push/服务变动，无在跑任务。最新结果.runtime/romance-flowers-full-20261014/delivery-feedback-20261015/corrected-markers-20261015/result.json；旧报告保留并标记被纠正。|

|Codex 短剧消抖经验检索|用户要求找短剧侧已解决的镜头抖动案例|codex/romance-sd25-continuity|仅short-drama-shake-cases-20261015本地证据及Luna只读对照；小说项目只读|完成：找到754/01:16最完整案例及722/752佐证，核旧请求/采用候选/正常apply/新take运动及发布验证。754修正街道→楼顶参考并整段替换过肩正面混杂构图为同机位三分之四正面单人物；722/752固定机位背景、动作局限人物，722另修重拍引入的重复身体。不能归因某一词；752专项未完成、722攻城车缺失均保留。754经验在小说Skill行中，恋综当前源及Luna安装Skill缺该行。实际Codex登录Luna同SDK已读取完整证据对照51张历史实图上下文，本轮未新看旧案图片。复用原scanner函数未修改，当前16原24fps/归一化25fps分别33/34个高频往返位移候选，193秒窗每秒约23/22次反转；原片已有信号，非证明转帧成因；Luna仍留主观震动未确认，不能当全通过或一词通用治法。小说只读、Skill及生产四份记录/take/预算未改，59次不变，0新生成/主会话看片/手修/导出/发Mac；无产品源码/commit/merge/push/服务改动，无在跑任务。结果.runtime/romance-flowers-full-20261014/short-drama-shake-cases-20261015/result.json。|

|Codex 共用Skill同步|小说/恋综/荒野/悬疑共享生产修复经验|codex/shared-skills-sync-20261015|仅共用指导及安装副本；保留本地协议和WIP|已完成共用Skill同步：统一episode-production/video-repair的Core与有限案例，754/722/752经验对齐；全局novel-video-repair同原则/案例同步。各版本工具协议、小说规划/运镜及荒野source_patch WIP保留，仅共用增量本地提交。40项加载回归、15包格式与各源公共段一致性、独立审阅、Codex登录Luna新建/同SDK续接真调用均过；当前恋综Luna已实际重读新版，旧attempt快照保留、其他在跑冻结任务不热替换。首个纯文件Docker探针有bwrap namespace权限问题已单独留档，正常工作流模式验证成功，未改主机配置。花系59次不变，0新视频/手工修片/推送/服务或额度变动。记录/mnt/disk1/zengzhitao/interactive-video-worktrees/skill-sync-20261015/.runtime/skill-sync-20261015/result.json|

|Codex Singularity模型对照＋重影LoRA调研|完整INT8v1.3下载、原四卡768P两段对照及真实Codex登录Luna审阅|codex/romance-sd25-continuity|仅独立runtime/delivery、GPU052独立模型/环境/临时服务；源码及既有生产只读|完成：4条独立样本、49.032秒对照片会话内可播放，按新指示未传Mac。Luna20张原生图同SDK复审，06原版相对稳而新版重影，16新版递花较好但两版三人构图未证满足；不代表原版合格或整集通过。34GB完整INT8源SHA校验过、复用底座；原四卡服务health/models已恢复、临时服务/转发已关。原生产59次保持，无人工看片/修片/重拍/源码修改/commit/push。LoRA研究确认两版都只额外挂一支8step，当前Ref8参数符合作者12/3；社区推荐4stepv0.1配方不同，未证明LoRA因果，研究0新生成。证据.runtime/singularity-ab-20261016/state.json及research/lora-ghosting-findings.json。|

|Codex Singularity作者配方实拍|作者配方768P三场景对照与H100彩噪根因修复|codex/romance-sd25-continuity|仅.runtime/singularity-author-20261016、独立delivery、GPU052测试环境；悬疑/荒野源只读|已收尾：Sage2.2 SM90真实函数被同名fake覆盖，按上游d970424一行修复；数值前错后正常，同输入重拍消除彩噪。恋综重影减轻但重复人物疑点/近景偏离仍fail。悬疑/荒野各1条768P对照完成，Luna真收15/17图、均偏原H3（机位/路线更守源）；比较片前原后新且画面流复制。新作者实验4条含1旧坏片；旧SG4条/生产计数保留。原四卡服务恢复healthy且模型ID一致；无本任务GPU任务、手工修片、Mac传输、commit或push。完整记录.runtime/singularity-author-20261016/result.json。|

|Codex 作者SP4与同步对照|作者完整配方主DiT四卡并行、原H3对照、增加镜头、Luna审片及左右同步|codex/romance-sd25-continuity|仅.runtime/singularity-sp4-ab-20261017、独立delivery、GPU052测试环境；其他分支源只读|已完成：真Ulysses SP4，实际checkpoint Sage/Sol双路径均与作者native逐值一致，四rank各600block/段；6对同素材/提示词/seed768P实拍+1对热机复测。登录Codex Luna6案101原生图、风格规则澄清复核15图，5案偏原H3、1案偏作者，不代表全片/音频验收；恋综作者重复人物仍fail。3个左右同步双段播放器、原片/阶段耗时/历史失败保留；封装音频EOF问题已限定结束时间修复，未改原始片或艺术内容。原四卡服务healthy且模型ID相同，无本任务遗留GPU任务、Mac发送、commit/push；未改生产或其他分支。记录.runtime/singularity-sp4-ab-20261017/result.json，state doctor无问题。|

|Codex Singularity优势覆盖实测|打斗/远景人物及作者宣称优势逐项对照|codex/romance-sd25-continuity|仅.runtime/singularity-strengths-20261017及独立delivery；小说/其他分支源只读，原四H100|已完成：9原有片段+Luna看5图写3诊断，共12对768P及双边热机复测（26条生成）；实际SP4双路径验证、每rank600block/段，原服务healthy/models相同。登录Codex Luna12案227图、时间证据复核20图，6案偏作者6案偏原，均为相对比较；部分CG冲击/VFX/材质好，远景同尺度保真未证、构图控制退化仍在。12段采样470.2→559.5秒(+19%)；3左右同步播放器，机械全解码/帧数通过，Luna拼接11pass/1uncertain原样保留。首段客户端时钟排除、剑斗阶段统计尾残片按完整正确请求JSON及journal交叉恢复，无重拍。模型/环境复用，新产物共享盘，无人工看片修片、生产变动、Mac发送、commit/push。记录.runtime/singularity-strengths-20261017/result.json。|

|Codex 三题材抖音配乐剪辑小样|拉取验证dy-cli与SoulTuner，恋综/悬疑/荒野各一段Agent剪辑配乐并审片|codex/romance-sd25-continuity|仅.runtime/douyin-bgm-pilot-20261019、独立delivery及本行；原剧集与其他分支只读|SoulTuner CPU功能实测已完成：37首真实公开曲目、M2D768维索引、3次DeepSeek4.1完整工作流；Luna复核为恋综待核/悬疑局部可试/荒野未通过，三类均返回11首而非3首。交付delivery/agent-evaluation/soultuner-20261019三候选试听和报告；抖音仍需用户App本人验证，三段热门BGM成片未完成。仅实验目录/交付目录/本行，无手动艺术修片、生产GPU变更、commit/push。|
