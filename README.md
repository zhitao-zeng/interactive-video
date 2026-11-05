# Interactive video studio

探险、恋综、悬疑共用一套维护主线。正式代码入口为
`/mnt/disk1/zengzhitao/interactive-video-worktrees/main`，通过项目参数选择内容。

新任务默认路径是：**原始素材包/创作意图 → 单个完全自主的创作/导演 Agent → 真实媒体、检查、修复、剪辑与交付**。
统一入口为 `scripts/pi_video_agent.py`，详见[完全 AGENT 生产](docs/pi-agent-production.md)。
旧 `create/produce/produce-batch` 只用于历史 edition 恢复，不再承担新任务生产。
读书、分章及小说规划属于独立小说项目，不是这里的前置流程。
Agent 负责内容判断与执行选择；程序保护来源、凭据、真实回执、预算和历史产物。

主线已整合 BGM 曲库接入、独立审片、原导演回修、经授权的一次性加时和三路批次调度。
入口与验收边界见[当前生产改进](docs/pi-agent-production.md#2026-11-05-已运行改进的主线入口)。

## 旧主线创作与恢复

以下入口只用于历史 edition 的原路径恢复，不是新任务默认生产线。

任务 JSON 示例（由调用端保存，原始 ZIP 保留）：

```json
{
  "mode": "adaptation",
  "project": "suspense",
  "source_archive": "/path/to/complete-material.zip",
  "brief": "基于完整素材改编，保持已确认事实，写完整剧本、分镜与互动结尾。",
  "target_seconds": [180, 300]
}
```

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py create \
  --task /path/to/task.json --output-dir .runtime/creator-project-edition
```

`project` 可选 `adventure`、`romance`、`suspense`。原创使用 `mode: original` 和完整 brief。
三类任务安装同一份生产 Skill，按项目加载类型指导；共同台词、镜头、空间及修复经验均共享。
创作只调用作者，不会自动生图、生成视频或采用为生产。修订使用同一任务和工作目录，追加
`--resume --feedback /path/to/review.md`；可用 `--config` 选择任务级作者配置。
旧 `scripts/creative_agent.py` 是同一创作实现的专用入口，既有任务可以继续使用。

## 旧主线生产（仅历史恢复）

历史原创任务只能在原目录按原预算恢复：

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py produce \
  --task /path/to/original-task.json \
  --output-dir .runtime/original-film --edition suspense-original-film --resume
```

任务使用原 `mode: original`、`project`、`brief`、`target_seconds` 和可选 `style`。
该入口保留旧任务的创作、素材、首帧、H3、配乐及审片记录，不用于新任务。
恢复必须追加 `--resume`，保留原作者、已生成素材、候选及拍摄额度。
新创作与新制作统一使用上方的完全 AGENT 入口。

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py prepare \
  --project romance --material /path/to/approved-material --edition romance-demo
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py assets \
  --project romance --edition romance-demo --kind all
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py run \
  --project romance --edition romance-demo --lane h3
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py run \
  --project romance --edition romance-demo --lane h3 --resume
```

准备接口保留批准的镜头、对白、人物归属、时间和互动选择，不另写一份剧情。
图片、视频与配音只在相应制作动作获得授权后执行。`sd25` 选择另一视频路线。
旧任务续跑保留原回执、take 与次数，不自动增加重拍额度；候选、审片完整和影片通过是不同状态。

### Agent 配置选择(重要)

同机生产直接用默认 `configs/agent_sandbox.json`:base_url 指向本机 8789 网关
(phanthy-local-api.service,常驻)、`docker_network: "host"`(容器回环≠宿主回环,
指 127.0.0.1 的配置必须带它,沙箱会启动即校验)。这条路由的 agent 轮次约 2-3 分钟。
`--config` 指向云端官方配置(如 official-ds41)仅用于跨机/无本机网关场景:云端 ds41
推理流慢(单轮 5-10 分钟),且该配置不带 host 网络,不可与 8789 混搭。

窗口续期:操作时间窗(默认 5400s)耗尽后 blocked,把
`{"seconds": 5400, "authorized_by": "用户xx批准", "reason": "..."}`
写入该集 run 目录 `window-grant.json` 后 `--resume` 即生效,单次消费、留档可审计;
提供方排队等待已自动退费(不烧创作预算)。

视频通道还可显式选择 `--lane sd20-fast` 或 `--lane sd20-mini`，分别发送
`sd2.0-fast` / `sd2.0-mini`，使用现有 `PHANROUTER_IMAGE_API_KEY` 和素材库通道。
两者暂按项目保守上限 15 秒、单路并发接入，共用该凭据的 `sd20-quota.stop`；
默认通道、旧任务和重拍额度保持。它们目前仅完成离线接口接线，尚未进行真实采样，
30 秒、各分辨率、音画效果及实际结算均待验证；不会自动切换过去。

## 查询历史任务

```bash
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py status \
  --project suspense --edition suspense-backrooms-h3-ep01-v2
```

此操作只读，不建立渲染器或触发修复。输出包含原实际目录、真实累计次数及已保存的终审状态；
没有完整终审时不会显示为零问题。历史目录链接在进入制作前解析到原路径，原素材、旧片、
审片与会话记录继续留在原工作区。后室等自定义交付目录也保留原导出版本。

## 维护

- `main`：唯一维护的程序、工具、公共 Skill 与三类项目源文件。
- `codex/feat/shared-pipeline`：测试和集成；修改在临时工作区完成后合入。
- `configs/interactive.json`：共同默认值和 `projects` 下的题材覆盖项；每集 profile 的原生采样和并发设置仍优先。
- 旧内容工作区：保留数据和旧任务，不再各自维护整套生产代码，不覆盖其他会话的在途修改。

见[维护约定](docs/branch-workflow.md)、[素材接口](docs/interactive-common-intake.md)和
[统一创作 Agent](docs/unified-creative-agent.md)。使用匹配项目的现有 Python 环境，凭据只留在本机 `.env`。
测试命令为 `PYTHONPATH=src .venv/bin/python -m pytest`；本机原包回归通过保留的数据链接读取原资料。
模型、原始图片/声音、视频、运行记录和交付文件均不入 Git。
