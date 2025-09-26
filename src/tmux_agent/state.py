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
from typing import Sequence


def _now_ts() -> int:
    return int(time.time())


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
        updated = self.updated_at or _now_ts()
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

    def _normalise_session_dict(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = payload.get("metadata")
        if isinstance(metadata_raw, str):
            try:
                payload["metadata"] = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                payload["metadata"] = {"raw": metadata_raw}
        elif metadata_raw is None:
            payload["metadata"] = {}
        payload.setdefault("status", "unknown")
        return payload

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

            CREATE TABLE IF NOT EXISTS agent_sessions (
                branch TEXT PRIMARY KEY,
                worktree_path TEXT NOT NULL,
                session_name TEXT NOT NULL,
                model TEXT,
                template TEXT,
                description TEXT,
                pid INTEGER,
                last_prompt TEXT,
                status TEXT,
                log_path TEXT,
                last_output TEXT,
                last_output_at INTEGER,
                metadata TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bus_offsets (
                name TEXT PRIMARY KEY,
                offset INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        existing_columns = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(agent_sessions)")
        }
        for column, column_type in (
            ("status", "TEXT"),
            ("log_path", "TEXT"),
            ("last_output", "TEXT"),
            ("last_output_at", "INTEGER"),
            ("metadata", "TEXT"),
        ):
            if column not in existing_columns:
                cur.execute(f"ALTER TABLE agent_sessions ADD COLUMN {column} {column_type}")
        self._conn.commit()

    # Agent sessions ---------------------------------------------------
    def upsert_agent_session(
        self,
        *,
        branch: str,
        worktree_path: str,
        session_name: str,
        model: str | None = None,
        template: str | None = None,
        description: str | None = None,
        pid: int | None = None,
        last_prompt: str | None = None,
        status: str | None = None,
        log_path: str | None = None,
        last_output: str | None = None,
        last_output_at: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_ts()
        payload_metadata = json.dumps(metadata or {})
        self._conn.execute(
            """
            INSERT INTO agent_sessions (
                branch,
                worktree_path,
                session_name,
                model,
                template,
                description,
                pid,
                last_prompt,
                status,
                log_path,
                last_output,
                last_output_at,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(branch) DO UPDATE SET
                worktree_path = excluded.worktree_path,
                session_name = excluded.session_name,
                model = excluded.model,
                template = excluded.template,
                description = excluded.description,
                pid = excluded.pid,
                last_prompt = excluded.last_prompt,
                status = excluded.status,
                log_path = excluded.log_path,
                last_output = excluded.last_output,
                last_output_at = excluded.last_output_at,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                branch,
                worktree_path,
                session_name,
                model,
                template,
                description,
                pid,
                last_prompt,
                status,
                log_path,
                last_output,
                last_output_at,
                payload_metadata,
                now,
                now,
            ),
        )
        self._conn.commit()

    def get_agent_session(self, branch: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT branch, worktree_path, session_name, model, template,
                   description, pid, last_prompt, status, log_path,
                   last_output, last_output_at, metadata, created_at, updated_at
            FROM agent_sessions
            WHERE branch = ?
            """,
            (branch,),
        ).fetchone()
        if not row:
            return None
        return self._normalise_session_dict(dict(row))

    def find_agent_by_session(self, session_name: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT branch, worktree_path, session_name, model, template,
                   description, pid, last_prompt, status, log_path,
                   last_output, last_output_at, metadata, created_at, updated_at
            FROM agent_sessions
            WHERE session_name = ?
            """,
            (session_name,),
        ).fetchone()
        if not row:
            return None
        return self._normalise_session_dict(dict(row))

    def list_agent_sessions(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT branch, worktree_path, session_name, model, template,
                   description, pid, last_prompt, status, log_path,
                   last_output, last_output_at, metadata, created_at, updated_at
            FROM agent_sessions
            ORDER BY updated_at DESC
            """
        )
        return [self._normalise_session_dict(dict(row)) for row in cur.fetchall()]

    def delete_agent_session(self, branch: str) -> None:
        self._conn.execute("DELETE FROM agent_sessions WHERE branch = ?", (branch,))
        self._conn.commit()

    def prune_agent_sessions(self, branches: Sequence[str]) -> None:
        placeholders = ",".join("?" for _ in branches)
        if not placeholders:
            self._conn.execute("DELETE FROM agent_sessions")
        else:
            self._conn.execute(
                f"DELETE FROM agent_sessions WHERE branch NOT IN ({placeholders})",
                tuple(branches),
            )
        self._conn.commit()

    def update_agent_runtime(
        self,
        branch: str,
        *,
        status: str | None = None,
        last_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get_agent_session(branch)
        if not existing:
            return
        merged_meta = dict(existing.get("metadata") or {})
        if metadata:
            merged_meta.update(metadata)
        self.upsert_agent_session(
            branch=branch,
            worktree_path=existing["worktree_path"],
            session_name=existing["session_name"],
            model=existing.get("model"),
            template=existing.get("template"),
            description=existing.get("description"),
            pid=existing.get("pid"),
            last_prompt=existing.get("last_prompt"),
            status=status or existing.get("status"),
            log_path=existing.get("log_path"),
            last_output=last_output or existing.get("last_output"),
            last_output_at=int(time.time()) if last_output else existing.get("last_output_at"),
            metadata=merged_meta,
        )

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

    def get_bus_offset(self, name: str) -> int:
        row = self._conn.execute("SELECT offset FROM bus_offsets WHERE name = ?", (name,)).fetchone()
        if not row:
            return 0
        return int(row[0])

    def set_bus_offset(self, name: str, offset: int) -> None:
        now = _now_ts()
        self._conn.execute(
            """
            INSERT INTO bus_offsets (name, offset, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                offset = excluded.offset,
                updated_at = excluded.updated_at
            """,
            (name, offset, now),
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

    def list_stage_states(self) -> list[StageState]:
        """Return all tracked stage states ordered by latest update."""
        cur = self._conn.execute(
            "SELECT host, pane_id, pipeline, stage, status, retries, data, updated_at"
            " FROM stage_state ORDER BY updated_at DESC"
        )
        rows = cur.fetchall()
        return [StageState.from_row(tuple(row)) for row in rows]

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
