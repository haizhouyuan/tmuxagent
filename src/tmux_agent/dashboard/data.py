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

    def agent_sessions(self) -> list["AgentSessionRow"]:
        store = StateStore(self.db_path)
        try:
            raw_sessions = store.list_agent_sessions()
        finally:
            store.close()
        rows = [AgentSessionRow.from_dict(item) for item in raw_sessions]
        rows.sort(key=lambda row: row.branch)
        return rows

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


@dataclass(frozen=True)
class AgentSessionRow:
    branch: str
    worktree_path: Path
    session_name: str
    model: str | None
    template: str | None
    description: str | None
    pid: int | None
    last_prompt: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentSessionRow":
        return cls(
            branch=payload.get("branch", ""),
            worktree_path=Path(payload.get("worktree_path", "")) if payload.get("worktree_path") else Path(""),
            session_name=payload.get("session_name", ""),
            model=payload.get("model"),
            template=payload.get("template"),
            description=payload.get("description"),
            pid=payload.get("pid"),
            last_prompt=payload.get("last_prompt"),
            created_at=datetime.fromtimestamp(int(payload.get("created_at", 0)), tz=timezone.utc),
            updated_at=datetime.fromtimestamp(int(payload.get("updated_at", 0)), tz=timezone.utc),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "worktree_path": str(self.worktree_path) if self.worktree_path else "",
            "session_name": self.session_name,
            "model": self.model,
            "template": self.template,
            "description": self.description,
            "pid": self.pid,
            "last_prompt": self.last_prompt,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }
