"""Pane inspection utilities for the dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..tmux import CaptureResult
from ..tmux import PaneInfo
from ..tmux import TmuxAdapter


@dataclass
class PaneSnapshot:
    pane_id: str
    session: str
    window: str
    title: str
    lines: list[str]


class PaneService:
    """Facade around TmuxAdapter for dashboard use."""

    def __init__(self, adapter: TmuxAdapter, capture_lines: int = 200) -> None:
        self._adapter = adapter
        self._capture_lines = capture_lines

    def list_panes(self) -> list[PaneInfo]:
        return self._adapter.list_panes()

    def capture(self, pane_id: str) -> CaptureResult:
        return self._adapter.capture_pane(pane_id, self._capture_lines)

    def snapshot(self, pane: PaneInfo) -> PaneSnapshot:
        capture = self.capture(pane.pane_id)
        lines = capture.content.splitlines()
        return PaneSnapshot(
            pane_id=pane.pane_id,
            session=pane.session_name,
            window=pane.window_name,
            title=pane.pane_title,
            lines=lines,
        )

    def snapshots(self) -> list[PaneSnapshot]:
        panes = self.list_panes()
        return [self.snapshot(pane) for pane in panes]

    def send(self, pane_id: str, text: str, enter: bool = True) -> None:
        self._adapter.send_keys(pane_id, text, enter=enter)

    @staticmethod
    def preview_lines(lines: Iterable[str], limit: int = 10) -> list[str]:
        buffered = list(lines)
        if len(buffered) <= limit:
            return buffered
        return buffered[-limit:]
