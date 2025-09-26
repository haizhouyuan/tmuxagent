"""Main orchestrator service coordinating pane decisions."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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
        self._phase_prompts = {
            phase: path
            for phase, path in config.phase_prompts.items()
            if path.exists()
        }
        self._last_command_at: dict[str, float] = {}
        self._confirmations_offset = "confirmations:orchestrator"

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
        self._process_confirmations()
        snapshots = self._collect_snapshots()
        now_ts = int(time.time())
        for snapshot in snapshots:
            self._update_heartbeat(snapshot, now_ts)
            self._maybe_rotate_log(snapshot.record.log_path)
            self._maybe_generate_summary(snapshot)
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

    def _maybe_rotate_log(self, log_path: Optional[Path]) -> None:
        if not log_path or not log_path.exists():
            return
        try:
            data = log_path.read_text(encoding="utf-8")
        except OSError:
            return
        lines = data.splitlines()
        max_lines = self.config.history_lines * 10
        keep_lines = self.config.history_lines * 5
        if len(lines) <= max_lines:
            return
        archive = log_path.with_suffix(log_path.suffix + ".archive")
        try:
            archive.write_text("\n".join(lines[:-keep_lines]) + "\n", encoding="utf-8")
        except OSError:
            pass
        log_path.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")

    def _maybe_generate_summary(self, snapshot: AgentSnapshot) -> None:
        summary_prompt_path = self.config.prompts.summary
        if not summary_prompt_path or not summary_prompt_path.exists():
            return
        prompt_template = summary_prompt_path.read_text(encoding="utf-8")
        summary_prompt = prompt_template.format(
            branch=snapshot.branch,
            session=snapshot.session,
            log_excerpt=snapshot.log_excerpt,
            metadata=json.dumps(snapshot.metadata, ensure_ascii=False, indent=2),
        )
        try:
            summary_text = self.codex.run_summary(summary_prompt)
        except Exception as exc:  # pragma: no cover - summarization best effort
            logger.debug("Summary generation failed for %s: %s", snapshot.branch, exc)
            return
        existing = snapshot.metadata.get("history_summaries")
        if isinstance(existing, list):
            history = existing[-9:] + [summary_text]
        else:
            history = [summary_text]
        self.agent_service.update_status(
            snapshot.branch,
            metadata={"history_summaries": history},
        )

    def _render_prompt(self, snapshot: AgentSnapshot) -> str:
        current_phase = str(snapshot.metadata.get("phase") or self.config.default_phase)
        prompt_path = self._phase_prompts.get(current_phase, self.config.prompts.command)
        command_template = prompt_path.read_text(encoding="utf-8")
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
                metadata={
                    "orchestrator_summary": decision.summary,
                    "orchestrator_last_command": [cmd.text for cmd in decision.commands],
                    "phase": decision.phase or snapshot.metadata.get("phase") or self.config.default_phase,
                    "blockers": list(decision.blockers) if decision.blockers else [],
                },
            )
        elif decision.phase or decision.blockers:
            meta_updates: dict[str, object] = {}
            if decision.phase:
                meta_updates["phase"] = decision.phase
            if decision.blockers:
                meta_updates["blockers"] = list(decision.blockers)
            if meta_updates:
                self.agent_service.update_status(snapshot.branch, metadata=meta_updates)
        confirmation_required = decision.requires_confirmation
        pending_payloads: list[dict[str, Any]] = []
        if decision.has_commands:
            sent = 0
            for command in decision.commands:
                if sent >= self.config.max_commands_per_cycle:
                    break
                target_session = command.session or snapshot.session
                risk_level = command.risk_level
                requires_manual = confirmation_required or risk_level in {"high", "critical"}
                if requires_manual:
                    confirmation_required = True
                meta = {
                    "risk_level": risk_level,
                    "phase": decision.phase
                    or snapshot.metadata.get("phase")
                    or self.config.default_phase,
                }
                if command.cwd:
                    meta["cwd"] = command.cwd
                if command.notes:
                    meta["notes"] = command.notes
                payload = {
                    "text": command.text,
                    "session": target_session,
                    "enter": command.enter,
                    "sender": "orchestrator",
                    "meta": meta,
                }
                if requires_manual:
                    pending_payloads.append(payload)
                else:
                    self._enqueue_command(payload, snapshot.branch)
                    sent += 1
        for payload in pending_payloads:
            self._record_pending_confirmation(snapshot.branch, payload)
        should_notify = decision.notify and (
            not self.config.notify_only_on_confirmation
            or confirmation_required
        )
        if should_notify:
            meta = {
                "requires_attention": True,
                "phase": decision.phase
                or snapshot.metadata.get("phase")
                or self.config.default_phase,
            }
            if decision.blockers:
                meta["blockers"] = list(decision.blockers)
            self.notifier.send(
                NotificationMessage(
                    title=f"{snapshot.branch} 需要关注",
                    body=decision.notify,
                    meta=meta,
                )
            )

    def _enqueue_command(self, payload: dict[str, Any], branch: str | None = None) -> None:
        self.bus.append_command(payload)
        if branch:
            self._last_command_at[branch] = time.time()

    def _record_pending_confirmation(self, branch: str, payload: dict[str, Any]) -> None:
        session = self.state_store.get_agent_session(branch)
        if not session:
            return
        metadata = session.get("metadata") or {}
        pending = metadata.get("pending_confirmation")
        if isinstance(pending, list):
            updated = pending + [payload]
        else:
            updated = [payload]
        self.agent_service.update_status(branch, metadata={"pending_confirmation": updated})

    def _record_error(self, snapshot: AgentSnapshot, message: str) -> None:
        self.agent_service.update_status(
            snapshot.branch,
            metadata={"orchestrator_error": message},
        )

    def _update_heartbeat(self, snapshot: AgentSnapshot, now_ts: int) -> None:
        self.agent_service.update_status(
            snapshot.branch,
            metadata={"orchestrator_heartbeat": now_ts},
        )

    def _process_confirmations(self) -> None:
        offset = self.state_store.get_bus_offset(self._confirmations_offset)
        entries, new_offset = self.bus.read_confirmations(offset)
        processed = False
        for entry in entries:
            branch = entry.get("branch")
            if not branch:
                continue
            action = str(entry.get("action") or "").lower()
            command_text = entry.get("command")
            meta = entry.get("meta") or {}
            if action in {"approve", "approved", "ok"}:
                self._approve_pending(branch, command_text, meta)
                processed = True
            elif action in {"deny", "denied", "reject"}:
                self._deny_pending(branch, command_text, meta)
                processed = True
        if processed and new_offset != offset:
            self.state_store.set_bus_offset(self._confirmations_offset, new_offset)

    def _approve_pending(self, branch: str, command_text: str | None, response_meta: dict[str, Any]) -> None:
        session = self.state_store.get_agent_session(branch)
        if not session:
            return
        metadata = session.get("metadata") or {}
        pending = metadata.get("pending_confirmation")
        if not isinstance(pending, list) or not pending:
            return
        remaining: list[dict[str, Any]] = []
        executed = False
        for item in pending:
            if not isinstance(item, dict):
                continue
            if command_text and item.get("text") != command_text:
                remaining.append(item)
                continue
            self._enqueue_command(item, branch)
            executed = True
        updates: dict[str, Any] = {"pending_confirmation": remaining}
        if response_meta:
            responses = metadata.get("confirmation_responses") or []
            if isinstance(responses, list):
                updates["confirmation_responses"] = responses + [response_meta]
            else:
                updates["confirmation_responses"] = [response_meta]
        if executed:
            self.agent_service.update_status(branch, metadata=updates)

    def _deny_pending(self, branch: str, command_text: str | None, response_meta: dict[str, Any]) -> None:
        session = self.state_store.get_agent_session(branch)
        if not session:
            return
        metadata = session.get("metadata") or {}
        pending = metadata.get("pending_confirmation")
        if not isinstance(pending, list) or not pending:
            return
        remaining: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            if command_text and item.get("text") == command_text:
                continue
            remaining.append(item)
        updates: dict[str, Any] = {
            "pending_confirmation": remaining,
            "orchestrator_error": f"命令已拒绝: {command_text or '未知'}",
        }
        if response_meta:
            responses = metadata.get("confirmation_responses") or []
            if isinstance(responses, list):
                updates["confirmation_responses"] = responses + [response_meta]
            else:
                updates["confirmation_responses"] = [response_meta]
        self.agent_service.update_status(branch, metadata=updates)

    def _should_process(self, branch: str) -> bool:
        last = self._last_command_at.get(branch)
        if last is None:
            return True
        return (time.time() - last) >= self.config.cooldown_seconds


__all__ = ["OrchestratorService", "AgentSnapshot"]
