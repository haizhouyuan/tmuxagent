"""Configuration loading for tmux-agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError


class HostConfig(BaseModel):
    """Configuration options for a monitored tmux host."""

    name: str
    socket: str = "default"
    session_filters: list[str] = Field(default_factory=list)
    pane_name_patterns: list[str] = Field(default_factory=list)
    capture_lines: int = 2000


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
        return AgentConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise ValueError(f"Invalid agent config at {path}: {exc}") from exc


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
