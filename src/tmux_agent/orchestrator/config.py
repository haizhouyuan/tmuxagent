"""Configuration helpers for the orchestrator service."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic import Field

try:  # Python 3.11+
    import tomllib as toml_loader  # noqa: WPS433
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as toml_loader  # type: ignore

class TaskSpec(BaseModel):
    """Static orchestrator task definition used for planning."""

    branch: str
    title: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    responsible: str | None = None
    phases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


DEFAULT_CONFIG_PATH = Path(".tmuxagent/orchestrator.toml")


class PromptConfig(BaseModel):
    """Locations of prompt templates used by the orchestrator."""

    command: Path = Path(".tmuxagent/prompts/command.md")
    summary: Path | None = Path(".tmuxagent/prompts/summary.md")
    delegate: Path | None = None
    stuck_detection: Path | None = Path(".tmuxagent/prompts/stuck_detection.md")

    def expand(self, base: Path) -> "PromptConfig":
        data = self.model_dump()
        expanded: dict[str, Path | None] = {}
        for key, value in data.items():
            if value is None:
                expanded[key] = None
                continue
            expanded[key] = (value if value.is_absolute() else (base / value)).resolve()
        return PromptConfig(**expanded)


def _expand_path(base: Path, value: Path) -> Path:
    return value if value.is_absolute() else (base / value).resolve()


class CodexConfig(BaseModel):
    """Codex CLI invocation details."""

    bin: str = "codex"
    extra_args: list[str] = Field(default_factory=list)
    timeout: float = 120.0
    env: dict[str, str] = Field(default_factory=dict)


class OrchestratorConfig(BaseModel):
    """Top level orchestrator configuration."""

    poll_interval: float = 45.0
    cooldown_seconds: float = 120.0
    max_commands_per_cycle: int = 2
    history_lines: int = 400
    session_cooldown_seconds: float = 20.0
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    phase_prompts: dict[str, Path] = Field(default_factory=dict)
    default_phase: str = "planning"
    completion_phase: str = "done"
    codex: CodexConfig = Field(default_factory=CodexConfig)
    notify_only_on_confirmation: bool = True
    dry_run: bool = False
    metrics_port: int | None = None
    metrics_host: str = "0.0.0.0"
    tasks: list[TaskSpec] = Field(default_factory=list)
    stall_timeout_seconds: float = 300.0
    stall_retries_before_notify: int = 2
    failure_alert_threshold: int = 3
    command_timeout_seconds: float = 45.0
    delegate_to_codex: bool = False

    def expand_paths(self, base: Path) -> "OrchestratorConfig":
        clone = self.model_copy(deep=True)
        clone.prompts = self.prompts.expand(base)
        clone.phase_prompts = {
            phase: _expand_path(base, path)
            for phase, path in self.phase_prompts.items()
        }
        return clone


def load_orchestrator_config(path: Path | None = None) -> OrchestratorConfig:
    """Load orchestrator configuration from TOML (fallback to defaults)."""

    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if config_path.exists():
        raw: dict[str, Any] = toml_loader.loads(config_path.read_text(encoding="utf-8"))
    else:
        raw = {}
    data = OrchestratorConfig.model_validate(raw)
    base = config_path.parent if config_path.exists() else Path.cwd()
    return data.expand_paths(base)


__all__ = [
    "CodexConfig",
    "OrchestratorConfig",
    "PromptConfig",
    "TaskSpec",
    "load_orchestrator_config",
]
