"""Adapter around the tmux CLI for pane discovery and interaction."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Iterable
from typing import Optional

from .config import SSHConfig


@dataclass
class PaneInfo:
    pane_id: str
    session_name: str
    window_name: str
    pane_title: str

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
                "#{pane_id}\t#{session_name}\t#{window_name}\t#{pane_title}",
            ]
        )
        panes: list[PaneInfo] = []
        for line in proc.stdout.strip().splitlines():
            if not line:
                continue
            pane_id, session_name, window_name, pane_title = line.split("\t")
            panes.append(
                PaneInfo(
                    pane_id=pane_id,
                    session_name=session_name,
                    window_name=window_name,
                    pane_title=pane_title,
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

    def send_keys(self, pane_id: str, keys: str, enter: bool = True) -> None:
        args = ["send-keys", "-t", pane_id]
        if keys:
            args.append(keys)
        if enter:
            args.append("C-m")
        self._run(args)


class FakeTmuxAdapter(TmuxAdapter):
    """Testing double that keeps pane buffers in memory."""

    def __init__(self, panes: dict[str, str] | None = None):
        super().__init__(tmux_bin="tmux")
        self._panes: dict[str, str] = panes or {}
        self._pane_meta: dict[str, tuple[str, str, str]] = {}
        for idx, pane in enumerate(self._panes):
            self._pane_meta[pane] = ("sess", f"window{idx}", f"pane{idx}")

    def set_meta(self, pane_id: str, session: str, window: str, title: str) -> None:
        self._pane_meta[pane_id] = (session, window, title)

    def append_output(self, pane_id: str, text: str) -> None:
        self._panes[pane_id] = self._panes.get(pane_id, "") + text

    def list_panes(self) -> list[PaneInfo]:
        panes = []
        for pane_id, (session, window, title) in self._pane_meta.items():
            panes.append(
                PaneInfo(
                    pane_id=pane_id,
                    session_name=session,
                    window_name=window,
                    pane_title=title,
                )
            )
        return panes

    def capture_pane(self, pane_id: str, capture_lines: int = 2000) -> CaptureResult:
        return CaptureResult(pane_id=pane_id, content=self._panes.get(pane_id, ""))

    def send_keys(self, pane_id: str, keys: str, enter: bool = True) -> None:
        suffix = keys + ("\n" if enter else "")
        self.append_output(pane_id, f"[SENT:{suffix}]")
