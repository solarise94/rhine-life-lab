# Manager Compact / Websearch / Artifact Preview 执行文档

## 目标

这份文档用于执行和验收以下一组已落地改动：

1. Manager 上下文压缩（compact）与 `/compact` 手动触发。
2. Manager Tavily Web Search / Extract 能力。
3. Artifact Preview Router 与结果预览抽屉。
4. Card 选中居中、横向滚动冲突修复、默认 detail 跳转移除。
5. DeepSeek 1M context window 兼容配置。

本次文档偏执行手册，不重复展开产品背景。

## 当前实现范围

### Backend

- [backend/app/api/chat.py](/home/solarise/blueprint_re_v3/backend/app/api/chat.py) 新增 `POST /projects/{project_id}/chat-compact`。
- [backend/app/services/manager_service.py](/home/solarise/blueprint_re_v3/backend/app/services/manager_service.py) 负责把 `session_messages`、`thinking_effort`、上下文与鉴权透传给 manager sidecar。
- [backend/app/models/chat.py](/home/solarise/blueprint_re_v3/backend/app/models/chat.py) 已扩展 chat timeline/compact 持久化字段。

### Manager Sidecar

- [manager-agent/src/server.js](/home/solarise/blueprint_re_v3/manager-agent/src/server.js) 已接入 `pi-agent-core` compaction：
  - `compact`
  - `prepareCompaction`
  - `estimateContextTokens`
  - `buildSessionContext`
- 已新增 `/compact` endpoint。
- 已新增 Tavily 工具：
  - `web_search`
  - `web_extract`
- 已把 `configure_card_execution` 纳入 manager prompt，避免让 card agent 直接向用户要权限。

### Frontend

- [frontend/components/manager-chat/ManagerChatPanel.tsx](/home/solarise/blueprint_re_v3/frontend/components/manager-chat/ManagerChatPanel.tsx)
  - 支持 `/compact` 拦截。
  - 支持 compact timeline item。
  - 支持 thinking/tool/compact 的时序展示与 session 持久化。
- [frontend/components/layout/ProjectWorkspace.tsx](/home/solarise/blueprint_re_v3/frontend/components/layout/ProjectWorkspace.tsx)
  - 接入结果预览抽屉。
- [frontend/lib/stores/workspace-ui-store.ts](/home/solarise/blueprint_re_v3/frontend/lib/stores/workspace-ui-store.ts)
  - 管理 Artifact Preview Router 状态。
- 结果预览入口已接到：
  - card file bag
  - files panel
  - results grid
- [frontend/components/cards/CardStream.tsx](/home/solarise/blueprint_re_v3/frontend/components/cards/CardStream.tsx)
  - 已增加卡片选中居中滚动。

## 配置项

### 必需

```bash
BLUEPRINT_MANAGER_BACKEND=pi
BLUEPRINT_INTERNAL_TOOL_TOKEN=...
BLUEPRINT_DEEPSEEK_API_KEY=...
```

### Manager Sidecar

```bash
MANAGER_AGENT_HOST=127.0.0.1
MANAGER_AGENT_PORT=18002
MANAGER_AGENT_PROVIDER=deepseek
MANAGER_AGENT_MODEL=deepseek-v4-pro
MANAGER_AGENT_TIMEOUT_MS=600000
```

### DeepSeek Context Window

当前按 DeepSeek 长上下文模式预留：

```bash
MANAGER_CONTEXT_WINDOW_TOKENS=1000000
MANAGER_COMPACTION_ENABLED=true
MANAGER_COMPACTION_KEEP_RECENT_TOKENS=120000
MANAGER_COMPACTION_RESERVE_TOKENS=16000
```

说明：

- `MANAGER_CONTEXT_WINDOW_TOKENS` 当前按 1M 兼容。
- `KEEP_RECENT_TOKENS` 控制压缩后仍保留的近端消息量。
- `RESERVE_TOKENS` 预留给当前回合输出、tool 调用与系统开销。

### Web Search

```bash
MANAGER_WEBSEARCH_ENABLED=true
TAVILY_API_KEY=...
TAVILY_BASE_URL=https://api.tavily.com
```

行为约束：

- 未开启 `MANAGER_WEBSEARCH_ENABLED` 或缺少 `TAVILY_API_KEY` 时，sidecar 仍应正常启动。
- 禁止把本地私有文件内容或 secrets 发给 Tavily。

## 执行顺序

### 1. 静态检查

```bash
node --check manager-agent/src/server.js
cd frontend && npm run build
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

当前基线：

- frontend build 已通过。
- backend tests 已通过，基线为 `88 tests OK`。

### 2. 本地联调

按以下顺序启动：

1. backend
2. manager-agent
3. frontend

建议确认：

- backend 能访问 sidecar `chat` 与 `compact` endpoint。
- sidecar 能读取 DeepSeek key。
- 如要测 websearch，再补 Tavily key。

### 3. 交互验收

#### A. Compact

1. 在 manager 对话中连续发送多轮消息，制造较长 session。
2. 手动输入 `/compact`。
3. 确认时间线出现：
   - `正在压缩上下文`
   - 完成后切为 `已压缩上下文`
4. 刷新页面，确认 compact item 仍在。
5. 继续追问，确认后续轮次仍能继承 compact 后上下文。

#### B. Web Search

1. 配置 `MANAGER_WEBSEARCH_ENABLED=true` 与 `TAVILY_API_KEY`。
2. 问一个需要最新外部信息的问题。
3. 确认时间线中有：
   - `已搜索网页`
   - `已读取网页`
4. 禁用 Tavily 后重启，确认 manager 仍可正常回答非联网问题。

#### C. Artifact Preview

1. 从 card output 打开结果。
2. 从 files panel 打开结果。
3. 从 results grid 打开结果。
4. 验证以下类型：
   - 图片
   - 表格
   - markdown/text
   - 二进制文件回退下载
5. 在预览抽屉中点击：
   - `Send to Manager`
   - `Explain this result`

#### D. Card Canvas

1. 点击左右边缘卡片。
2. 确认展开后卡片滚动到可视中心。
3. 横向滚动时不触发浏览器返回。
4. 点击 card 后不再自动跳到 detail 区。

## 验收标准

满足以下条件才算这一轮完成：

- `/compact` 可用，且 compact 事件可持久化。
- 自动/手动 compact 都不会破坏后续 manager 对话上下文。
- Web search 在开启时可见、在关闭时可降级。
- 结果预览不再依赖“先下载再查看”。
- card 选中、展开、居中和横向滚动行为稳定。
- thinking、tool use、compact 在时间线中按真实顺序呈现。

## 观察点与排障

### 1. `/compact` 没生效

优先检查：

- frontend 是否把 `session_messages` 传给 `/chat-compact`
- backend 是否把 `session_messages` 透传给 sidecar
- sidecar 是否启用了 `MANAGER_COMPACTION_ENABLED`

重点文件：

- [frontend/lib/api.ts](/home/solarise/blueprint_re_v3/frontend/lib/api.ts)
- [backend/app/services/manager_service.py](/home/solarise/blueprint_re_v3/backend/app/services/manager_service.py)
- [manager-agent/src/server.js](/home/solarise/blueprint_re_v3/manager-agent/src/server.js)

### 2. Web Search 工具不出现

优先检查：

- `MANAGER_WEBSEARCH_ENABLED`
- `TAVILY_API_KEY`
- manager prompt 是否已包含 `web_search` / `web_extract`

### 3. Timeline 顺序错乱

高风险文件是：

- [frontend/components/manager-chat/ManagerChatPanel.tsx](/home/solarise/blueprint_re_v3/frontend/components/manager-chat/ManagerChatPanel.tsx)

重点检查：

- SSE event 合并逻辑
- thinking/tool/compact item 的 id 复用逻辑
- session 保存 signature 是否覆盖 timeline 状态变化

### 4. 结果预览抽屉打开但空白

优先检查：

- `assetId` 是否正确
- `getResultAsset` 返回的 preview 类型
- preview drawer store 是否被重复 reset

## 回滚策略

如果上线后发现问题，按风险从低到高回滚：

1. 关闭 Web Search

```bash
MANAGER_WEBSEARCH_ENABLED=false
```

2. 关闭自动 Compact

```bash
MANAGER_COMPACTION_ENABLED=false
```

3. 保留 frontend UI，临时停用 `/compact` 入口。

4. 如需彻底回退，回退以下改动组：
   - manager-agent compaction
   - backend `/chat-compact`
   - frontend compact timeline / preview drawer

## 后续待办

这份执行文档之外，建议下一步继续做：

1. 把 compact 动画与状态提示进一步统一到时间线展示。
2. 增加 manager 权限配置可视入口，避免只能靠 prompt/tool 内部兜底。
3. 为 Artifact Preview 增加更完整的 report/html/pdf 路由与内嵌查看器。
4. 做一次真实浏览器 smoke，重点覆盖长会话 compact 与 Tavily 联网。

## 部署命令

如本轮验收通过，可使用已有脚本：

```bash
bash scripts/deploy_user_systemd.sh
```
