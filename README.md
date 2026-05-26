# 莱茵生命实验室

`莱茵生命实验室` 是一个面向科研分析项目的生信管理系统。将卡片编排、执行器、评审器、结果预览、报告导出和运行时配置整合到同一套工作台里。


## 核心能力

- Manager AI 通过受控工具读写项目蓝图
- 通用执行器管理输入与输出
- 专用的 Reviewer 负责校验结果
- 结果支持表格、文本、图像和报告预览
- 项目状态、卡片、运行、资产、报告与会话都持久化在项目目录
- 技能库、MCP 库、脚本模板、Card 模板可挂载到执行配置

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

- `python3.13+`
- `python3.13-venv` 或等价的 `venv` 支持
- `node` / `npm`
- `bubblewrap` (`bwrap`)
- `systemd --user`
- `conda` 或 `mamba`
- R
- Pi CLI 

默认执行器沙箱模式是 `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`，部署脚本会做 smoke test，失败则直接中止。

如果没有 Node.js，先装 Node `22.19.0+` 再继续。后端则要求 Python `3.13+`。

# 安装方式

说真的，这种事交给智能体去干吧。人类敲配置文件容易手滑。

优先建议先安装一个终端执行器，再让 agent 自己完成私有仓库登录、下载、依赖检查和部署。


推荐执行器：

1. `pi`：默认推荐，兼容性最好，支持项目 API 注入。
2. `OpenCode`：部分兼容，支持原生 CLI 登录和项目 API 注入。
3. `Claude Code`：部分兼容，仅支持原生 CLI 登录。
4. `Codex CLI`：部分兼容，仅支持原生 CLI 登录。

建议先准备：

```bash
node -v
npm -v
```

如果没有 Node.js，先装 Node `22.19.0+` 再继续。后端则要求 Python `3.13+`。

## 给 Agent 的安装 Prompt

都什么年代了，还在手动安装，复制下面这段 prompt 让 agent 帮你完成吧。

优先直接把下面这段 prompt 发给你已经安装好的执行器：

直接把下面这段 prompt 发给你已经安装好的cli：
```text
请登录 GitHub 并拉取私有仓库 https://github.com/solarise94/rhine-life-lab ，然后阅读 docs/for_agent_install.md，根据文档引导完成项目安装、依赖检查、服务启动和安装验证。
```

##  落后的本地安装方式

如果你不想让 agent 代装，或者 agent 卡住了，可以使用安装脚本。它会：

- 自动检查并尝试安装系统依赖（当前支持 apt 系）
- 自动检查 `bubblewrap` 沙箱可用性
- 自动探测默认 Conda、Python runtime、R runtime
- 显式校验 backend Python `3.13+` 和 Node.js `22.19.0+`
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
- 显式校验 backend Python `3.13+` 与 Node.js `22.19.0+`
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

## 执行器配置

执行器默认使用 `pi`，UI 里会标注 `pi` 为最佳兼容；`OpenCode`、`Claude Code`、`Codex CLI` 可选但属于部分兼容。

支持矩阵：

| 执行器 | 原生 CLI 登录 | 项目 API 注入 | 备注 |
| --- | --- | --- | --- |
| `pi` | 不支持 | 支持 | 默认推荐，使用 DeepSeek/Pi 配置 |
| `opencode` | 支持 | 支持 | 项目 API 可走 OpenAI-compatible 或 provider-native |
| `claude_code` | 支持 | 不支持 | 使用本机 Claude Code 登录态 |
| `codex` | 支持 | 不支持 | 使用本机 Codex 登录态 |

认证模式：

- `cli_native`：不注入项目 API key，执行器使用系统里已经登录好的 CLI 账号或本机配置。bwrap 会把原生目录作为只读路径暴露给 CLI，不能在 run 内刷新登录。
- `project_api`：wrapper 才会注入项目里的 API key、base URL、model，并生成 run-scoped provider config。

命令模板优先使用 `*_COMMAND_JSON`，不要优先写 shell 字符串。JSON argv 模板里每个元素都是单独 argv，能稳定处理 WSL 和 Linux 下带空格的路径，例如 `/mnt/c/Users/xu/Documents/New project/...`。

```env
BLUEPRINT_PI_COMMAND_JSON=["bash","{repo_root}/scripts/blueprint_pi_launch.sh","{executor_prompt_path}"]
BLUEPRINT_OPENCODE_COMMAND_JSON=["opencode","run","--file","{executor_prompt_path}","--format","json","--dangerously-skip-permissions","Read {executor_prompt_path} and complete the Blueprint executor contract exactly."]
BLUEPRINT_CLAUDE_CODE_COMMAND_JSON=["claude","-p","@{executor_prompt_path}","--output-format","stream-json","--verbose"]
BLUEPRINT_CODEX_COMMAND_JSON=["codex","exec","{executor_prompt_path}"]
```

旧的 `*_COMMAND` 字符串模板仍可用，但只作为兼容路径。新安装和 WSL 部署不要依赖 shell 拼接命令。

## 本地开发

后端：

```bash
python3.13 -m venv .venv/backend
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
