from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Blueprint RE v3"
    api_prefix: str = "/api"
    data_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[3] / "workspace")
    schema_version: str = "0.1.0"
    project_prefix: str = "proj"
    default_project_id: str = "demo-rnaseq"
    default_project_name: str = "RNA-seq Demo Project"
    frontend_origin: str = "http://127.0.0.1:3000"
    artifact_size_threshold_mb: int = 50
    deepseek_api_base_url: str = "https://api.deepseek.com/anthropic"
    deepseek_api_key: SecretStr | None = None
    pi_deepseek_base_url: str = "https://api.deepseek.com"
    manager_model: str = "deepseek-v4-pro"
    executor_model: str = "deepseek-v4-flash"
    reviewer_model: str = "deepseek-v4-flash"
    library_summarizer_model: str = "deepseek-v4-flash"
    manager_backend: str = "pi"
    manager_temperature: float = 0.2
    manager_max_tokens: int = 2400
    manager_timeout_seconds: int = 600
    reviewer_max_tokens: int = 1800
    reviewer_max_turns: int = 24
    pi_manager_url: str = "http://127.0.0.1:18002"
    backend_api_base_url: str = "http://127.0.0.1:18001/api"
    internal_tool_token: SecretStr | None = None
    default_worker_type: str = "pi"
    worker_timeout_seconds: int = 900
    executor_sandbox_mode: str = "bwrap"
    executor_max_concurrent_runs: int = 3
    executor_conda_base: Path = Path("/home/solarise/miniconda3")
    executor_host_root_readonly: bool = True
    executor_extra_ro_binds: str = Field(default_factory=lambda: f"{Path.home()}/.nvm,{Path.home()}/.local")
    opencode_command: str | None = None
    pi_command: str | None = None
    claude_code_command: str | None = None
    codex_command: str | None = None

    model_config = {
        "env_prefix": "BLUEPRINT_",
        "env_file": ".env",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
