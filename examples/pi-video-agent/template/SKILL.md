---
name: episode-production
description: 自主创作、改编和制作互动短片；用于故事像设定讲解、人物被动、对白无来回、节奏或画面表达不成立，以及把剧本转为真实媒体请求时的问题。
---

# 自主创作与制作

当前 TASK 决定目标、保留范围、交付与预算。原稿是可改编材料：未被任务明确保护的角色分工、事件顺序、对白、世界规则和结尾都可重构；制作中发现设计问题，也可回到故事重写。tools/ 是可修改的起点，镜头、模型、首帧、检查和剪辑由你选择。

## 创作抓手

看看当前段落正在改变什么：人物的处境、关系或判断，还是观众的期待和体验。人物用一个办法得到回应后，下一步为何不同；若他坚持，后果又怎样显出他的选择？对白可以争取、拒绝、照顾、隐瞒或共同享受，不只是轮流交代设定，也不必每句对抗。

场戏、其中的节拍、单次 API 片段不是同一种单位。不要为一镜几秒的制作限制，把每句话切成独立小故事；也不要求每镜反转或每场失败。具体用法与V2正反例见[写戏机制](references/reference-film-craft.md)：

- 做了许多事却没有变化：看行动、回应与策略/坚持的后果。
- 设定很多却不带动事件：看规则怎样改变办法或提供操作体验。
- 观众或人物的理解不对：看谁知道什么、正在等哪种未知。
- 对白流畅但关系不动：看言语行动、道具与关系账。
- 平静生活、程序或景观：看它本来要兑现的触感、回应和观看变化。
- 结尾缺落点或解释太满：看已兑现的部分、尚待的未知和修改代价。

这些是遇到问题时的选择，不是必填表、生产步骤或每次逐项审查。优先保留已经成立的表演、趣味与合理省略。

## 按当前任务查资料

- 写对白或声音表演：[对白](references/dialogue-craft.md)、[声音](references/sound-direction.md)。
  每个说话角色一句固定英文声线写在 `subject_definitions` 里，每句只写该句情绪。
- 选择审美、构图、镜内调度、镜组衔接与节奏：[20片视觉方法](references/reference-film-visual-craft.md)。按当前问题取用，不要求每镜填表或套同一种结构；具体技术写法可查[选镜](references/camera-selection.md)、[空间](references/spatial-blockout.md)、[镜头语言](references/camera-language.md)、[导演手法](references/director-camera-styles.md)。简单近景无需额外建模。
- 实际媒体不成立：[成片反馈与修复](references/director-feedback.md)。先区分故事/场面设计、请求/参考、模型执行或后期，再选能改变结果的修法。
- 使用H3：[请求格式](references/h3-format.md)、[接口与实测风险](references/h3-pitfalls.md)。拆镜是应对具体失真的选择，不是所有场景的固定结构。
- 需要类型经验：[旅行冒险](references/genres/travel.md)、[恋综](references/genres/romance.md)、[悬疑](references/genres/suspense.md)。[完整六方法](creation-methods.json)可选，不要求六稿或多个Agent。
- 只有当前任务要求榜单交付时，使用[榜单格式](references/interactive-leaderboard.md)；不把旧任务的时长、世界卡、投票页强加给文学试稿或别的项目。
- 查历史解法前看[经验适用性](references/experience-applicability.md)、[案例](references/case-lessons.md)。旧host phase/check/apply与固定JSON是背景，不接管本任务，也不冻结你对故事的修改权。

## 真实执行的边界

角色身份描述保留稳定外貌、服装和声线；位置、朝向、是否露脸与当前人数写在本镜，不把“面向镜头”等临时调度复制进每镜身份定义。参考卡与越肩/第一视角的取舍见[成片反馈与修复](references/director-feedback.md)。

仅写稿时交付文字；制作时以`../../CAPABILITIES.md/JSON`、[工具用法](../../TOOLS.md)和实际请求为准。参考图须作为真实附件，按顺序说明身份/风格/状态用途；图里不存在的对象不能靠引用变成已入画。支持首帧绑定的接口才将它作为真实t=0；普通参考图不保证固定起帧。镜内后续动作不要提前完成，前镜末态不倒退。

保留原输入、请求、task回执、本地失败报告、素材及采用关系；原额度不因恢复重置。ASR、音轨长度和工具退出不替代观看判断。没有新的实质问题就交付，不为用完余额继续制作；无法完成时如实说明。故事取舍仍由你负责，不将技术事实伪写成内容已经通过。
