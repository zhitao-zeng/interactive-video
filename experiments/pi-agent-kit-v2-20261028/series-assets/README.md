# 系列资产（按系列分目录）

这里不是所有项目共用的缓存。每个系列一个子目录：

- `zhiyao/`：知遥系列的固定角色资产（当前是身份基准、综合视觉卡及 `.asset.json`）。
- 其他项目/系列：新建 `series-assets/<系列名>/`，只放该系列跨集稳定的角色/世界资产。

`starter.py init` **不会默认注入**这些文件；需要时显式传入：

```sh
--asset-cache <kit>/series-assets/zhiyao
```

规则：

- 图片和 sidecar 必须一起保留；sidecar 里的 `asset_id` 让同系列新一集直接复用素材库资产，不重复 CreateAsset。`group_id` 不一致时会自动重新登记，不影响正确性。
- 本目录的 PNG/JPG 是本机缓存，按 `.gitignore` 不入 Git；sidecar、本 README 和快照说明入库。换机器时从已有 episode `input/` 复制同名图片（sha256 与 sidecar 一致）即可恢复，asset_id 不变。
- 只放该系列跨集稳定的角色/世界资产。本集新角色、新场景、新道具的参考图放在该集自己的 `input/`，不要放这里。
- 新增复用资产时，把图片和 sidecar 平铺放在对应系列目录；文件名就是新 workspace `input/` 里的文件名。
