# 心动待定 EP01

原包第一季10集/36条分支视频；本次仅接入EP01并实拍厨房24秒小样。

- full：原90句对白、29个原镜组来源和3处2秒空镜均保留，共93个技术阶段。H3 first-frame组由现有select_mode导出，标准prepare/pack可执行；未生成整集。
- pilot：原第14—19句连续厨房对话，两组各12秒；三模型相同来源、人物和厨房参考，无音色参考，原生对白。
- 参考图：六位人物沿用原包本人脸部图；厨房、餐厅、采访角和修订露台由API按Luna方案生成并经独立Luna接受。原露台不确定版本保留。路径与评估见intake.json。
- layout.json属于原作者相对空间方案，不是测量户型。实际采访角可见窗、餐厅与露台用途以scene-layout-review-notes.json和原EP01为准；单视图不能证明整屋拓扑。
- 完整材料目前选择H3 first-frame以执行原2秒空镜；不承诺普通SD完整首集已准备好。三模型共同pilot不含此H3专属模式。
- 明确来源与说话人受保护，不能把已提供素材包当作重写授权。片尾选项保留原稿，未为24秒厨房样片添加尚未发生的投票情节。

可从主线 scripts/interactive_video.py prepare --project romance --material projects/romance/xindongdaiding-v1/full --edition <新目录> 接入；prepare本身不生成影片。
本次H3/SD实拍、原任务ID/用量/实际请求、失败准备与修复轨迹在归属工作区.runtime/xindong-pilot-20261020保留，旧剧集和预算不变。

Agent实拍复核：画面相对偏向Fast，但三版均未通过夏宁服装要求；原生音轨逐版保留，声音自然度未获确定结论。这是候选对比，不能作为正式成片通过。

实际请求归因补充：原稿规定了墨绿裙，但六份发送请求没有裙装约束，且把服装交给脸部图；SD目标摘要仍称探险，H3摘要仍有户外模板文字。Agent确认这是源稿到请求的交接缺口，见input-handoff-findings.json；不能据本轮推断三模型收到明确裙装后仍不遵从。当前材料保留待后续修复，未直接扩整集或更换默认模型。
