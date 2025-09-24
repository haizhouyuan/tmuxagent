"""SQLite-backed persistence for the tmux agent."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional


class StageStatus(str, Enum):
    IDLE = "IDLE"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class StageState:
    host: str
    pane_id: str
    pipeline: str
    stage: str
    status: StageStatus = StageStatus.IDLE
    retries: int = 0
    data: Dict[str, Any] | None = None
    updated_at: int | None = None

    def to_row(self) -> tuple[Any, ...]:
        updated = self.updated_at or int(time.time())
        return (
            self.host,
            self.pane_id,
            self.pipeline,
            self.stage,
            self.status.value,
            self.retries,
            json.dumps(self.data or {}),
            updated,
        )

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "StageState":
        host, pane_id, pipeline, stage, status, retries, data_json, updated_at = row
        data = json.loads(data_json) if data_json else {}
        return cls(
            host=host,
            pane_id=pane_id,
            pipeline=pipeline,
            stage=stage,
            status=StageStatus(status),
            retries=retries,
            data=data,
            updated_at=updated_at,
        )


class StateStore:
    """Encapsulates SQLite operations for offsets, stage state, and approvals."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS pane_offsets (
                host TEXT NOT NULL,
                pane_id TEXT NOT NULL,
                offset INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (host, pane_id)
            );

            CREATE TABLE IF NOT EXISTS stage_state (
                host TEXT NOT NULL,
                pane_id TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                retries INTEGER NOT NULL DEFAULT 0,
                data TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (host, pane_id, pipeline, stage)
            );

            CREATE TABLE IF NOT EXISTS approval_tokens (
                host TEXT NOT NULL,
                pane_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                token TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (host, pane_id, stage)
            );
            """
        )
        self._conn.commit()

    # Pane offsets -----------------------------------------------------
    def get_offset(self, host: str, pane_id: str) -> int:
        cur = self._conn.execute(
            "SELECT offset FROM pane_offsets WHERE host = ? AND pane_id = ?",
            (host, pane_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, host: str, pane_id: str, offset: int) -> None:
        now = int(time.time())
        self._conn.execute(
            "INSERT INTO pane_offsets (host, pane_id, offset, updated_at) VALUES (?, ?, ?, ?)\n"
            "ON CONFLICT(host, pane_id) DO UPDATE SET offset = excluded.offset, updated_at = excluded.updated_at",
            (host, pane_id, offset, now),
        )
        self._conn.commit()

    # Stage state ------------------------------------------------------
    def load_stage_state(self, host: str, pane_id: str, pipeline: str, stage: str) -> StageState:
        cur = self._conn.execute(
            "SELECT host, pane_id, pipeline, stage, status, retries, data, updated_at\n"
            "FROM stage_state WHERE host = ? AND pane_id = ? AND pipeline = ? AND stage = ?",
            (host, pane_id, pipeline, stage),
        )
        row = cur.fetchone()
        if row:
            return StageState.from_row(tuple(row))
        return StageState(host=host, pane_id=pane_id, pipeline=pipeline, stage=stage)

    def save_stage_state(self, state: StageState) -> None:
        row = state.to_row()
        self._conn.execute(
            "INSERT INTO stage_state (host, pane_id, pipeline, stage, status, retries, data, updated_at)\n"
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n"
            "ON CONFLICT(host, pane_id, pipeline, stage) DO UPDATE SET\n"
            " status = excluded.status,\n"
            " retries = excluded.retries,\n"
            " data = excluded.data,\n"
            " updated_at = excluded.updated_at",
            row,
        )
        self._conn.commit()

    def reset_pipeline(self, host: str, pane_id: str, pipeline: str) -> None:
        self._conn.execute(
            "DELETE FROM stage_state WHERE host = ? AND pane_id = ? AND pipeline = ?",
            (host, pane_id, pipeline),
        )
        self._conn.commit()

    # Approval tokens --------------------------------------------------
    def upsert_approval_token(
        self, host: str, pane_id: str, stage: str, token: str, expires_at: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO approval_tokens (host, pane_id, stage, token, expires_at) VALUES (?, ?, ?, ?, ?)\n"
            "ON CONFLICT(host, pane_id, stage) DO UPDATE SET token = excluded.token, expires_at = excluded.expires_at",
            (host, pane_id, stage, token, expires_at),
        )
        self._conn.commit()

    def get_approval_token(self, host: str, pane_id: str, stage: str) -> Optional[tuple[str, int]]:
        cur = self._conn.execute(
            "SELECT token, expires_at FROM approval_tokens WHERE host = ? AND pane_id = ? AND stage = ?",
            (host, pane_id, stage),
        )
        row = cur.fetchone()
        if not row:
            return None
        token = row["token"]
        expires_at = int(row["expires_at"])
        if expires_at < int(time.time()):
            self.delete_approval_token(host, pane_id, stage)
            return None
        return token, expires_at

    def delete_approval_token(self, host: str, pane_id: str, stage: str) -> None:
        self._conn.execute(
            "DELETE FROM approval_tokens WHERE host = ? AND pane_id = ? AND stage = ?",
            (host, pane_id, stage),
        )
        self._conn.commit()

    def expire_tokens(self) -> None:
        now = int(time.time())
        self._conn.execute(
            "DELETE FROM approval_tokens WHERE expires_at < ?",
            (now,),
        )
        self._conn.commit()
