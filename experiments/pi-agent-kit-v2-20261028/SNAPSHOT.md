# 快照：Pi agent kit v2（2026-10-28）

从 `.runtime/pi-agent-kit-v2-20261028/` 提取的可复用源码快照。运行中的 EP09A/W22 workspace 留在 `.runtime`，没有搬动。

## 内容

- `starter.py`：`init`（模板复制、skill、音频配置、`--previous` 同集复用、`--asset-cache`、发布目录解析）与 `run`（原生会话、代理剥离）。
- `template/`：`SYSTEM.md`（自主制作 + Bash 边界 + 同集复用）、`TOOLS.md`（媒体请求示例 + 已验证硬限制 + 配乐搜索/采用 + 3 秒选项页）、`SKILL.md`、`TASK.md`、`tools/`（media/assemble/audio_service/request_budget/plan.example/interaction；media 增加 music 搜索，assemble 支持显式审核后的 BGM 混音，interaction 追加 3 秒 A/B/C 页）。
- `series-assets/zhiyao/`：知遥系列的身份基准、综合视觉卡 + `.asset.json` sidecar；按系列分目录，不是全项目共用。`init` 不默认注入，需要时显式 `--asset-cache series-assets/zhiyao`。PNG 是本机缓存，按 `.gitignore` 不入库，sidecar 和 README 入库。
- `test_starter.py`：13 项离线测试（含配乐混音、lane/首帧策略、3 秒选项页）。
- `MUSIC-NOTES.md` / `MUSIC-GAPS.md`：61 首曲库的真实检索结果、覆盖缺口、按剧集节拍要补的风格与授权建议。
- `tools/probe_gateway_limit.py`：网关 12MiB 上限的二分探测（无效模型名，不触发真实生成）。
- `README.md`、`V2-NOTES.md`、`VALIDATION.md`：设计、改动与验证记录。

## 不包含

- 各 run 的 workspace、session、媒体、日志（仍只在 `.runtime/`）。
- `node_modules`（由 `init --dependencies` 指向现有安装）。

## 验证

```sh
PI_STARTER_SDK_ROOT=/mnt/disk1/zengzhitao/interactive-video-worktrees/main \
PI_STARTER_NODE_MODULES=/mnt/disk1/zengzhitao/interactive-video-worktrees/pi-video-model-ab-20261027/.runtime/pi-full-episode-20261028/code/node_modules \
/mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python -m pytest -q test_starter.py
```

结果：9 passed。JS 请求体预算：`bun template/tools/test_request_budget.mjs` → `{"ok":true,...}`。

## 使用

```sh
python starter.py init <workspace> --sdk-root .../main --dependencies .../node_modules \
  --input <episode input> --lane sd2.0-fast --first-frame selective --interaction-page required \
  [--asset-cache <kit>/series-assets/zhiyao] [--previous <same-episode run>]
python starter.py run <workspace> --endpoint official-ds41
```

`--previous` 只用于同一集上一次尝试：生成 `input/PREVIOUS.md` 和 `input/PREVIOUS-MEDIA.tsv`，只读链接，不复制媒体。不同集/其他 W 不复用。
