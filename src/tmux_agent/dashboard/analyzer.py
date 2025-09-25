"""Analyze pane output to derive high-level activity status."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional

from .panes import PaneSnapshot


SENTRY_PREFIX = "### SENTRY "


class PaneStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    WAITING_INPUT = "WAITING_INPUT"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    UNKNOWN = "UNKNOWN"


ERROR_KEYWORDS = [
    "traceback",
    "error",
    "exception",
    "failure",
    "fail",
    "✖",
    "terminated",
]

SUCCESS_KEYWORDS = [
    "success",
    "succeeded",
    "completed",
    "done",
    "finished",
    "✔",
]

WAITING_KEYWORDS = [
    "awaiting input",
    "waiting for input",
    "enter command",
    "provide input",
    "codex>",
    "claude>",
]

SEVERITY_ORDER = {
    PaneStatus.ERROR: 5,
    PaneStatus.WAITING_INPUT: 4,
    PaneStatus.RUNNING: 3,
    PaneStatus.IDLE: 2,
    PaneStatus.UNKNOWN: 1,
    PaneStatus.SUCCESS: 0,
}


@dataclass
class PaneActivity:
    pane_id: str
    session: str
    window: str
    title: str
    status: PaneStatus
    summary: str
    last_event: str | None
    captured_at: datetime
    is_active: bool
    attention: bool
    agent: str | None = None
    project: str = "unknown"
    tail_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pane_id": self.pane_id,
            "session": self.session,
            "window": self.window,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "last_event": self.last_event,
            "captured_at": self.captured_at.isoformat(),
            "is_active": self.is_active,
            "attention": self.attention,
            "agent": self.agent,
            "project": self.project,
            "tail_excerpt": self.tail_excerpt,
        }


@dataclass
class WindowActivity:
    session: str
    window: str
    panes: list[PaneActivity]

    @property
    def status(self) -> PaneStatus:
        return _aggregate_status(p.status for p in self.panes)

    @property
    def attention(self) -> bool:
        return any(p.attention for p in self.panes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "window": self.window,
            "status": self.status.value,
            "attention": self.attention,
            "panes": [pane.to_dict() for pane in self.panes],
        }


@dataclass
class SessionActivity:
    session: str
    windows: list[WindowActivity]
    project: str

    @property
    def status(self) -> PaneStatus:
        return _aggregate_status(win.status for win in self.windows)

    @property
    def attention(self) -> bool:
        return any(win.attention for win in self.windows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "project": self.project,
            "status": self.status.value,
            "attention": self.attention,
            "windows": [window.to_dict() for window in self.windows],
        }


def _aggregate_status(statuses: Iterable[PaneStatus]) -> PaneStatus:
    current = PaneStatus.SUCCESS
    current_rank = SEVERITY_ORDER[current]
    for status in statuses:
        rank = SEVERITY_ORDER.get(status, 0)
        if rank > current_rank:
            current = status
            current_rank = rank
    return current


class PaneStatusAnalyzer:
    def __init__(self, project_resolver: Callable[[PaneSnapshot], str] | None = None) -> None:
        self._project_resolver = project_resolver or self._default_project

    def analyze(self, snapshot: PaneSnapshot) -> PaneActivity:
        agent = self._infer_agent(snapshot)
        status, summary, last_event = self._determine_status(snapshot)
        tail_excerpt = self._tail_excerpt(snapshot.lines)
        attention = status in {PaneStatus.ERROR, PaneStatus.WAITING_INPUT}
        project = self._project_resolver(snapshot)
        return PaneActivity(
            pane_id=snapshot.pane_id,
            session=snapshot.session,
            window=snapshot.window,
            title=snapshot.title,
            status=status,
            summary=summary,
            last_event=last_event,
            captured_at=snapshot.captured_at,
            is_active=snapshot.is_active,
            attention=attention,
            agent=agent,
            project=project,
            tail_excerpt=tail_excerpt,
        )

    def build_board(self, snapshots: list[PaneSnapshot]) -> list[SessionActivity]:
        activities = [self.analyze(snapshot) for snapshot in snapshots]
        sessions: dict[str, dict[str, Any]] = {}
        for pane in activities:
            session_bucket = sessions.setdefault(
                pane.session,
                {
                    "project": pane.project,
                    "windows": {},
                },
            )
            session_bucket["project"] = pane.project or session_bucket.get("project") or "unknown"
            session_bucket.setdefault("windows", {}).setdefault(pane.window, []).append(pane)

        session_nodes: list[SessionActivity] = []
        for session_name, payload in sorted(sessions.items()):
            window_nodes = []
            for window_name, pane_list in sorted(payload["windows"].items()):
                ordered_panes = sorted(pane_list, key=lambda p: (p.title or "", p.pane_id))
                window_nodes.append(
                    WindowActivity(
                        session=session_name,
                        window=window_name,
                        panes=ordered_panes,
                    )
                )
            session_nodes.append(
                SessionActivity(
                    session=session_name,
                    windows=window_nodes,
                    project=payload.get("project", "unknown"),
                )
            )
        return session_nodes

    def _determine_status(self, snapshot: PaneSnapshot) -> tuple[PaneStatus, str, Optional[str]]:
        lines = snapshot.lines
        reversed_lines = list(reversed(lines))

        sentry_event = self._latest_sentry_event(reversed_lines)
        if sentry_event:
            status_from_event = self._status_from_sentry(sentry_event)
            summary = self._summary_from_sentry(sentry_event)
            if summary is None:
                summary = self._fallback_summary(lines)
            return status_from_event, summary, sentry_event.get("raw")

        text_tail = "\n".join(line.lower() for line in lines[-40:])
        last_non_empty = self._fallback_summary(lines)

        if _contains_any(text_tail, ERROR_KEYWORDS):
            return PaneStatus.ERROR, last_non_empty, None
        if _contains_any(text_tail, SUCCESS_KEYWORDS):
            return PaneStatus.SUCCESS, last_non_empty, None
        if _contains_any(text_tail, WAITING_KEYWORDS):
            return PaneStatus.WAITING_INPUT, last_non_empty or "等待输入", None

        if snapshot.is_active:
            return PaneStatus.RUNNING, last_non_empty or "运行中", None

        if lines:
            return PaneStatus.IDLE, last_non_empty, None

        return PaneStatus.UNKNOWN, "暂无输出", None

    @staticmethod
    def _tail_excerpt(lines: list[str], limit: int = 8) -> str:
        if not lines:
            return ""
        tail_lines = [line.rstrip() for line in lines[-limit:]]
        while tail_lines and not tail_lines[-1].strip():
            tail_lines.pop()
        if not tail_lines:
            return ""
        return "\n".join(tail_lines[-limit:])

    def _latest_sentry_event(self, reversed_lines: list[str]) -> dict[str, Any] | None:
        for line in reversed_lines:
            if line.startswith(SENTRY_PREFIX):
                payload = line[len(SENTRY_PREFIX) :].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    return {"type": "RAW", "raw": line}
                data["raw"] = line
                return data
        return None

    def _status_from_sentry(self, event: dict[str, Any]) -> PaneStatus:
        event_type = event.get("type", "").upper()
        if event_type == "STATUS":
            if event.get("ok") is True:
                return PaneStatus.SUCCESS
            if event.get("ok") is False:
                return PaneStatus.ERROR
            return PaneStatus.RUNNING
        if event_type == "ERROR":
            return PaneStatus.ERROR
        if event_type == "ASK":
            return PaneStatus.WAITING_INPUT
        return PaneStatus.UNKNOWN

    def _summary_from_sentry(self, event: dict[str, Any]) -> str | None:
        summary = event.get("summary") or event.get("detail") or event.get("question")
        if summary:
            return str(summary)
        stage = event.get("stage")
        if stage:
            event_type = event.get("type")
            return f"{stage} · {event_type}" if event_type else stage
        return None

    def _fallback_summary(self, lines: list[str]) -> str:
        for line in reversed(lines):
            stripped = line.strip()
            if stripped:
                return stripped[:240]
        return ""

    def _infer_agent(self, snapshot: PaneSnapshot) -> str | None:
        title = (snapshot.title or "").lower()
        window = (snapshot.window or "").lower()
        session = (snapshot.session or "").lower()
        if "codex" in title or "codex" in window or "codex" in session:
            return "codex"
        if "claude" in title or "claude" in window or "claude" in session:
            return "claude"
        return None

    def _default_project(self, snapshot: PaneSnapshot) -> str:
        session = (snapshot.session or "").lower()
        window = (snapshot.window or "").lower()
        title = (snapshot.title or "").lower()
        for key in (session, window, title):
            if key.startswith("storyapp") or "storyapp" in key:
                return "storyapp"
            if key.startswith("points") or "points" in key:
                return "points"
        return "others"


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    return any(needle in haystack for needle in needles)
