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

|Codex|恋综提示词结构修复＋Blender白模，SD2.5 26秒A/B|codex/romance-sd25-continuity|/mnt/disk1/zengzhitao/interactive-video-worktrees/romance-sd25-continuity；英文提示词保留空格、恋综源状态/动作整理、专用白模及小段实验|在途，预计30–60分钟；宋乔进门/落座/咖啡邀请原05+06，15+11秒；A旧提示词，B状态结构+几何参考，每臂两条、各一次。已继承未提交实现，与荒野隔离；主生产、旧产物/计数不改，无commit/push。|

|Codex 荒野会话 01a13346|笑之国声音/音乐/原生中文改进＋SD2.5 480P完整版|codex/adventure-rhythm-20261013|本worktree通用声音与指定场景文字编译、独立SD版；旧H338次及恋综不变|在途：已定位强制无音乐、重复语气与禁止场景文字冲突；保留旧片、声线与全部历史，修复后跑完整SD有界流程；额度不足立即停，结束后统一评估。未提交推送。|

|Codex integration|共用链路已合入main并同步本分支|adventure|保留本分支内容、运行配置与后续增量|main=0f49c49；共用3263测试和独立复核通过；无生成/推送。|
