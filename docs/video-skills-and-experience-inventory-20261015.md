# 视频创作经验与 Skill 盘点

核对日期：2026年10月15日。

现有积累足以支持一个持续负责创作的主创 Agent。当前主要缺口是素材包与原创想法的创作入口、不同项目之间的经验接入，以及同一作者从创作到制作的交接。运镜、声音和修复都有可复用内容，但保存位置和实际加载状态不同。

本次按能力去重，定位到10个主要相关 Skill 入口，另有6种代码化创作方法、镜头语言手册、声音表演经验和多组模型实验。不同工作树中的同名副本不重复计数。以下状态来自当前文件、加载代码和已有验证记录；没有重新生成视频、启动模型或改动其他项目。

## 现有 Skill 分工

| Skill | 负责什么 | 当前实际位置与接入方式 |
| --- | --- | --- |
| novel-comic-drama-director | 小说改编、创作到制作的总入口，区分原生声音与锁定录音流程 | [荒野分支入口](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/.codex/skills/novel-comic-drama-director/SKILL.md)。本会话已修正强制先配音、必传参考的旧要求；其他副本需分别核对。 |
| episode-production | 单集创作、请求准备、素材与当前证据检查，调用既有制作和修复工具 | [实际制作 Skill](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/episode-production/SKILL.md)。宿主按当前代码版本复制到单集沙箱，支持持续会话；不同分支的参考文档存在差异。 |
| h3-request-agent | 把已批准分镜和选定素材写成最终H3请求，保持对白、人物、时序与引用 | [H3请求 Skill](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/h3-request-agent/SKILL.md)。是请求创作工具，不自行决定生成次数。 |
| video-repair | 为现有片段诊断问题并提出keep、edit、input、retake、stop等当前宿主允许的候选 | [产线修复 Skill](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/video-repair/SKILL.md)。各分支保留本地工具协议，不能照搬另一版的操作字段。 |
| novel-video-repair | 跨原文、剧本、素材、请求、视频和旧尝试找最早错误，形成修复提案 | [全局诊断 Skill](/home/zengzhitao/.codex/skills/novel-video-repair/SKILL.md)。已经存在；诊断授权和实际重拍授权分别处理。 |
| romance-reality-show | 恋综关系推进、自然接话、真实空间、反应镜头与互动选择 | [恋综创作 Skill](/home/zengzhitao/.codex/skills/romance-reality-show/SKILL.md)。有参考观察与项目案例，未因此自动装进所有生产沙箱。 |
| suspense-short-drama | 悬疑调查、规则与代价、证据推动行动、事实边界及分支知识一致性 | [悬疑创作 Skill](/home/zengzhitao/.codex/skills/suspense-short-drama/SKILL.md)。有实际样本记录，适用方法按故事选择。 |
| minimax-h3-ops | H3部署、现有服务调用、卡组与精度、任务查询下载、故障定位 | [H3运维 Skill](/home/zengzhitao/.codex/skills/minimax-h3-ops/SKILL.md)。运行时先核对现场服务，不把旧主机表当在线状态。 |
| research-engineer-loop | 保留多轮实验的输入、比较条件、结果、失败与继续位置 | [实验 Skill](/home/zengzhitao/.codex/skills/research-engineer-loop/SKILL.md)。适合多轮模型/数据实验，不是每次改稿的必经程序。 |
| feishu-send-file | 通过已有应用发送明确要求交付的文件，记录上传与发送结果 | [飞书交付 Skill](/home/zengzhitao/.codex/skills/feishu-send-file/SKILL.md)。Mac交付另有已验证的Tailscale工具和回执，未封装为单独Skill。 |

通用编程、计划、代码审阅和工作树工具另有安装，不计入这10项视频相关能力。内置图像生成等工具也不等于已经接入角色资产的实际生产路径。

## 创作与分镜积累

### 六种本地创作方法

六种方法已保存在规划代码与文档中，是参考上游方法后的本地实现；它们不是六个全部自动装进生产Agent的独立Skill。

| 方法 | 已沉淀的用途 |
| --- | --- |
| 山音 shanyin | 核心戏剧动作、外部事件和内部反应的节奏、潜台词与镜头组。 |
| 社区 community | 人物和道具识别特征、物件状态、同期声、运动方向与回扣。 |
| Drama | 单集观看承诺、因果递进、每场变化与明确交接。 |
| 造梦师 dream | 场景事实、因果痕迹、摄影机观察位置、身体行为和光源。 |
| Leos | 场次重点、空间、刺激、身体调整、带目标的动作及余波。 |
| Visual | 戏剧变化、镜头职责、信息顺序、环境细节与切点。 |

共同经验是先写能通读的分场，再由导演设计镜头；正文台词、说话人、事件来源和镜头编号交接清楚。导演不重新发明已确定的故事。镜头可以长短不同，动作和对白同时发生时不重复计时。源码入口与证据见[六种方法说明](/mnt/disk1/zengzhitao/novel/novel-manga-video/docs/story-methods.md)。

### 互动剧与类型经验

已建立原始素材、采用稿、生成请求、实际视频与用户反馈的追溯。素材包里的既定事实、人物猜测和允许创作的空白应分开；原创作品由作者建立设定，采用后保持一致。

旅行伪纪录片已有本项目方向：以世界异常开场，随后用世界介绍卡建立观看前提；自拍、主观视线、行动、奇观和反应各自承担信息。线索改变下一步行动，环境镜头交代位置、障碍或线索，减少重复说明。连续剧承接已发生结果，片尾选择先铺理由，首集不替观众选好下一站。[旅行创作记录](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/projects/adventure/world-travel-direction.md)

恋综已有接话动机、人物差异、生活细节回收、座位与行李状态、听者反应等经验；悬疑已有可知规则、调查行动、误导依据、证据回读和分支信息状态。类型方法可以组合，不要求一集用齐全部技巧。

## 运镜经验已经存在

[镜头语言手册](/mnt/disk1/zengzhitao/novel/novel-manga-video/skills/episode-production/references/camera-language.md)覆盖50项：机位构图、相机运动、焦点与光学效果、组合揭示和多镜衔接。包括过肩、主观视角、横移、推拉、弧移、环绕、顶拍、拉焦、急变焦、眩晕变焦、遮挡揭示和J/L-cut等。

最重要的写法是：本镜目的 → 可见起点与取景 → 触发 → 相机路径、速度或焦点变化 → 可辨认终点 → 保持或切镜。相机和演员运动分开，前景只写当前画内局部，人物卡提供身份而不强迫全身入画；焦点交代前后两端。

已有实际H3记录，且保留失败结果：

- 早期探测累计18次，之后另有完整覆盖轮60次请求，以及复杂写法续测36次；这些属于不同实验记录。
- 完整覆盖的50项中，43项是H3独立单镜，7项是已有H3素材的后期剪辑。43个首轮单镜的核心手法观察为33项可见、7项部分、3项未见；这不是整镜合格率或稳定成功率。
- 过肩去副本、顶拍和拉焦曾因具体提示改写改善；眩晕变焦、纯光学变焦、精确路径和准时停机仍有不足。新改稿也有退化案例，不能把复杂效果直接判为模型永远不支持。
- J/L-cut实验确实做了后期衔接，但现有逐段视频接口没有因此获得跨请求声音记忆或自动J/L剪辑。

证据：[完整50项实测](/mnt/disk1/zengzhitao/novel/novel-manga-video/docs/camera-language-full-h3-20261015.md)、[复杂写法续测](/mnt/disk1/zengzhitao/novel/novel-manga-video/docs/camera-language-prompt-refine-20261015.md)。小说主工作区已把手册加入Skill入口、安装清单和共用camera说明；这些最新文件包含未提交工作。

## 声音与现场感经验

用户已试听偏好2026年10月15日的H3新B：逐句表演说明前置、情绪在事件后逐步变化、说完闭嘴观察，并完全不传参考音频。9秒小样保留原对白、图片、种子、768P及8步参数，音乐和物理声景不变；这是组合效果的采用依据。

可复用的写法已接入当前荒野创作Skill和实际Agent资料：

- 固定角色的表达习惯与每句当下表演分开。说明音高、快慢、音量、气息、重音和句尾中真正有用的变化。
- 表演说明在对应对白前、`<d>`外；原对白逐字保留。情绪由读到的规则、他人回应或现场发现触发，不提前泄露后续反应。
- 停顿承担观察、倾听或判断，结束时有闭嘴状态；时长同时容纳发声与动作，不靠加词或空等补足。
- 无参考模式核对实际音频条件为0，并同步移除Audio定义、音频保留与任务标签。需要锁定声线的任务继续尊重原约定，无参考声音的跨段音色稳定性尚未由这条短样证明。
- 脚步、纸张、衣料、装备与动作对应；音乐随事件进退并让位给人声。现场对白、私有心声、环境声和配乐分清。
- 字幕按实际语音对齐计划台词，检查模型是否已自带字幕，避免双层字幕。ASR完整和声纹接近不等于自然、有吸引力。

其他实验也是经验：原始H3权重与8步LoRA对比，用户未感觉明显收益；IndexTTS2.5方案被用户评价更差。SD2.5同场带/不带声音参考已生成并保留，但本会话没有明确的听感胜出结论。社区参考强度等建议不能写成本项目已成功的方案。

证据：[声音与采用记录](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/docs/interactive-sound-production.md)、[实际Agent表演指南](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/episode-production/references/authoring.md)、[用户试听反馈](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/.runtime/adventure-h3-frontload-noaudio-20261015/user-feedback.json)。

## 素材 生成 审核与修复经验

| 能力 | 已有经验与实现 | 实际边界 |
| --- | --- | --- |
| 外部素材接入 | 保留原始文件、素材引用、采用分镜、互动选项和来源追溯；已有统一制作接入与导入保护。 | 当前互动入口主要接已批准material和分镜，尚不是任意ZIP或一句想法的完整自主创作入口。 |
| 小说与资料继承 | 原文登记、逐章阅读、人物/地点归并、前情、外貌阶段、实体与别名、按章补缺和缓存。 | 导入原文或任务入队不等于已经读书建库；新书修复入口部分验证仍标为离线，不能视为全部真实集数已跑通。 |
| 人物与物件资产 | 同一人多视图仍是一人；身体与穿戴、独立副本、持有人、可见与在场分别表达；使用实际图片核对。 | 不能把带脸的装甲图称为空甲，不能因图库缺场景就搬动故事地点。 |
| 空间与动作连续性 | 起始—动作—结果只演一次；跨镜保留世界站位、朝向、座位、道具和装备状态；布局/白模可辅助。 | 屏幕左右不等于世界位置；首帧约束和白模不能保证后续不换座或不泄漏替身。 |
| H3请求 | 六段结构、独立图片/声音编号、人物与说话人绑定、逐镜切点、原对白保护、来源与参考一致性。 | 请求正确不代表视频执行正确；只删标签不能移除真实音频输入。 |
| SD2.5接入 | 原有资产库、真人参考、异步登记就绪、模型自己的引用语法、480P小样与并发试制。 | SD不能照搬H3语法；分辨率与可读文字必须看实际结果，不能由请求值或标题推断。 |
| 审核 | 实际原片、最终片、邻镜和当前请求联合判断；明显分屏、复制、比例错误、遮眼与关键误字有当前证据。 | 合理的构图/动作偏差不自动重拍；镜子、门窗或前后切镜本身不算分屏。声音效果需听审。 |
| 自动修复 | 从原文→剧本→参考→实际请求→当前take找到最早错误，选择保留、输入/资产/请求/声音修正、重拍或停止。 | 候选通过格式检查不等于修好；误报先复核，未知证据保持未完成。不同分支只用本地已提供的动作协议。 |
| 证据与缓存交接 | 抽帧、原生视频与实际ASR交接；缺chunks补同一take的分析；当前输入与旧判词分开。 | 不能因分析缺失重拍，不能把截图路径存在说成已看图，不能用旧错误覆盖新片。 |
| 修复经验复用 | 共用Core、repair-playbook、case-lessons和相关历史，记录实际采用、失败及结果。31个历史案例曾被引用154次，并非154种独立修法。 | 失败尝试不是成功配方；案例不新增权限、次数或本集设定。 |
| 次数与续跑 | 已接受任务优先续取，保存全部take和预算；有条件的一次追加只在指定输入和真实预检通过后激活。 | 重启不补额度，不能为重拍把历史清零；网络超时不能直接当任务未创建。 |
| 发布包装 | 统一画幅编码、实际语音字幕、封面、世界卡、水印、互动JSON与片尾完整选项。 | 世界卡/字幕不等于允许覆盖剧情证据；本会话已取消的法牌/纸条后期盖字仍保留该决定。 |

主要入口：[共用经验](/mnt/disk1/zengzhitao/novel/novel-manga-video/docs/production-lessons.md)、[修复手册](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/episode-production/references/repair-playbook.md)、[案例](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/skills/episode-production/references/case-lessons.md)、[根因层修复记录](/mnt/disk1/zengzhitao/novel/novel-manga-video/docs/agent-rootcause-repair-20261015.md)。

## 超分和其他视频项目

超分已经有独立工作台和真实对照，不是只有下载记录。[工作台](/mnt/disk2/zengzhitao/superres/README.md)包含SeedVR2、FlashVSR、FlashVSR+、Real-ESRGAN的环境和调用方法；[8段真人H3对照结果](/mnt/disk2/zengzhitao/superres/experiments/adventure-h3-20261015/comparison-results.json)中，FlashVSR+较均衡，Real-ESRGAN偏保守，SeedVR2-3B本组设置出现新增伪纹理。未测的7B和FlashVSR v1.1不能承接该结果。

通用经验是内容先验收、按镜头超分、字幕后渲染、统一规格比较。超分不能修复错误人数、人物身份或事件；本组没有高分辨率真值，新增纹理不能称为真实还原。目前主要是工具、说明与实验，尚未作为互动剧默认后期或独立生产Skill接入。

| 项目 | 可复用积累 | 与当前互动剧的关系 |
| --- | --- | --- |
| [Avatar Video](/mnt/disk1/zengzhitao/avatar-video/README.md) | 图片加音频的LongCat数字人、H3后端、分段生成、词级歌词对齐、原声与图像资产管理。 | 独立的声音驱动演唱/说话路径，不能直接替代H3原生现场口播。当前本轮没有重新验证服务在线。 |
| [MV管线](/mnt/disk1/zengzhitao/avatar-video/gesture-dance/README.md) | 歌词语义分段、音频切段、人物图引用、逐段视频、拼接与最终音轨组合。 | 文档含旧SD2.0流程，是剪辑与交接经验，不代表当前互动剧已经使用。 |
| [Idol项目群](/mnt/disk1/zengzhitao/idol-end2end-generator/docs/idol_projects_overview.md) | 人设、七图一致性、图生图参考、歌曲规划、Suno异步任务、失败隔离、并发与可续跑。 | 主要是已有服务和文档，非单独互动剧创作Skill；历史模型/并发参数需按当前任务重新选择。 |
| [ASMR与Foley](/mnt/disk1/zengzhitao/media-gen/asmr-generation/docs/project-status-and-data-layout.md) | Stable Audio动作声、Fish语音后端、素材来源和材质对齐、实际听审与分类指标分开。 | 有本地研究工具，部分路线尚未完成听审；没有自动接入当前剧集，也不因模型可用就默认换声轨。 |
| [H3夜班](/mnt/disk1/zengzhitao/h3-night-shift/README.md) | 卡组、服务占用、资源队列、启动验证、原服务恢复、任务查询和输出及时保存。 | 属于运维和产能能力；旧部署记录不证明当前主机在线或授权可动。 |

## 盘点时的接入差异（实施前快照）

截至本次文件快照：

| 工作区 | 运镜手册与安装清单 | 本轮新声音表演指南 |
| --- | --- | --- |
| 小说main实际工作区 | 有，包含未提交的运镜接线和文档 | 未找到本轮新增段落 |
| 小说episode-agent工作树 | 未找到上述新版运镜资源 | 未找到本轮新增段落 |
| 互动main与shared-pipeline | 未找到上述新版运镜资源 | 未找到本轮新增段落 |
| 恋综、悬疑工作树 | 未找到上述新版运镜资源 | 未找到本轮新增段落 |
| 当前荒野工作树 | 未找到上述新版运镜资源 | 有，创作入口和实际Agent资料已接入 |

共用修复原则此前已做过同步；同步记录包含15个Skill包检查、40项加载回归，以及真实新建/续接探针。它验证的是当时的共用修复指导，不包含后来新增的全部运镜/声音资料。不能把一次同步解释成各分支永远自动相同。

生产沙箱当前只允许安装episode-production包，其他Skill目录直接塞入会被拒绝。全局恋综、悬疑Skill和六套代码化方法，不会仅因主机上存在就被沙箱自动读到。应通过同一包的按需参考资料和当前任务清单接入。

[源码加载入口](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/src/novel_manga/application/agents/episode/inputs.py)、[持续会话安装逻辑](/mnt/disk1/zengzhitao/worktrees/interactive-adventure-rhythm/src/novel_manga/application/agents/episode/session.py)。当前代码的Skill、沙箱当前副本与旧attempt快照是不同状态；本次未热改在跑任务。

## 一个主创 Agent 的实现方式

沿用现有单集持续会话和制作工具，由同一个主创角色负责设定、剧本、分镜、互动与改稿；workflow负责保存、调用模型、预算和交付，保留独立验收。无需另造生产队列或把六种创作方法各启动一遍。

1. **补两个创作输入。** 素材包入口读取原文件和人物/场景/道具资产，提取既定事实与允许改编范围；原创入口读取用户意图，先产出明确标为原创的设定稿，作为后续依据。保留真实来源，不把新编内容伪装为附件原文。类型、画幅、真人/动画风格、时长和互动方式作为任务输入；本会话的3–5分钟要求不能被小说source路径的默认片长替代。
2. **显式选择主创Agent写稿。** 当前source路径有普通模型先起稿、异常再交Agent的优化。要实现用户要求，须让创作入口直接进入主创Agent会话；不能仅把input_mode改为source就称为已实现。继续复用当前上下文、check/adopt和持续会话机制。
3. **在一个Skill包里按需读经验。** 创作时读取所选类型和剧作方法，拆镜时读camera-language，声音设计时读新表演规则，失败时读repair-playbook及相关历史。材料加入实际安装清单和manifest，旧快照保留，下一次授权调用再刷新。
4. **输出沿用现有交接。** Agent交出可通读的剧本、chapter_script、素材需求、分镜/片段计划、准确对白与表演、interaction及制作约定。具体格式由当前宿主合同决定，不新造一份互不相认的格式。定稿后按原制作流程生成和有限修复。
5. **先验证创作闭环。** 用现有素材包在独立工作区完成一次全稿，核对事实继承、开场与推进、镜头目的、对白表演和选择铺垫；再验证原创入口。创作验证本身不启动H3/SD、不消耗旧视频次数。只有后续明确制作任务才进入生成。

共享能力应在合适的集成工作树中合并，保留小说运镜和荒野声音两边的现有工作，再按用户授权同步；不得覆盖各内容分支的素材、配置与在产记录。以上为盘点阶段的实现方案；随后的本分支实施见下节，没有跨分支同步。

## 同日实施更新：统一主创入口

用户随后明确要求直接接入，并用笑之国原始素材包完成一版全稿，暂不生成视频。
当前荒野分支已新增[统一主创入口](unified-creative-agent.md)，直接复用现有持续会话、
ZIP导入和单一episode-production包，接入运镜、声音、类型创作、六种方法、白模空间
及修复资料。主机存在的Skill不再被当作已经自动安装；运行记录保留实际读取证据。

这次作者输入是原始55镜文档与两张角色图，未提供旧v3改稿。作者完成全稿后，格式
错误和独立通读发现的剧情问题交回同一会话修订。白模交付为可进一步建模的空间设计，
不是已运行Blender；修复资料用于创作自审，没有启动视频修复或重拍。

当前接线仅在荒野独立分支，其他分支和上表的历史快照不因此自动更新。视频、图片、
TTS生成次数均未增加；旧成片和历史保留，不提交、不推送。

## 路径与核对范围

小说主源码实际位于`/mnt/disk1/zengzhitao/novel/novel-manga-video`；旧`/mnt/disk1/zengzhitao/novel-manga-video`的源码和文档是入口链接，输入、输出和运行目录仍在旧地址。二者不是两套独立创作能力。

本次核对覆盖小说主线及Agent工作树、互动main/shared与三类内容、全局视频相关Skill、H3运维、数字人/MV/Idol、Foley和disk2超分。历史报告用于定位经验和已验证范围，没有逐段重播所有影片或把旧服务状态当成当前状态。

去重入口与7个工作树文件快照保存在`.runtime/video-knowledge-inventory-20261015/skill-entrypoints.json`和`branch-capabilities.json`。盘点阶段只新增本报告与证据；后续统一入口实施记录在`.runtime/unified-creator-laughing-country-20261015`。生产配置、旧版、次数、其他会话文件和远端服务保持原状态，未提交或推送。
