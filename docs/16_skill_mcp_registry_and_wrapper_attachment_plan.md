# Skill / MCP 注册库与 Wrapper 挂载执行文档

## 目标

这轮方案把 `Skill Library`、`MCP Library` 和执行 wrapper 的关系收敛成一套稳定结构，避免能力直接塞进 Manager 上下文，也避免后续能力扩展把执行链路做乱。

目标有五个：

1. `skill` / `mcp` 不直接常驻注入 Manager 的 pi agent。
2. 它们先注册进系统能力库，变成可搜索、可摘要、可复用的条目。
3. 注册时由 `deepseek-v4-flash` 读取源描述并生成短摘要与标签。
4. Manager 只通过 tool call 检索能力库，再把选中的条目挂到 card 的执行配置里。
5. wrapper 在实际 run 启动时完成真实挂载，而不是让 Manager 假设这些能力一直都在。

这份文档是执行方案文档，给后续开发和 fork 复用。

## 核心原则

- `skill` / `mcp` 是系统能力库条目，不是 Manager 的常驻能力。
- Manager 只看摘要级元数据，不直接读原始 `SKILL.md` / `README.md` 全文。
- 摘要在注册阶段生成，不在对话阶段现算，避免每次聊天消耗上下文和 token。
- card 只保存“选择结果”，例如 `skill_id` / `mcp_id`，不保存大段原文。
- wrapper 决定“怎么挂载”，Manager 只决定“挂什么”。
- run 级挂载必须可追溯，能回答“这次执行用了哪些能力”。

## 为什么要这样设计

如果把 skill 或 MCP 直接注册到 Manager agent 常驻能力，会有四个问题：

1. 上下文持续膨胀。
2. Manager 会误以为所有能力默认可用。
3. 执行不可复现，难以知道某张 card 到底依赖了什么。
4. UI、配置、执行环境之间没有统一来源，后续维护会越来越脆。

把它们做成“先注册、再搜索、再挂载”的模式，Manager 认知会更轻，执行链路也更清楚。

## 分层架构

整体拆成五层：

1. 源能力层
2. 注册库层
3. Manager 检索层
4. Card 配置层
5. Wrapper 挂载层

### 1. 源能力层

这层是原始材料，不直接给 Manager 使用。

来源包括：

- `skill`
  - `SKILL.md`
  - 关联说明文件
  - 技能目录结构
- `mcp`
  - `README.md`
  - manifest / config
  - 启动脚本
  - 运行时说明

### 2. 注册库层

新增统一的 `LibraryRegistryService`，负责把原始能力变成结构化条目。

注册后的条目至少包含：

- `id`
- `kind`
- `name`
- `summary_short`
- `summary_long`
- `tags`
- `source_path`
- `source_hash`
- `enabled`
- `runtime_requirements`
- `compatibility_notes`
- `generated_by`
- `generated_at`

其中：

- `summary_short` 给 Manager 搜索和列表展示用。
- `summary_long` 给设置页详情展示用。
- `source_hash` 用来判断是否需要重新摘要。
- `runtime_requirements` / `compatibility_notes` 由后端补结构化约束，不完全依赖模型推断。

### 3. Manager 检索层

Manager 不直接获得 skill / MCP 正文，只拿到搜索工具。

建议工具：

- `list_skill_library`
- `search_skill_library`
- `get_skill_library_item`
- `list_mcp_library`
- `search_mcp_library`
- `get_mcp_library_item`

其中：

- `search_*` 是主要入口
- `list_*` 适合调试或浏览
- `get_*_item` 只在需要看更细摘要或兼容性时再用

### 4. Card 配置层

Manager 选中条目后，通过 `configure_card_execution` 或同类工具把选择结果写到 card。

card 上只保存轻量配置，例如：

```json
{
  "skills": ["single-cell-plotting"],
  "mcp_servers": ["omicverse"]
}
```

如果以后需要加参数，再扩展成对象形式，但第一阶段不复杂化。

### 5. Wrapper 挂载层

真正运行 card 时，wrapper 根据 card 上保存的选择结果，解析注册库并做运行期挂载。

- skill：解析源路径，生成 run-local skill 挂载
- MCP：生成 run-local MCP config，并绑定到执行环境

这样 Manager 认知轻，执行器可复现，run 级依赖也可追踪。

## Skill / MCP 的注册方式

## Skill 注册流程

1. 扫描技能来源目录
   - `~/.codex/skills/*/SKILL.md`
   - `~/.agents/skills/*/SKILL.md`
2. 提取基础信息
   - 标题
   - frontmatter
   - 简述段
   - 路径
3. 计算 `source_hash`
4. 若 hash 未变化且已有条目，则复用旧摘要
5. 若是新增或内容变化，则调用 `deepseek-v4-flash`
6. 生成：
   - `summary_short`
   - `summary_long`
   - `tags`
   - `use_cases`
7. 写入 `skills.json`

## MCP 注册流程

1. 扫描 MCP 来源目录或清单
2. 提取：
   - `README.md`
   - manifest / config
   - 启动方式
   - runtime 相关提示
3. 计算 `source_hash`
4. 调 `deepseek-v4-flash` 生成用途摘要和标签
5. 后端补充结构化字段：
   - `supported_runtimes`
   - `network_requirement`
   - `env_requirement`
   - `launch_hint`
6. 写入 `mcp.json`

## 摘要生成规则

摘要应偏“用途”和“适用场景”，而不是实现细节。

推荐风格：

- “用于改善单细胞绘图”
- “用于单细胞数据分析”
- “用于网页检索与文献内容抽取”
- “用于生信运行环境下的 omics 工具调用”

不推荐：

- 原文复述
- 过长安装说明
- prompt 规则细节
- 超过 1 到 2 句话的列表说明

建议把 summarizer 单独配成：

- `BLUEPRINT_LIBRARY_SUMMARIZER_MODEL=deepseek-v4-flash`

## 注册库存储

第一阶段不必上数据库，直接使用本地 JSON registry。

建议路径：

- `workspace/_system/library/skills.json`
- `workspace/_system/library/mcp.json`

分开存比单个大文件更便于调试和演进。

### Skill 条目示例

```json
{
  "id": "single-cell-plotting",
  "kind": "skill",
  "name": "Single Cell Plotting",
  "summary_short": "用于改善单细胞绘图",
  "summary_long": "提供单细胞降维、marker 和 cluster 可视化相关能力，适合结果展示类 card。",
  "tags": ["single-cell", "plotting", "visualization"],
  "source_path": "/home/solarise/.agents/skills/single-cell-plotting/SKILL.md",
  "source_hash": "sha256:...",
  "enabled": true,
  "runtime_requirements": [],
  "compatibility_notes": [],
  "generated_by": "deepseek-v4-flash",
  "generated_at": "2026-05-25T09:10:00Z"
}
```

### MCP 条目示例

```json
{
  "id": "omicverse",
  "kind": "mcp",
  "name": "OmicVerse",
  "summary_short": "用于单细胞与组学数据分析",
  "summary_long": "提供 omics 相关工具访问能力，适合需要 omicverse 运行时和辅助接口的分析 card。",
  "tags": ["omics", "single-cell", "runtime"],
  "source_path": "/path/to/omicverse/README.md",
  "source_hash": "sha256:...",
  "enabled": true,
  "runtime_requirements": ["omicverse"],
  "compatibility_notes": [],
  "supported_runtimes": ["omicverse"],
  "launch_hint": "requires omicverse runtime",
  "generated_by": "deepseek-v4-flash",
  "generated_at": "2026-05-25T09:10:00Z"
}
```

## Manager 的认知与行为规则

Manager 的 prompt 需要明确更新：

- skill / MCP 是可搜索的注册库，不是默认常驻能力
- 不要默认认为所有 skill / MCP 已启用
- 只有当 card 目标明确需要时才去搜索能力库
- 选择后只写条目 id，不复制正文
- 除非用户明确问，不要为了说明能力而粘贴库条目的长摘要

建议加入一条硬规则：

`Search the skill/MCP library when a card may benefit from reusable execution abilities, then attach only the selected ids to executor configuration.`

## Manager 工具设计

建议最终保留这组工具：

- `list_skill_library`
- `search_skill_library(query, tags?, runtime?, top_k?)`
- `get_skill_library_item(skill_id)`
- `list_mcp_library`
- `search_mcp_library(query, runtime?, top_k?)`
- `get_mcp_library_item(mcp_id)`

行为预期：

- `search_*` 返回 name、短摘要、tags、兼容 runtime、enabled 状态
- `get_*` 再返回长摘要、source_path、compatibility_notes、launch_hint
- Manager 通常先 `search_*`，命中候选后才 `get_*`

## Wrapper 挂载设计

## 总原则

- Manager 决定选什么
- wrapper 决定怎么挂

## Skill 挂载

执行时：

1. wrapper 读取 `executor_context.skills`
2. 在 registry 中解析 skill 条目
3. 为当前 run 构造最小 skill 挂载结构
4. 注入 run-local skill 路径或 `.pi` 配置

对于 `pi`，近端实现应尽量利用其已有能力：

- `--skill <path>`
- `cwd/.pi/settings.json`
- `cwd/.pi/skills`

建议第一阶段采用显式、最可控的方式：

- run 启动时只挂本次选中的 skill
- 使用 `--no-skills` 配合显式 `--skill <path>`
- 避免把全局 skill 目录无差别暴露给执行器

## MCP 挂载

执行时：

1. wrapper 读取 `executor_context.mcp_servers`
2. 在 registry 中解析所选 MCP
3. 结合当前 runtime / app settings 生成 run-local MCP config
4. 将配置注入 run 目录和执行环境

近端约束：

- 不要假设 `pi` 已经原生支持本项目的 MCP 配置格式
- wrapper 可以先拥有 run-local MCP config 的生成权
- 即便底层 agent 暂时不能原生消费，也要保留显式降级说明

也就是说：

- skill 可以优先走 `pi` 原生挂载能力
- MCP 先走 wrapper 自己的配置与注入层

## Script Library 与 Skill/MCP 的先后关系

这里必须明确顺序，否则执行器封装会反复返工。

推荐顺序：

1. `Script Library`
2. `Skill Library`
3. `MCP Library`

原因：

- `Script Library` 负责可复用脚本资产与 wrapper 打包基线
- skill 是对执行器能力的轻量增强
- MCP 是运行时工具提供者，依赖 wrapper 封装形态已经稳定

所以 skill / MCP 的注册库虽然可以先做，但真正挂载设计必须兼容脚本资产打包流，而不是单独另起一套。

## 搜索与排序

第一阶段不需要向量库，轻量检索就够用：

- 名称匹配
- tag 匹配
- 摘要关键词匹配
- runtime 过滤

后续库大了再考虑：

- embeddings
- hybrid search
- usage ranking
- project/domain 个性化排序

## 前端产品形态

## 结构调整

以下内容不应该长期留在 `SideNav`：

- `Runtime Preferences`
- `Skill Library`
- `MCP Library`
- `API Settings`

应迁到工作台单独配置页，例如：

- `Settings`
- 或 `Runtime & Libraries`

## 页面结构建议

设置页建议分成四个区块：

1. `API Settings`
2. `Runtime Preferences`
3. `Skill Library`
4. `MCP Library`

### API Settings

负责：

- DeepSeek API key
- Tavily API key
- summarizer model
- 其他运行时 API 配置

### Runtime Preferences

负责：

- script preference
- python runtime
- R runtime
- 默认运行环境提示

这块应项目级持久化，而不是纯前端临时状态。

### Skill Library

提供：

- 搜索
- 列表
- 启用/禁用
- 查看摘要
- 查看源路径
- 刷新注册
- 重新生成摘要

### MCP Library

提供：

- 搜索
- 列表
- 查看兼容 runtime
- 查看 launch hint
- 启用/禁用
- 刷新注册
- 重新生成摘要

## UI 逻辑建议

- 这页是系统配置页，不是日常主工作面，默认做折叠分区。
- `API Settings` 与 `Runtime Preferences` 适合放在上方。
- `Skill Library` 与 `MCP Library` 适合放在下方，偏“资源库浏览”。
- 列表项建议只显示：
  - 名称
  - 一句摘要
  - tags
  - enabled 状态
- 点击列表项后，再展开长摘要、兼容性、源路径。

不要把原始 `SKILL.md` / `README.md` 全文直接灌进页面主列表，否则会非常重。

## 与 card 配置的关系

设置页负责系统级能力管理，不负责人工逐张 card 手工挂 skill / MCP。

card 的能力选择仍以 Manager tool call 为主。

如果后续要给人类可视化排查，可做只读展示：

- 某 card 当前挂了哪些 skill
- 某 card 当前挂了哪些 MCP

但不建议先开放大规模人工编辑入口，否则又会出现配置分叉。

## 后端接口建议

建议新增系统级 library API：

- `GET /api/library/skills`
- `GET /api/library/mcp`
- `GET /api/library/skills/search?q=...`
- `GET /api/library/mcp/search?q=...`
- `GET /api/library/skills/{skill_id}`
- `GET /api/library/mcp/{mcp_id}`
- `POST /api/library/skills/refresh`
- `POST /api/library/mcp/refresh`
- `POST /api/library/skills/{skill_id}/resummarize`
- `POST /api/library/mcp/{mcp_id}/resummarize`

项目内如果还有旧入口，可以先做兼容 shim，但长期应收敛到统一 library API。

## 推荐落地顺序

1. 引入 `LibraryRegistryService`
2. 打通 skill registry 扫描、摘要、缓存
3. 打通 MCP registry 扫描、摘要、缓存
4. 暴露 list / search / detail / refresh / resummarize API
5. 前端新增设置页，迁出侧栏配置
6. 修改 Manager prompt 与 tool surface
7. 在 wrapper 中按 registry id 做真实挂载
8. 最后再补更强的搜索排序和人工运营能力

## 风险与注意点

### 1. 摘要漂移

模型摘要会有不稳定性。

应对：

- 只在内容变化时重生成
- 支持手动 `resummarize`
- 保留 `source_path` 与 hash

### 2. 摘要过长

会污染检索和 UI。

应对：

- `summary_short` 强限制一句话
- `summary_long` 限制短段落

### 3. MCP 元数据不完整

只靠 README 很难可靠推断运行要求。

应对：

- runtime 结构化字段由后端补
- 模型只负责用途摘要和标签

### 4. Manager 误挂载

搜索命中不代表真的适用。

应对：

- 返回 `runtime_requirements`
- 返回 `compatibility_notes`
- prompt 强调“仅在明确需要时挂载”

### 5. 过度消耗上下文

如果 Manager 每次都先查库，会造成不必要的 tool 调用。

应对：

- prompt 里明确：仅在 card 明显需要复用能力时才查库
- 默认优先依赖蓝图上下文和已有执行经验
- 不把 library 条目正文并入对话历史

## Definition Of Done

- `skill` / `mcp` 不再被视为 Manager 常驻能力
- 有统一的注册库，而不是直接读源文件或硬编码
- 注册时使用 `deepseek-v4-flash` 生成短摘要和标签
- Manager 通过搜索工具查库，而不是直接持有全文
- card 只保存轻量选择结果
- wrapper 在 run 启动时完成真实挂载
- `Skill Library` / `MCP Library` / `API Settings` 迁到独立设置页
- 整体结构兼容后续 `Script Library → Skill Library → MCP Library` 的执行器封装顺序
