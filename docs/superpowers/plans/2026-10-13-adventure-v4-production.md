# Adventure v4 Production Implementation Plan

> **For agentic workers:** Use executing-plans inline. The user explicitly authorized these previously reported fixes and full production; keep the existing worktree and do not commit or push.

**Goal:** 修复已复现的输入问题，完整运行第四版H3 768P与SD2.5 480P试制，并统一评估。

**Architecture:** 继续使用现有准备、打包、语音门、内容审查及有限修复。作者场次、机位和表演进入现有字段；modern-live风格声明生成目标。荒野新稿独有的证据清单驱动后期合成：先根据实际采用视频定位镜头与物件平面，再把准确文字/必要节点合成到物件内；不能定位的项目明确失败，不回退成顶部结论。恋综和旧版无此清单，沿旧路径。

**Tech Stack:** 现有Python环境、Pillow、FFmpeg、既有JSON视觉模型、现有H3/SD提供者。

**Spec:** projects/adventure/revisions/2026-10-13-platform-rewrite/production-readiness.md 及同目录第四版剧本/分镜。

## Global Constraints

- 全程仅当前worktree与新荒野输出；共享在途板只改本会话行。
- 用户最新更正：SD原生480P试制，H3原生768P；最终1080×1920、25fps、H.264/AAC，记录上采样。
- 同稿、原真人参考及林野声线、复用SD注册记录；不新建模型/素材库，不变动画。
- 旧版H3 23次/SD17次及失败留存；本轮新稿初拍、重试逐次记录，原请求续修不能免计。
- 完整pipeline后统一评估；不边生成边人工改稿、逐镜追加重拍。原自动上限不变。
- 不commit、push、merge或发布；不重启共享服务。

## Review Focus

- 随行镜头重编译后仍是作者机位，不重新附加固定机位。
- 表演意图不改变声线或逐字台词，场次与物理地点分别保存。
- 实际切点迟移/物件缺席时不提前显示信息，定位失败不伪造画内证据。
- 物件平面透视与手部遮挡；最终字幕须在证据合成之后保持可读。
- 480P属于原生试制，1080交付不能冒充原生清晰度；历史及既有有限重试保持。

### Task 1: Authored inputs and native resolution

**Files:** preparation.py、story/compilation.py、application/packing/context.py、configs/styles/modern-live.json、tests/test_adventure_v4_production.py。

**Interfaces:** prepare_episode原签名不变；CompilerOptions新增默认保持旧行为的video_kind；作者camera_policy从profile读取。新稿可声明resolution=480p，H3短边仍由768配置决定。

- [ ] 写回归：prepare第四版保留scene_id和voice_delivery；打包请求为真人目标、authored机位、保护原台词和177秒。
- [ ] 运行回归，Expected: 旧准备/编译无法满足上述断言。
- [ ] 实现：`emotion = base_voice + '；' + shot['voice_delivery']`（缺字段沿旧值）；`scene_id=shot.get('scene_id', shot['location'])`；`video_kind`来自modern-live；profile.camera_policy='authored'。
- [ ] 运行相关既有回归，Expected: 新断言及旧默认/恋综相关测试通过。

### Task 2: Actual evidence and complete choices

**Files:** interactive/evidence.py、interactive/delivery.py、tests/test_adventure_evidence.py；第四版新增evidence_surfaces.json。

**Interfaces:** `prepare_evidence(ctx, assembly, destination) -> dict`，返回实际shot_spans、物件定位/合成记录、passed及缺口；`composite_evidence(source, output, observations, cues, font) -> Path`保留视频时长与原音频。evidence_events增加可选shot_spans，未提供保持旧行为。

- [ ] 写回归：实际第二镜8秒才出现时，信息从8秒开始；缺失镜头不按比例补；四边形透视保留区域外画面；硬切前后不跨镜插值；完整尾卡包含两个reason。
- [ ] 运行回归，Expected: 缺少接口/旧比例分配无法满足。
- [ ] 视觉定位使用既有ask_json与judge_settings，保留有界请求及结果缓存；只输出实际镜头/物件四角/遮挡。合成以实际观察时刻为准。缺口单独进入交付candidate状态。
- [ ] 保持旧交付无证据清单时不运行新合成；尾卡无reason时保持原布局；Expected: 上述回归及已有交付检查通过。

### Task 3: Frozen comparison and full production

**Files:** interactive/preparation.py、scripts/interactive_video.py、tests/test_adventure_v4_production.py；新master及两个独立edition，比较manifest与执行记录。

**Interfaces:** prepare CLI新增--revision/--edition/--assets-from，保存新稿及原身份/场景/声线。run继续已有--edition。两个lane使用同一master快照；SD profile480p，H3短边768。

- [ ] 写回归：准备新edition不改旧产物；不得覆盖已有生产历史；原SD注册记录复用且与图像相符；新lane只复制准备文件与资产，不复制另一lane生成历史。
- [ ] 实现最小edition准备；Expected: 原版history/generation_counts保留、无新图/声线请求。
- [ ] 跑相关测试及全量既有测试，Expected: 全绿；提交不执行。
- [ ] 按executing-plans要求进行一次独立代码审阅，只覆盖本轮diff；修正重要问题后开启完整pipeline。
- [ ] 两版完整流程各运行一次，只有既有自动有界重试；保存请求、原生尺寸、计数、时间及失败。
- [ ] 两版结束后统一按实际最终帧/ASR/音轨数据与质量门评估；声音未试听时明确记录，不冒充通过。

## Execution ledger

- Pre-flight: 现有隔离worktree保留源未提交实现。Task 1的作者字段被Task 2定位提示读取，Task 2的shot_spans被delivery消费；Task 3固定同一输入供两lane。
- Ruling: 用户禁止提交/推送，覆盖技能中的commit与merge步骤；审阅采用本轮修改前快照与工作文件diff，不以空HEAD范围代替。
- Ruling: 用户已授权已呈现的制作问题修正及整集比较，本轮直接执行；最新SD480P更正优先于原720P预算。

- Task 1: complete. RED：scene_id与camera_policy丢失；GREEN：23项相关回归通过；新稿不再统一追加镇定自然，原声线描述及逐字台词保持。
- Task 2: complete. RED：实际切点、透视、遮挡、完整选项接口失败；GREEN：含真实FFmpeg帧数及音频原样保留检查的回归通过。
- Task 3 preparation: complete. RED：新revision接口缺失；GREEN：独立准备/保留旧历史回归通过。两版master/edition冻结，同一34镜、17单元、177秒；H3 768P，SD480P。
- Final review: 一次独立审阅，发现3个Important。展开/合本时间不能外推、移动遮挡不能冻结、节点需绑定实际指认锚点；均有先失败后通过回归。无法可靠跟踪时省略合成并报告，不伪造物件；新后期结果始终candidate，待最终视觉评估。
- Final review fixes: 3项RED→GREEN；完整suite3202/3202通过，73.33秒；git diff --check通过。
- Ruling: 审阅中的480P试制标签虽标Minor，但属于原Task 3已定要求；本轮已在比较manifest及交付receipt增加请求/实际原生尺寸、试制/上采样说明，不作为额外范围修法。
- Full production: starting both authorized pipelines in separate processes; no human segment edits or extra retakes.

- Infrastructure recovery: 原文字网关多次502，H3准备全组结束后有9个有效英文请求、8个未准备；尚无H3视频提交。原诊断/日志保持。只读核实已有GPU52 Qwen可读原角色图，真实model ID为Qwen3.8-27B；旧小写名实际404。
- Ruling: 在同一edition恢复完整H3流程，保留9个有效请求及所有旧失败；仅进程内选择已可用本地文字服务。未改剧本/机位/台词/参考或视频上限、未重启服务，也未切恋综配置。
- Recovery RED→GREEN: 进程内文字服务选择回归先失败后通过；26项相关检查通过；随后完整3203/3203通过，73.49秒。
- User decision: SD本轮暂不补额度；已生成11段（480×854、24fps），缺6段，状态incomplete。剩余生成不启动，原配额停止标记、回执与11次保留；只对既有错误/缺失审查结果走现有review_only，不生成或修稿。
