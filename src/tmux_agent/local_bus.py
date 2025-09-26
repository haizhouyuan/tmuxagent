"""Local message bus for notifications and commands."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable


@dataclass
class BusNotification:
    id: str
    ts: float
    title: str
    body: str
    meta: dict[str, Any]


@dataclass
class BusCommand:
    id: str
    ts: float
    text: str
    session: str | None = None
    enter: bool = True
    meta: dict[str, Any] | None = None


class LocalBus:
    """Append-only JSONL files for notifications and commands."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.notifications_path = self.base_dir / "notifications.jsonl"
        self.commands_path = self.base_dir / "commands.jsonl"
        for path in (self.notifications_path, self.commands_path):
            if not path.exists():
                path.touch()
        self._write_lock = threading.Lock()

    # Notification helpers --------------------------------------------------
    def append_notification(self, payload: dict[str, Any]) -> None:
        payload.setdefault("ts", time.time())
        payload.setdefault("id", self._default_id(prefix="n"))
        self._append_jsonl(self.notifications_path, payload)

    def recent_notifications(self, *, limit: int = 50, since_ts: float | None = None) -> list[dict[str, Any]]:
        lines = list(self._tail_jsonl(self.notifications_path, limit=limit * 2 or 100))
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None and data.get("ts", 0) <= since_ts:
                continue
            rows.append(data)
        rows.sort(key=lambda item: item.get("ts", 0))
        if limit:
            rows = rows[-limit:]
        return rows

    def read_notifications(self, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        size = os.path.getsize(self.notifications_path)
        if offset > size:
            offset = 0
        entries: list[dict[str, Any]] = []
        with self.notifications_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            new_offset = handle.tell()
        return entries, new_offset

    # Command helpers -------------------------------------------------------
    def append_command(self, payload: dict[str, Any]) -> None:
        payload.setdefault("ts", time.time())
        payload.setdefault("id", self._default_id(prefix="c"))
        self._append_jsonl(self.commands_path, payload)

    def read_commands(self, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        size = os.path.getsize(self.commands_path)
        if offset > size:
            offset = 0
        commands: list[dict[str, Any]] = []
        with self.commands_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    commands.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            new_offset = handle.tell()
        return commands, new_offset

    # Internal utilities ----------------------------------------------------
    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(data)
                handle.write("\n")

    def _tail_jsonl(self, path: Path, *, limit: int = 100) -> Iterable[str]:
        if limit <= 0:
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                end_pos = handle.tell()
                block_size = 4096
                data = b""
                lines: list[bytes] = []
                while end_pos > 0 and len(lines) <= limit:
                    seek_pos = max(0, end_pos - block_size)
                    handle.seek(seek_pos)
                    data = handle.read(end_pos - seek_pos) + data
                    end_pos = seek_pos
                    lines = data.splitlines()
                text_lines = [line.decode("utf-8", errors="ignore") for line in lines[-limit:]]
                return text_lines
        except FileNotFoundError:
            return []
        return []

    def _default_id(self, prefix: str) -> str:
        return f"{prefix}-{int(time.time() * 1000)}"

