# 恋综连续交流编译与H3短样实施计划

**Goal:** 将用户已批准的恋综连续交流编译接到实际SD/H3请求，先做固定三人26秒对照并传Mac。

**Architecture:** 复用现有chapter_script、authored_groups、参考打包和media.generation请求/提交；新增恋综conversation编译入口，以一个连续镜头容纳多轮对白。SD读取中文prompt，H3读取同一来源构建的prompt_h3。旧小说编译入口保持原行为。

**Spec:** 本会话用户批准的“恋综分段与提示词接线、固定三人同室、20–30秒、先H3、确认改善才整集”。此为现有链路内的有界修改，直接执行，不另起产品架构或请求重复批准。

## Constraints

- 不提交SD任务；H3原生仅支持544/768，本轮544P，导出480P并注明。
- A/B各两段13秒，共4条，固定同一seed、素材、台词、时长；不追加艺术重拍。
- 三人已坐定，原房间、四椅、三箱，第四椅空。不引入白模；静默第三人保留。
- 不改旧生产目录、视频、预算；无提交/推送。SD只离线验证实际build_request选择。

## Task 1: 编译与链路回归

Files: src/novel_manga/interactive/conversation.py；romance.py的pack_romance分派；tests/test_romance_conversation.py。
接口：pack_conversation(directory)输出既有clip_plan；每组一个连续镜头、多turns，start/end与三人/行李使用已有字段；英文authored_frame逐句DIALOGUE槽绑定。
- [x] 先测：SD/H3实际build_request各自采用新prompt、多轮台词不遗漏、第三人在场及箱子参考齐全、空end_state拒绝；新模块缺失时RED。
- [x] 实现：按production_mode=romance_conversation分派；复用参考/声线编译，替换专用prompt，保留h3_stamp。
- [x] 定向测试GREEN及旧恋综导入/连续性回归。

## Task 2: 有界样本与交付

Files: scripts/romance_conversation_ab.py；outputs/romance-conversation-h3；delivery/romance/conversation-h3。
- [x] 构造两段连续三人交流，A按旧逐阶段模板、B按连续take编译，台词素材及seed匹配，保存实际请求。
- [x] 检查H3服务和原生分辨率；使用现有media.generation.build_request/submit调用，不另写provider请求。
- [x] 四条完成后统一解码、ASR、关键帧核对与A/B合成；按结果报告，不把编译成功当成画面改善。
- [x] Mac发送回执落盘；短样未经证实不跑整集。

## Review focus

多轮H3台词槽全部绑定；SD不误用H3字段；静默角色与行李图不丢；跨段相同结束/开始状态；旧小说与旧恋综入口无变化。

## Rulings / ledger

- 已有用户批准及执行指令，沿当前隔离worktree直接实施；不执行技能中与工作区无提交授权相冲突的commit步骤。
- H3无法原生480，不扩大部署/模型范围；使用544并输出480播放版，不能宣称原生480。

## Completion

编译/实际请求回归与旧入口回归通过；独立审阅发现H3表演cue遗漏，新增失败回归后修复。全量3195通过。四条H3测试在修复前已冻结并生成，保留原请求；修后仅离线请求验证，无追加重拍。B复制人物/跨段位置失败，不能扩整集；两份480P播放文件已传Mac。无commit/push。
