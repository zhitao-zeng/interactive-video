# 可修改的 Pi 视频 Agent 初版

给 Agent 一套起步代码和完整制作资料，由它自主创作、调工具、检查与剪辑。没有固定镜头数、生产阶段、候选次数或 finish 路由。保留原生 read/write/edit/bash、原生会话与 compaction。正常退出仅表示会话结束，不自动宣称成片通过。

复用了并行 worktree `pi-experiments-20261028/.runtime/pi-agent-kit-20261028/code/` 的 Pi 1.4.0 接入、SD 首帧、素材上传、Eureka GPU 听审写法。去掉其固定两张知遥图、固定剧集指令、assemble 缓存和 finish 验收。H3 通过现有媒体 SDK 调用。旧运行、原素材、在途请求均不修改。

**这些是可编辑的起点，不是仅允许 Agent 填参数的工具。** 初始化后 tools/、SYSTEM.md、TASK.md 和 skills/ 都在任务目录中；后续运行只加载这份工作副本。init 拒绝覆盖已有目录，resume 不重新复制模板。共享 SDK 与 node_modules 仅复用，项目 Python 环境不安装新依赖。

本机初始化示例（只复制文本与输入，不启动 LLM 或付费生成）：

```bash
/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python scripts/pi_video_agent.py init .runtime/pi-v2 \
  --sdk-root /mnt/disk1/zengzhitao/interactive-video-worktrees/main \
  --dependencies /mnt/disk1/zengzhitao/interactive-video-worktrees/pi-video-model-ab-20261027/.runtime/pi-full-episode-20261028/code/node_modules \
  --input /实际素材目录 \
  [--asset-cache examples/pi-video-agent/series-assets/zhiyao] \
  [--viewpoint first-person|mixed|auto] \
  [--previous /同一集上一次尝试]
```

`--viewpoint` 选择全程第一视角、混合视角或导演自定（默认 `auto`）。它随制作说明进入导演的首次任务；人类 TASK 和后续明确要求优先。第一视角可以切镜，混合视角不规定比例；续跑沿用原会话，不重新加载这项选择。

`--previous` 只用于同一集：复制为当前工作区私有副本 `input/previous/`，并生成 `PREVIOUS.md` 与 `PREVIOUS-MEDIA.tsv`；不同集/其他 W 不复用。`--asset-cache` 按系列显式传入，zhiyao 用 `series-assets/zhiyao/`（知遥身份/服装卡和 sidecar，不默认注入；其他项目/系列建自己的目录）；`local.json` 会自动写入发布目录。默认复制 sdk_root/skills/episode-production 全部资料，并导出完整六方法；可用 `--skills` 选择另一份完整资料。只替换 Skill 导航，旧版导航保留为背景，不强制旧字段和阶段。实际任务写入工作副本 TASK.md，包括改编范围、模型、额度、交付目标；空白模板未授权付费。

选择项目已有端点或设置 PI_PROBE_MODEL、PI_PROBE_BASE、PI_PROBE_API_KEY 后运行：

```bash
<local.json中的python> .runtime/pi-v2/starter.py run .runtime/pi-v2 --endpoint <已有端点名>
<local.json中的python> .runtime/pi-v2/starter.py run .runtime/pi-v2 --endpoint <同一端点名> --resume
```

默认沿用已验证的 DS/Kimi 兼容接入，context_window=1048576、max_output_tokens=128000；可改 local.json，服务实际能力仍以端点为准。密钥只进入环境，不写配置/日志。Bun 直接加载 SDK（本机旧 Node 的 Pi CLI 不兼容，未安装新 Node）。

媒体脚本用法见工作副本 TOOLS.md。保留一次真实提交的边界：实际附件与实际 task id 留证；未知提交不隐式再发。Agent 可以改连接脚本，但仍受用户原预算、原始证据和真实交付要求约束。没有新增状态机、轮询调度器或内容审核服务。

本效率分支已通过16项相关Python回归、JS预算检查及独立代码审查；SNAPSHOT.md / V2-NOTES.md保留此前基线记录。原生会话恢复沿用原起点、时限与请求计数。

2026-10-29，W24《月亮牧场》在原4小时/256次Actor请求内自主完成307.56秒影片，实际195次请求、10163.79秒，无外层内容接续。独立四集复盘仍发现内容问题，不据此宣称画质或总成本普遍改善。本地证据位于工作树 `.runtime/pi-agent-efficiency-20261028/retrospective/SUMMARY.md`，不随源码分发。

复盘后的制作指导见 `template/SYSTEM.md`、`template/SKILL.md` 和 `template/skill-extra/references/director-feedback.md`。W24原始运行指令和影片保持不变。

2026-10-29，另完成20部原片的全程抽帧覆盖与重点密帧，并与上述四集实际片段比较。新增可选的 `reference-film-craft.md`，默认按任务范围允许大幅改写；旧固定初剪/终剪顺序已放开。没有新增生产阶段或工具代码。

同一W25原包、相同初始工具/SD2 Fast/选择性首帧/5400秒128请求，完成两版新短片：A为108.56秒、80请求、2817.204秒、18视频task和8图片task；B为108.20秒、108请求、2269.615秒、15视频task和5图片task。两版均无人工内容续接。B实际读到新增指导，工具等待减少，但单人盲看片略偏A，不能宣称新指导已普遍提高质量或降低账单。原始实验、独立看片与完整轨迹在工作树 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/COMPARISON.md`。

试用后又补充了具体牵引、视觉机制与剧情一致、字幕实际边界及失败检查不能报通过的经验；这几句未另跑新片验证。测试时的B说明与后续差分分别保留，未修改两版影片、制作稿、自审或预算。

2026-10-30吸收用户新版92页深析的少量增量：等待中的未知是否变化、局部与终局的区别、静态构图的证据边界、行动先后与责任、修改的创作代价。只更新Skill导航和既有参考页；此前W25 A/B与K3《桥口没有蓝》均未使用这份v2，未新生成或改写旧稿。阅读对照及历史勘误保存在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/v2-reading/REVIEW.md`。

随后经用户授权重构并测试：创作入口与核心改为六个按需机制，媒体反馈保留原美术/连续性与后期经验，H3拆镜建议不再写成普遍创作硬限制。产品工具没有改变，也没有新增常规生产门。

实际用同K3完成五组新旧纯文本对照：两名匿名模型读者复核后三组一致偏重构版、一组一致偏旧版、一组分歧；针对回应者信息来源只补一段，再用一题封存情境比较v1/v2，两读者各偏一版。共12次创作调用、零媒体生成，原始稿未润色，不为赢继续重写。本轮观察到人物回应与体验组织方面的局部收益，未证明全面质量提升或第4题通路问题已在实写中解决。结果与原文在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/skill-refactor-test-20261030/RESULTS.md`；最终创作资料与受测v2包一致，未合主线。

2026-10-30随后按用户要求定向补充信息传递并复测：只改接收者段落，当前源文件与本轮受测after一致，取代上段所述旧v2措辞。原监视器回归+两道独立迁移题共6次K3纯文本请求，244.162秒、0媒体、0重试。两名匿名模型读者均认为迁移两题两版成立；监视器新稿已交代当面约定和音箱反馈，但“排除到只剩风”仍缺充分反馈依据。两人观看偏好均为监视器偏新、其余两题偏旧，不宣称稳定改善或全部通过。不改稿、不追加次数、不加生产流程。详情与全部原稿在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/information-transfer-test-20261030/RESULTS.md`。

2026-10-30补入20片的审美/构图/镜内调度/镜组衔接/节奏经验：新增可选 `reference-film-visual-craft.md` 并设直接入口，原美术例子统一迁入；不改SYSTEM或产品工具，不加流程。原片研究覆盖20张原帧和重点原MP4片段。4次K3设计、3次内置生图（2张对照+1张共享参考）；静态匿名略偏更新前，未证实提升。动态计划18/19秒各3镜，首批3个SD提交HTTP403额度不足，其余3本地拦截，0受理/0短镜。没有切模型/账号或追加生成；动态等待额度或用户选择。完整记录在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/visual-craft-ab-20261030/RESULTS.md`。

用户随后选择现有本地H3另做同模型比较：同实例/同实际附图/相同采样与配对seed，6个首轮镜头164.55秒，0重拍，合成旧18秒/新19秒。两名匿名视觉评阅最终偏新版的并行收工与相视共饮，中段邀请/察觉仍偏弱；静态题此前略偏旧，未证明普遍提升。源片裁点已核对，缺失的关键回应未藏在额外尾部。原SD403/本地拦截均保留，产品工具与Skill不再改动。结果在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/visual-craft-h3-ab-20261030/RESULTS.md`。

2026-10-30按用户要求复现《梦的游牧》：仅一张共同普通风格参考，无首帧/尾帧续接，10个首轮本地H3镜269.361秒，0重拍/新增生图/K3。原片24fps帧数与10镜切点保留，再统一25fps，60.84秒；原片音轨视觉版与H3原生声音版分开标注。独立实际帧复核认为同类冷灰巨构/微人尺度和5→6亮区承接成立，但3/4/9几何及5/8光线运动未逐镜复现，不报告原样复刻或配乐生成成功。实际输入/10个ID/原片对照保留，Skill与产品工具未改，无新流程。结果在 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/dream-nomad-h3-no-first-frame-20261030/RESULTS.md`。

2026-10-30换《地牢酒馆01》巨人接待段：4个无首帧首轮H3镜114.796秒，1张中性普通参考，0重拍/生图/K3。源选段25.6秒，生成版保留全帧27.04秒与H3原声；拼屏静音、不变速。实际独立加密抽帧确认付款、咬肉缺口、举/降桶、后退倒地可读，复古低多边形与尺度成立；酒渍跨镜消失、桌面短暂多道具保留，不因小偏差重拍。仅巨人段，不冒称70秒整片/所有探险通用能力。产品工具与Skill未改。记录 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/dungeon-tavern-h3-no-first-frame-20261030/RESULTS.md`。

2026-10-30《月亮牧场》原稿精简vs新Skill改写实拍：A75.64秒/7视频任务（6首拍+1自主补归栏），B65.16秒/6首拍；同8任务上限、同原生H3/共享素材库/无首帧，4共用补图。原作者自行看实际媒体并剪辑/字幕/决定修复，root单入口派发。两匿名画面/字幕评阅均偏A的试探/查证/改路层次，B路线/近景/轻松关系更好；新大改未证明整体提升，负结果保留，不再改稿追赢。全片长窗ASR漏检以短窗定位，未误判为电影丢配音。产品工具/Skill不变。成果 `.runtime/pi-agent-efficiency-20261028/reference-research-20261029/moon-pasture-rewrite-ab-20261030/RESULTS.md`。
