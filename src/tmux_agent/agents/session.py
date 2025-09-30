"""Tmux session helpers dedicated to agent panes."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..tmux import PaneInfo
from ..tmux import TmuxAdapter


@dataclass(frozen=True)
class SessionSpawnResult:
    session_name: str
    pane_id: str


class AgentSessionManager:
    """Manage tmux sessions for AI agents."""

    def __init__(self, adapter: TmuxAdapter, prefix: str = "agent-") -> None:
        self._adapter = adapter
        self._prefix = prefix

    def session_name(self, branch: str) -> str:
        safe = branch.replace("/", "-").replace(":", "-")
        return f"{self._prefix}{safe}"

    def spawn(
        self,
        branch: str,
        worktree_path: Path,
        command: str,
        *,
        env: dict[str, str] | None = None,
        log_path: Path | None = None,
    ) -> SessionSpawnResult:
        session = self.session_name(branch)
        if self._adapter.session_exists(session):
            raise ValueError(f"会话 {session} 已存在")
        self._adapter.new_session(session, start_directory=str(worktree_path))
        pane = self._first_pane(session)
        if not pane:
            raise RuntimeError("新建会话未返回任何 pane")
        self._setup_logging(pane.pane_id, log_path)
        command_text = self._augment_command(command, env)
        self._adapter.send_keys(pane.pane_id, command_text, enter=True)
        return SessionSpawnResult(session_name=session, pane_id=pane.pane_id)

    def ensure_spawned(
        self,
        branch: str,
        worktree_path: Path,
        command: str,
        *,
        env: dict[str, str] | None = None,
        log_path: Path | None = None,
    ) -> SessionSpawnResult:
        session = self.session_name(branch)
        if not self._adapter.session_exists(session):
            return self.spawn(branch, worktree_path, command, env=env, log_path=log_path)
        pane = self._first_pane(session)
        if not pane:
            raise RuntimeError("目标会话存在但未找到 pane")
        self._setup_logging(pane.pane_id, log_path)
        self._adapter.send_keys(pane.pane_id, self._augment_command(command, env), enter=True)
        return SessionSpawnResult(session_name=session, pane_id=pane.pane_id)

    def send(self, branch: str, text: str, *, enter: bool = True) -> None:
        pane = self._first_pane(self.session_name(branch))
        if not pane:
            raise ValueError("目标会话不存在或无 pane")
        self._adapter.send_keys(pane.pane_id, text, enter=enter)

    def kill(self, branch: str) -> None:
        session = self.session_name(branch)
        if self._adapter.session_exists(session):
            self._adapter.kill_session(session)

    def capture_tail(self, branch: str, lines: int = 200) -> str:
        pane = self._first_pane(self.session_name(branch))
        if not pane:
            raise ValueError("目标会话不存在或无 pane")
        capture = self._adapter.capture_pane(pane.pane_id, lines)
        return capture.content

    def session_panes(self, branch: str) -> list[PaneInfo]:
        return self._adapter.panes_for_session(self.session_name(branch))

    # ------------------------------------------------------------------
    def _setup_logging(self, pane_id: str, log_path: Path | None) -> None:
        if not log_path:
            return
        expanded = log_path.expanduser()
        expanded.parent.mkdir(parents=True, exist_ok=True)
        if not expanded.exists():
            expanded.touch()
        command = f"cat >> {shlex.quote(str(expanded))}"
        self._adapter.pipe_pane(pane_id, command, append=True)

    def _augment_command(self, command: str, env: dict[str, str] | None) -> str:
        if not env:
            return command
        exports = [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
        return " && ".join([*exports, command])

    def _first_pane(self, session_name: str) -> Optional[PaneInfo]:
        panes = self._adapter.panes_for_session(session_name)
        return panes[0] if panes else None
