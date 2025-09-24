"""Configuration loading for tmux-agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError
from pydantic import model_validator


class SSHConfig(BaseModel):
    """SSH connection options for a remote tmux host."""

    host: str
    port: int = 22
    user: str | None = None
    key_path: str | None = Field(default=None, alias="key")
    password: str | None = None
    timeout: int = 30

    model_config = {"populate_by_name": True}


class TmuxConfig(BaseModel):
    """tmux interaction options for a host."""

    socket: str = "default"
    session_filters: list[str] = Field(default_factory=list)
    pane_name_patterns: list[str] = Field(default_factory=list)
    capture_lines: int = 2000
    poll_interval_ms: int | None = None


class HostConfig(BaseModel):
    """Configuration options for a monitored tmux host."""

    name: str = "local"
    ssh: SSHConfig | None = None
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_structure(cls, data: Any) -> Any:
        """Support both nested and legacy flat host configuration."""
        if not isinstance(data, dict):
            return data

        payload = dict(data)

        if "tmux" not in payload:
            tmux_keys = {k: payload.pop(k) for k in list(payload.keys()) if k in {"socket", "session_filters", "pane_name_patterns", "capture_lines", "poll_interval_ms"}}
            if tmux_keys:
                payload["tmux"] = tmux_keys

        if "ssh" not in payload:
            ssh_field_map = {
                "host": "host",
                "port": "port",
                "user": "user",
                "key": "key",
                "key_path": "key",
                "password": "password",
                "timeout": "timeout",
            }
            ssh_values: dict[str, Any] = {}
            for key, target in ssh_field_map.items():
                if key in payload:
                    ssh_values[target] = payload.pop(key)
            if ssh_values:
                payload["ssh"] = ssh_values

        return payload


class AgentConfig(BaseModel):
    """Top-level agent configuration."""

    poll_interval_ms: int = 1500
    tmux_bin: str = "tmux"
    sqlite_path: Path = Path("~/.tmux_agent/state.db")
    approval_dir: Path = Path("~/.tmux_agent/approvals")
    notify_channel: str = Field(default="stdout", alias="notify")
    hosts: list[HostConfig] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }

    @model_validator(mode="after")
    def _ensure_hosts(self) -> "AgentConfig":
        if not self.hosts:
            self.hosts = [HostConfig(name="local")]
        return self

    def expanded_sqlite_path(self) -> Path:
        return self.sqlite_path.expanduser()

    def expanded_approval_dir(self) -> Path:
        return self.approval_dir.expanduser()


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_agent_config(path: Path) -> AgentConfig:
    raw = load_yaml(path)
    try:
        config = AgentConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise ValueError(f"Invalid agent config at {path}: {exc}") from exc
    return config


class TriggerSpec(BaseModel):
    log_regex: str | None = None
    message_type: str | None = None
    after_stage_success: str | None = None


class ActionSpec(BaseModel):
    send_keys: str | None = None
    shell: str | None = None


class StageConfig(BaseModel):
    name: str
    triggers: dict[str, list[TriggerSpec]] | None = None  # e.g. {"any_of": [...]}
    actions_on_start: list[ActionSpec] = Field(default_factory=list)
    success_when: dict[str, list[TriggerSpec]] | None = None
    fail_when: dict[str, list[TriggerSpec]] | None = None
    require_approval: bool = False
    on_fail: list[dict[str, Any]] = Field(default_factory=list)


class PipelineMatcher(BaseModel):
    window_name: str | None = None
    pane_title: str | None = None


class PipelineConfig(BaseModel):
    name: str
    match: dict[str, list[PipelineMatcher]]
    stages: list[StageConfig]


class PolicyConfig(BaseModel):
    principles: list[str] = Field(default_factory=list)
    pipelines: list[PipelineConfig]


def load_policy(path: Path) -> PolicyConfig:
    raw = load_yaml(path)
    try:
        return PolicyConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid policy file at {path}: {exc}") from exc
