"""Adapter around the tmux CLI for pane discovery and interaction."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Iterable
from typing import Optional
from typing import Sequence

from .config import SSHConfig


@dataclass
class PaneInfo:
    pane_id: str
    session_name: str
    window_name: str
    pane_title: str
    is_active: bool = False
    width: int | None = None
    height: int | None = None

    def matches_patterns(self, patterns: Iterable[str]) -> bool:
        if not patterns:
            return True
        for pattern in patterns:
            compiled = re.compile(pattern)
            if (self.pane_title and compiled.search(self.pane_title)) or (
                self.window_name and compiled.search(self.window_name)
            ):
                return True
        return False


@dataclass
class CaptureResult:
    pane_id: str
    content: str


class TmuxAdapter:
    """Wrapper around tmux commands, supporting local and SSH execution."""

    def __init__(
        self,
        tmux_bin: str = "tmux",
        socket: str | None = None,
        ssh: Optional[SSHConfig] = None,
    ) -> None:
        self.tmux_bin = tmux_bin
        self.socket = socket
        self.ssh = ssh

    def _tmux_command(self, args: list[str]) -> list[str]:
        cmd = [self.tmux_bin]
        if self.socket and self.socket != "default":
            cmd += ["-L", self.socket]
        cmd.extend(args)
        return cmd

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        tmux_cmd = self._tmux_command(args)

        if self.ssh:
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={self.ssh.timeout}",
                "-p",
                str(self.ssh.port),
            ]
            if self.ssh.key_path:
                ssh_cmd += ["-i", self.ssh.key_path]
            target = f"{self.ssh.user}@{self.ssh.host}" if self.ssh.user else self.ssh.host
            ssh_cmd.append(target)
            cmd = ssh_cmd + tmux_cmd
        else:
            cmd = tmux_cmd

        return subprocess.run(cmd, check=True, text=True, capture_output=True)

    def list_panes(self) -> list[PaneInfo]:
        proc = self._run(
            [
                "list-panes",
                "-a",
                "-F",
                "#{pane_id}\t#{session_name}\t#{window_name}\t#{pane_title}\t#{?pane_active,1,0}\t#{pane_width}\t#{pane_height}",
            ]
        )
        panes: list[PaneInfo] = []
        for line in proc.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 6)
            if len(parts) < 7:
                parts += [""] * (7 - len(parts))
            (
                pane_id,
                session_name,
                window_name,
                pane_title,
                active_flag,
                pane_width,
                pane_height,
            ) = parts
            panes.append(
                PaneInfo(
                    pane_id=pane_id,
                    session_name=session_name,
                    window_name=window_name,
                    pane_title=pane_title,
                    is_active=active_flag == "1",
                    width=int(pane_width) if pane_width else None,
                    height=int(pane_height) if pane_height else None,
                )
            )
        return panes

    def capture_pane(self, pane_id: str, capture_lines: int = 2000) -> CaptureResult:
        proc = self._run(
            [
                "capture-pane",
                "-p",
                "-t",
                pane_id,
                "-S",
                f"-{capture_lines}",
            ]
        )
        return CaptureResult(pane_id=pane_id, content=proc.stdout)

    def send_keys(self, pane_id: str, keys: str | Sequence[str], enter: bool = True) -> None:
        if isinstance(keys, str):
            chunks = keys.split("\n")
            for idx, chunk in enumerate(chunks):
                args = ["send-keys", "-t", pane_id]
                if chunk:
                    args.append(chunk)
                # send newline between chunks, but avoid double-appending final enter for empty strings
                is_last_chunk = idx == len(chunks) - 1
                if not is_last_chunk:
                    args.append("C-m")
                elif enter:
                    args.append("C-m")
                self._run(args)
            if not chunks and enter:
                self._run(["send-keys", "-t", pane_id, "C-m"])
            return

        args = ["send-keys", "-t", pane_id]
        args.extend(keys)
        if enter:
            args.append("C-m")
        self._run(args)

    def pipe_pane(self, pane_id: str, command: str, *, append: bool = True) -> None:
        args = ["pipe-pane", "-t", pane_id]
        if append:
            args.append("-o")
        args.append(command)
        self._run(args)

    # Session helpers ---------------------------------------------------
    def new_session(self, session_name: str, *, start_directory: str | None = None) -> None:
        args = ["new-session", "-d", "-s", session_name]
        if start_directory:
            args += ["-c", start_directory]
        self._run(args)

    def kill_session(self, session_name: str) -> None:
        self._run(["kill-session", "-t", session_name])

    def session_exists(self, session_name: str) -> bool:
        try:
            self._run(["has-session", "-t", session_name])
        except subprocess.CalledProcessError:
            return False
        return True

    def list_sessions(self) -> list[str]:
        proc = self._run(["list-sessions", "-F", "#{session_name}"])
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines

    def panes_for_session(self, session_name: str) -> list[PaneInfo]:
        return [pane for pane in self.list_panes() if pane.session_name == session_name]


class FakeTmuxAdapter(TmuxAdapter):
    """Testing double that keeps pane buffers in memory."""

    def __init__(self, panes: dict[str, str] | None = None):
        super().__init__(tmux_bin="tmux")
        self._panes: dict[str, str] = panes or {}
        self._pane_meta: dict[str, tuple[str, str, str, bool, int | None, int | None]] = {}
        self._sessions: dict[str, list[str]] = {}
        self._pane_counter = 0
        self._pipe_commands: dict[str, str] = {}
        for idx, pane in enumerate(self._panes):
            self._pane_meta[pane] = ("sess", f"window{idx}", f"pane{idx}", False, None, None)
            self._sessions.setdefault("sess", []).append(pane)

    def set_meta(self, pane_id: str, session: str, window: str, title: str) -> None:
        self._pane_meta[pane_id] = (session, window, title, False, None, None)
        self._sessions.setdefault(session, []).append(pane_id)

    def set_full_meta(
        self,
        pane_id: str,
        *,
        session: str,
        window: str,
        title: str,
        active: bool = False,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        self._pane_meta[pane_id] = (session, window, title, active, width, height)
        panes = self._sessions.setdefault(session, [])
        if pane_id not in panes:
            panes.append(pane_id)

    def append_output(self, pane_id: str, text: str) -> None:
        self._panes[pane_id] = self._panes.get(pane_id, "") + text

    def list_panes(self) -> list[PaneInfo]:
        panes = []
        for pane_id, (session, window, title, active, width, height) in self._pane_meta.items():
            panes.append(
                PaneInfo(
                    pane_id=pane_id,
                    session_name=session,
                    window_name=window,
                    pane_title=title,
                    is_active=active,
                    width=width,
                    height=height,
                )
            )
        return panes

    def capture_pane(self, pane_id: str, capture_lines: int = 2000) -> CaptureResult:
        return CaptureResult(pane_id=pane_id, content=self._panes.get(pane_id, ""))

    def send_keys(self, pane_id: str, keys: str | Sequence[str], enter: bool = True) -> None:
        if isinstance(keys, str):
            suffix = keys
        else:
            suffix = "|".join(keys)
        suffix += "\n" if enter else ""
        self.append_output(pane_id, f"[SENT:{suffix}]")

    def pipe_pane(self, pane_id: str, command: str, *, append: bool = True) -> None:  # noqa: ARG002
        existing = self._pipe_commands.get(pane_id)
        if append and existing == command:
            return
        self._pipe_commands[pane_id] = command
        marker = "[PIPE_APPEND]" if append else "[PIPE]"
        self.append_output(pane_id, f"{marker}{command}\n")

    # Session helpers ---------------------------------------------------
    def new_session(self, session_name: str, *, start_directory: str | None = None) -> None:  # noqa: ARG002
        if session_name in self._sessions:
            raise RuntimeError(f"session {session_name} already exists")
        pane_id = self._allocate_pane_id()
        self._sessions[session_name] = [pane_id]
        self._panes.setdefault(pane_id, "")
        self._pane_meta[pane_id] = (session_name, "window0", "pane0", True, None, None)

    def kill_session(self, session_name: str) -> None:
        pane_ids = self._sessions.pop(session_name, [])
        for pane_id in pane_ids:
            self._pane_meta.pop(pane_id, None)
            self._panes.pop(pane_id, None)

    def session_exists(self, session_name: str) -> bool:
        return session_name in self._sessions

    def list_sessions(self) -> list[str]:
        return sorted(self._sessions.keys())

    def panes_for_session(self, session_name: str) -> list[PaneInfo]:
        pane_ids = self._sessions.get(session_name, [])
        panes: list[PaneInfo] = []
        for pane_id in pane_ids:
            if pane_id not in self._pane_meta:
                continue
            session, window, title, active, width, height = self._pane_meta[pane_id]
            panes.append(
                PaneInfo(
                    pane_id=pane_id,
                    session_name=session,
                    window_name=window,
                    pane_title=title,
                    is_active=active,
                    width=width,
                    height=height,
                )
            )
        return panes

    def _allocate_pane_id(self) -> str:
        self._pane_counter += 1
        pane_id = f"%{self._pane_counter}"
        while pane_id in self._panes:
            self._pane_counter += 1
            pane_id = f"%{self._pane_counter}"
        return pane_id
