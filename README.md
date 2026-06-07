# 莱茵生命实验室

`莱茵生命实验室` 是一个面向科研分析项目的本地工作台，以流程蓝图的方式管理生物信息分析流程，方便对每个步骤进行微调得出准确无误的科学结果。
相比于线性分析的生物信息分析agent，本项目支持用户在分析的过程中：新开分支、修改图片排版、重复单步计算等等微调工作。


## 核心能力

- 用 Manager 对话驱动分析，而不是手工维护大量脚本状态
- 用卡片组织任务、输入、输出、依赖和运行历史
- 在同一个工作台里查看文件、结果、日志和报告
- 用 Reviewer 做结果校验，避免“跑完即结束”
- 把项目状态、会话、运行和资产持久化到本地目录

## 快速开始

### 环境要求

- `Python 3.13+`
- `Node.js 22.19.0+` 与 `npm`
- `bubblewrap` (`bwrap`)
- `systemd --user`
- `conda` 或 `mamba`
- `R`

默认沙箱模式是 `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`。如果 `bwrap` smoke test 失败，部署会直接中止，不会静默降级成裸跑。

### 方式一：让 Agent 安装

如果你本机已经有终端 Agent CLI，这条路径最省事。把下面这段 prompt 发给它：

```text
请拉取仓库 https://github.com/solarise94/rhine-life-lab ，然后阅读 docs/for_agent_install.md，严格按安装指南完成环境探查、依赖解决、项目部署、配置、烟雾测试和安装总结。
```

它会完成仓库拉取、依赖检查、`.env` 生成、前后端与 `manager-agent` 部署、`systemd --user` 启动和烟雾测试。

### 方式二：手动安装

```bash
git clone https://github.com/solarise94/rhine-life-lab.git
cd rhine-life-lab
bash scripts/install_blueprint_re.sh --interactive
```

脚本会检查系统依赖、校验 `Python 3.13+` 和 `Node 22.19.0+`、验证 `bwrap`、探测默认 Conda/Python/R runtime，并生成 `.env` 后完成部署。

如果你已经准备好了 `.env`，也可以直接执行：

```bash
bash scripts/deploy_user_systemd.sh
```

### 安装完成后

- 前端：`http://127.0.0.1:13001`（nginx gateway）
- 后端：`http://127.0.0.1:18001`
- Next.js：`http://127.0.0.1:13002`（internal）

默认会启动这四个服务：

- `blueprint-re-nginx.service`
- `blueprint-re-backend.service`
- `blueprint-re-manager-agent.service`
- `blueprint-re-frontend.service`

## 基本使用

### 1. 新建项目

打开前端后创建项目，或者直接进入已有项目。
![项目入口界面](docs/images/readme-projects-hero-final.png)

### 2. 上传资料

把原始数据、说明文档和参考文件上传到项目里，作为分析输入。
![测试项目文件视图](docs/images/readme-demo-files.png)


### 3. 告诉 Manager 目标


直接在对话框里描述目标，例如：
![测试项目任务卡片视图](docs/images/readme-demo-tasks.png)

- “帮我做这批样本的差异分析”
- “先看看这个 count matrix 的结构，再拆成几个分析卡片”
- “把上次失败的卡片继续推进”

Manager 会根据上下文拆任务、创建或更新卡片，并安排执行。

### 4. 查看卡片和结果

进入项目后，重点看卡片、依赖关系、运行状态和结果产出。卡片详情里可以继续看状态、依赖、结果和失败原因。

### 5. 评审与导出

运行完成后，Reviewer 会做结果校验。通过后可以继续整理报告、导出结果，或者让 Manager 推进下一步。

## 常用运维命令

查看服务状态：

```bash
systemctl --user status blueprint-re-nginx.service
systemctl --user status blueprint-re-manager-agent.service
systemctl --user status blueprint-re-backend.service
systemctl --user status blueprint-re-frontend.service
```

重启服务：

```bash
systemctl --user restart blueprint-re-nginx.service
systemctl --user restart blueprint-re-manager-agent.service
systemctl --user restart blueprint-re-backend.service
systemctl --user restart blueprint-re-frontend.service
```

看日志：

```bash
journalctl --user -u blueprint-re-nginx.service -n 100 --no-pager
journalctl --user -u blueprint-re-manager-agent.service -n 100 --no-pager
journalctl --user -u blueprint-re-backend.service -n 100 --no-pager
journalctl --user -u blueprint-re-frontend.service -n 100 --no-pager
```

## 关键配置

最小必需配置：

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

常用扩展配置：

- `BLUEPRINT_EXECUTOR_MODEL=deepseek-v4-flash`
- `BLUEPRINT_REVIEWER_MODEL=deepseek-v4-flash`
- `BLUEPRINT_REVIEWER_MAX_TURNS=24`
- `MANAGER_WEBSEARCH_ENABLED=true`
- `TAVILY_API_KEY=...`
- `MANAGER_CONTEXT_WINDOW_TOKENS=1000000`
- `MANAGER_COMPACTION_ENABLED=true`

完整模板见 [.env.example](.env.example)。

## 仓库结构

```text
backend/        FastAPI 后端
frontend/       Next.js 前端
manager-agent/  Manager AI sidecar
deploy/         systemd 用户服务模板
scripts/        部署、迁移、安装、烟测脚本
docs/           产品与实现文档
workspace/      本地运行时项目数据（不纳入仓库）
```

## 本地开发

后端：

```bash
python3.13 -m venv .venv/backend
.venv/backend/bin/pip install -e backend
.venv/backend/bin/python scripts/generate_backend_schemas.py
.venv/backend/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 18001
```

前端：

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:18001/api NEXT_PUBLIC_UPLOAD_API_BASE_URL=http://127.0.0.1:18001/api npm run dev
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

## 更多文档

- 安装细节：[docs/for_agent_install.md](docs/for_agent_install.md)
- 文档导航：[docs/README.md](docs/README.md)
- 如果你要继续分叉或改架构，可从这些文档开始：
- [docs/13_fork_architecture_and_product_logic.md](docs/13_fork_architecture_and_product_logic.md)
- [docs/15_manager_runtime_libraries_and_report_plan.md](docs/15_manager_runtime_libraries_and_report_plan.md)
- [docs/16_skill_mcp_registry_and_wrapper_attachment_plan.md](docs/16_skill_mcp_registry_and_wrapper_attachment_plan.md)
- [docs/17_explicit_output_contract_and_submission_validation_plan.md](docs/17_explicit_output_contract_and_submission_validation_plan.md)
- [docs/22_dependency_attention_and_provider_hardening.md](docs/22_dependency_attention_and_provider_hardening.md)
