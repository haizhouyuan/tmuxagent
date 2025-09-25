"""Pane inspection utilities for the dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
from typing import Iterable
from typing import Sequence

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
    is_active: bool
    width: int | None = None
    height: int | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
            is_active=pane.is_active,
            width=pane.width,
            height=pane.height,
            captured_at=datetime.now(timezone.utc),
        )

    def snapshots(self) -> list[PaneSnapshot]:
        panes = sorted(
            self.list_panes(),
            key=lambda pane: (pane.session_name, pane.window_name, pane.pane_title, pane.pane_id),
        )
        return [self.snapshot(pane) for pane in panes]

    def send(
        self,
        pane_id: str,
        *,
        text: str | None = None,
        enter: bool = True,
        keys: Sequence[str] | None = None,
    ) -> None:
        if text is not None and keys:
            raise ValueError("Specify either text or keys, not both")

        if keys:
            self._adapter.send_keys(pane_id, keys, enter=enter)
            return

        if text is not None:
            self._adapter.send_keys(pane_id, text, enter=enter)
            return

        if enter:
            # Allow sending a bare Enter stroke from the UI
            self._adapter.send_keys(pane_id, "", enter=True)
            return

        raise ValueError("input required")

    @staticmethod
    def preview_lines(lines: Iterable[str], limit: int = 10) -> list[str]:
        buffered = list(lines)
        if len(buffered) <= limit:
            return buffered
        return buffered[-limit:]
