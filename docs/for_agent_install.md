# Blueprint RE Agent Install Guide

本文件给接手仓库的 agent 使用。目标不是解释产品，而是让 agent 能稳定把程序装起来。

## Preferred Path

优先使用：

```bash
bash scripts/install_blueprint_re.sh
```

如果你需要手工改参数，再使用：

```bash
bash scripts/install_blueprint_re.sh --interactive
```

这个脚本负责：

- 检查并安装基础系统依赖（当前支持 apt 主机）
- 检查 `bubblewrap` 是否真的可用
- 显式校验 backend Python `3.13+` 与 Node.js `22.19.0+`
- 探测默认 Conda 目录
- 探测默认 Python runtime
- 探测默认 R runtime
- 生成 `.env`
- 调用 `scripts/deploy_user_systemd.sh`
- 安装并重启三套 `systemd --user` 服务

## Expected Host Assumptions

默认假设：

- Linux
- 用户可用 `systemd --user`
- 有 `apt-get`，或依赖已预装
- 后端安装 Python `3.13+`
- Node.js `22.19.0+`
- 允许安装：
  - `bubblewrap`
  - `python3`
  - `python3-venv`
  - `python3-pip`
  - `nodejs`
  - `npm`
- `git`
- `gh`
- `systemd`

如果主机不是 apt 系，agent 不要强行猜测包管理器；应明确告诉用户手动安装依赖后再继续。

注意：apt 自动安装只负责基础包存在，不保证自动把 Python 升到 `3.13+`、或把 Node 升到 `22.19.0+`。版本不满足时，agent 应先解决语言运行时版本，再继续部署。

## Private Repo Bootstrap

如果仓库还是私有的，agent 应优先处理：

1. 检查 `gh` 是否已安装。
2. 如果没有，先安装 `gh`。
3. 执行 `gh auth login` 并引导用户完成授权。
4. 使用 `gh repo clone <owner>/<repo>` 或等价私有仓库 URL 完成下载。
5. 进入仓库后再运行安装脚本。

## API Key Policy

首次安装不要求用户立刻输入 API key。

允许空值完成安装：

- `BLUEPRINT_DEEPSEEK_API_KEY`
- `TAVILY_API_KEY`

后续再通过两种方式补：

1. 前端工作台设置页。
2. 仓库根目录 `.env`。

## Executor Selection And Auth

默认执行器应保持 `pi`。它是当前最佳兼容路径，支持项目 API 注入，安装脚本和 UI 都按它作为默认值处理。

其他执行器可安装，但要标注为部分兼容：

| Worker | 推荐程度 | 登录/项目 API | Tool policy 原生注入 | MCP 原生注入 | Skill 原生注入 | 配置要求 |
| --- | --- | --- | --- | --- | --- | --- |
| `pi` | 最佳兼容 | `project_api` 支持；`cli_native` 不支持 | 部分支持，主要由 Blueprint prompt/bwrap 约束 | 不支持 | 支持，转换为 `pi --skill <path>` | 配好 DeepSeek/Pi 项目 API |
| `opencode` | 部分兼容 | `cli_native` 和 `project_api` 均支持 | 部分支持，写入 run-scoped capability config | 部分支持，写入 OpenCode config 并暴露 `OPENCODE_MCP_CONFIG` | 部分支持，写入 OpenCode config/env 的 skill paths | 原生登录或 OpenAI-compatible/provider-native 项目 API |
| `claude_code` | 部分兼容 | 仅 `cli_native` | 支持安全子集，映射为 `--permission-mode`、`--allowedTools`/`--disallowedTools` | 支持，映射为 `--mcp-config <path>` | 非原生，仅通过 Blueprint env/prompt 暴露 paths | 本机 Claude Code 已登录 |
| `codex` | 部分兼容 | 仅 `cli_native` | 不支持原生注入；仅 Blueprint prompt/bwrap 约束 | 不支持 | 不支持 | 本机 Codex CLI 已登录 |

`cli_native` 表示 wrapper 不注入项目 API key，不写 auth-bearing config，只让 CLI 使用系统已有登录态或本机配置。bwrap 会把原生目录作为只读路径暴露给执行器；如果 CLI 需要刷新 token，应先在宿主机手动登录，不能在 run 沙箱内刷新。

`project_api` 表示 wrapper 使用项目配置注入 API key/base URL/model。当前只应给 `pi` 和 `opencode` 使用；不要给 `claude_code` 或 `codex` 注入项目 API。

Agent 安装时不要把 `executor_profile` 当作 profile id 使用。`executor_profile` 是稳定 worker 名，例如 `pi_worker`、`opencode_worker`；真实选择的 profile id 应写入 `executor_profile_id`。

## Executor Command Templates

新安装必须优先写 `*_COMMAND_JSON`，不要优先写旧的 shell 字符串模板。

JSON argv 模板规则：

- 每个 JSON 数组元素就是一个 argv。
- 占位符只在单个 argv 元素内替换，不经过 shell 二次切分。
- 这能正确处理 WSL 和 Linux 下带空格的路径，例如 `/mnt/c/Users/xu/Documents/New project/...`。
- `{repo_root}` 指仓库根目录，适合引用仓库内脚本。
- `{executor_prompt_path}` 指当前 run 的 executor prompt。

推荐默认值：

```env
BLUEPRINT_PI_COMMAND_JSON=["bash","{repo_root}/scripts/blueprint_pi_launch.sh","{executor_prompt_path}"]
BLUEPRINT_OPENCODE_COMMAND_JSON=["opencode","run","--file","{executor_prompt_path}","--format","json","--dangerously-skip-permissions","Read {executor_prompt_path} and complete the Blueprint executor contract exactly."]
BLUEPRINT_CLAUDE_CODE_COMMAND_JSON=["claude","-p","@{executor_prompt_path}","--output-format","stream-json","--verbose"]
BLUEPRINT_CODEX_COMMAND_JSON=["codex","exec","{executor_prompt_path}"]
```

旧变量 `BLUEPRINT_PI_COMMAND`、`BLUEPRINT_OPENCODE_COMMAND`、`BLUEPRINT_CLAUDE_CODE_COMMAND`、`BLUEPRINT_CODEX_COMMAND` 仍可作为兼容 fallback，但如果路径里有空格，agent 应改成 JSON argv，而不是尝试手写复杂 shell quoting。

WSL 特别注意：

- 不要把仓库路径中的空格当成用户错误；长期方案是 JSON argv。
- 不要让 `conda run /mnt/c/Users/.../New project/...` 通过 shell 字符串拼接。
- 不要因为 bwrap 只读挂载原生目录而改成无沙箱执行。

## Runtime Detection Rules

安装脚本按以下顺序探测 Conda base：

1. `BLUEPRINT_EXECUTOR_CONDA_BASE`
2. `CONDA_PREFIX`
3. `~/miniconda3`
4. `~/miniforge3`
5. `~/anaconda3`
6. `/opt/conda`

后端 bootstrap Python 必须满足 `>=3.13`。如果宿主机默认 `python3` 更低版本，agent 应先安装或切换到 Python 3.13，再运行部署。

默认 Python runtime 候选顺序：

1. `omicverse`
2. `analysis`
3. `base`

默认 R runtime 候选顺序：

1. `bioconductor`
2. `r-bio`
3. `base`
4. 如果系统里有 `Rscript`，则回落为 `__system__`

Node.js 也必须满足 `>=22.19.0`，因为 `frontend` 和 `manager-agent` 都按这个上限基线部署。

## After Install Verification

安装完成后，agent 应至少验证：

```bash
systemctl --user status blueprint-re-manager-agent.service
systemctl --user status blueprint-re-backend.service
systemctl --user status blueprint-re-frontend.service
```

以及：

```bash
curl -fsS http://127.0.0.1:18001/healthz
curl -I http://127.0.0.1:13001
```

## Manual Fallback

如果交互安装脚本不适合当前场景：

1. 准备 `.env`
2. 运行：

```bash
bash scripts/deploy_user_systemd.sh
```

## Important Constraints

- 不要因为 `bwrap` 失败而静默降级到无沙箱执行。
- 不要把 API key 写进文档、日志或提交。
- 不要假设用户已经准备好 R/Bioconductor；如果没有，先装主程序，再让用户后续补 runtime。
- 如果 agent 只负责把工作台跑起来，空 API key 是允许的；此时 manager/online features 运行时会明确提示缺少 key。
- 默认优先无交互安装；只有在用户明确要求时才切到 `--interactive`。
