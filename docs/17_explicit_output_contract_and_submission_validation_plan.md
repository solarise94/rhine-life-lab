# 显式输出契约与提交校验执行文档

## 目标

这轮改造把 Blueprint 的输出交付从“名称启发式”切到“显式契约驱动”：

- card 输出不能再靠 label、asset_id 或命名习惯推断类型。
- Manager 在创建或修改 card 时，必须显式声明输出契约。
- executor 交付结果时优先只提交 `role + path`，降低 tool call 出错率。
- 后端负责从文件和契约校验“大类是否正确”，并在需要时校验“格式是否命中”。
- reviewer 只审核执行证据、脚本契约和交付真实性，不再替系统猜输出应该是图还是表。

## 最新规则

### 输出契约字段

每个 card output 使用结构化对象：

```json
{
  "role": "go_bp_bubble",
  "label": "GO_BP 独立气泡图",
  "artifact_class": "figure",
  "accepted_formats": ["svg", "png", "pdf"],
  "preferred_format": "svg",
  "required": true,
  "description": "单独交付的 GO_BP 气泡图。"
}
```

必填：

- `role`
- `label`
- `artifact_class`

可选：

- `accepted_formats`
- `preferred_format`
- `required`
- `description`
- `asset_id`
- `status`

### artifact_class

第一阶段支持：

- `figure`
- `table`
- `document`
- `model`
- `archive`
- `binary`

### accepted_formats

`accepted_formats` 是可选格式列表，不再是必填字段。

规则：

- 如果填写了 `accepted_formats`，后端必须同时校验大类和格式。
- 如果没有填写 `accepted_formats`，后端只校验 `artifact_class`。
- `preferred_format` 只有在填写时才生效；如果同时提供 `accepted_formats`，它必须属于该列表。

这意味着：

- Manager 可以只规定“我要图”“我要表”，不规定后缀。
- 也可以规定“我要图，而且只能是 svg/png/pdf 之一”。
- 允许一个 output 同时接受多个格式，不必写死单一后缀。

## 设计原则

- 不允许系统长期依赖启发式输出类型推断。
- Manager 对业务输出负责“交什么”。
- 系统只负责“默认放哪”和“如何核验”。
- 文件格式识别可以有内置映射，但那只是检测能力，不是输出策略。
- 旧项目数据做一次性迁移，不保留长期运行时兼容层。

## 数据模型

### Card.outputs

`Card.outputs` 从轻量 `{label, asset_id?, status?}` 升级为 `CardOutputSpec[]`。

### TaskPacket.expected_outputs

运行时输出契约使用 `TaskOutputSpec[]`：

```json
{
  "role": "go_bp_bubble",
  "label": "GO_BP 独立气泡图",
  "artifact_class": "figure",
  "accepted_formats": ["svg", "png", "pdf"],
  "preferred_format": "svg",
  "path_hint": "results/rna_enrich_viz/run_xxx/go_bp_bubble.svg"
}
```

说明：

- `artifact_class` 来自 card 契约，不能由 worker 猜。
- `accepted_formats` 原样继承 card 契约，允许为空。
- `path_hint` 由系统生成，属于运行便利，不是业务意图。

### path_hint 默认生成

默认路径规则仍然保留：

- 优先用 `preferred_format`
- 否则用 `accepted_formats[0]`
- 如果两者都没有，就按 `artifact_class` 的平台默认扩展名生成路径

这个默认扩展名只用于生成默认文件名，例如：

- `figure -> svg`
- `table -> tsv`
- `document -> md`

它不是验收白名单。

## Manager 侧规则

### create_card / update_card

Manager 在写 `outputs[]` 时必须提供显式契约。

不允许：

- 只写 `{"label": "GO BP 气泡图"}`
- 不写 `role`
- 不写 `artifact_class`

允许：

- 只限定大类，不限定格式
- 同时给出多个可接受格式
- 一个业务卡同时声明主结果和 supporting outputs

示例：

```json
[
  {
    "role": "go_bp_bubble",
    "label": "GO_BP 独立气泡图",
    "artifact_class": "figure"
  },
  {
    "role": "go_bp_bubble_data",
    "label": "GO_BP 气泡图底层数据",
    "artifact_class": "table",
    "accepted_formats": ["tsv", "csv"],
    "preferred_format": "tsv"
  }
]
```

### Prompt 约束

Manager 提示词需要明确：

- `outputs[]` 是显式输出契约，不是标签列表
- `artifact_class` 必填
- `accepted_formats` 可选
- 如果用户明确关心交付格式，再填 `accepted_formats`
- 如果主交付是图而支撑数据也要保留，必须拆成两个 outputs

## Executor 提交协议

### 提交目标

executor 提交时尽量少填元信息，避免协议报错。

推荐提交：

```json
{
  "outputs": [
    {
      "role": "go_bp_bubble",
      "path": "results/rna_enrich_viz/run_xxx/go_bp_bubble.svg",
      "description": "GO_BP standalone bubble plot"
    }
  ]
}
```

不强制 executor 手写：

- `artifact_class`
- `format`
- `mime_type`
- `size_bytes`

这些由后端根据文件和契约判断。

## Manifest / 校验规则

### 严格校验项

后端必须校验：

- `role` 必须在 `expected_outputs` 中声明
- `path` 必须存在于允许目录
- 文件必须存在
- 检测到的 `artifact_class` 必须匹配契约
- 如果契约提供了 `accepted_formats`，检测到的格式必须属于该列表

### 非严格项

后端不应因为以下情况直接失败：

- 契约未提供 `accepted_formats`
- 文件格式不是 `preferred_format`，但仍属于 `accepted_formats`

### 示例

契约：

- `artifact_class=figure`
- `accepted_formats=["svg","png","pdf"]`

实际提交：

- `plot.svg` -> pass
- `plot.png` -> pass
- `plot.tsv` -> fail

契约：

- `artifact_class=figure`
- 未填写 `accepted_formats`

实际提交：

- `plot.svg` -> pass
- `plot.png` -> pass
- `plot.tsv` -> fail

## Reviewer 边界

reviewer 重点核查：

- executor 是否真的按脚本执行
- preserved script 是否与任务一致
- manifest 是否真实反映执行结果
- executor 是否承认缺依赖、偷懒、跳过输入或使用占位逻辑

reviewer 不负责：

- 重新定义输出类型
- 替系统决定图表还是表格
- 做环境修复决策

## 一次性迁移

旧项目里的 `graph/cards.json` 若仍是旧结构：

```json
{"label": "DEG 表", "asset_id": "deg_table_v1"}
```

需要一次性迁移为：

```json
{
  "role": "deg_table",
  "label": "DEG 表",
  "artifact_class": "table",
  "accepted_formats": ["tsv", "csv"],
  "preferred_format": "tsv",
  "asset_id": "deg_table_v1"
}
```

迁移策略：

- 根据已有 asset 的 `asset_type`、路径扩展名、文件扩展名做一次性推断
- 没有可靠格式信息时，保留空 `accepted_formats`
- 迁移后持久化回项目数据
- 迁移完成后系统内部不再依赖旧结构

## 落地范围

这轮改造需要覆盖：

- `backend/app/models/output_contracts.py`
- `backend/app/models/cards.py`
- `backend/app/models/runs.py`
- `backend/app/services/worker_service.py`
- `backend/app/services/manifest_service.py`
- `backend/app/services/manager_blueprint_tools.py`
- `backend/app/services/manager_planner.py`
- `backend/app/services/patch_apply.py`
- `backend/app/services/project_service.py`
- `backend/app/workers/*`
- `frontend/lib/types.ts`
- manager-agent tool schema
- 旧 workspace/card 数据的一次性迁移

## 完成判据

完成这轮后，应满足：

1. 新建 card 时，manager 只能提交显式输出契约。
2. run 启动时，`TaskPacket.expected_outputs` 全部来自 card 契约。
3. executor 只提交 `role + path` 也能完成交付。
4. manifest 校验在“未指定格式”时只按 `artifact_class` 验收。
5. manifest 校验在“指定格式列表”时同时按 `artifact_class + accepted_formats` 验收。
6. reviewer 不再依赖旧的输出类型猜测。
7. 旧项目 cards 数据完成一次性迁移。
8. 后端测试、前端构建、部署与烟测通过。
