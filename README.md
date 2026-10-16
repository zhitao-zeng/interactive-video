# Interactive video studio

探险、恋综、悬疑共用一套维护主线。正式代码入口为
`/mnt/disk1/zengzhitao/interactive-video-worktrees/main`，通过项目参数选择内容。

默认路径是：**完整素材包或创作意图 → 单个创作 Agent → 剧本、分镜与互动选择 → 制作、独立审片和修复**。
读书、分章及小说规划属于独立小说项目，不是这里的前置流程。
Agent 负责内容判断；程序负责来源/格式校验、工具执行、媒体装配、预算、缓存和交付。

## 从素材包创作

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

## 制作已批准的素材

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
