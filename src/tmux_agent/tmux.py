"""Adapter around the tmux CLI for pane discovery and interaction."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Iterable


@dataclass
class PaneInfo:
    pane_id: str
    session_name: str
    window_name: str
    pane_title: str

    def matches_patterns(self, patterns: Iterable[str]) -> bool:
        if not patterns:
            return True
        return any(self.pane_title and __import__("re").search(pat, self.pane_title) for pat in patterns)


@dataclass
class CaptureResult:
    pane_id: str
    content: str


class TmuxAdapter:
    """Simple thin wrapper around tmux shell commands."""

    def __init__(self, tmux_bin: str = "tmux", socket: str | None = None):
        self.tmux_bin = tmux_bin
        self.socket = socket

    def _base_cmd(self) -> list[str]:
        cmd = [self.tmux_bin]
        if self.socket and self.socket != "default":
            cmd += ["-L", self.socket]
        return cmd

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = self._base_cmd() + args
        return subprocess.run(cmd, check=True, text=True, capture_output=True)

    def list_panes(self) -> list[PaneInfo]:
        args = [
            "list-panes",
            "-a",
            "-F",
            "#{pane_id}\t#{session_name}\t#{window_name}\t#{pane_title}",
        ]
        proc = self._run(args)
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
        args = [
            "capture-pane",
            "-p",
            "-t",
            pane_id,
            "-S",
            f"-{capture_lines}",
        ]
        proc = self._run(args)
        return CaptureResult(pane_id=pane_id, content=proc.stdout)

    def send_keys(self, pane_id: str, keys: str, enter: bool = True) -> None:
        args = ["send-keys", "-t", pane_id]
        for chunk in keys.split(" "):
            args.append(chunk)
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
        # For testing, record command in buffer to assert behavior.
        suffix = keys + ("\n" if enter else "")
        self.append_output(pane_id, f"[SENT:{suffix}]")
