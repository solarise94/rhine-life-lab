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
