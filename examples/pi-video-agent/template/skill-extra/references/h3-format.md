# H3 请求格式：Pi 自主导演

这里描述当前 Pi 工具的用法。旧 host 的 structured turns、自动编号、情绪插入和后期配音不是本入口的功能；不要等工具替你补这些内容。完整 Prompt 由你写，媒体脚本负责附件、接口角色、提交和回执。

官方语法细节见[上游指南](official/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)，案例不是本任务的场景或固定分镜。

## 主体与实际附件

人物综合卡先帮助你读懂设计，给模型的附件按当前镜头选：通常可用身份/衣装清楚的单幅，必要时再补有独立用途的其他视图。多视图仍是同一人物，图中的示范姿态、排版、文字和灰底不是本镜拍摄内容。单幅图也可能残留这些内容，不能只凭文件名判断它干净。是否裁切、换图或保留综合卡由你按当前需要选择，不要求每镜新建参考。

人物是否入画、出现几个人、各自在哪里，与参考图里有几个人像分开确定。画外说话、第一视角手部或越肩前景不要求再补出一个正脸主体。空间和光线以当前场景为准；关键位置需要视觉依据时选相应场景图，不要只按角色名固定引用一组人物卡、遗漏当前场景。文字足以交代的场景无需为此额外生图。

- 按 `references` 的真实数组顺序引用 `<Picture 1>`、`<Picture 2>`；音频独立编号为 `<Audio 1>`。一个人物的多个视图绑定到同一个 `<Subject N>`，不能把视图数当人物数。Subject 也可指场景或物件。
- 在正文写明每张图用于哪位人物的身份、服装，或哪个空间、物件。`purposes` 会附加用途说明，不会锁定身份或自动决定谁入画。FastH3 未填 `purposes` 时原样传 Prompt，正文尤其不能省略绑定。
- 稳定身份不包含本镜朝向、站位或是否露脸。这些写在当前镜头；图中的正面肖像不要求视频也朝向镜头。综合卡的选择与裁切见[参考使用经验](director-feedback.md#参考与首帧服务于具体问题)。
- 普通图片参考不等于首帧。云 H3 和 FastH3 只支持普通参考；本地 H3 的显式 `first_frame` 才由工具绑定 t=0，并要求该图也在 references 首位。SD 首帧限制另见 [工具用法](../../../TOOLS.md)。

## 六段正文

按顺序使用 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。场景、动作与声音说明用英文；中文台词放在 `<d>[Chinese] ...</d>`。

`subject_definitions` 定义当前涉及的主体及其图片来源。说话角色可以有一句稳定声线；不要把每镜的动作、朝向混进身份定义。

`summary` 以真实条件对应的 `[reference generation]` 或 `[reference generation + audio reference]` 开始，接简短场景/动作概述。不要复述台词、牌面文字、场号或选项名；无对白写 `No spoken words.`。英文摘要曾被误念，见[实测风险](h3-pitfalls.md)，不能把“完全不用英文”当修法。

`retention_analysis` 对主体的所需属性说明 `fully_preserved`、`partially_preserved`、`attribute_transfer` 或 `weak_reference`。说清保留的是身份、衣装还是空间；不是要求保留参考卡的背景、排版、姿态或所有人物视图。音色参考用 `reference`，不因此复述参考录音里的词。

`detailed_description` 写实际可见起态、按序发生的行为及结果。单镜可用 `[Shot 1]` 和 `At 00:00.000,`；若主动设计镜内切换，后续 Shot 的时间是本片段累计时间。保留 `Planned duration: X seconds.` 与请求时长一致。是否切镜、是否多人同框由当前表达和实际结果决定。

各段描述同一个镜头：人物定义、摘要、保留关系和正文中的人数、朝向、左右、物件持有与动作先后要能同时成立。修正时替换冲突的原句，不在结尾再叠一段相反指令。已经成立的空间说明保留，不按字数机械删减。

## 对白与声音

说话人 `(S1)`、`(S2)` 按本片段首次发声编号，独立于 Subject 和 Audio。完整台词只出现一次，情绪、语速和动作放在标签外，并跟随引发它的事件。例如：

```text
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
With her back still to the camera, she speaks quietly, without turning.
<Subject 1> (S1) says <d>[Chinese] 先看看眼前的路。</d>
```

没有传音频就不写 Audio 定义或音频参考任务标签。画外说话明确是 off-screen voice；第一视角的声音不要求画面再出现一个说话者。更多表演取舍见[声音](sound-direction.md)。

`overall_soundscape` 写环境；跟动作同步的声响放在动作旁。若另做后期配乐，`non_diegetic_music` 写 `None.`，避免生成两份音乐。无对白镜写无任何人声，不把 ASR 或音轨存在当作实际听审通过。
