from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys

from app.core.config import default_conda_base, default_conda_base_candidates
from app.models.runs import TaskPacket
from app.workers.base import PermissionRequest, WorkerAdapter, WorkerLaunchSpec


_BWRAP_SMOKE_OK: bool | None = None


def _ensure_bwrap_runtime() -> str:
    global _BWRAP_SMOKE_OK
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap requires the bubblewrap executable (bwrap).")
    if _BWRAP_SMOKE_OK is None:
        result = subprocess.run(
            [
                bwrap,
                "--die-with-parent",
                "--ro-bind",
                "/usr",
                "/usr",
                "--ro-bind",
                "/bin",
                "/bin",
                "--ro-bind-try",
                "/lib",
                "/lib",
                "--ro-bind-try",
                "/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--",
                "/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _BWRAP_SMOKE_OK = result.returncode == 0
    if not _BWRAP_SMOKE_OK:
        raise RuntimeError(
            "BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap requires a working bubblewrap namespace. "
            "Run scripts/deploy_user_systemd.sh and fix deploy/runtime-dependencies.yml requirements."
        )
    return bwrap


class CommandTemplateWorkerAdapter(WorkerAdapter):
    command_template: str | None = None
    requires_configuration: bool = False
    declares_network_access: bool = False
    supports_sandbox: bool = True

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: object,
    ) -> WorkerLaunchSpec:
        if not self.is_configured(settings):
            raise RuntimeError(f"Worker adapter {self.name} is not configured.")
        self._validate_executor_policy(packet)
        library_paths = self._write_library_bindings(packet=packet, run_dir=run_dir)
        contract_paths = self._write_contract_files(packet=packet, run_dir=run_dir, library_paths=library_paths)
        r_profile_path = self._write_runtime_r_profile(run_dir)
        mapping = {
            "task_packet_path": str(packet_path),
            "run_dir": str(run_dir),
            "project_root": str(project_root),
            "result_dir": str(project_root / packet.run_context.result_dir),
            "manifest_path": str(run_dir / "manifest.json"),
            "manifest_candidate_path": str(run_dir / "manifest.candidate.json"),
            "transcript_path": str(run_dir / "transcript.md"),
            "executor_brief_path": str(contract_paths["executor_brief_path"]),
            "executor_prompt_path": str(contract_paths["executor_prompt_path"]),
            "adapter_contract_path": str(contract_paths["adapter_contract_path"]),
            "manager_brief_path": str(run_dir / "manager_brief.json"),
            "worker_type": self.name,
            "python": sys.executable,
            "repo_root": str(Path(__file__).resolve().parents[2].parent),
        }
        argv_template = self.resolve_command_argv_template(settings)
        if argv_template:
            command = self._render_argv_template(argv_template, mapping)
        else:
            template = self.resolve_command_template(settings)
            if not template:
                raise RuntimeError(f"Worker adapter {self.name} is not configured.")
            # Quote values to protect paths with spaces in the legacy string template
            quoted_mapping = {k: shlex.quote(v) for k, v in mapping.items()}
            command = shlex.split(template.format(**quoted_mapping))
        backend_root = Path(__file__).resolve().parents[2]
        pythonpath = os.environ.get("PYTHONPATH", "")
        merged_pythonpath = str(backend_root) if not pythonpath else f"{backend_root}{os.pathsep}{pythonpath}"
        runtime_env = packet.executor_context.runtime_bindings.env if packet.executor_context else {}
        conda_env = packet.executor_context.runtime_bindings.conda_env if packet.executor_context else None
        r_env = packet.executor_context.runtime_bindings.r_env if packet.executor_context else None
        command, python_extra_env = self._apply_conda_runtime(command, conda_env=conda_env, settings=settings)
        r_extra_env = self._apply_r_runtime(r_env, settings=settings, base_path=python_extra_env.get("PATH"))
        extra_env = {**python_extra_env, **r_extra_env}
        adapter_extra_env = self.extra_environment(packet=packet, settings=settings)
        environment = {
            **os.environ,
            **runtime_env,
            **extra_env,
            "BLUEPRINT_PROJECT_ROOT": str(project_root),
            "BLUEPRINT_TASK_ID": packet.task_id,
            "BLUEPRINT_RUN_ID": packet.task_id,
            "BLUEPRINT_CARD_ID": packet.card_id,
            "BLUEPRINT_RUN_DIR": str(run_dir),
            "BLUEPRINT_RESULT_DIR": packet.run_context.result_dir,
            "BLUEPRINT_TASK_PACKET": str(packet_path),
            "BLUEPRINT_MANIFEST_PATH": str(run_dir / "manifest.json"),
            "BLUEPRINT_MANIFEST_CANDIDATE_PATH": str(run_dir / "manifest.candidate.json"),
            "BLUEPRINT_TRANSCRIPT_PATH": str(run_dir / "transcript.md"),
            "BLUEPRINT_EXECUTOR_BRIEF": str(contract_paths["executor_brief_path"]),
            "BLUEPRINT_EXECUTOR_PROMPT": str(contract_paths["executor_prompt_path"]),
            "BLUEPRINT_ADAPTER_CONTRACT": str(contract_paths["adapter_contract_path"]),
            "BLUEPRINT_MANAGER_BRIEF": str(run_dir / "manager_brief.json"),
            "BLUEPRINT_DEPENDENCY_ISSUE_PATH": str(run_dir / "dependency_issue.json"),
            "BLUEPRINT_DEPENDENCY_REPORT_TOOL": str(contract_paths["dependency_report_tool_path"]),
            "BLUEPRINT_ALLOWED_PATHS": json.dumps(packet.allowed_paths),
            "BLUEPRINT_READONLY_PATHS": json.dumps(packet.readonly_paths),
            "BLUEPRINT_FORBIDDEN_PATHS": json.dumps(packet.forbidden_paths),
            "BLUEPRINT_WORKER_TYPE": self.name,
            "BLUEPRINT_EXECUTOR_PROFILE": (
                packet.executor_context.executor_profile if packet.executor_context and packet.executor_context.executor_profile else ""
            ),
            "BLUEPRINT_EXECUTOR_PROFILE_ID": (
                packet.executor_context.executor_profile_id if packet.executor_context and packet.executor_context.executor_profile_id else ""
            ),
            "BLUEPRINT_EXECUTOR_SKILLS": json.dumps(packet.executor_context.skills if packet.executor_context else []),
            "BLUEPRINT_EXECUTOR_MCP_SERVERS": json.dumps(packet.executor_context.mcp_servers if packet.executor_context else []),
            "BLUEPRINT_EXECUTOR_SKILL_BINDINGS": str(library_paths["skill_bindings_path"]),
            "BLUEPRINT_EXECUTOR_MCP_BINDINGS": str(library_paths["mcp_bindings_path"]),
            "BLUEPRINT_EXECUTOR_MCP_CONFIG": str(library_paths["mcp_config_path"]),
            "BLUEPRINT_PI_SKILL_PATHS": json.dumps(library_paths["skill_paths"]),
            "BLUEPRINT_RUNTIME_WORKING_DIR": packet.executor_context.runtime_bindings.working_dir if packet.executor_context else ".",
            "BLUEPRINT_MANAGER_REPORT_STDOUT_PREFIX": (
                packet.manager_reporting_contract.stdout_prefix if packet.manager_reporting_contract else "BP_EVENT "
            ),
            "R_PROFILE_USER": str(r_profile_path),
            "R_DEFAULT_DEVICE": "pdf",
            "PYTHONPATH": merged_pythonpath,
        }
        environment.update(adapter_extra_env)
        sandboxed = self._should_use_bwrap(settings)
        if sandboxed:
            command = self._wrap_with_bwrap(
                command,
                packet=packet,
                project_root=project_root,
                run_dir=run_dir,
                environment=environment,
                adapter_extra_env_keys=set(adapter_extra_env),
                settings=settings,
            )
        return WorkerLaunchSpec(
            command=command,
            cwd=project_root,
            environment=environment,
            permission_requests=self._build_permission_requests(packet),
            sandboxed=sandboxed,
        )

    def extra_environment(self, *, packet: TaskPacket, settings: object) -> dict[str, str]:
        return {}

    @staticmethod
    def _should_use_bwrap(settings: object) -> bool:
        return getattr(settings, "executor_sandbox_mode", "none") == "bwrap"

    def uses_sandbox(self, settings: object) -> bool:
        return self.supports_sandbox and self._should_use_bwrap(settings)

    @staticmethod
    def _apply_conda_runtime(command: list[str], *, conda_env: str | None, settings: object) -> tuple[list[str], dict[str, str]]:
        if not conda_env:
            return command, {}
        conda_base, env_path = CommandTemplateWorkerAdapter._resolve_conda_runtime(conda_env, settings)
        conda_bin = conda_base / "bin" / "conda"
        if conda_bin.exists() and env_path.exists():
            return [str(conda_bin), "run", "-p", str(env_path), "--no-capture-output", *command], {}
        return command, {
            "PATH": f"{env_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "CONDA_PREFIX": str(env_path),
            "CONDA_DEFAULT_ENV": conda_env,
        }

    @staticmethod
    def _resolve_conda_runtime(conda_env: str, settings: object) -> tuple[Path, Path]:
        configured_base = Path(getattr(settings, "executor_conda_base", default_conda_base()))
        candidates = default_conda_base_candidates(configured_base)
        if conda_env.startswith("/"):
            env_path = Path(conda_env)
            return env_path.parent.parent if env_path.parent.name == "envs" else configured_base, env_path
        for base in candidates:
            env_path = base / "envs" / conda_env
            if env_path.exists():
                return base, env_path
        return configured_base, configured_base / "envs" / conda_env

    @staticmethod
    def _apply_r_runtime(r_env: str | None, *, settings: object, base_path: str | None = None) -> dict[str, str]:
        rscript_path = CommandTemplateWorkerAdapter._resolve_rscript_runtime(r_env, settings)
        if rscript_path is None:
            return {}
        env: dict[str, str] = {
            "BLUEPRINT_RSCRIPT": str(rscript_path),
        }
        if r_env:
            env["BLUEPRINT_R_RUNTIME"] = r_env
            env["PATH"] = f"{rscript_path.parent}{os.pathsep}{base_path or os.environ.get('PATH', '')}"
        r_user_libs = CommandTemplateWorkerAdapter._r_user_library_paths(rscript_path)
        if r_user_libs:
            env["R_LIBS_USER"] = os.pathsep.join(str(path) for path in r_user_libs)
        return env

    @staticmethod
    def _resolve_rscript_runtime(r_env: str | None, settings: object) -> Path | None:
        if not r_env:
            found = shutil.which("Rscript")
            return Path(found) if found else None
        if r_env.startswith("/"):
            runtime_path = Path(r_env)
            if runtime_path.name == "Rscript" and runtime_path.exists():
                return runtime_path
            rscript_path = runtime_path / "bin" / "Rscript"
            return rscript_path if rscript_path.exists() else None
        configured_base = Path(getattr(settings, "executor_conda_base", default_conda_base()))
        candidates = default_conda_base_candidates(configured_base)
        for base in candidates:
            rscript_path = base / "envs" / r_env / "bin" / "Rscript"
            if rscript_path.exists():
                return rscript_path
            if r_env == "base":
                base_rscript = base / "bin" / "Rscript"
                if base_rscript.exists():
                    return base_rscript
        return None

    @staticmethod
    def _wrap_with_bwrap(
        command: list[str],
        *,
        packet: TaskPacket,
        project_root: Path,
        run_dir: Path,
        environment: dict[str, str],
        adapter_extra_env_keys: set[str],
        settings: object,
    ) -> list[str]:
        bwrap = _ensure_bwrap_runtime()
        result_dir = project_root / packet.run_context.result_dir
        script_run_dir = project_root / "scripts" / "generated" / packet.task_id
        tmp_dir = run_dir / "tmp"
        cache_dir = run_dir / "cache"
        home_dir = run_dir / "home"
        state_dir = run_dir / "state"
        pi_agent_dir = state_dir / "pi-agent"
        pi_session_dir = state_dir / "pi-sessions"
        xdg_config_dir = run_dir / "config"
        xdg_data_dir = run_dir / "data"
        xdg_state_dir = state_dir / "xdg"
        for path in (
            result_dir,
            script_run_dir,
            tmp_dir,
            cache_dir,
            home_dir,
            pi_agent_dir,
            pi_session_dir,
            xdg_config_dir,
            xdg_data_dir,
            xdg_state_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        host_root_readonly = bool(getattr(settings, "executor_host_root_readonly", True))
        writable_binds = [run_dir, result_dir, script_run_dir]
        readonly_binds = [Path("/")] if host_root_readonly else [project_root]
        masked_paths = CommandTemplateWorkerAdapter._project_mask_paths(packet, project_root, run_dir)
        backend_root = Path(__file__).resolve().parents[2]
        repo_root = backend_root.parent
        current_python = Path(sys.executable)
        python_runtime_paths = CommandTemplateWorkerAdapter._python_runtime_ro_binds(current_python)
        launch_template_paths = CommandTemplateWorkerAdapter._launch_template_ro_binds(adapter_extra_env_keys, environment)
        r_user_libs = CommandTemplateWorkerAdapter._r_user_library_ro_binds(settings)
        bind_args: list[str] = [
            bwrap,
            "--die-with-parent",
            "--clearenv",
        ]
        if host_root_readonly:
            bind_args.extend(["--ro-bind", "/", "/"])
        else:
            bind_args.extend(["--ro-bind", str(project_root), str(project_root)])
            system_ro_binds = ["/bin", "/usr", "/lib", "/lib64", "/etc", "/opt", "/run/systemd/resolve"]
            extra_ro_binds = CommandTemplateWorkerAdapter._extra_ro_binds(settings)
            repo_runtime_binds = [str(backend_root), str(repo_root / "scripts")]
            for host_path in [
                *system_ro_binds,
                *extra_ro_binds,
                *repo_runtime_binds,
                *python_runtime_paths,
                *launch_template_paths,
                *r_user_libs,
            ]:
                if Path(host_path).exists():
                    bind_args.extend(["--ro-bind", host_path, host_path])
                    readonly_binds.append(Path(host_path))
        bind_args.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
            ]
        )
        for path in masked_paths:
            bind_args.extend(["--tmpfs", str(path)])
        bind_args.extend(
            [
                "--bind",
                str(run_dir),
                str(run_dir),
                "--bind",
                str(result_dir),
                str(result_dir),
                "--bind",
                str(script_run_dir),
                str(script_run_dir),
                "--chdir",
                str(project_root),
            ]
        )
        conda_base = Path(getattr(settings, "executor_conda_base", default_conda_base()))
        if not host_root_readonly and conda_base.exists() and str(conda_base) not in {"/bin", "/usr", "/lib", "/lib64", "/etc", "/opt"}:
            bind_args.extend(["--ro-bind", str(conda_base), str(conda_base)])
            readonly_binds.append(conda_base)
        env_keys = {
            "BLUEPRINT_PROJECT_ROOT",
            "BLUEPRINT_RUN_DIR",
            "BLUEPRINT_RESULT_DIR",
            "BLUEPRINT_TASK_PACKET",
            "BLUEPRINT_MANIFEST_PATH",
            "BLUEPRINT_MANIFEST_CANDIDATE_PATH",
            "BLUEPRINT_TRANSCRIPT_PATH",
            "BLUEPRINT_EXECUTOR_BRIEF",
            "BLUEPRINT_EXECUTOR_PROMPT",
            "BLUEPRINT_ADAPTER_CONTRACT",
            "BLUEPRINT_MANAGER_BRIEF",
            "BLUEPRINT_ALLOWED_PATHS",
            "BLUEPRINT_READONLY_PATHS",
            "BLUEPRINT_FORBIDDEN_PATHS",
            "BLUEPRINT_WORKER_TYPE",
            "BLUEPRINT_EXECUTOR_PROFILE",
            "BLUEPRINT_EXECUTOR_PROFILE_ID",
            "BLUEPRINT_AUTH_MODE",
            "BLUEPRINT_API_PROTOCOL",
            "BLUEPRINT_EXECUTOR_SKILLS",
            "BLUEPRINT_RUNTIME_WORKING_DIR",
            "BLUEPRINT_MANAGER_REPORT_STDOUT_PREFIX",
            "BLUEPRINT_RSCRIPT",
            "BLUEPRINT_R_RUNTIME",
            "R_PROFILE_USER",
            "R_DEFAULT_DEVICE",
            "R_LIBS_USER",
            "PYTHONPATH",
            "PATH",
            "CONDA_PREFIX",
            "CONDA_DEFAULT_ENV",
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "R_USER_CACHE_DIR",
            "MPLCONFIGDIR",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "CLAUDE_CONFIG_DIR",
            "OPENCODE_CONFIG_DIR",
            "CODEX_CONFIG_DIR",
            "http_proxy",
            "https_proxy",
            "no_proxy",
            "PI_CODING_AGENT_DIR",
            "PI_CODING_AGENT_SESSION_DIR",
            "PI_SKIP_VERSION_CHECK",
        }
        if packet.executor_context:
            env_keys.update(packet.executor_context.runtime_bindings.env)
        env_keys.update(adapter_extra_env_keys)
        sandbox_plan_path = run_dir / "sandbox_plan.json"
        environment.update(
            {
                "BLUEPRINT_SANDBOX_PLAN": str(sandbox_plan_path),
                "HOME": str(home_dir),
                "USER": environment.get("USER") or environment.get("LOGNAME") or "blueprint",
                "LOGNAME": environment.get("LOGNAME") or environment.get("USER") or "blueprint",
                "TMPDIR": str(tmp_dir),
                "XDG_CACHE_HOME": str(cache_dir),
                "XDG_CONFIG_HOME": str(xdg_config_dir),
                "XDG_DATA_HOME": str(xdg_data_dir),
                "XDG_STATE_HOME": str(xdg_state_dir),
                "R_USER_CACHE_DIR": str(cache_dir / "R"),
                "MPLCONFIGDIR": str(cache_dir / "matplotlib"),
                "PI_CODING_AGENT_DIR": str(pi_agent_dir),
                "PI_CODING_AGENT_SESSION_DIR": str(pi_session_dir),
                "PI_SKIP_VERSION_CHECK": environment.get("PI_SKIP_VERSION_CHECK", "1"),
            }
        )
        if r_user_libs and "R_LIBS_USER" not in environment:
            environment["R_LIBS_USER"] = os.pathsep.join(str(path) for path in r_user_libs)

        # Capture real host paths before bwrap rewrites them
        # This allows renderers to find host CLI auth/config directories
        host_home = os.environ.get("HOME", "")
        host_xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
        host_claude_config = os.environ.get("CLAUDE_CONFIG_DIR", "")
        host_opencode_config = os.environ.get("OPENCODE_CONFIG_DIR", "")
        host_codex_config = os.environ.get("CODEX_CONFIG_DIR", "")
        host_pi_agent_dir = os.environ.get("PI_CODING_AGENT_DIR", "")

        if host_home:
            environment["BLUEPRINT_HOST_HOME"] = host_home
        if host_xdg_config:
            environment["BLUEPRINT_HOST_XDG_CONFIG_HOME"] = host_xdg_config
        if host_claude_config:
            environment["BLUEPRINT_HOST_CLAUDE_CONFIG_DIR"] = host_claude_config
        if host_opencode_config:
            environment["BLUEPRINT_HOST_OPENCODE_CONFIG_DIR"] = host_opencode_config
        if host_codex_config:
            environment["BLUEPRINT_HOST_CODEX_CONFIG_DIR"] = host_codex_config
        if host_pi_agent_dir:
            environment["BLUEPRINT_HOST_PI_CODING_AGENT_DIR"] = host_pi_agent_dir

        env_keys.add("BLUEPRINT_SANDBOX_PLAN")
        env_keys.add("BLUEPRINT_HOST_HOME")
        env_keys.add("BLUEPRINT_HOST_XDG_CONFIG_HOME")
        env_keys.add("BLUEPRINT_HOST_CLAUDE_CONFIG_DIR")
        env_keys.add("BLUEPRINT_HOST_OPENCODE_CONFIG_DIR")
        env_keys.add("BLUEPRINT_HOST_CODEX_CONFIG_DIR")
        env_keys.add("BLUEPRINT_HOST_PI_CODING_AGENT_DIR")
        if "LANG" not in environment and os.environ.get("LANG"):
            environment["LANG"] = os.environ["LANG"]
        sandbox_plan = {
            "mode": "bwrap",
            "network": "host",
            "network_isolation": False,
            "host_root_readonly": host_root_readonly,
            "project_root": str(project_root),
            "readonly_binds": CommandTemplateWorkerAdapter._dedupe_paths(readonly_binds),
            "writable_binds": CommandTemplateWorkerAdapter._dedupe_paths(writable_binds),
            "masked_paths": CommandTemplateWorkerAdapter._dedupe_paths(masked_paths),
            "tmp_dir": str(tmp_dir),
            "cache_dir": str(cache_dir),
            "home_dir": str(home_dir),
            "pi_agent_dir": str(pi_agent_dir),
            "pi_session_dir": str(pi_session_dir),
            "conda_base": str(conda_base) if conda_base.exists() else None,
            "conda_env": packet.executor_context.runtime_bindings.conda_env if packet.executor_context else None,
            "r_env": packet.executor_context.runtime_bindings.r_env if packet.executor_context else None,
            "rscript": environment.get("BLUEPRINT_RSCRIPT"),
            "backend_root": str(backend_root),
            "python_executable": str(current_python),
            "clearenv": True,
            "env_keys": sorted(key for key in env_keys if key in environment),
            "runtime_env_keys": sorted(packet.executor_context.runtime_bindings.env) if packet.executor_context else [],
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        sandbox_plan_path.write_text(json.dumps(sandbox_plan, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        for key in sorted(env_keys):
            if key in environment:
                bind_args.extend(["--setenv", key, environment[key]])
        bind_args.extend(["--", *command])
        return bind_args

    @staticmethod
    def _extra_ro_binds(settings: object) -> list[str]:
        raw = getattr(settings, "executor_extra_ro_binds", "") or ""
        paths: list[str] = []
        for item in str(raw).split(","):
            value = os.path.expanduser(os.path.expandvars(item.strip()))
            if value:
                paths.append(value)
        return paths

    @staticmethod
    def _project_mask_paths(packet: TaskPacket, project_root: Path, run_dir: Path) -> list[Path]:
        paths: list[Path] = []
        for item in packet.forbidden_paths:
            value = item.strip().lstrip("/")
            if not value:
                continue
            path = project_root / value
            if run_dir == path or run_dir in path.parents:
                continue
            paths.append(path)
        seen: set[str] = set()
        result: list[Path] = []
        for path in paths:
            if not path.exists():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for path in paths:
            value = str(path)
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _python_runtime_ro_binds(python_path: Path) -> list[str]:
        paths = [python_path]
        try:
            resolved = python_path.resolve()
        except OSError:
            resolved = python_path
        paths.append(resolved)
        for parent in python_path.parents:
            if parent.name == ".venv":
                paths.append(parent)
                break
        return CommandTemplateWorkerAdapter._dedupe_paths([path for path in paths if path.exists()])

    @staticmethod
    def _launch_template_ro_binds(env_keys: set[str], environment: dict[str, str]) -> list[str]:
        paths: list[Path] = []
        for key in env_keys:
            value = environment.get(key, "")
            for token in shlex.split(value):
                if not token.startswith("/"):
                    continue
                path = Path(os.path.expanduser(os.path.expandvars(token)))
                if not path.exists():
                    continue
                paths.append(path.parent if path.is_file() else path)
        return CommandTemplateWorkerAdapter._dedupe_paths(paths)

    @staticmethod
    def _r_user_library_ro_binds(settings: object) -> list[Path]:
        return CommandTemplateWorkerAdapter._r_user_library_paths(
            CommandTemplateWorkerAdapter._resolve_rscript_runtime(None, settings)
        )

    @staticmethod
    def _r_user_library_paths(rscript_path: Path | None = None) -> list[Path]:
        paths: list[Path] = []
        raw = os.environ.get("R_LIBS_USER", "")
        for item in raw.split(os.pathsep):
            value = os.path.expanduser(os.path.expandvars(item.strip()))
            if value and Path(value).exists():
                paths.append(Path(value))
        if rscript_path and rscript_path.exists():
            try:
                result = subprocess.run(
                    [
                        str(rscript_path),
                        "--no-init-file",
                        "--no-site-file",
                        "-e",
                        "cat(Sys.getenv('R_LIBS_USER'))",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if result and result.returncode == 0:
                for item in result.stdout.split(os.pathsep):
                    value = os.path.expanduser(os.path.expandvars(item.strip()))
                    if value and Path(value).exists():
                        paths.append(Path(value))
        r_home = Path.home() / "R"
        if r_home.exists():
            paths.append(r_home)
        seen: set[str] = set()
        result_paths: list[Path] = []
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result_paths.append(path)
        return result_paths

    def resolve_command_template(self, settings: object) -> str | None:
        if self.command_template:
            return self.command_template
        return None

    def resolve_command_argv_template(self, settings: object) -> list[str] | None:
        """Return a structured argv template (list of strings with placeholders).

        When provided, this is preferred over resolve_command_template() to avoid
        shlex.split issues with paths containing spaces.
        Subclasses may override to provide a structured argv list.
        """
        return None

    @staticmethod
    def _render_argv_template(template: list[str], mapping: dict[str, str]) -> list[str]:
        """Render each argv element by substituting placeholders."""
        try:
            return [item.format(**mapping) for item in template]
        except KeyError as exc:
            missing = exc.args[0]
            raise RuntimeError(f"Command argv template referenced unknown placeholder {{{missing}}}.") from exc

    def is_configured(self, settings: object) -> bool:
        return bool(self.resolve_command_template(settings) or self.resolve_command_argv_template(settings))

    def capability_metadata(self, settings: object) -> dict[str, object]:
        metadata = super().capability_metadata(settings)
        metadata["execution_mode"] = "command_template"
        return metadata

    def _build_permission_requests(self, packet: TaskPacket) -> list[PermissionRequest]:
        requests = [
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_results",
                target=f"results/{packet.card_id}/{packet.task_id}/",
                action="write",
                reason="Worker needs to write outputs under the declared result directory.",
            ),
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_run_dir",
                target=f"runs/{packet.task_id}/",
                action="write",
                reason="Worker needs to write transcript, logs, and manifest for the current run.",
            ),
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_generated_scripts",
                target="scripts/generated/",
                action="write",
                reason="Worker may generate reusable helper scripts under scripts/generated/.",
            ),
        ]
        network_policy = packet.executor_context.tool_policy.network if packet.executor_context else "prompt"
        if self.declares_network_access and network_policy == "prompt":
            requests.append(
                PermissionRequest(
                    request_id=f"perm_{packet.task_id}_network",
                    target="network",
                    action="network",
                    reason="Worker requested conditional network access under the executor tool policy.",
                )
            )
        return requests

    @staticmethod
    def _write_runtime_r_profile(run_dir: Path) -> Path:
        path = run_dir / ".Rprofile"
        path.write_text(
            "options(device = function(...) grDevices::pdf(file = file.path(Sys.getenv('BLUEPRINT_RUN_DIR', '.'), 'Rplots.pdf'), ...))\n",
            encoding="utf-8",
        )
        return path

    def _validate_executor_policy(self, packet: TaskPacket) -> None:
        network_policy = packet.executor_context.tool_policy.network if packet.executor_context else "prompt"
        if self.declares_network_access and network_policy == "deny":
            raise RuntimeError(
                f"Worker adapter {self.name} requires model/network access, but executor_context.tool_policy.network=deny."
            )

    def _write_contract_files(
        self,
        *,
        packet: TaskPacket,
        run_dir: Path,
        library_paths: dict[str, object],
    ) -> dict[str, Path]:
        executor_brief_path = run_dir / "executor_brief.md"
        executor_prompt_path = run_dir / "executor_prompt.md"
        adapter_contract_path = run_dir / "adapter_contract.json"
        dependency_report_tool_path = run_dir / "report_dependency_issue.py"
        self._write_dependency_report_tool(dependency_report_tool_path)
        executor_brief_path.write_text(self._render_executor_brief(packet), encoding="utf-8")
        executor_prompt_path.write_text(self._render_executor_prompt(packet), encoding="utf-8")
        adapter_contract_path.write_text(
            json.dumps(
                {
                    "worker_type": self.name,
                    "task_packet_path": "task_packet.json",
                    "executor_prompt_path": "executor_prompt.md",
                    "manifest_path": "manifest.json",
                    "manifest_candidate_path": "manifest.candidate.json",
                    "manager_brief_path": "manager_brief.json",
                    "dependency_issue_path": "dependency_issue.json",
                    "dependency_report_tool_path": "report_dependency_issue.py",
                    "executor_validation_path": "executor_validation.json",
                    "skill_bindings_path": str(Path(str(library_paths["skill_bindings_path"])).relative_to(run_dir)),
                    "mcp_bindings_path": str(Path(str(library_paths["mcp_bindings_path"])).relative_to(run_dir)),
                    "mcp_config_path": str(Path(str(library_paths["mcp_config_path"])).relative_to(run_dir)),
                    "stdout_prefix": packet.manager_reporting_contract.stdout_prefix if packet.manager_reporting_contract else "BP_EVENT ",
                    "allowed_paths": packet.allowed_paths,
                    "readonly_paths": packet.readonly_paths,
                    "forbidden_paths": packet.forbidden_paths,
                    "declares_network_access": self.declares_network_access,
                    "template_fields": [
                        "python",
                        "project_root",
                        "run_dir",
                        "result_dir",
                        "task_packet_path",
                        "manifest_path",
                        "manifest_candidate_path",
                        "transcript_path",
                        "executor_brief_path",
                        "executor_prompt_path",
                        "adapter_contract_path",
                        "manager_brief_path",
                        "worker_type",
                    ],
                    "expected_outputs": [item.model_dump() for item in packet.expected_outputs],
                    "required_manifest_fields": [
                        "run_id",
                        "status",
                        "summary",
                        "created_assets",
                        "code_artifacts",
                        "commands_executed",
                        "validation_evidence",
                    ],
                    "manifest_status_values": ["success", "failed", "partial"],
                    "executor_tools": [
                        {
                            "name": "report_dependency_issue",
                            "path": "report_dependency_issue.py",
                            "purpose": "Report missing runtime dependencies to the card/manager instead of installing packages ad hoc.",
                            "example": (
                                "python runs/{run_id}/report_dependency_issue.py "
                                "--ecosystem R --package clusterProfiler --package enrichplot "
                                "--manager Bioconductor --message 'Required enrichment packages are unavailable.'"
                            ).format(run_id=packet.task_id),
                            "arguments": {
                                "--ecosystem": "python | R | conda | system | other",
                                "--package": "repeatable missing package/tool name",
                                "--manager": "optional package manager such as pip, conda, CRAN, Bioconductor",
                                "--runtime": "optional selected runtime/environment name",
                                "--message": "optional human-readable reason",
                                "--non-blocking": "mark as warning instead of blocking the run",
                            },
                        }
                    ],
                    "manifest_schema": {
                        "run_id": "string",
                        "status": "success | failed | partial",
                        "summary": "string",
                        "created_assets": "array of {role,path,label?,asset_id?,description?,artifact_class?,format?}",
                        "code_artifacts": "array of {path,language?,purpose?,sha256?}",
                        "validation_evidence": {
                            "input_conclusion": "short factual note about the declared inputs and whether they were used",
                        },
                        "commands_executed": "array of strings",
                        "metrics": "object",
                        "key_findings": "array of strings",
                        "recommended_graph_updates": "array of objects",
                        "warnings": "array of strings",
                    },
                    "code_artifact_scope": f"scripts/generated/{packet.task_id}/",
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "executor_brief_path": executor_brief_path,
            "executor_prompt_path": executor_prompt_path,
            "adapter_contract_path": adapter_contract_path,
            "dependency_report_tool_path": dependency_report_tool_path,
        }

    def _write_library_bindings(self, *, packet: TaskPacket, run_dir: Path) -> dict[str, object]:
        library_root = run_dir / "library"
        skills_root = library_root / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        skill_bindings = list(packet.executor_context.template_metadata.get("library_skill_bindings") or []) if packet.executor_context else []
        mcp_bindings = list(packet.executor_context.template_metadata.get("library_mcp_bindings") or []) if packet.executor_context else []

        copied_skill_paths: list[str] = []
        for binding in skill_bindings:
            source_path_value = binding.get("source_path")
            skill_id = str(binding.get("id") or "skill")
            if not source_path_value:
                continue
            source_path = Path(str(source_path_value))
            if not source_path.exists():
                continue
            source_dir = source_path.parent if source_path.is_file() else source_path
            destination_dir = skills_root / skill_id
            if destination_dir.exists():
                shutil.rmtree(destination_dir)
            shutil.copytree(source_dir, destination_dir)
            copied_skill_paths.append(str(destination_dir))
            binding["run_path"] = str(destination_dir)

        mcp_config_payload = {"mcpServers": {}}
        for binding in mcp_bindings:
            config = binding.get("config")
            if isinstance(config, dict):
                servers = config.get("mcpServers")
                if isinstance(servers, dict):
                    mcp_config_payload["mcpServers"].update(servers)

        skill_bindings_path = library_root / "skill_bindings.json"
        mcp_bindings_path = library_root / "mcp_bindings.json"
        mcp_config_path = library_root / "mcp.json"
        skill_bindings_path.write_text(json.dumps(skill_bindings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mcp_bindings_path.write_text(json.dumps(mcp_bindings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mcp_config_path.write_text(json.dumps(mcp_config_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "skill_bindings_path": skill_bindings_path,
            "mcp_bindings_path": mcp_bindings_path,
            "mcp_config_path": mcp_config_path,
            "skill_paths": copied_skill_paths,
        }

    @staticmethod
    def _write_dependency_report_tool(path: Path) -> None:
        path.write_text(
            '''from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = ArgumentParser(description="Report missing Blueprint executor runtime dependencies.")
    parser.add_argument("--ecosystem", default="other", choices=["python", "R", "conda", "system", "other"])
    parser.add_argument("--package", action="append", dest="packages", required=True)
    parser.add_argument("--manager", default="")
    parser.add_argument("--runtime", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--non-blocking", action="store_true")
    args = parser.parse_args()

    packages = [item for item in (args.packages or []) if item]
    runtime = args.runtime or (
        os.environ.get("BLUEPRINT_R_RUNTIME", "") if args.ecosystem == "R" else os.environ.get("BLUEPRINT_PYTHON_RUNTIME", "")
    )
    blocking = not args.non_blocking
    package_text = ", ".join(packages)
    message = args.message or f"Missing required {args.ecosystem} runtime dependencies: {package_text}."
    event = {
        "type": "issue_report",
        "stage": "runtime_dependency_check",
        "severity": "high" if blocking else "medium",
        "needs_manager": blocking,
        "message": message,
        "suggested_actions": [
            "Install the missing dependencies in the selected runtime environment.",
            "Select a compatible Python/R runtime for this card and rerun it.",
        ],
        "metadata": {
            "issue_kind": "runtime_dependency_missing",
            "dependency_status": "missing",
            "ecosystem": args.ecosystem,
            "missing_packages": packages,
            "package_manager": args.manager,
            "runtime": runtime,
            "blocking": blocking,
        },
    }

    issue_path = Path(os.environ.get("BLUEPRINT_DEPENDENCY_ISSUE_PATH", "dependency_issue.json"))
    issue_payload = {
        "schema_version": "dependency_issue.v1",
        "run_id": os.environ.get("BLUEPRINT_RUN_ID") or os.environ.get("BLUEPRINT_TASK_ID"),
        "card_id": os.environ.get("BLUEPRINT_CARD_ID"),
        "created_at": _utc_now(),
        "blocking": blocking,
        "issues": [event],
    }
    _atomic_write_json(issue_path, issue_payload)

    brief_path = Path(os.environ.get("BLUEPRINT_MANAGER_BRIEF", "manager_brief.json"))
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8")) if brief_path.exists() else {}
    except json.JSONDecodeError:
        brief = {}
    brief["run_id"] = os.environ.get("BLUEPRINT_RUN_ID") or os.environ.get("BLUEPRINT_TASK_ID") or brief.get("run_id")
    issues = list(brief.get("issues") or [])
    issues.append(event)
    brief["issues"] = issues
    dependency_issues = list(brief.get("dependency_issues") or [])
    dependency_issues.append(event)
    brief["dependency_issues"] = dependency_issues
    _atomic_write_json(brief_path, brief)

    prefix = os.environ.get("BLUEPRINT_MANAGER_REPORT_STDOUT_PREFIX", "BP_EVENT ")
    print(prefix + json.dumps(event, ensure_ascii=False), flush=True)
    return 3 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
            encoding="utf-8",
        )

    def _render_executor_brief(self, packet: TaskPacket) -> str:
        lines = [
            f"# Executor Brief for {packet.task_id}",
            "",
            "## Task",
            f"- Project: {packet.project_id}",
            f"- Card: {packet.card_id} ({packet.card_title})",
            f"- Goal: {packet.goal}",
            "",
            "## Inputs",
        ]
        if packet.input_assets:
            lines.extend(f"- {item.asset_id}: {item.path} [{item.type}]" for item in packet.input_assets)
        else:
            lines.append("- No linked input assets.")
        lines.extend(
            [
                "",
                "## Expected Outputs",
            ]
        )
        lines.extend(
            f"- {item.role}: {item.path_hint} [{item.artifact_class}; accepted={', '.join(item.accepted_formats) if item.accepted_formats else 'any'}; preferred={item.preferred_format or 'auto'}]"
            for item in packet.expected_outputs
        )
        lines.extend(
            [
                "",
                "## Runtime Policy",
                f"- Allowed paths: {', '.join(packet.allowed_paths)}",
                f"- Readonly paths: {', '.join(packet.readonly_paths) if packet.readonly_paths else 'none'}",
                f"- Forbidden paths: {', '.join(packet.forbidden_paths)}",
            ]
        )
        if packet.executor_context:
            python_runtime = packet.executor_context.runtime_bindings.conda_env or "system"
            r_runtime = packet.executor_context.runtime_bindings.r_env or "system"
            lines.extend(
                [
                    "",
                    "## Executor Context",
                    f"- Profile: {packet.executor_context.executor_profile or 'none'}",
                    f"- Skills: {', '.join(packet.executor_context.skills) if packet.executor_context.skills else 'none'}",
                    f"- MCP servers: {', '.join(packet.executor_context.mcp_servers) if packet.executor_context.mcp_servers else 'none'}",
                    f"- Python runtime: {python_runtime}",
                    f"- R runtime: {r_runtime}",
                    f"- Skill bindings file: runs/{packet.task_id}/library/skill_bindings.json",
                    f"- MCP bindings file: runs/{packet.task_id}/library/mcp_bindings.json",
                    f"- MCP config file: runs/{packet.task_id}/library/mcp.json",
                ]
            )
            lines.extend(f"- Instruction: {item}" for item in packet.executor_context.instruction_blocks)
            lines.extend(
                f"- Reference: {item.path} ({item.type})" for item in packet.executor_context.references
            )
        lines.extend(
            [
                "",
                "## Reporting Contract",
                "- Report progress/issues/final summary through BP_EVENT stdout or manager_brief.json.",
                "- If required runtime packages or tools are missing, report them with report_dependency_issue.py instead of installing packages.",
                "- Preserve executed code under scripts/generated/{run_id}/ and declare it in manifest.code_artifacts.",
                "- Agent CLI wrappers must write manifest.candidate.json first; the wrapper promotes it to manifest.json only after schema validation.",
                "- Backend validation will reject missing outputs, missing code evidence, path violations, and placeholder data.",
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_executor_prompt(self, packet: TaskPacket) -> str:
        lines = [
            f"You are the {self.name} executor for Blueprint run {packet.task_id}.",
            "",
            "Primary objective:",
            packet.goal,
            "",
            "Executor contract:",
            "- The backend validates outputs using task_packet.json, adapter_contract.json, "
            "manifest.json, manager_brief.json, and preserved code artifacts.",
            "- If validation fails, the run will return structured errors for repair instead of being accepted by Manager.",
            "- Keep executable analysis code under scripts/generated/{run_id}/ and declare it in manifest.code_artifacts.",
            "- Write your final manifest to manifest.candidate.json. The wrapper validates it and promotes it to manifest.json.",
            "- If your launch environment provides BLUEPRINT_MANIFEST_CANDIDATE_PATH, write the candidate manifest there.",
            "- If required runtime dependencies are missing, do not install packages with pip, conda, install.packages, or BiocManager. "
            "Use the dependency report tool and stop the analysis until the runtime is fixed.",
            "- After all result files, manager_brief.json, and manifest.candidate.json are written, exit immediately with success. "
            "Do not keep chatting, inspecting files, or printing result contents.",
            "",
            "Task packet:",
            "- JSON path: task_packet.json",
            f"- Project root: {packet.run_context.project_root if packet.run_context else '.'}",
            f"- Run dir: {packet.run_context.run_dir if packet.run_context else 'runs/current'}",
            f"- Result dir: {packet.run_context.result_dir if packet.run_context else 'results/current'}",
            f"- Python runtime: {packet.executor_context.runtime_bindings.conda_env if packet.executor_context and packet.executor_context.runtime_bindings.conda_env else 'system'}",
            f"- R runtime: {packet.executor_context.runtime_bindings.r_env if packet.executor_context and packet.executor_context.runtime_bindings.r_env else 'system'}",
            "- For R work, prefer BLUEPRINT_RSCRIPT when set; do not assume the Python conda environment also contains R.",
            "",
            "Input assets:",
        ]
        if packet.input_assets:
            lines.extend(f"- {item.asset_id}: {item.path} ({item.type})" for item in packet.input_assets)
        else:
            lines.append("- No materialized input assets were attached.")
        lines.extend(
            [
                "",
                "Expected outputs:",
            ]
        )
        lines.extend(
            f"- {item.role}: {item.path_hint} [{item.artifact_class}; accepted={', '.join(item.accepted_formats) if item.accepted_formats else 'any'}; preferred={item.preferred_format or 'auto'}]"
            for item in packet.expected_outputs
        )
        if packet.executor_context:
            lines.extend(
                [
                    "",
                    "Executor context:",
                    f"- Profile: {packet.executor_context.executor_profile or 'none'}",
                    f"- Skills: {', '.join(packet.executor_context.skills) if packet.executor_context.skills else 'none'}",
                    f"- MCP servers: {', '.join(packet.executor_context.mcp_servers) if packet.executor_context.mcp_servers else 'none'}",
                    f"- Skill bindings file: runs/{packet.task_id}/library/skill_bindings.json",
                    f"- MCP bindings file: runs/{packet.task_id}/library/mcp_bindings.json",
                    f"- MCP config file: runs/{packet.task_id}/library/mcp.json",
                ]
            )
            lines.extend(f"- Instruction: {item}" for item in packet.executor_context.instruction_blocks)
            lines.extend(f"- Reference: {item.path} ({item.type})" for item in packet.executor_context.references)
        dependency_tool_path = f"runs/{packet.task_id}/report_dependency_issue.py"
        lines.extend(
            [
                "",
                "Stdout discipline:",
                "- Do not print tables, matrices, SVG, reports, JSON manifests, or file contents to stdout.",
                "- Do not run cat/head/tail on large outputs for the user. The user will inspect files through Blueprint previews.",
                "- Stdout should contain only short BP_EVENT progress updates or one concise final sentence with output paths.",
                "- Once manifest.candidate.json validates in your own reasoning and manager_brief.json is written, stop the process.",
                "",
                "Runtime dependency policy:",
                "- Probe required Python/R/system packages before doing expensive analysis.",
                "- Do not create or modify conda environments and do not install missing analysis packages inside the run.",
                "- When a required dependency is missing, call the reporting tool, then stop rather than substituting a weaker method silently.",
                f"- Tool: python {dependency_tool_path} --ecosystem R --package <missing-package> --manager <CRAN|Bioconductor|conda|pip> --message '<why it is required>'",
                "- For multiple missing packages, repeat --package for each one.",
                "- Use --non-blocking only for optional dependencies where the analysis can still produce valid outputs.",
            ]
        )
        lines.extend(
            [
                "",
                "Output contract:",
                "- manifest.candidate.json must declare every created asset in created_assets with role/path.",
                "- manifest.candidate.json must declare preserved code in code_artifacts when assets are created.",
                "- status must be exactly one of: success, failed, partial.",
                "- Use created_assets, not outputs.",
                "- Include summary and commands_executed.",
                "- manager_brief.json should summarize final status for Manager; it must not mutate graph/card state.",
                "",
                "Input reporting rules:",
                "- The declared inputs are already available in task_packet.json and will be forwarded to Reviewer.",
                "- Record a short factual input conclusion in manifest.validation_evidence.input_conclusion.",
                "- Do not restate the full input list in the manifest unless you have a concrete reason to do so.",
                "- Do not use report_dependency_issue.py for input reporting or manifest completeness questions.",
                "- Use report_dependency_issue.py only when a required runtime dependency is actually missing and you need to stop for environment repair.",
            ]
        )
        return "\n".join(lines) + "\n"
