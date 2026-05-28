# Dependency Attention Derived Diagnostics Plan

## 背景

当前系统已经有明确的 Asset provenance：

- card 的输入依赖来自 `card.inputs[].asset_id`。
- card 的当前输出契约来自 `card.outputs[]`。
- materialized asset 的数据 lineage 来自 `Asset.depends_on`。
- `linked_assets` 可能包含历史绑定，不应作为当前数据依赖的权威来源。

之前尝试过用 PatchApplyService / 链式 stale 传播去主动改下游状态，但这条路会把底层数据依赖语义变复杂：为了让 UI 看起来连续，容易弱化真实 Asset provenance。新的方向是保留现有依赖逻辑，只新增一层派生的 dependency attention 诊断。

核心语义：

> 当某个 card 或 asset 仍然存在，但它依赖的输入资产已经失效、过期、缺失，或者不是上游 card 当前同 role 输出时，系统应提示 Manager 和用户：这条结果链不再可靠，需要重新检查、重跑或重新绑定。

这不是 rollback，也不是自动传播状态。它是当前项目状态的派生诊断。

## 目标

1. 不改变现有 Asset provenance 和 canvas 连线逻辑。
2. 不自动重绑下游 inputs。
3. 不自动把 accepted card 改成 stale。
4. 让 Manager 在修改/删除上游 card 后能立刻看到“下游可能需要 dependency attention 检查”的提示。
5. 让非 auto 模式下的用户能在 UI 和 Manager 回复里看到明确 ATTENTION。
6. 让诊断基于当前事实可重复计算，不依赖一次性 patch side effect。

## 非目标

- 不实现 semantic rollback / snapshot restore。
- 不恢复旧的 PatchApplyService 链式传播方案。
- 不把 `linked_assets` 升级为依赖权威来源。
- 不在本方案中删除历史 asset 或历史 run。
- 不要求 manager 每次发现 attention 都自动重跑；auto mode 下仍由 manager 判断。

## 当前代码基础

### 已有可复用能力

- `AssetTimelineService.required_asset_uses()` 已以 `card.inputs` 和 module depends 计算 required assets。
- `FlowService.get_work_order()` 已能把 missing / nonvalid required assets 放到 `blocked_by_asset_ids`。
- `ManagerBlueprintTools.inspect_project_summary()` 是 manager 常用的轻量入口。
- `ManagerBlueprintTools` 已承载 Manager 的 card mutation / inspect 工具入口。
- 前端已有 `stale` / `superseded` status badge，但还没有独立的 derived ATTENTION overlay。

### 当前不足

1. `blocked_by_asset_ids` 主要服务于可启动性，对已经 accepted 的下游结果不够显眼。
2. Manager summary 里没有直接暴露“accepted card 引用旧 asset / invalid input”的诊断。
3. UI 对 accepted card 的不可靠输入没有独立 ATTENTION 标记。
4. `linked_assets` 保留历史资产，容易让 agent 或用户误以为旧资产仍是当前输出。
5. 如果旧 asset 仍是 `valid`，但 producer card 当前同 role output 已换成新 asset id，仅靠 status 无法发现它已过期。
6. Manager `update_card` / `delete_card` 改变上游蓝图语义后，当前没有统一提示告诉 Manager：下游可能需要做 dependency attention 检查。

## 设计原则

### 权威来源

Dependency attention 必须按以下优先级计算：

1. `card.inputs[].asset_id`：card 的当前输入依赖。
2. `Asset.depends_on`：materialized asset 的真实生成依赖。
3. `card.outputs[]`：producer card 的当前输出契约和当前 output asset id。
4. `graph.runs` + `Asset.created_by_run`：定位 asset 的 producer card。
5. `Asset.metadata.role`：判断同一 producer card 下同 role 的旧新版本关系。

`linked_assets` 只用于历史展示和兼容，不参与 currentness 判断。

### 状态不变性

诊断服务默认只读，不写：

- 不改 `card.status`。
- 不改 `asset.status`。
- 不改 `card.inputs`。
- 不改 `card.outputs`。
- 不改 `linked_assets`。
- 不把 ATTENTION 持久写入 card。
- 不新增 card-level `attention` / `warnings` / `derived_status` 持久字段。

如果后续需要 manager 采取动作，应通过现有工具显式执行，例如 `update_card`、`rerun_card`、`review_card_run`。

ATTENTION 是当前 project snapshot 的派生诊断。用户如果看到 ATTENTION 后认为不符合真实分析意图，应通过 Manager 重置、调整或重跑相关 card；系统不提供“编辑 ATTENTION 本身”的入口。

### 可解释性

每条 attention issue 必须给出：

- affected card / asset
- 触发原因
- 触发的 input asset 或 upstream asset
- 如果存在，当前推荐的新 asset id
- 简短的 manager-facing / user-facing message
- 建议动作，但不自动执行

## 新增后端服务

建议新增：

```text
backend/app/services/dependency_attention_service.py
```

### 服务职责

`DependencyAttentionService` 是只读派生服务：

- 输入当前 project snapshot。
- 根据 cards / graph / runs / assets 计算 dependency attention。
- 返回可解释 issues、按 card 聚合的索引和 fingerprint。
- 不负责修复、不写文件、不修改 graph。

它不应该依赖 patch apply side effects。任何时刻重新读取同一份 snapshot，都应该得到同一份 attention 结果。

建议公开接口：

```python
class DependencyAttentionService:
    def analyze_project(self, snapshot: dict) -> dict:
        ...

    def issues_for_card(self, snapshot: dict, card_id: str) -> list[dict]:
        ...
```

`analyze_project()` 是主入口。`issues_for_card()` 只是 `analyze_project()` 的过滤便捷方法，避免两套逻辑分叉。

### 数据结构

建议先用普通 dict 输出，避免过早扩 schema；如果后续稳定，再加 Pydantic model。

项目级输出：

```json
{
  "issue_count": 2,
  "fingerprint": "sha256-of-stable-issue-facts",
  "issues": [],
  "issues_by_card": {
    "rna_html_report": []
  },
  "severity_counts": {
    "warning": 1,
    "error": 1
  }
}
```

单条 issue：

```json
{
  "issue_id": "input_asset_outdated:rna_html_report:asset_old:asset_new",
  "severity": "warning",
  "kind": "input_asset_outdated",
  "card_id": "rna_html_report",
  "card_title": "综合分析 HTML 报告",
  "asset_id": "asset_run_old_tf_table",
  "asset_status": "valid",
  "label": "TF富集结果表",
  "producer_card_id": "rna_tf",
  "producer_role": "rna_tf_tf_enrich_table",
  "current_asset_id": "asset_run_new_tf_table",
  "message": "输入资产 TF富集结果表 仍引用 rna_tf 的旧版本输出；当前同 role 输出已变更。",
  "suggested_actions": [
    "检查下游结果是否仍可信",
    "必要时重跑该 card",
    "若用户确认沿用旧结果，可忽略该 attention"
  ]
}
```

字段约定：

- `issue_id`: 稳定、可排序、可用于去重。不要包含时间戳。
- `kind`: 机器可读类型。
- `severity`: 第一版使用 `info` / `warning` / `error`。
- `card_id`: 受影响 card。asset-only issue 可为空，但第一版建议只产出 card-facing issues。
- `asset_id`: 触发 issue 的旧/坏 input asset。
- `current_asset_id`: 如果能确定推荐的新当前 asset，则填写。
- `producer_card_id`: 触发旧新版本判断时的上游 card。
- `producer_role`: 用于 currentness 判断的 role。
- `message`: 面向 manager 和用户的短句。
- `suggested_actions`: 建议动作，不代表系统已执行。

fingerprint 只包含稳定事实：

```python
stable_parts = [
    (
        issue["kind"],
        issue.get("card_id"),
        issue.get("asset_id"),
        issue.get("asset_status"),
        issue.get("current_asset_id"),
        issue.get("producer_card_id"),
        issue.get("producer_role"),
    )
    for issue in sorted(issues, key=lambda item: item["issue_id"])
]
fingerprint = sha256(json.dumps(stable_parts, sort_keys=True).encode("utf-8")).hexdigest()
```

不要把 `message`、`suggested_actions`、时间戳、排序不稳定的对象放进 fingerprint。

### 内部索引

`analyze_project()` 开始时一次性建立索引，避免规则里重复扫描：

```python
cards = snapshot["cards"]
graph = snapshot["graph"]

card_by_id = {card.card_id: card for card in cards}
asset_by_id = {asset.asset_id: asset for asset in graph.assets}
run_by_id = {run.run_id: run for run in graph.runs}
run_card_by_id = {run.run_id: run.card_id for run in graph.runs}
```

planned output 索引：

```python
planned_output_by_asset_id = {}
current_output_by_card_role = {}

for card in cards:
    for output in card.outputs:
        if not output.asset_id:
            continue
        planned_output_by_asset_id[output.asset_id] = {
            "card_id": card.card_id,
            "role": output.role,
            "output": output,
        }
        if output.role:
            current_output_by_card_role[(card.card_id, output.role)] = output
```

materialized asset producer / role 索引：

```python
producer_card_by_asset = {}
role_by_asset = {}

for asset in graph.assets:
    producer_card_id = run_card_by_id.get(asset.created_by_run or "")
    if producer_card_id:
        producer_card_by_asset[asset.asset_id] = producer_card_id
    role = str(asset.metadata.get("role") or "").strip()
    if role:
        role_by_asset[asset.asset_id] = role
```

注意：

- `planned_output_by_asset_id` 只用于判断 input 是否指向未 materialize 但已计划的上游输出。
- `current_output_by_card_role` 只记录 card 当前 output contract。
- 如果同一 card 出现重复 role，第一版可以让后者覆盖前者，但应产生 `info` 或 debug 日志；更严格的重复 role 校验可以留给 card validation。
- 不用 `linked_assets` 建索引。

### issue kinds

#### `input_asset_missing`

Card input 引用了不存在的 asset id。

触发条件：

- `card.inputs[].asset_id` 不为空。
- asset id 不在 `graph.assets`。
- asset id 也不是 timeline 中的 planned output。

伪代码：

```python
for card in cards:
    for input_ref in card.inputs:
        asset_id = input_ref.asset_id
        if not asset_id:
            continue
        if asset_id not in asset_by_id and asset_id not in planned_output_by_asset_id:
            emit(
                kind="input_asset_missing",
                severity="error",
                card=card,
                asset_id=asset_id,
                label=input_ref.label,
            )
```

如果 input 指向 planned output，第一版不报 missing。是否可启动由现有 work order 决定。

#### `input_asset_not_valid`

Card input 引用的 asset 存在，但状态不是可用于可信输入的状态。

触发条件：

- asset status 不在 `{valid, candidate}`。
- 典型状态：`stale`, `superseded`, `rejected`, `archived`, `missing`。

默认 severity：

- `stale`, `superseded`: warning
- `rejected`, `archived`, `missing`: error
- `candidate`: 不报 issue。candidate 本身就是未被明确验收的结果，用户和 Manager 应理解它不是最终可信结果；Dependency Attention 只提示已经 accepted/valid 的链条被后续改动破坏的情况。

伪代码：

```python
VALID_INPUT_STATUSES = {"valid", "candidate"}
ERROR_INPUT_STATUSES = {"rejected", "archived", "missing"}

asset = asset_by_id.get(asset_id)
if asset and asset.status not in VALID_INPUT_STATUSES:
    emit(
        kind="input_asset_not_valid",
        severity="error" if asset.status in ERROR_INPUT_STATUSES else "warning",
        card=card,
        asset_id=asset.asset_id,
        asset_status=asset.status,
        label=input_ref.label,
    )
```

#### `input_asset_outdated`

Card input 引用的 asset 仍可能是 `valid`，但它不是 producer card 当前同 role 输出。

触发条件：

1. input asset 存在。
2. input asset 有 `created_by_run`。
3. 该 run 可在 `graph.runs` 中定位到 producer card。
4. input asset 有 role：只使用 `asset.metadata["role"]`。
5. producer card 当前 `outputs[]` 中存在相同 role。
6. 当前 output 的 `asset_id` 与 input asset id 不同。
7. 当前 output asset 存在且 status 在 `{valid, candidate}`。

这条规则用于发现“旧 asset 还 valid，但上游已经有更新版本”的真实风险。

伪代码：

```python
for card in cards:
    for input_ref in card.inputs:
        asset = asset_by_id.get(input_ref.asset_id or "")
        if not asset:
            continue
        producer_card_id = producer_card_by_asset.get(asset.asset_id)
        role = role_by_asset.get(asset.asset_id)
        if not producer_card_id or not role:
            continue

        current_output = current_output_by_card_role.get((producer_card_id, role))
        if not current_output or not current_output.asset_id:
            continue
        if current_output.asset_id == asset.asset_id:
            continue

        current_asset = asset_by_id.get(current_output.asset_id)
        if current_asset and current_asset.status in {"valid", "candidate"}:
            emit(
                kind="input_asset_outdated",
                severity="warning",
                card=card,
                asset_id=asset.asset_id,
                asset_status=asset.status,
                label=input_ref.label,
                producer_card_id=producer_card_id,
                producer_role=role,
                current_asset_id=current_asset.asset_id,
            )
```

边界：

- 只在能确定 producer card + role + current output 时触发。
- 不用 path、label、文件名猜 role。
- 不使用 `planned_asset_id` 作为 role fallback。`planned_asset_id` 是 planned output id，不是稳定语义 role。
- 如果 current output asset 不存在，交给 `output_asset_not_valid` 或 planned output 状态处理，不在这里猜测。

#### `input_producer_card_inactive`

Card input 引用的 asset 来自某个 producer card，但 producer card 已不再是可用上游。

触发条件：

1. input asset 存在。
2. input asset 能通过 `created_by_run -> run.card_id` 定位 producer card。
3. producer card status 在 `{cancelled, rejected, superseded}`。
4. 当前 card 仍在使用该 input asset。

这条规则用于 manager 删除或取消上游 card 后，提醒下游仍在消费历史产物。

伪代码：

```python
INACTIVE_PRODUCER_CARD_STATUSES = {"cancelled", "rejected", "superseded"}

for card in cards:
    for input_ref in card.inputs:
        asset = asset_by_id.get(input_ref.asset_id or "")
        if not asset:
            continue
        producer_card_id = producer_card_by_asset.get(asset.asset_id)
        producer_card = card_by_id.get(producer_card_id or "")
        if not producer_card:
            continue
        if producer_card.status in INACTIVE_PRODUCER_CARD_STATUSES:
            emit(
                kind="input_producer_card_inactive",
                severity="warning",
                card=card,
                asset_id=asset.asset_id,
                asset_status=asset.status,
                label=input_ref.label,
                producer_card_id=producer_card.card_id,
                producer_card_status=producer_card.status,
            )
```

边界：

- 不要求 asset 自身变成 stale/superseded。只要上游 card 被 manager 取消/拒绝/替代，下游就应看到“你正在使用历史产物”的提醒。
- 如果用户是有意保留旧分支，这条 issue 可以被用户/manager 解释为可接受，而不是强制修复。

#### `input_producer_output_removed`

Card input 引用的 asset 来自某个 producer card 的 role，但 producer card 当前 outputs 已不再声明该 role。

触发条件：

1. input asset 存在。
2. input asset 能定位 producer card。
3. input asset 有 `metadata.role`。
4. producer card 当前 `outputs[]` 不再包含该 role。

这条规则用于 manager 编辑上游 card outputs 时，发现下游仍依赖被移除的输出语义。

伪代码：

```python
for card in cards:
    for input_ref in card.inputs:
        asset = asset_by_id.get(input_ref.asset_id or "")
        if not asset:
            continue
        producer_card_id = producer_card_by_asset.get(asset.asset_id)
        role = role_by_asset.get(asset.asset_id)
        producer_card = card_by_id.get(producer_card_id or "")
        if not producer_card_id or not role or not producer_card:
            continue
        producer_roles = {output.role for output in producer_card.outputs if output.role}
        if role not in producer_roles:
            emit(
                kind="input_producer_output_removed",
                severity="warning",
                card=card,
                asset_id=asset.asset_id,
                asset_status=asset.status,
                label=input_ref.label,
                producer_card_id=producer_card_id,
                producer_role=role,
            )
```

边界：

- 不用 label/path 猜 role。
- 如果 producer card 只是把 role 改名，这条 issue 会提示下游需要人工确认或 manager 显式更新。

#### `output_asset_not_valid`

Accepted card 的当前 outputs 指向 candidate / missing / nonvalid asset。

触发条件：

- `card.status == "accepted"`。
- `card.outputs[].asset_id` 非空。
- output asset 不存在，或 status 不为 `valid`。

这能发现类似：卡片显示 accepted，但 outputs 指到 candidate 资产，或 outputs 与 linked_assets 不一致。

伪代码：

```python
for card in cards:
    if card.status != "accepted":
        continue
    for output in card.outputs:
        if not output.asset_id:
            emit(
                kind="output_asset_not_valid",
                severity="error",
                card=card,
                asset_status="missing",
                producer_role=output.role,
            )
            continue
        asset = asset_by_id.get(output.asset_id)
        if not asset:
            emit(
                kind="output_asset_not_valid",
                severity="error",
                card=card,
                asset_id=output.asset_id,
                producer_role=output.role,
            )
            continue
        if asset.status != "valid":
            emit(
                kind="output_asset_not_valid",
                severity="warning" if asset.status == "candidate" else "error",
                card=card,
                asset_id=asset.asset_id,
                asset_status=asset.status,
                producer_role=output.role,
            )
```

说明：

- `candidate` output 在 accepted card 上是 warning，不是 blocker。它说明验收绑定状态不一致。
- 如果 card 还在 `reviewing` / `needs_review`，candidate output 是正常中间态，不报这个 issue。

#### `asset_lineage_invalid`

Materialized asset 本身看起来可用，但它的 `depends_on` 链条里有缺失或非 valid upstream asset。

触发条件：

- asset status 在 `{valid, candidate}`。
- DFS/BFS 检查 `asset.depends_on`。
- 任一 upstream asset missing 或 status 不在 `{valid, candidate}`。

第一版保留局部 DFS，但不做全项目 asset graph 扫描。这样可以发现“当前 input/output asset 看起来仍 valid，但它的上游数据 lineage 已经断裂”的问题，同时控制成本和噪音。

建议第一版只对以下起点做 lineage 检查：

- 每个 `card.inputs[].asset_id` 指向的 materialized asset。
- 每个 accepted card 当前 `outputs[].asset_id` 指向的 materialized asset。

伪代码：

```python
def find_invalid_lineage_roots(start_asset_id: str, max_nodes: int = 200) -> list[dict]:
    invalid = []
    seen = set()
    queue = list(asset_by_id.get(start_asset_id).depends_on)
    while queue and len(seen) < max_nodes:
        upstream_id = queue.pop(0)
        if upstream_id in seen:
            continue
        seen.add(upstream_id)
        upstream = asset_by_id.get(upstream_id)
        if upstream is None:
            invalid.append({"asset_id": upstream_id, "status": "missing"})
            continue
        if upstream.status not in {"valid", "candidate"}:
            invalid.append({"asset_id": upstream.asset_id, "status": upstream.status})
            continue
        queue.extend(upstream.depends_on)
    return invalid
```

对 card input 触发：

```python
invalid_roots = find_invalid_lineage_roots(input_asset.asset_id)
if invalid_roots:
    emit(
        kind="asset_lineage_invalid",
        severity="warning",
        card=card,
        asset_id=input_asset.asset_id,
        upstream_invalid_assets=invalid_roots[:8],
    )
```

边界：

- 如果 input asset 自己已经 nonvalid，会由 `input_asset_not_valid` 负责；lineage issue 可跳过，避免重复。
- 如果 traversal 达到 `max_nodes`，可附加 `truncated=true`，但不升级 severity。
- 不从所有 assets 出发做全量 lineage attention。
- 不把历史 `linked_assets` 纳入 lineage DFS 起点。
- `max_nodes` 第一版建议为 200；超过上限只标记 truncated，不继续扩展扫描。

### 去重与排序

同一 card 可能被多条规则命中。第一版不强行合并不同 kind，但同一 kind / card / asset / current_asset 组合必须去重。

建议 issue id 规则：

```python
def issue_id(kind, card_id=None, asset_id=None, current_asset_id=None, role=None):
    parts = [kind, card_id or "-", asset_id or "-", current_asset_id or "-", role or "-"]
    return ":".join(parts)
```

输出排序：

1. severity：error > warning > info
2. card step
3. card id
4. kind
5. asset id

这样 Manager 和 UI 每次看到的顺序稳定。

### 作用范围

第一版只产出 card-facing issues：

- 有 `card_id` 的 input/output/lineage issue。
- asset-only 的全图健康检查暂不做。

原因：

- 用户和 Manager 的动作单位主要是 card。
- 减少大项目中无消费历史资产带来的噪音。
- 避免把只存在于 `linked_assets` 的旧历史产物误报成问题。

## 集成点

### FlowService / WorkOrder

在 `FlowService.get_work_order()` 中加入：

```json
{
  "dependency_attention": [...],
  "dependency_attention_count": 3
}
```

每个 work item 也加：

```json
{
  "attention_issue_ids": ["..."],
  "attention_severity": "warning"
}
```

注意：

- `can_start` 第一版不因为 attention 自动变 false。
- 如果 input asset 是 missing / rejected / archived，现有 blocker 仍然应阻塞 start。
- 对 accepted card，attention 仍应出现，即使 `active=false`。

### ManagerBlueprintTools

#### Mutating tool 后置诊断

第一版不让 mutating tool 直接返回完整 dependency attention。`update_card` / `delete_card` 只在写入成功后返回一个轻量的“下游可能受影响，需要做依赖检查”提示。

```text
update_card 成功写入
-> reload after snapshot
-> 计算本次 source card 的递归 affected downstream card ids
-> tool response 返回 dependency_attention_check_recommended
-> Manager 如需完整诊断，再显式调用 dependency attention inspect tool
```

```text
delete_card 成功写入（实际为 status=cancelled）
-> reload after snapshot
-> 计算本次 source card 的递归 affected downstream card ids
-> tool response 返回 dependency_attention_check_recommended
```

这里不“挂 ATTENTION”，也不写入下游状态。mutating tool 的职责只是提示 Manager：本次修改可能影响下游数据可靠性。

affected downstream 范围：

- tool response 返回递归受影响子图中的 card id / depth / reason summary，不返回完整 issue 列表。
- 递归范围由 `card.inputs` / `card.outputs` / `Asset.depends_on` 派生，不使用 `linked_assets` 扩展。
- 递归 affected downstream 只用于提示 Manager 哪些下游需要检查。
- tool response 可以展示深层/叶子 card，因为用户通常最关心最终报告、最终图表或结论是否受影响。
- 修复执行不能按 leaf-first。即使 response 展示了深层/叶子 card，实际修复仍必须按 upstream-first 顺序执行：直接下游先修，下一层后修，最终 report/leaf card 最后修。

建议 response 同时表达两种顺序，避免 Manager 混淆：

```json
{
  "dependency_attention_check_recommended": true,
  "affected_downstream": [
    {
      "card_id": "rna_enrich_viz",
      "dependency_depth": 1,
      "reason": "Consumes outputs from the updated source card."
    },
    {
      "card_id": "rna_html_report",
      "dependency_depth": 2,
      "reason": "Depends on an intermediate downstream asset."
    }
  ],
  "recommended_next_tool": "inspect_dependency_attention",
  "repair_execution_order_hint": ["rna_enrich_viz", "rna_html_report"]
}
```

约束：

- `affected_downstream` 是递归影响面提示，可以包含所有受影响 card。
- `affected_downstream` 不代表这些 card 已经存在 ATTENTION issue，只表示它们可能依赖本次变更的上游。
- 如果 `affected_downstream` 为空，`dependency_attention_check_recommended` 应为 false 或省略。
- `dependency_depth` 表示从本次变更 source card 到 affected card 的最短依赖层级。
- `repair_execution_order_hint` 必须按 `dependency_depth` 从小到大排序；同层 card 可按稳定 id 排序。
- Manager 可以在回复用户时优先强调叶子/交付物受影响，但实际修复工具调用必须 upstream-first。
- 完整 `dependency_attention` issues 只能通过 inspect tool / summary / card detail 获取，不由 mutation response 直接塞入。

触发范围：

- `update_card` 改动 `status`。
- `update_card` 改动 `inputs`。
- `update_card` 改动 `outputs`。
- `update_card` 改动 `linked_modules`，且会影响 required assets。
- `delete_card` 将 card 标成 `cancelled`。

不作为第一版主动触发点：

- `review_run` 成功后不触发。run review 是执行产物落地，不代表 manager 修改蓝图语义；如果需要下游变更，应由 manager 后续显式 `update_card` / `rerun_card`。
- 普通读取 API 不 enqueue wake，只按需返回当前派生 attention。

tool response 示例：

```json
{
  "ok": true,
  "card_id": "rna_tf",
  "dependency_attention_check_recommended": true,
  "affected_downstream": [
    {
      "card_id": "rna_html_report",
      "dependency_depth": 1,
      "reason": "Consumes an output from rna_tf or an asset derived from it."
    }
  ],
  "recommended_next_tool": "inspect_dependency_attention"
}
```

如果 auto mode 正在运行，manager sidecar 应把这类 tool response 当作需要检查的下一步上下文，并显式调用 dependency attention inspect tool 获取完整诊断；如果非 auto，则把“下游可能需要依赖检查”汇报给用户，或按用户要求再做 inspect。

#### `inspect_dependency_attention`

新增 Manager tool，专门调用 `DependencyAttentionService` 做完整依赖诊断。

建议参数：

```json
{
  "card_ids": ["rna_html_report"],
  "source_card_id": "rna_tf",
  "include_recursive_downstream": true,
  "max_issues": 50
}
```

语义：

- 无参数时返回项目级 compact dependency attention。
- 指定 `card_ids` 时，只返回这些 card 的完整 issues。
- 指定 `source_card_id` 且 `include_recursive_downstream=true` 时，先计算 source 的递归 downstream，再返回这些 downstream card 的完整 issues。
- 返回 `repair_execution_order`，按 upstream-first 排序，供 Manager 后续修复使用。
- 不写状态，不 enqueue wake。

response 示例：

```json
{
  "issue_count": 1,
  "dependency_attention": [
    {
      "kind": "input_asset_outdated",
      "card_id": "rna_html_report",
      "asset_id": "asset_run_old_tf_table",
      "current_asset_id": "asset_run_new_tf_table",
      "producer_card_id": "rna_tf",
      "message": "综合报告仍引用 rna_tf 的旧版本 TF 产物；当前同 role 输出已变更。"
    }
  ],
  "affected_downstream": [
    {
      "card_id": "rna_html_report",
      "dependency_depth": 1,
      "issue_count": 1
    }
  ],
  "repair_execution_order": ["rna_html_report"]
}
```

#### `inspect_project_summary`

返回 compact attention：

```json
{
  "counts": {
    "dependency_attention": 2
  },
  "dependency_attention": [
    {
      "kind": "input_asset_outdated",
      "card_id": "rna_html_report",
      "asset_id": "asset_old",
      "current_asset_id": "asset_new",
      "message": "..."
    }
  ]
}
```

Manager auto 首次 inspect 就能看到。

#### `get_card_detail`

返回该 card 相关的完整 attention：

```json
{
  "dependency_attention": [...]
}
```

#### `find_cards`

可选：增加 `has_attention` filter。第一版可以不做，避免工具参数膨胀。

#### `find_assets`

保持现有 status/query 能力即可。第一版不加 `attention` filter。

### Manager Prompt

更新 `manager-agent/src/server.js` system prompt：

- 明确 `linked_assets` 可能包含历史资产，不能当作当前依赖权威来源。
- 如果 `inspect_project_summary` 或 `inspect_dependency_attention` 返回 dependency attention，必须先处理或汇报它。
- 如果 `update_card` / `delete_card` 返回 `dependency_attention_check_recommended=true`，下一步应调用 `inspect_dependency_attention` 获取完整诊断，除非用户明确不需要。
- auto mode 下遇到 attention：
  - 如果是旧输入但有明确 current asset，且属于保留原工作流的机械修复，可以自动更新下游 input，并按 upstream-first 顺序重跑受影响链条。
  - 如果涉及数据可信性或用户意图不明确，停止自动推进并向用户说明。
  - 不要静默忽略 accepted card 的 attention。

建议 prompt 文案：

```text
Dependency attention is a derived warning that a card or asset depends on missing, nonvalid, or outdated upstream data.
Do not treat linked_assets as the current dependency source; use card.inputs, card.outputs, and asset.depends_on.
When update_card or delete_card returns dependency_attention_check_recommended, call inspect_dependency_attention before deciding whether to continue.
In auto mode, inspect dependency_attention before starting new runs. If the fix is mechanical, provides a clear current_asset_id, and preserves the user's workflow, you may update downstream inputs and rerun affected cards in upstream-first dependency order. If the scientific intent or branch choice is ambiguous, report the attention and stop or wait for user direction.
```

### Auto Wake

第一版不新增 `dependency_attention_detected` wake event。

原因：

- Manager 造成蓝图变更时，`update_card` / `delete_card` 的 tool response 已经同步返回 `dependency_attention_check_recommended`。
- auto mode 下，正在运行的 Manager 可以立即调用 `inspect_dependency_attention`，不需要再通过 wake event 把自己叫醒一次。
- 非 auto 模式下，也不需要写 wake event；Manager 可以在本次回复里说明“下游可能需要依赖检查”，用户也可以在 UI / inspect 中看到派生 ATTENTION。
- 不写 wake event 可以避免 fingerprint 去重、重复唤醒、自激和 repair session 状态复杂度。

明确规则：

- `review_run` 成功后不 enqueue dependency attention wake。
- `update_card` / `delete_card` 成功后不 enqueue dependency attention wake。
- `inspect_dependency_attention` 只读，不 enqueue wake。
- 如果未来出现外部进程绕过 Manager tools 修改 card / asset，再重新评估是否需要 background scan 或 wake。

### Dependency Repair Session

第一版不实现 dependency repair session。

原因：

- 不写 dependency attention wake event 后，repair session 的主要价值消失。
- 噪音控制由 mutating tool 的轻量提示和 `inspect_dependency_attention` 的显式调用边界解决。
- Manager 每一步仍通过普通 tool call 显式修改和重跑，不需要额外 repair 状态机。

约束：

- 不在 `graph.metadata` 写 repair session。
- 不向前端暴露 repair session。
- 不新增 repair session badge / panel / auto status。
- 如果后续增加 background dependency scan 或外部 mutation wake，再重新评估是否需要 repair session。

#### 诊断方向与修复方向

需要区分两件事：

1. 诊断/汇报可以 leaf-aware。
2. 执行修复必须 upstream-first。

诊断时，Manager 可以优先向用户展示最终交付物受影响，例如综合报告、最终图表、结论 card。这有助于用户理解影响面。

但真正修复时不能先修叶子。叶子先重跑后，如果中间上游再重跑，叶子结果会再次过期。

修复执行规则：

```text
changed upstream source
-> direct affected downstream
-> next downstream layer
-> final report / leaf cards
```

Manager repair planning 应按受影响子图拓扑顺序执行：

1. 从 source card 的直接 consumers 开始。
2. 更新直接下游 inputs 或重跑直接下游 card。
3. 等直接下游产生可信 outputs 后，再处理下一层。
4. 最后处理 report / leaf cards。
5. repair 结束后做 final attention check。

如果 Manager 只是向用户汇报影响面，可以按 leaf/user-facing card 优先展示；如果 Manager 要实际修复，必须按上游到下游顺序。

因此第一版需要明确区分：

- mutation response 的 `affected_downstream`：递归影响面提示，提醒 Manager 需要检查。
- `inspect_dependency_attention` 的 `dependency_attention`：完整诊断输出，面向理解具体问题。
- `repair_execution_order`：修复执行计划，必须 upstream-first。

#### 为什么不是完整状态机

第一版不需要完整 dependency repair 状态机，原因：

- attention 计算本身是内存线性扫描，不是性能瓶颈。
- 不写 wake event，避免了 auto wake 自激。
- mutation response 只提示“需要检查”，完整诊断由 Manager 显式调用，降低 tool response 噪音。
- Manager 每步显式决策，且修复顺序由 `repair_execution_order` 约束。

后续如果项目规模达到数百张 cards / 数千 assets，再考虑：

- 增量 affected subtree 计算。
- 持久 repair plan。
- 每个 affected card 的 repair status。
- scoped final validation。

### Frontend

前端只显示派生 ATTENTION，不改 card status。

数据来源：

- card 列表 /详情可以从 summary、work order 或 `get_card_detail` 的派生结果读取 ATTENTION。
- 前端不把 ATTENTION 写回 card。
- 前端不提供 suppress / dismiss attention 按钮。
- 如果用户认为提示不对，应让 Manager 检查并重置、调整或重跑相关 card，使派生诊断自然消失。

#### ModuleCard

如果 card 有 attention：

- 显示 `ATTENTION` badge。
- badge tooltip / detail 显示简短原因。
- 不覆盖原 status badge。

#### CardDetailPanel

新增 Dependency Attention 区块：

- kind
- affected input/output
- current asset id
- suggested action

不显示 dependency repair session。第一版没有 repair session；前端只展示派生 ATTENTION issue。

#### ConnectionLines

不改变线的计算来源。仍然按现有 Asset provenance / card inputs outputs 画线。

如果后续想表现风险线：

- 只在已有线基础上增加 warning style。
- 不引入 `dependency_edges` 替代 Asset 连线。

第一版建议不改线，只加 card badge/detail。

## OAA 示例预期

场景：

- `rna_tf` 重跑后当前 outputs 指向 `run_63c2e4be9b51`。
- `rna_html_report` 如果仍引用旧 `run_81d50040bcd3` 或 `run_18477514fc4f` 的 TF asset，则应出现：

```text
input_asset_outdated:
rna_html_report input "TF富集结果表" references old rna_tf output asset_run_81d50040bcd3_...
current rna_tf output for role rna_tf_tf_enrich_table is asset_run_63c2e4be9b51_...
```

如果 `rna_html_report.outputs` 指向 candidate asset，但 card status 是 accepted，则应出现：

```text
output_asset_not_valid:
rna_html_report is accepted, but output report_html points to candidate asset asset_run_0b9e3a459aeb_report_html_1.
```

Manager auto 可据此判断：

- 如果用户明显希望继续同一条分析流，更新 report inputs 到当前 TF assets 并重跑 report。
- 如果是否沿用旧 TF 结果不明确，提醒用户选择。

## 测试计划

### Backend unit tests

新增 `backend/tests/test_dependency_attention.py`。

覆盖：

1. input asset missing -> `input_asset_missing`
2. input asset superseded -> `input_asset_not_valid`
3. input asset valid but producer current same-role output changed -> `input_asset_outdated`
4. input asset producer card cancelled -> `input_producer_card_inactive`
5. input asset producer output role removed -> `input_producer_output_removed`
6. accepted card output points to candidate -> `output_asset_not_valid`
7. valid asset depends_on superseded upstream -> `asset_lineage_invalid`
8. old asset only appears in linked_assets, not in inputs/depends_on -> no issue
9. candidate input does not issue warning because candidate is explicitly unaccepted

### WorkOrder tests

覆盖：

1. accepted downstream card still receives attention.
2. attention does not force `can_start=false` by itself.
3. missing / rejected input remains blocker.

### Manager tool tests

覆盖：

1. `inspect_project_summary` includes compact dependency attention.
2. `get_card_detail` includes full dependency attention.
3. `inspect_dependency_attention` returns full issues for selected cards or recursive downstream of a source card.
4. manager prompt / compact tool text does not hide attention count.
5. `update_card` cancelling or removing an upstream output returns `dependency_attention_check_recommended` and affected downstream hints.
6. `delete_card` returns `dependency_attention_check_recommended` and affected downstream hints.
7. mutating tool response does not include full unrelated project attention issues.

### Wake / repair omission tests

覆盖：

1. `update_card` does not enqueue `dependency_attention_detected`.
2. `delete_card` does not enqueue `dependency_attention_detected`.
3. `review_run` success does not enqueue dependency attention wake.
4. auto disabled and auto enabled both avoid dependency attention wake events.
5. no repair session metadata is written.
6. affected downstream hints include recursive affected cards, while inspect repair execution order remains upstream-first.

### Frontend verification

如果本轮实现 UI：

1. accepted card with attention shows ATTENTION badge.
2. status badge remains accepted.
3. card detail shows attention issue.
4. no layout overlap on mobile/desktop.

## 容易出问题的点

### 1. Candidate input 不触发 ATTENTION

现有 work order 允许 `{valid, candidate}` 作为可用输入。Dependency Attention 也不对 candidate input 报 warning。

明确规则：

- candidate input 不报 ATTENTION。
- candidate 本身就是未被明确验收的结果，风险语义已经由 `candidate` 状态表达。
- Dependency Attention 只提示已经 accepted/valid 的依赖链被后续 manager edit/delete、asset invalidation 或 producer output change 破坏的情况。
- accepted card output 指向 candidate 要报 warning，因为 accepted 与 candidate 状态冲突。

后续不建议把 candidate input 降级为 attention，除非产品语义改变为“candidate 不允许作为任何运行输入”。

### 2. input_asset_outdated 只用 metadata.role

依赖 `Asset.metadata.role`。如果历史 asset 没有 role，就无法判断同 role 新旧版本。

已确认规则：

- 只用 `Asset.metadata.role` 判断 outdated。
- 不用 path、label、文件名 fallback。
- 不用 `planned_asset_id` fallback，因为它是 planned output id，不是 role。
- 没有 `metadata.role` 时不猜测，只靠 status / missing / producer inactive / output removed 判断。

`Asset.metadata.role` 的来源链：

```text
card.outputs[].role
-> task_packet.expected_outputs[].role
-> executor manifest.created_assets[].role
-> WorkerService._materialize_run_assets()
-> Asset.metadata["role"]
```

### 3. 不做 suppress attention 机制

不需要额外的 suppress attention 标记。

原因：

- ATTENTION 是派生诊断，不修改 `card.status` / `asset.status` / `inputs`。
- ATTENTION 不持久写入 card，因此没有需要用户手动清除的 card 状态。
- 用户或 Manager 选择不处理，本身就是忽略该派生提示。
- 再加 suppress 字段会制造第二套“忽略语义”，增加状态复杂度。
- 如果用户认为 ATTENTION 不符合分析意图，应通过 Manager 重置、调整或重跑相关 card，而不是 suppress 诊断。

多分支场景：

- 用户可能故意保留旧 asset 做分支报告。
- 此时 `input_asset_outdated` 可以提示，但不强制修复。
- auto mode 在分支意图不明确时不要自动改，应向用户说明。

### 4. Auto mode 明确机械修复

已确认规则：

- 如果 dependency attention 给出明确 `current_asset_id`。
- 且修复只是保留原工作流、把下游 input 从旧 asset 更新到当前 asset。
- Manager 在 auto mode 下可以自动 `update_card`，然后按 upstream-first 顺序重跑受影响链条。
- 如果涉及分支选择、科学解释、参数变化、方法变化，则不能自动修复，应汇报用户。

### 5. Transitive lineage 成本

大项目里递归检查所有 assets 的 `depends_on` 可能有成本。

已确认第一版规则：

- 保留局部 DFS，用于 `asset_lineage_invalid`。
- 只从 card-facing 起点出发：`card.inputs[].asset_id` 和 accepted/report-selected `outputs[].asset_id`。
- 不做全项目 asset graph 扫描。
- 不只停留在直接 input/output；允许沿 `Asset.depends_on` 向上游查找断裂 lineage。
- 限制 DFS 节点数，例如 200；超过上限标记 `truncated=true`。

### 6. Wake event 与历史 patch 接口

如果所有 run review / asset status change 都触发扫描，可能产生噪音。

已确认第一版规则：

- 不写 `dependency_attention_detected` wake event。
- manager `update_card` / `delete_card` 成功后只返回 `dependency_attention_check_recommended` 和 affected downstream hints。
- Manager 如需完整诊断，显式调用 `inspect_dependency_attention`。
- Manager inspect / UI 每次请求实时派生。
- 不接 `PatchApplyService` 后置诊断 hook。

历史 patch 接口处理方向：

- 不继续扩展 PatchApplyService 的 dependency attention 行为。
- 后续优先评估历史 patch/edit 接口是否仍需要保留。
- 如果仍有内部入口需要修改 card/asset/module，应尽量收敛到 Manager card tools 或统一 mutation service。
- 只有在确认 PatchApplyService 仍是活跃写入口后，才考虑接入同一个后置诊断 hook；第一版不做。

### 7. Repair session 边界

第一版不实现 repair session。

已确认：

- 不写 repair session metadata。
- 不新增前端 repair session badge。
- 不新增前端 repair session panel。
- 不把 repair session active 映射成 `running`、`repairing` 或其他用户可见 card 状态。
- 如果未来引入 background dependency scan / wake，再重新评估是否需要 repair session 做降噪。

## 推荐实施顺序

1. 新增 `DependencyAttentionService`，只读派生 issues。
2. 给 OAA 典型状态写 backend tests。
3. 新增 `inspect_dependency_attention` Manager tool。
4. 接入 `ManagerBlueprintTools.inspect_project_summary()` 和 `get_card_detail()`。
5. 接入 manager `update_card` / `delete_card` 轻量 affected downstream hint，不返回完整 issues。
6. 更新 manager-agent prompt，让 auto/user 模式知道何时调用 `inspect_dependency_attention`。
7. 接入 `FlowService.get_work_order()`，给 UI/work order 提供 attention。
8. 前端显示 card ATTENTION badge 和 detail 区块。
9. 明确不接 dependency attention wake event / repair session。

## 待确认问题

当前本方案的产品语义确认项已收敛。后续实现过程中如果发现新的入口或 UI 状态冲突，再补充到本节。
