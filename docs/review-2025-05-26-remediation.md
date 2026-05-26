# Blueprint RE v3 修复执行文档

**日期：** 2026-05-26  
**对应审查：** `docs/review-2025-05-26.md`  
**状态：** 待执行  
**用途：** 本文档作为 2025-05-26 审查结果的修正文档和实施计划。后续修复拆分、提测、回归，以本文为准；原审查文档保留“初审记录”属性，不再直接作为排期依据。

---

## 1. 结论摘要

这轮问题里，真正需要优先落地的是三类：

1. `manager-agent` 启动期可用性和请求入口健壮性。
2. `backend` run 生命周期、输出校验、项目删除并发这三条执行链路。
3. 前后端接口对齐和上传代理正确性。

按影响面看，第一批应先修会导致“服务健康但不可用”、run 状态错乱、输出静默漏检、项目删除并发破坏、以及部署时无法快速诊断的问题。  
按实施策略看，不建议继续在原 review 文档上逐条修辞争论，应该直接以“确认成立 / 修正文案 / 落地方案 / 验证标准”四段式推进。

---

## 2. 已确认问题

### P0. Manager Agent 健康但不可用

- **结论：** 成立。
- **现状：**
  - `manager-agent/src/server.js:2181` 在实际请求时才检查 API key。
  - `manager-agent/src/server.js:2518` 的 `/healthz` 无条件返回 200。
  - `scripts/deploy_user_systemd.sh:275` 会把空的 `BLUEPRINT_DEEPSEEK_API_KEY` 写入环境文件。
- **风险：**
  - systemd 与反向代理会判定服务健康。
  - 真实 `/chat`、`/chat-stream` 请求全部失败。
  - 首次部署和密钥失效场景难排查。
- **修复方案：**
  - 启动期校验 `apiKey`、模型配置和可解析的上游 URL。
  - 配置无效时直接 `process.exit(1)`，不允许以“健康但不可用”形态运行。
  - `/healthz` 改为区分 `ready` 与 `not ready`；未就绪返回 `503`，响应体包含最少诊断字段。
  - 部署脚本在生产部署缺 key 时直接失败，不再写空值继续。
- **验收标准：**
  - 缺 key 启动时进程非 0 退出。
  - `/healthz` 在无 key 时返回 503。
  - 有效配置下 `/healthz` 返回 200 且 `ready=true`。

### P0. Run 在 `Popen` 前被写成 `running`

- **结论：** 成立。
- **现状：**
  - `backend/app/services/worker_service.py:717` 先写状态和事件。
  - `backend/app/services/worker_service.py:723` 才真正拉起子进程。
  - `backend/app/services/worker_service.py:452` 的 `cancel_run()` 在该窗口期可能拿不到进程句柄。
- **风险：**
  - run 状态与真实进程状态不一致。
  - cancel 可能“看起来成功”，但实际没取消到任何东西。
  - 子进程失败、取消、事件归档之间会出现竞态脏状态。
- **修复方案：**
  - 引入显式 `launching` 状态。
  - 顺序调整为：构造命令 -> `Popen` 成功 -> 注册 `self._processes[run_id]` -> 写 `running` -> 发 `run_started`。
  - `cancel_run()` 识别 `launching`，允许取消尚未完全注册的 run。
  - `_run_status()` 改为在项目锁内读取。
- **验收标准：**
  - `Popen` 失败时 run 不会短暂进入 `running`。
  - launching 状态下的 cancel 能稳定结束 run。
  - 并发压测下不会再出现“运行中但无进程句柄”的记录。

### P0. 输出文件缺失被静默跳过

- **结论：** 成立。
- **现状：** `backend/app/services/executor_validation_service.py:158` 对不存在的 `created_assets` 直接 `continue`。
- **风险：**
  - manifest 声称交付了文件，但后端确定性校验仍然可能放行。
  - reviewer 没抓到时，最终 run 结果会被错误接受。
- **修复方案：**
  - 对缺失文件生成确定性的 `missing_output` 校验错误。
  - 错误中包含 `asset_id`、声明路径、预期 role、以及检测阶段。
  - 将该类问题视为失败条件，而不是 warning。
- **验收标准：**
  - 缺文件 manifest 必定产生结构化错误。
  - 对应 run 无法进入成功或待 review 终态。

### P0. 资产与输出契约按位置 zip 配对

- **结论：** 成立。
- **现状：** `backend/app/services/worker_service.py:1760` 使用 `zip(..., strict=False)`。
- **风险：**
  - 顺序错时会静默错绑。
  - 列表长度不一致时会静默截断。
  - `asset_id`、输出角色、状态回写可能全部落到错误对象。
- **修复方案：**
  - 先按 manifest `asset_id` 精确 join。
  - 若无 `asset_id`，退化为按 `role` join。
  - 若出现重复 `role`、缺少 planned id、或 unmatched 记录，明确写 warning/error。
  - 删除按位置 zip 的隐式回退逻辑。
- **验收标准：**
  - 乱序情况下仍能正确绑定。
  - 长度不一致或重复 role 时不会静默成功。
  - 新单测覆盖乱序、缺项、重复项三种情况。

### P0. `delete_project()` 与后台 run 线程并发

- **结论：** 成立。
- **现状：** `backend/app/services/project_service.py:202` 直接 `rmtree()`，未与执行线程协同。
- **风险：**
  - 后台执行线程在读写过程中失去目录。
  - 结果文件、状态文件、事件日志可能只写一半。
  - 后续项目恢复、审计、清理都会被污染。
- **修复方案：**
  - 删除前先拿项目锁。
  - 由 `WorkerService` 检查该项目是否仍有活动 run。
  - 若存在活动 run，直接返回 `409 Conflict`。
  - 更稳妥的后续方案是引入 `deleting` 状态，先取消并 join 线程，再执行删除。
- **验收标准：**
  - 活动 run 存在时删除接口稳定返回 409。
  - 无活动 run 时删除成功，不影响其他项目。

### P0. 非法 Host/URL 可触发未处理 rejection

- **结论：** 成立。
- **现状：**
  - `manager-agent/src/server.js:2522` 的 `new URL(...)` 在顶层 `try` 外。
  - `manager-agent/src/server.js:2557` 用 `void handle(req, res)` 丢弃 promise rejection。
- **风险：**
  - 单个恶意或脏请求可直接打死 Node 进程。
  - 该问题与是否有认证无关，请求入口即受影响。
- **修复方案：**
  - 把 URL 解析包入最外层 `try/catch`。
  - `createServer` 改成 `handle(req, res).catch(...)`，统一错误收口。
  - 为 SSE 写流增加 `res.writableEnded || res.destroyed` 防护，并在 `close` 后标记不可再写。
- **验收标准：**
  - 非法 Host 请求不会导致进程退出。
  - SSE 客户端断开后不会继续写流。

### P1. `/runs/{run_id}` 对不存在 run 返回 500

- **结论：** 成立。
- **现状：** `backend/app/api/runs.py:90` 使用裸 `next(...)`。
- **修复方案：**
  - 改为 `next(..., None)`。
  - 未找到时返回 404，并给出明确错误消息。
- **验收标准：**
  - 不存在 run_id 时返回 404。
  - 新增后端单测覆盖该路径。

### P1. run 结束后 WebSocket 不主动关闭

- **结论：** 成立。
- **现状：** `backend/app/api/runs.py:166` 在终态下只 sleep，不 `break`。
- **修复方案：**
  - 当 run 已到终态且待发送事件已清空时主动 `break`。
  - 明确在 server 侧关闭 WebSocket。
- **验收标准：**
  - run 完成后连接会自然结束。
  - 不再残留无限轮询协程。

### P1. `run_` 前缀事件被误标为 executor 来源

- **结论：** 成立。
- **现状：** `backend/app/services/worker_service.py:1169` 使用过粗的前缀判定。
- **修复方案：**
  - 改为显式事件类型枚举。
  - manager 发出的 run 生命周期事件保持 `manager` 或系统来源，不再根据前缀推断。
- **验收标准：**
  - `run_started`、`run_cancelled` 等来源字段正确。

### P1. `_run_status()` 无锁读取

- **结论：** 成立。
- **现状：** `backend/app/services/worker_service.py:1659` 读取 graph 时未持项目锁。
- **修复方案：**
  - 所有 `_run_status()` 读操作都在项目锁内完成。
  - 若已有锁调用链，避免二次锁死，统一整理调用约束。
- **验收标准：**
  - 并发 run/cancel/cleanup 场景下无异常状态回读。

### P1. `/advanced/proposals` 前后端不对齐

- **结论：** 成立。
- **现状：**
  - `frontend/lib/api.ts:303`、`frontend/lib/hooks.ts:191` 会请求该端点。
  - `backend/app/api/advanced.py` 当前不存在对应 GET 路由。
- **修复方案：**
  - 最小改法：后端新增 `GET /proposals`，直接返回 `store.load_proposals()`。
  - 暂不建议先删前端 query；新增路由的改动面更集中，也更符合现有缓存逻辑。
- **验收标准：**
  - 前端刷新工作区不再产生 404。
  - proposals query 能正常读到 store 中的内容。

### P2. 前端 `/api` 代理路由会损坏 multipart 上传

- **结论：** 成立，且不是死代码。
- **现状：**
  - `frontend/app/api/[...path]/route.ts:23` 使用 `request.text()` 转发 body。
  - 上传路径 `frontend/lib/api.ts:234` 确实走该代理路由。
- **风险：**
  - PNG、zip、xlsx 等二进制上传会被 UTF-8 文本化并损坏。
  - 聊天上传和文件面板上传都受影响。
- **修复方案：**
  - 转发时直接使用 `request.body`。
  - 有 body 时设置 `duplex: "half"`。
  - 保持原始 headers/body 流，不再做 `text()` 解码。
- **验收标准：**
  - PNG、zip、xlsx 上传后字节一致。
  - `npm run build` 通过。

### P2. 部署脚本重复写入 `BLUEPRINT_*`

- **结论：** 成立。
- **现状：** `scripts/deploy_user_systemd.sh:244` 先写 heredoc，再 `env | grep '^BLUEPRINT_'` 追加覆盖。
- **风险：**
  - 同名变量后写覆盖前写，行为依赖当前 shell 环境。
  - 部署结果不再由脚本逻辑单独决定。
- **修复方案：**
  - 改为白名单变量集合。
  - 每个变量只写一次。
  - 对关键变量缺失直接 fail-fast。
- **验收标准：**
  - 同名变量不会重复出现。
  - 生成的 `backend.env` 内容可预测。

### P2. dev 脚本与默认配置端口不一致

- **结论：** 成立。
- **现状：**
  - `scripts/dev.sh:37` 启动后端使用 8000。
  - `backend/app/core/config.py:32` 默认 API 基址仍指向 18001。
- **修复方案：**
  - 统一默认端口。
  - 同时检查 manager-agent 与 frontend 对该默认值的依赖。
- **验收标准：**
  - 无额外环境变量时，本地 dev 一键启动路径正确。

### P3. 安装脚本漏写 `library_summarizer_model`

- **结论：** 成立。
- **现状：** `scripts/install_blueprint_re.sh:162` 未写入该配置项。
- **修复方案：**
  - 在安装脚本模板中补写 `BLUEPRINT_LIBRARY_SUMMARIZER_MODEL`。
  - 若已有 `.env` 迁移逻辑，保持向后兼容。
- **验收标准：**
  - 新安装实例默认具备该配置项。

---

## 3. 误报或需要修正文案的项

以下项目不应继续按原 review 的严重度推进：

### A. 前端 systemd `WorkingDirectory` 错误

- **结论：** 不成立。
- **修正文案：** 当前本地构建产物位于 `frontend/.next/standalone/frontend/server.js`，与 `deploy/systemd/blueprint-re-frontend.service:8` 一致，不能再作为阻塞问题。

### B. `compact()` 参数顺序错误

- **结论：** 不成立。
- **修正文案：** 当前 `manager-agent/src/server.js:2044` 的调用顺序与 `pi-agent-core` 类型签名匹配，不应继续按 bug 处理。

### C. 前端 API 代理路由是死代码

- **结论：** 不成立。
- **修正文案：** 不能依据 rewrite 配置直接断言 route handler 永远不会命中。当前上传路径已经证明该代理路由在真实流量里被使用，应把问题定性为“活代码里的二进制转发缺陷”。

### D. WebSocket 运行事件在部署环境完全失效

- **结论：** 原文案夸大。
- **修正文案：**
  - `frontend/lib/api.ts:572` 的 `getRunEventsWsUrl()` 在 `/api` 基址下确实坏掉。
  - 但目前全仓未找到调用点，前端实际使用的是 HTTP 轮询 `frontend/lib/hooks.ts:200`。
  - 因此这不是当前部署阻塞项，而是“坏掉但未接线的 helper”。
- **处理建议：**
  - 若近期不打算接前端 WebSocket，删除未使用 helper，避免以后误判。
  - 若要保留，需单独设计接线、代理和关闭语义。

---

## 4. 修复批次建议

### 第一批：稳定性和部署可诊断性

目标：先消除“服务看起来正常但实际不可用”和“进程入口可被单请求打死”的问题。

包含：

- Manager Agent 启动期校验与 `/healthz` 语义修正。
- 部署脚本对 `BLUEPRINT_DEEPSEEK_API_KEY` 的 fail-fast。
- 请求入口最外层 `try/catch`。
- `handle(req, res).catch(...)`。
- SSE 在连接关闭后的防继续写保护。

验收：

- `node --check src/server.js`
- 缺 key 启动失败 smoke test
- 非法 Host 请求不杀进程 smoke test

### 第二批：run 生命周期和并发安全

目标：修 run 状态机、取消语义、项目删除并发、状态读取一致性。

包含：

- `launching` 状态引入。
- `Popen` 成功后再进入 `running`。
- `cancel_run()` 支持 launching。
- `_run_status()` 加锁读取。
- `delete_project()` 遇活动 run 返回 409。

验收：

- 后端单测覆盖 run 404、活动 run 删除 409。
- 人工并发测试：启动、取消、删除交叉操作不再出现脏状态。

### 第三批：结果校验与输出映射

目标：消除“静默成功”的结果验收漏洞。

包含：

- `missing_output` 确定性错误。
- 输出契约与资产按 `asset_id` / `role` join。
- duplicate role、missing planned id、unmatched 明确报错或警告。
- 事件来源显式枚举。

验收：

- 新增后端单测：
  - 缺失输出文件
  - 乱序输出映射
  - 缺项映射
  - 重复 role

### 第四批：接口对齐与上传正确性

目标：收敛前后端契约不一致和文件代理损坏问题。

包含：

- 新增 `GET /advanced/proposals`。
- WebSocket 终态主动关闭。
- 代理路由透传二进制 body。

验收：

- `npm run build`
- 实际上传 `png` / `zip` / `xlsx`
- proposals 刷新不再报 404

### 第五批：配置和安装收尾

目标：清理部署脚本、默认端口、安装脚本缺项，避免后续重复踩坑。

包含：

- `deploy_user_systemd.sh` 改白名单单次写入。
- dev 默认端口统一。
- 安装脚本补 `BLUEPRINT_LIBRARY_SUMMARIZER_MODEL`。

验收：

- 新生成 env 文件无重复键。
- 本地 dev 默认路径自洽。
- 安装脚本生成的新 `.env` 含完整字段。

---

## 5. 详细实施清单

### 5.1 `manager-agent`

#### 启动期配置校验

- 在模块初始化阶段或 server 启动前集中校验：
  - `apiKey`
  - provider / model
  - base URL 是否可解析
- 建议提供单点函数，例如 `validateStartupConfig()`，返回结构化结果，供：
  - 启动失败判定
  - `/healthz` 复用

#### 请求入口兜底

- `new URL(...)` 必须位于最外层 try 内。
- `createServer((req, res) => { ... })` 中不可继续使用 `void handle(...)`。
- 应统一写成 promise 链收口，并保证出错时：
  - 如果 headers 未发送，返回 400/500
  - 如果 SSE 已建立，优雅结束流

#### SSE 写流保护

- `writeSseEvent()` 先检查：
  - `res.destroyed`
  - `res.writableEnded`
  - 本地 `streamClosed` 标记
- `req.close` / `res.close` / abort 回调统一更新关闭标记。

### 5.2 `backend/app/services/worker_service.py`

#### run 状态机

- 建议新增稳定状态：
  - `queued`
  - `launching`
  - `running`
  - `cancelling`
  - terminal states
- 关键原则：
  - 没有进程句柄前，不得写 `running`
  - 没发出 `run_started` 前，不得对外表现为已启动
  - launching 期间 cancel 必须有明确落点

#### 进程注册与取消

- `Popen` 成功后立即登记到 `self._processes`。
- launching 期间需要单独记录“待取消”意图，避免句柄晚到后继续执行。
- `cancel_run()` 返回值应反映真实动作：
  - 已登记进程并发出 kill/terminate
  - launching 中已标记取消
  - 已终态无需取消

#### 输出映射

- 先构建 `planned_by_asset_id`、`planned_by_role`。
- 再对 manifest / 实际资产逐条匹配。
- 明确区分：
  - 硬错误：missing required output、duplicate binding、unknown output role
  - 软警告：optional output 缺失、描述不一致

### 5.3 `backend/app/services/executor_validation_service.py`

#### 缺失文件的确定性失败

- 不允许 `continue` 吞掉缺失文件。
- issue 建议至少带：
  - code: `missing_output`
  - severity: `error`
  - role / asset_id
  - path
  - message

#### 与 reviewer 的职责边界

- 缺文件、路径越界、格式不匹配，应由确定性验证直接失败。
- reviewer 只处理“看起来像假数据”“结果可疑”这类非完全确定性问题。

### 5.4 `backend/app/api`

#### `runs.py`

- `GET /runs/{run_id}`：
  - 不存在则 404
  - 不再让 `StopIteration` 泄漏为 500
- WebSocket：
  - run 到终态并发完事件后主动结束循环
  - 关闭连接前可发送最后一次终态快照

#### `advanced.py`

- 新增 `GET /proposals`
- 返回结构保持与前端 query 预期一致
- 若历史空文件/缺文件可出现，明确返回空列表或空对象，不要抛 500

### 5.5 `frontend`

#### 代理路由

- 转发 body 时直接复用 `request.body`
- 有 body 时为 `fetch` 设置 `duplex: "half"`
- 不对 multipart 做文本化中转

#### WebSocket helper 处理策略

二选一：

1. 若近期不用：
   - 删除未接线的 `getRunEventsWsUrl()` helper
   - 避免后续误判“部署坏了”
2. 若近期要启用：
   - 单列设计任务，处理基址、代理、upgrade、终态关闭

当前建议先走方案 1。

### 5.6 脚本与部署

#### `scripts/deploy_user_systemd.sh`

- 去掉 `env | grep '^BLUEPRINT_'` 的整体拼接。
- 改为白名单：
  - 只写当前部署需要的变量
  - 每个变量只落一次
  - 缺关键值时退出

#### `scripts/dev.sh` / `backend/app/core/config.py`

- 统一默认端口。
- 若必须保留多个端口语义，至少让 dev 脚本显式导出对应环境变量，不能靠隐式默认值碰运气。

#### `scripts/install_blueprint_re.sh`

- 补 `BLUEPRINT_LIBRARY_SUMMARIZER_MODEL`。
- 若安装脚本带交互提示，同步补 prompt 和默认值说明。

---

## 6. 验证矩阵

### 后端单测

至少新增：

- `get_run` 对不存在 run 返回 404。
- `missing_output` 会产生 error。
- `_sync_card_outputs` 的乱序、缺项、重复 role 场景。
- `delete_project` 在活动 run 时返回 409。

### Manager Agent 验证

- `cd manager-agent && node --check src/server.js`
- 缺 key 启动应失败。
- 构造非法 Host 请求，进程不退出。
- SSE 客户端中断后服务端无继续写流异常。

### 前端验证

- `cd frontend && npm run build`
- 通过代理上传：
  - `png`
  - `zip`
  - `xlsx`
- 校验上传后二进制摘要或人工打开结果正常。

### 集成验证

- 启动一个 run，在 launching 阶段取消。
- 活动 run 时尝试删除 project，确认 409。
- run 正常结束后，WebSocket 若保留，连接应自动关闭；若 helper 删除，则确认无死引用。

---

## 7. 建议排期

建议分两天到三天完成，不要一次性把所有项揉成一个大补丁。

### Patch 1

- `manager-agent` 启动校验、健康检查、请求入口兜底、SSE 收尾
- `deploy_user_systemd.sh` 的 key fail-fast 与环境变量白名单

### Patch 2

- `worker_service.py` run 状态机
- `_run_status()` 加锁
- `project_service.py` 删除并发保护

### Patch 3

- `executor_validation_service.py` 缺失输出错误
- `worker_service.py` 输出映射重做
- 事件来源枚举修正

### Patch 4

- `runs.py` 404 与 WS 关闭
- `advanced.py` proposals 端点
- 前端代理 multipart 修复
- 默认端口与安装脚本收尾

---

## 8. 非目标项

本轮不建议顺手处理以下内容，避免修复面失控：

- 未确认会导致当前部署故障的 WebSocket 前端接线改造
- 与本轮主故障无直接关系的低优先级代码清洁项
- 对 `compact()` 调用的无依据重构
- 基于原 review 误报条目的“补偿式改动”

---

## 9. 最终建议

接下来应把 `docs/review-2025-05-26.md` 视为“初始发现清单”，把本文档视为“执行基线”。  
如果只允许先做一批，优先级顺序应是：

1. `manager-agent` 启动与入口健壮性
2. run 状态机与项目删除并发
3. 输出缺失校验与输出映射
4. proposals / 上传代理 / WebSocket 收尾
5. 脚本和默认配置清理

这套顺序的原则很简单：先修会让系统“看起来正常但实际不可用”或“静默写错结果”的问题，再收敛接口与配置尾巴。
