# 配乐接入与 61 首曲库实测（2026-10-28）

## 接进 v2 的部分

- `tools/media.py` 新增 `action:"music"`：调用 SDK 的
  `novel_manga.application.music_selection.search`，返回本地曲库候选
  （id/title/artist/path/license/attribution/audio_evidence/review_status/fit_status/scene/subscene）。
- `tools/assemble.py` 新增可选 `music`：显式 `review.status="accepted"` + `reason` 才混音；
  支持 gain、loop、fade in/out，写 `.music.json` 记录曲目和授权信息。
- 搜索不是采用；候选的 `review_status` 是 `unreviewed`，必须自己看 `audio_evidence` 或用
  `media.py audio` 实际听后再采用。
- 10 项离线测试通过（新增 assemble 配乐混音/拒绝未审核配乐）。
- 真实曲目端到端：BGM-006 `Echoes Of Home`（Scott Buckley，CC BY 4.0）+ 2 秒测试片 →
  `assemble` 输出 2.02 秒，`.music.json` 记录 license/attribution；`track_ids` 精确查询返回 BGM-006。
- 当前 EP09A/W22 在途 workspace 未改动；新能力对下一次 init/resume 生效。

## 真实搜索实测（61 首库）

查询 1（EP09A 邮局/记忆）：`quiet old post office, warm memory, sparse piano and soft strings, no prominent vocals`

返回 3 个候选：

- BGM-013 `Dark Walk` — 悬疑后室 / 台阶深处、危险确认
- BGM-020 `Heartbreaking` — 恋综 / 失落、误会、落空
- BGM-008 `With These Hands` — 异世界旅行探险 / 旅途回望、温柔收尾；实际音频证据是合成器 + 鼓机

查询 2（W22 沙漠/旅行）：`sunlit desert travel adventure, hopeful forward motion, light percussion, wide cinematic, no prominent vocals`

返回 3 个候选：

- BGM-005 `Aphelion` — 异世界旅行探险 / 巨大空间、发现目标
- sdd-1176716 `A Morning In The City` — 未分类，**有人声**
- sdd-257059 `Gracias` — 未分类，**有人声**

## 结论：61 首能跑流程，不够稳定选配

- 24 首有分类：异世界旅行探险 / 悬疑后室 / 恋综各 8 首；24 个 subscene 基本每类只有 1 首。
- 37 首未分类的老演示库会混进结果，并可能在“no vocals”查询里返回有人声的曲目。
- 对“安静邮局/温暖记忆/稀疏钢琴”这种具体情绪，库里没有对应曲目，检索只能给悬疑、失恋或明亮合成器。
- 因此：单集试配够用；40 集系列或要求稳定整段适配不够。建议至少扩到每个 subscene 3–5 首，
  并给曲目补 `has_human_vocals`/配器/情绪元数据，检索时按负约束过滤；未分类的 37 首要么重新标注，要么排除。

## 抖音短剧 / VLOG 热门 BGM

本地 61 首不是抖音热门库，`catalogue_scope` 也写着 `popularity not verified`。要找热门曲目：

- 先走有授权的来源：抖音音乐开放平台/商用曲库、Epidemic Sound、Artlist、Musicbed、Uppbeat 等；
  已有的 Scott Buckley / Kevin MacLeod 是 CC BY，需按 attribution 署名。
- 不建议直接抓取抖音热门歌曲音频入库：版权和平台条款都不允许，且无法保留可核验的授权信息。
- 可以先把“短剧/VLOG 常用风格”补进曲库：温暖钢琴、轻 lo-fi、cinematic indie、upbeat corporate、
  tension pulse、emotional strings、travel percussion；每类 3–5 首，再用 `import_music_library.py`
  的 catalog/授权字段导入，保留 license/attribution。
- 如果要按“热门榜单”选，建议只把榜单当作**需求清单**（歌名/风格/情绪），实际入库仍用可授权版本或
  已购买授权的曲库；检索结果必须带 license/attribution 才能采用。
