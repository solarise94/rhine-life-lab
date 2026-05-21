# Blueprint RE v3

一个按 `docs/` 蓝图落地的 Git-native 生信分析项目管理 Web 应用。

核心边界：

- 用户只通过 Manager AI 表达意图，不直接编辑 Graph IR
- Manager AI 先生成 proposal / patch，再由后端校验并应用
- Graph、Cards、Runs、Report 都持久化到项目目录
- 每次 accepted proposal / run review 都会写入 Git commit
- 默认界面是对话 + 卡片 + 详情，不把 Graph IR 暴露成主编辑界面
- 部署方式按用户级 `systemd --user` 设计

## 目录

```text
backend/   FastAPI 后端
frontend/  Next.js 前端
deploy/    systemd 用户服务模板
scripts/   本地开发、schema 生成、部署脚本
docs/      产品蓝图、数据契约、实现规范
workspace/ 运行时项目目录，默认自动生成 demo project
```

## 已实现模块

- Project / Tasks / Results / Report / Advanced 五个主视图
- Project scaffold 初始化
- Graph / Cards / Assets / Claims / Runs / Report JSON 持久化
- proposal store 与 patch store
- patch allowlist 校验 + cycle / schema / readonly 校验
- patch apply + Git commit + commit 失败自动恢复
- async worker adapters + task packet + manifest + run event stream
- DeepSeek Manager AI proposal 生成（Anthropic 兼容接口）
- proposal modify / semantic rollback
- manager review accept/reject
- report projection + reorder + HTML export
- artifact pointer 基础服务
- runtime approval 风险分级与用户确认接口
- Pydantic JSON schema 生成脚本
- 用户级 `systemd` 部署脚本

## 本地开发

后端：

```bash
python3 -m venv .venv/backend
.venv/backend/bin/pip install -e backend
cp .env.example .env
.venv/backend/bin/python scripts/generate_backend_schemas.py
.venv/backend/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

后端启动前需要在仓库根目录准备 `.env`。Manager AI 默认通过 Pi agent sidecar 调用 DeepSeek，并只暴露受控蓝图工具：

```env
BLUEPRINT_DEEPSEEK_API_BASE_URL=https://api.deepseek.com/anthropic
BLUEPRINT_DEEPSEEK_API_KEY=sk-...
# Manager tool-use requests should use deepseek-v4-pro or deepseek-v4-flash.
BLUEPRINT_MANAGER_MODEL=deepseek-v4-pro
BLUEPRINT_MANAGER_BACKEND=pi
BLUEPRINT_PI_MANAGER_URL=http://127.0.0.1:18002
BLUEPRINT_BACKEND_API_BASE_URL=http://127.0.0.1:18001/api
BLUEPRINT_INTERNAL_TOOL_TOKEN=change-me
BLUEPRINT_MANAGER_TIMEOUT_SECONDS=600
BLUEPRINT_DEFAULT_WORKER_TYPE=pi
# The pi executor requires BLUEPRINT_PI_COMMAND to point at a real non-interactive pi CLI or wrapper.
# DeepSeek settings are reused by the backend executor reviewer, not as a pi fallback executor.
# Command-template placeholders:
# {python} {project_root} {run_dir} {result_dir} {task_packet_path}
# {manifest_path} {transcript_path} {executor_brief_path} {executor_prompt_path}
# {adapter_contract_path} {manager_brief_path} {worker_type}
# For opencode / pi / claude-code these values are provider CLI launch templates
# consumed by the unified wrapper, not the backend worker command itself.
# Codex currently remains a direct backend command template.
# Replace <tool-specific-args> with the syntax expected by your installed CLI.
# A local wrapper script is usually the least fragile option if provider CLI flags change.
# BLUEPRINT_OPENCODE_COMMAND=opencode <tool-specific-args> {executor_prompt_path}
# BLUEPRINT_CODEX_COMMAND=codex <tool-specific-args> {executor_prompt_path}
# BLUEPRINT_CLAUDE_CODE_COMMAND=claude-code <tool-specific-args> {executor_prompt_path}
# BLUEPRINT_PI_COMMAND=pi --no-session -p @{executor_prompt_path}
# BLUEPRINT_OPENCODE_COMMAND=bash /absolute/path/to/opencode-launch.sh {executor_prompt_path}
```

前端：

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api npm run dev
```

默认地址：

- 前端：`http://127.0.0.1:3000`
- 后端：`http://127.0.0.1:8000`

## 用户级 systemd 部署

执行：

```bash
bash scripts/deploy_user_systemd.sh
```

脚本会完成：

1. 创建后端虚拟环境并安装依赖
2. 安装前端依赖并构建 Next.js
3. 写入 `~/.config/blueprint-re/*.env`
4. 安装三个 `systemd --user` 服务
5. `enable --now` 启动前后端

生成的服务：

- `blueprint-re-backend.service`
- `blueprint-re-manager-agent.service`
- `blueprint-re-frontend.service`

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

如果希望退出登录后仍保持运行，需要单独执行系统层的 linger 配置，这一步通常需要管理员权限，不在部署脚本内处理。

## Demo 流程

启动后默认会创建 `workspace/demo-rnaseq`。

可直接验证：

1. 打开 `Tasks`
2. 输入“客户想增加免疫浸润分析模块”
3. 接受 proposal
4. 对 planned card 点击“开始执行”
5. 对 `needs_review` 的 card 点击“接受结果”
6. 在 `Results` 和 `Report` 查看新结果
7. 在 `Advanced` 查看 graph / proposals / git history

## 说明

Manager AI 现在通过 Pi agent sidecar 进行正常聊天和工具循环；sidecar 不加载 shell/write/edit 工具，只能调用后端受控工具读取蓝图、生成/修改/删除 proposal。如果模型不可用、工具校验失败且无法自修复、或密钥缺失，`/chat` 会直接失败并返回错误，不再走关键词 fallback。Worker 已切成异步子进程执行模型；默认执行器类型是 `pi`，但必须通过 `BLUEPRINT_PI_COMMAND` 指向真实非交互 Pi CLI 或 wrapper。`opencode/pi/claude_code` 通过统一 wrapper 接入外部 provider CLI launch template；它们统一收到 `task_packet.json`、`executor_prompt.md`、`adapter_contract.json` 和 `BLUEPRINT_*` 运行时环境变量。执行器完成后，后端会校验 manifest、代码证据和输出资产，并用 Manager AI 的 DeepSeek 配置运行 reviewer；最终 card/graph 更新仍由 Manager review 处理。
