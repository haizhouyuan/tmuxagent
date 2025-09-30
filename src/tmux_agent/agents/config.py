"""Configuration helpers for agent CLI."""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # noqa: WPS433
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


CONFIG_DIR = ".tmuxagent"
CONFIG_FILE = "fleet.toml"


@dataclass
class FleetConfig:
    repo_root: Path
    worktree_root: Path
    templates_dir: Path
    command_template: str
    default_model: str
    tmux_prefix: str
    state_db: Path
    tmux_bin: str = "tmux"
    tmux_socket: str | None = None

    @property
    def config_path(self) -> Path:
        return self.repo_root / CONFIG_DIR / CONFIG_FILE


def detect_repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    current = start
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            raise ValueError("未在 Git 仓库内，无法定位 repo 根目录")
        current = current.parent


def load_fleet_config(start: Path | None = None) -> FleetConfig:
    repo_root = detect_repo_root(start)
    config_path = repo_root / CONFIG_DIR / CONFIG_FILE
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle) or {}

    agent_section = data.get("agent", {}) if isinstance(data, dict) else {}
    worktree_section = data.get("worktree", {}) if isinstance(data, dict) else {}
    tmux_section = data.get("tmux", {}) if isinstance(data, dict) else {}
    state_section = data.get("state", {}) if isinstance(data, dict) else {}

    worktree_root = Path(worktree_section.get("root", f"{CONFIG_DIR}/worktrees"))
    templates_dir = Path(agent_section.get("templates_dir", f"{CONFIG_DIR}/templates"))
    command_template = agent_section.get("command", "claude --model {model}")
    default_model = agent_section.get("model", "claude-sonnet-4-20250514")
    tmux_prefix = tmux_section.get("prefix", "agent-")
    tmux_bin = tmux_section.get("bin", "tmux")
    tmux_socket = tmux_section.get("socket")
    state_db = Path(state_section.get("db", os.path.expanduser("~/.tmux_agent/state.db")))

    return FleetConfig(
        repo_root=repo_root,
        worktree_root=(worktree_root if worktree_root.is_absolute() else (repo_root / worktree_root)).resolve(),
        templates_dir=(templates_dir if templates_dir.is_absolute() else (repo_root / templates_dir)).resolve(),
        command_template=command_template,
        default_model=default_model,
        tmux_prefix=tmux_prefix,
        state_db=Path(os.path.expanduser(str(state_db))).resolve(),
        tmux_bin=tmux_bin,
        tmux_socket=tmux_socket,
    )


def write_default_config(repo_root: Path) -> Path:
    config_dir = repo_root / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = config_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    worktree_dir = config_dir / "worktrees"
    worktree_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / CONFIG_FILE
    if not config_path.exists():
        payload = textwrap.dedent(
            f"""
            [agent]
            command = "claude --model {{model}}"
            model = "claude-sonnet-4-20250514"
            templates_dir = "{CONFIG_DIR}/templates"

            [worktree]
            root = "{CONFIG_DIR}/worktrees"

            [tmux]
            prefix = "agent-"
            bin = "tmux"

            [state]
            db = "~/.tmux_agent/state.db"
            """
        ).strip()
        config_path.write_text(payload + "\n", encoding="utf-8")
    return config_path
