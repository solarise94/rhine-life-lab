# 莱茵生命实验室

`莱茵生命实验室` 是一个面向科研分析项目的 Git-native 生信管理系统。它把 Manager AI、Blueprint 卡片编排、执行器、评审器、结果预览、报告导出和运行时配置整合到同一套工作台里。


## 核心能力

- Manager AI 通过受控工具读写项目蓝图，而不是直接改底层 Graph IR
- 任务用 `Card` 描述，输入输出、运行时、脚本偏好和交付契约可显式配置
- 执行器与评审器分离，运行结果必须经过 reviewer 校验
- 结果支持表格、文本、图像和报告预览
- 项目状态、卡片、运行、资产、报告与会话都持久化在项目目录
- 技能库、MCP 库、脚本模板、Card 模板可挂载到执行配置
- 默认部署为本机 `systemd --user` 三服务架构

## 仓库结构

```text
backend/        FastAPI 后端
frontend/       Next.js 前端
manager-agent/  Manager AI sidecar
deploy/         systemd 用户服务模板
scripts/        部署、迁移、烟测、安装脚本
docs/           产品与实现文档
workspace/      本地运行时项目数据（不纳入仓库）
```

## 运行依赖

需要的系统级依赖：

- `python3`
- `python3-venv`
- `node` / `npm`
- `bubblewrap` (`bwrap`)
- `systemd --user`

可选但常见的运行环境：

- `conda` 或 `mamba`
- R 运行时与 Bioconductor 依赖
- Pi CLI / Opencode / Claude Code / Codex 等执行器 wrapper 对应 CLI

默认执行器沙箱模式是 `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`，部署脚本会做 smoke test，失败则直接中止。

## 推荐安装路径

优先建议先安装一个终端执行器，再让 agent 自己完成私有仓库登录、下载、依赖检查和部署。

推荐优先级：

1. `Codex CLI`
   - 官方说明：https://help.openai.com/en/articles/11381614-api-codex-cli-and-sign-in-with-chatgpt
   - 安装：`npm install -g @openai/codex`
2. `Claude Code`
   - 官方说明：https://docs.anthropic.com/en/docs/claude-code/getting-started
   - 安装：`npm install -g @anthropic-ai/claude-code`
3. `Kimi Code`
   - 官方入口：https://www.kimi.com/code
   - 官方帮助：https://www.kimi.com/help/getting-started/overview
   - 如果你已经在用 Kimi Code CLI，建议让 agent 直接打开官方页面读取当前安装方式，不要手写旧命令。
4. `pi`
   - GitHub：https://github.com/earendil-works/pi
   - 安装：`npm install -g @earendil-works/pi-coding-agent`

建议先准备：

```bash
node -v
npm -v
```

如果没有 Node.js，先装 Node 18+ 再继续。

## 给 Agent 的安装 Prompt

都什么年代了，还在手动安装，复制下面这段 prompt 让 agent 帮你完成吧。

灵感文案引用自 oh-my-openagent：
`Or read the Installation Guide, but seriously, let an agent do it. Humans fat-finger configs.`
来源：<https://github.com/code-yeongyu/oh-my-openagent/blob/dev/README.md>

优先直接把下面这段 prompt 发给你已经安装好的执行器：

```text
请登录 GitHub 并拉取私有仓库 https://github.com/solarise94/rhine-life-lab ，然后阅读 docs/for_agent_install.md，根据文档引导完成项目安装、依赖检查、服务启动和安装验证；除非确实无法自动处理，否则不要把脚本阅读和手工操作再交还给我。
```

## 本地兜底安装

如果你不想让 agent 代装，或者 agent 卡住了，再退回交互式安装脚本。它会：

- 自动检查并尝试安装系统依赖（当前支持 apt 系）
- 自动检查 `bubblewrap` 沙箱可用性
- 自动探测默认 Conda、Python runtime、R runtime
- 允许先跳过 DeepSeek / Tavily API key，后续再在 UI 或 `.env` 中补
- 收集 reviewer / runtime / compaction 相关配置
- 在仓库根目录生成本地 `.env`
- 调用部署脚本安装前后端与 manager-agent
- 写入 `~/.config/blueprint-re/*.env`
- 注册并启动 `systemd --user` 服务

执行：

```bash
git clone https://github.com/solarise94/rhine-life-lab.git laehyn-labs
cd laehyn-labs
bash scripts/install_blueprint_re.sh --interactive
```

安装完成后默认地址：

- 前端：`http://127.0.0.1:13001`
- 后端：`http://127.0.0.1:18001`

## 无交互部署

如果你已经准备好 `.env`，也可以直接：

```bash
cp .env.example .env
bash scripts/deploy_user_systemd.sh
```

这会安装并启动：

- `blueprint-re-backend.service`
- `blueprint-re-manager-agent.service`
- `blueprint-re-frontend.service`

部署脚本会额外处理：

- 缺失系统依赖时自动安装（apt）
- `bwrap` smoke test
- 默认 Conda / Python runtime / R runtime 探测
- 将默认 runtime 写入后端环境，供新项目初始化使用

常用命令：

```bash
systemctl --user status blueprint-re-manager-agent.service
systemctl --user status blueprint-re-backend.service
systemctl --user status blueprint-re-frontend.service
systemctl --user restart blueprint-re-manager-agent.service
systemctl --user restart blueprint-re-backend.service
systemctl --user restart blueprint-re-frontend.service
journalctl --user -u blueprint-re-manager-agent.service -n 100 --no-pager
journalctl --user -u blueprint-re-backend.service -n 100 --no-pager
journalctl --user -u blueprint-re-frontend.service -n 100 --no-pager
```

## 关键配置

最小必需：

```env
BLUEPRINT_DEEPSEEK_API_BASE_URL=https://api.deepseek.com/anthropic
BLUEPRINT_DEEPSEEK_API_KEY=sk-your-key
BLUEPRINT_PI_DEEPSEEK_BASE_URL=https://api.deepseek.com
BLUEPRINT_MANAGER_MODEL=deepseek-v4-pro
BLUEPRINT_MANAGER_BACKEND=pi
BLUEPRINT_PI_MANAGER_URL=http://127.0.0.1:18002
BLUEPRINT_BACKEND_API_BASE_URL=http://127.0.0.1:18001/api
BLUEPRINT_INTERNAL_TOOL_TOKEN=change-me
```

常用扩展：

- `BLUEPRINT_EXECUTOR_MODEL=deepseek-v4-flash`
- `BLUEPRINT_REVIEWER_MODEL=deepseek-v4-flash`
- `BLUEPRINT_REVIEWER_MAX_TURNS=24`
- `MANAGER_WEBSEARCH_ENABLED=true`
- `TAVILY_API_KEY=...`
- `MANAGER_CONTEXT_WINDOW_TOKENS=1000000`
- `MANAGER_COMPACTION_ENABLED=true`

完整模板见 [.env.example](/home/solarise/blueprint_re_v3/.env.example:1)。

## 本地开发

后端：

```bash
python3 -m venv .venv/backend
.venv/backend/bin/pip install -e backend
.venv/backend/bin/python scripts/generate_backend_schemas.py
.venv/backend/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api npm run dev
```

Manager agent：

```bash
cd manager-agent
npm install
npm start
```

## 数据与隐私

以下内容默认不纳入仓库：

- `.env` 和本机 env 文件
- `workspace/` 里的项目数据、聊天会话、运行产物
- `.claude/`
- `AGENTS.md`
- `.venv/`、`node_modules/`、`.next/`

仓库适合推送源码、文档、脚本和 schema；不适合推送本机运行数据、tokens、systemd env、聊天历史和实验原始资产。

## 测试

后端：

```bash
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

前端构建校验：

```bash
cd frontend
npm run build
```

Manager agent 语法检查：

```bash
node --check manager-agent/src/server.js
```

## Fork 建议

如果你要从这个项目继续分叉为新的科研管理系统，优先看这些文档：

- [docs/13_fork_architecture_and_product_logic.md](/home/solarise/blueprint_re_v3/docs/13_fork_architecture_and_product_logic.md:1)
- [docs/15_manager_runtime_libraries_and_report_plan.md](/home/solarise/blueprint_re_v3/docs/15_manager_runtime_libraries_and_report_plan.md:1)
- [docs/16_skill_mcp_registry_and_wrapper_attachment_plan.md](/home/solarise/blueprint_re_v3/docs/16_skill_mcp_registry_and_wrapper_attachment_plan.md:1)
- [docs/17_explicit_output_contract_and_submission_validation_plan.md](/home/solarise/blueprint_re_v3/docs/17_explicit_output_contract_and_submission_validation_plan.md:1)

这些文档基本覆盖了 manager、执行器 wrapper、skill/MCP registry、结果契约和报告链路。
