"""Main orchestrator service coordinating pane decisions."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import Optional

from ..agents.service import AgentRecord
from ..agents.service import AgentService
from ..local_bus import LocalBus
from ..notify import Notifier
from ..notify import NotificationMessage
from ..state import StateStore
from .codex_client import CodexClient
from .codex_client import OrchestratorDecision
from .config import OrchestratorConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentSnapshot:
    record: AgentRecord
    log_excerpt: str
    metadata: dict[str, object]

    @property
    def branch(self) -> str:
        return self.record.branch

    @property
    def session(self) -> str:
        return self.record.session_name


class OrchestratorService:
    """Periodically evaluates agent panes and injects Codex decisions."""

    def __init__(
        self,
        *,
        agent_service: AgentService,
        state_store: StateStore,
        bus: LocalBus,
        notifier: Notifier,
        codex: CodexClient,
        config: OrchestratorConfig,
    ) -> None:
        self.agent_service = agent_service
        self.state_store = state_store
        self.bus = bus
        self.notifier = notifier
        self.codex = codex
        self.config = config
        self._last_command_at: dict[str, float] = {}

    def run_forever(self) -> None:  # pragma: no cover - integration flow
        interval = self.config.poll_interval
        logger.info("Starting orchestrator loop (interval %.1fs)", interval)
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Orchestrator interrupted, exiting")

    def run_once(self) -> None:
        snapshots = self._collect_snapshots()
        for snapshot in snapshots:
            if not self._should_process(snapshot.branch):
                continue
            prompt = self._render_prompt(snapshot)
            try:
                decision = self.codex.run(prompt)
            except Exception as exc:
                logger.error("Codex execution failed for %s: %s", snapshot.branch, exc)
                self._record_error(snapshot, str(exc))
                continue
            self._handle_decision(snapshot, decision)

    def _collect_snapshots(self) -> list[AgentSnapshot]:
        records = self.agent_service.list_agents()
        snapshots: list[AgentSnapshot] = []
        for record in records:
            log_excerpt = self._read_log_tail(record.log_path)
            metadata = dict(record.metadata or {})
            snapshots.append(AgentSnapshot(record=record, log_excerpt=log_excerpt, metadata=metadata))
        return snapshots

    def _read_log_tail(self, log_path: Optional[Path]) -> str:
        if not log_path:
            return ""
        try:
            data = log_path.expanduser().read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        lines = data.splitlines()
        return "\n".join(lines[-self.config.history_lines :])

    def _render_prompt(self, snapshot: AgentSnapshot) -> str:
        command_template = self.config.prompts.command.read_text(encoding="utf-8")
        summary_template = (
            self.config.prompts.summary.read_text(encoding="utf-8")
            if self.config.prompts.summary and self.config.prompts.summary.exists()
            else None
        )
        metadata_json = json.dumps(snapshot.metadata, ensure_ascii=False, indent=2)
        payload = {
            "branch": snapshot.branch,
            "session": snapshot.session,
            "model": snapshot.record.model,
            "template": snapshot.record.template,
            "description": snapshot.record.description,
            "status": snapshot.record.status,
            "metadata": metadata_json,
            "log_excerpt": snapshot.log_excerpt,
            "summary_prompt": summary_template or "",
        }
        return command_template.format(**payload)

    def _handle_decision(self, snapshot: AgentSnapshot, decision: OrchestratorDecision) -> None:
        logger.debug("Decision for %s: %s", snapshot.branch, decision)
        if decision.summary:
            self.agent_service.update_status(
                snapshot.branch,
                status="orchestrated",
                metadata={"orchestrator_summary": decision.summary},
            )
        if decision.has_commands:
            sent = 0
            for command in decision.commands:
                if sent >= self.config.max_commands_per_cycle:
                    break
                target_session = command.session or snapshot.session
                self.bus.append_command(
                    {
                        "text": command.text,
                        "session": target_session,
                        "enter": command.enter,
                        "sender": "orchestrator",
                    }
                )
                sent += 1
                self._last_command_at[snapshot.branch] = time.time()
        if decision.notify and (not self.config.notify_only_on_confirmation or decision.requires_confirmation):
            self.notifier.send(
                NotificationMessage(
                    title=f"{snapshot.branch} 需要关注",
                    body=decision.notify,
                )
            )

    def _record_error(self, snapshot: AgentSnapshot, message: str) -> None:
        self.agent_service.update_status(
            snapshot.branch,
            metadata={"orchestrator_error": message},
        )

    def _should_process(self, branch: str) -> bool:
        last = self._last_command_at.get(branch)
        if last is None:
            return True
        return (time.time() - last) >= self.config.cooldown_seconds


__all__ = ["OrchestratorService", "AgentSnapshot"]
