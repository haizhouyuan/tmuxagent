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
from uuid import uuid4

from .. import metrics
from ..agents.service import AgentRecord
from ..agents.service import AgentService
from ..local_bus import LocalBus
from ..notify import Notifier
from ..notify import NotificationMessage
from ..state import StateStore
from .codex_client import CodexClient
from .codex_client import OrchestratorDecision
from .config import OrchestratorConfig
from .config import TaskSpec

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
        self._session_busy_until: dict[str, float] = {}
        self._session_queue: dict[str, list[dict[str, Any]]] = {}
        self._pending_by_branch: dict[str, list[dict[str, Any]]] = {}
        self._task_plan: dict[str, TaskSpec] = {task.branch: task for task in config.tasks}
        self._metrics_started = False
        self._confirmations_offset = "confirmations:orchestrator"
        audit_dir = self.agent_service.config.repo_root / ".tmuxagent" / "logs"
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._audit_log_path = audit_dir / "orchestrator-actions.jsonl"

    def run_forever(self) -> None:  # pragma: no cover - integration flow
        interval = self.config.poll_interval
        self._maybe_start_metrics()
        logger.info("Starting orchestrator loop (interval %.1fs)", interval)
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Orchestrator interrupted, exiting")

    def run_once(self) -> None:
        self._flush_queued_commands()
        self._process_confirmations()
        snapshots = self._collect_snapshots()
        now_ts = int(time.time())
        for snapshot in snapshots:
            self._update_heartbeat(snapshot, now_ts)
            self._maybe_rotate_log(snapshot.record.log_path)
            self._maybe_generate_summary(snapshot)
            if not self._should_process(snapshot):
                continue
            prompt = self._render_prompt(snapshot)
            start_time = time.perf_counter()
            try:
                decision = self.codex.run(prompt)
            except Exception as exc:
                logger.error("Codex execution failed for %s: %s", snapshot.branch, exc)
                self._record_error(snapshot, str(exc))
                continue
            metrics.observe_decision_latency(time.perf_counter() - start_time)
            self._handle_decision(snapshot, decision)
        self._flush_queued_commands()

    def _maybe_start_metrics(self) -> None:
        if self._metrics_started:
            return
        if self.config.metrics_port:
            try:
                metrics.start_server(self.config.metrics_port, self.config.metrics_host)
                logger.info(
                    "Started Prometheus exporter on %s:%s",
                    self.config.metrics_host,
                    self.config.metrics_port,
                )
            except Exception as exc:  # pragma: no cover - exporter failure should not kill service
                logger.warning("Failed to start metrics exporter: %s", exc)
            else:
                self._metrics_started = True

    def _collect_snapshots(self) -> list[AgentSnapshot]:
        records = self.agent_service.list_agents()
        snapshots: list[AgentSnapshot] = []
        for record in records:
            log_excerpt = self._read_log_tail(record.log_path)
            metadata = dict(record.metadata or {})
            metadata = self._apply_task_plan(record, metadata)
            queued = self._pending_by_branch.get(record.branch)
            if queued:
                metadata["queued_commands"] = [dict(item) for item in queued]
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

    def _apply_task_plan(self, record: AgentRecord, metadata: dict[str, Any]) -> dict[str, Any]:
        plan = self._task_plan.get(record.branch)
        if not plan:
            return metadata
        updates: dict[str, Any] = {}

        if plan.depends_on:
            existing = metadata.get("depends_on")
            if isinstance(existing, list):
                merged = [dep for dep in existing if isinstance(dep, str)]
            else:
                merged = []
            for dep in plan.depends_on:
                if dep not in merged:
                    merged.append(dep)
            if merged and merged != existing:
                updates["depends_on"] = merged

        if plan.responsible and metadata.get("responsible") != plan.responsible:
            updates["responsible"] = plan.responsible

        if plan.phases:
            current_plan = metadata.get("phase_plan")
            if current_plan != plan.phases:
                updates["phase_plan"] = list(plan.phases)

        if plan.title and metadata.get("orchestrator_task") != plan.title:
            updates["orchestrator_task"] = plan.title

        if plan.tags:
            existing_tags = metadata.get("tags")
            tag_set = {tag for tag in plan.tags}
            if isinstance(existing_tags, list):
                tag_set.update(str(tag) for tag in existing_tags)
            merged_tags = sorted(tag_set)
            if merged_tags and merged_tags != existing_tags:
                updates["tags"] = merged_tags

        if updates:
            self.agent_service.update_status(record.branch, metadata=updates)
            metadata.update(updates)
        return metadata

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
        phase_history_updates: list[str] | None = None
        if decision.phase:
            existing_history = snapshot.metadata.get("phase_history")
            if isinstance(existing_history, list):
                history_items = [str(item) for item in existing_history if isinstance(item, str)]
            else:
                history_items = []
            if not history_items or history_items[-1] != decision.phase:
                history_items.append(decision.phase)
                phase_history_updates = history_items[-10:]
        target_phase = (
            decision.phase
            or snapshot.metadata.get("phase")
            or self.config.default_phase
        )
        if decision.summary:
            meta_payload: dict[str, Any] = {
                "orchestrator_summary": decision.summary,
                "orchestrator_last_command": [cmd.text for cmd in decision.commands],
                "phase": target_phase,
                "blockers": list(decision.blockers) if decision.blockers else [],
            }
            if phase_history_updates is not None:
                meta_payload["phase_history"] = phase_history_updates
            self.agent_service.update_status(
                snapshot.branch,
                status="orchestrated",
                metadata=meta_payload,
            )
            snapshot.metadata.update(meta_payload)
        elif decision.phase or decision.blockers or phase_history_updates is not None:
            meta_updates: dict[str, Any] = {}
            if decision.phase:
                meta_updates["phase"] = decision.phase
            if decision.blockers:
                meta_updates["blockers"] = list(decision.blockers)
            if phase_history_updates is not None:
                meta_updates["phase_history"] = phase_history_updates
            if meta_updates:
                self.agent_service.update_status(snapshot.branch, metadata=meta_updates)
                snapshot.metadata.update(meta_updates)
        else:
            if phase_history_updates is not None:
                self.agent_service.update_status(
                    snapshot.branch,
                    metadata={"phase_history": phase_history_updates},
                )
                snapshot.metadata["phase_history"] = phase_history_updates
        confirmation_required = decision.requires_confirmation
        pending_payloads: list[dict[str, Any]] = []
        if decision.has_commands:
            sent = 0
            dispatched_texts: list[str] = []
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
                    dispatched = self._enqueue_command(payload, snapshot.branch)
                    if dispatched:
                        sent += 1
                        dispatched_texts.append(command.text)
                    else:
                        queued = self._pending_by_branch.get(snapshot.branch)
                        if queued:
                            snapshot.metadata["queued_commands"] = [dict(item) for item in queued]
                        elif "queued_commands" in snapshot.metadata:
                            snapshot.metadata["queued_commands"] = []
            if not decision.summary and dispatched_texts:
                existing_cmds = snapshot.metadata.get("orchestrator_last_command")
                if isinstance(existing_cmds, list):
                    merged_cmds = [str(item) for item in existing_cmds if isinstance(item, str)]
                else:
                    merged_cmds = []
                merged_cmds.extend(dispatched_texts)
                merged_cmds = merged_cmds[-10:]
                self.agent_service.update_status(
                    snapshot.branch,
                    metadata={"orchestrator_last_command": merged_cmds},
                )
                snapshot.metadata["orchestrator_last_command"] = merged_cmds
        for payload in pending_payloads:
            self._record_pending_confirmation(snapshot.branch, payload)
        queued_view = self._pending_by_branch.get(snapshot.branch)
        if queued_view:
            snapshot.metadata["queued_commands"] = [dict(item) for item in queued_view]
        elif "queued_commands" in snapshot.metadata:
            snapshot.metadata["queued_commands"] = []
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

    def _enqueue_command(
        self,
        payload: dict[str, Any],
        branch: str | None = None,
    ) -> bool:
        session = payload.get("session")
        if session and not self._can_send_session(session):
            entry = self._queue_command(session, payload, branch)
            if branch and entry.get("summary"):
                self._update_branch_queue(branch, add=entry["summary"])
            metrics.record_command("queued")
            return False
        self._dispatch_command(payload, branch, queued=False)
        return True

    def _dispatch_command(
        self,
        payload: dict[str, Any],
        branch: str | None,
        *,
        queued: bool,
    ) -> None:
        now = time.time()
        session = payload.get("session")
        if self.config.dry_run:
            logger.info("[dry-run] skip dispatch: %s", payload.get("text"))
            metrics.record_command("dry_run")
        else:
            self.bus.append_command(payload)
            if session:
                self._session_busy_until[session] = now + self.config.session_cooldown_seconds
            metrics.record_command("dispatched")
        if branch:
            self._last_command_at[branch] = now
            log_payload = {
                "ts": now,
                "branch": branch,
                "event": "command",
                "payload": payload,
            }
            if queued:
                log_payload["queued"] = True
            if self.config.dry_run:
                log_payload["dry_run"] = True
            self._log_action(log_payload)

    def _queue_command(
        self,
        session: str,
        payload: dict[str, Any],
        branch: str | None,
    ) -> dict[str, Any]:
        meta = dict(payload.get("meta") or {})
        queue_id = meta.get("queue_id") or f"queue-{uuid4().hex}"
        queued_at = meta.get("queued_at") or time.time()
        meta["queue_id"] = queue_id
        meta["queued_at"] = queued_at
        enriched_payload = dict(payload)
        enriched_payload["meta"] = meta
        entry = {
            "branch": branch,
            "payload": enriched_payload,
            "queue_id": queue_id,
            "session": session,
            "queued_at": queued_at,
            "summary": {
                "queue_id": queue_id,
                "text": enriched_payload.get("text"),
                "session": session,
                "risk_level": meta.get("risk_level"),
                "queued_at": queued_at,
            },
        }
        queue = self._session_queue.setdefault(session, [])
        queue.append(entry)
        if branch:
            self._log_action(
                {
                    "ts": queued_at,
                    "branch": branch,
                    "event": "queued",
                    "payload": enriched_payload,
                }
            )
        return entry

    def _flush_queued_commands(self) -> None:
        if not self._session_queue:
            return
        now = time.time()
        for session in list(self._session_queue.keys()):
            if not self._can_send_session(session, now):
                continue
            queue = self._session_queue.get(session)
            if not queue:
                self._session_queue.pop(session, None)
                continue
            entry = queue.pop(0)
            payload = entry.get("payload", {})
            branch = entry.get("branch")
            queue_id = entry.get("queue_id")
            self._dispatch_command(payload, branch, queued=True)
            if branch and queue_id:
                self._update_branch_queue(branch, remove_id=queue_id)
            if queue:
                self._session_queue[session] = queue
            else:
                self._session_queue.pop(session, None)

    def _can_send_session(self, session: str, now: float | None = None) -> bool:
        busy_until = self._session_busy_until.get(session)
        if busy_until is None:
            return True
        current = now or time.time()
        if current >= busy_until:
            self._session_busy_until.pop(session, None)
            return True
        return False

    def _update_branch_queue(
        self,
        branch: str | None,
        *,
        add: dict[str, Any] | None = None,
        remove_id: str | None = None,
    ) -> None:
        if not branch:
            return
        current = list(self._pending_by_branch.get(branch, []))
        changed = False
        if add:
            current.append(add)
            changed = True
        if remove_id:
            filtered = [item for item in current if item.get("queue_id") != remove_id]
            if len(filtered) != len(current):
                current = filtered
                changed = True
        if not changed:
            return
        metrics.set_queue_size(branch, len(current))
        if current:
            self._pending_by_branch[branch] = current
            self.agent_service.update_status(branch, metadata={"queued_commands": current})
        else:
            self._pending_by_branch.pop(branch, None)
            self.agent_service.update_status(branch, metadata={"queued_commands": []})

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
        metrics.set_pending_confirmations(branch, len(updated))
        self._log_action(
            {
                "ts": time.time(),
                "branch": branch,
                "event": "pending_confirmation",
                "payload": payload,
            }
        )

    def _record_error(self, snapshot: AgentSnapshot, message: str) -> None:
        self.agent_service.update_status(
            snapshot.branch,
            metadata={"orchestrator_error": message},
        )
        metrics.record_error(snapshot.branch)
        self.notifier.send(
            NotificationMessage(
                title="Orchestrator 异常",
                body=f"{snapshot.branch}: {message}",
                meta={"requires_attention": True, "severity": "critical"},
            )
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
            session_name = item.get("session")
            if session_name:
                self._session_busy_until.pop(session_name, None)
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
            metrics.set_pending_confirmations(branch, len(remaining))
            self._log_action(
                {
                    "ts": time.time(),
                    "branch": branch,
                    "event": "confirmation",
                    "status": "approved",
                    "command": command_text,
                    "meta": response_meta,
                }
            )

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
        metrics.set_pending_confirmations(branch, len(remaining))
        self._log_action(
            {
                "ts": time.time(),
                "branch": branch,
                "event": "confirmation",
                "status": "denied",
                "command": command_text,
                "meta": response_meta,
            }
        )

    def _is_phase_completed(self, branch: str) -> bool:
        session = self.state_store.get_agent_session(branch)
        if not session:
            return False
        metadata = session.get("metadata") or {}
        return metadata.get("phase") == self.config.completion_phase

    def _log_action(self, entry: dict[str, Any]) -> None:
        try:
            with self._audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False))
                handle.write("\n")
        except OSError:
            logger.debug("Failed to write orchestrator audit entry")

    def _should_process(self, snapshot: AgentSnapshot) -> bool:
        branch = snapshot.branch
        last = self._last_command_at.get(branch)
        if last is None:
            cooldown_ok = True
        else:
            cooldown_ok = (time.time() - last) >= self.config.cooldown_seconds

        deps = snapshot.metadata.get("depends_on")
        if isinstance(deps, list) and deps:
            missing = [dep for dep in deps if not self._is_phase_completed(dep)]
            if missing:
                if snapshot.metadata.get("blockers") != missing:
                    self.agent_service.update_status(branch, metadata={"blockers": missing})
                return False
            if snapshot.metadata.get("blockers"):
                self.agent_service.update_status(branch, metadata={"blockers": []})

        return cooldown_ok


__all__ = ["OrchestratorService", "AgentSnapshot"]
