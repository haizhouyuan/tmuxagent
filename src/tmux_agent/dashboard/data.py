"""Data helpers for the dashboard views."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state import StageState
from ..state import StateStore


@dataclass(frozen=True)
class StageRow:
    host: str
    pane_id: str
    pipeline: str
    stage: str
    status: str
    retries: int
    updated_at: datetime
    details: dict[str, Any]


class DashboardDataProvider:
    """Read-only access layer for dashboard endpoints."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).expanduser()

    def stage_rows(self) -> list[StageRow]:
        store = StateStore(self.db_path)
        try:
            states = store.list_stage_states()
        finally:
            store.close()
        rows = [self._convert_state(state) for state in states]
        rows.sort(key=lambda item: (item.host, item.pipeline, item.stage, item.pane_id))
        return rows

    def status_summary(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in self.stage_rows():
            counts[row.status] += 1
        return dict(counts)

    @staticmethod
    def _convert_state(state: StageState) -> StageRow:
        updated = datetime.fromtimestamp(state.updated_at or 0, tz=timezone.utc)
        return StageRow(
            host=state.host,
            pane_id=state.pane_id,
            pipeline=state.pipeline,
            stage=state.stage,
            status=state.status.value,
            retries=state.retries,
            updated_at=updated,
            details=state.data or {},
        )
