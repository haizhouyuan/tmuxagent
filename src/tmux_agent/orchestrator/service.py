"""Main orchestrator service coordinating pane decisions."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Optional
from uuid import uuid4

from .. import metrics
from ..constants import COMMAND_HISTORY_LIMIT
from ..constants import COMMAND_RESULT_SENTINEL
from ..agents.service import AgentRecord
from ..agents.service import AgentService
from ..local_bus import LocalBus
from ..notify import Notifier
from ..notify import NotificationMessage
from ..state import StateStore
from .codex_client import CodexClient
from .codex_client import CodexError
from .codex_client import OrchestratorDecision
from .config import OrchestratorConfig
from .config import TaskSpec
from .decomposer import decompose_requirements
from .insights import build_recommendations

logger = logging.getLogger(__name__)


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]|[@-Z\\-_]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B-\x1F\x7F]")
_SPINNER_PREFIXES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏", "⠛", "⠓")
_PROMPT_SNIPPETS = (
    "⏎ send",
    "Ctrl+J newline",
    "Ctrl+T transcript",
    "Ctrl+C quit",
    "Alt+↑ edit",
)

_CODE_BLOCK_RE = re.compile(r"```(?:bash)?\n(.*?)```", re.DOTALL)

ERROR_REPEAT_NOTIFY_THRESHOLD = 3
ERROR_SUPPRESSION_WINDOW_SECONDS = 300


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
        self._delegate_mode = config.delegate_to_codex
        self._phase_prompts = {
            phase: path
            for phase, path in config.phase_prompts.items()
            if path.exists()
        }
        self._stuck_prompt_path = (
            config.prompts.stuck_detection
            if config.prompts.stuck_detection and config.prompts.stuck_detection.exists()
            else None
        )
        if self._stuck_prompt_path:
            logger.debug("Stuck detection prompt loaded: %s", self._stuck_prompt_path)
        else:
            logger.debug("Stuck detection prompt not configured or missing")
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
        self._command_tracker_limit = COMMAND_HISTORY_LIMIT
        self._stall_counts: dict[str, int] = {}
        self._failure_alert_state: dict[str, int] = {}
        self._error_state: dict[str, dict[str, Any]] = {}
        self._fallback_sequences: dict[str, list[dict[str, Any]]] = {
            "storyapp/ci-hardening": [
                {"keys": ["Escape"], "enter": False},
                {"text": "bash -lc 'find docs -mindepth 1 -maxdepth 1 -printf \"%f\\n\" 2>&1 || ( echo \"[WARN] find 失败，尝试 ls\"; ls -la --group-directories-first --color=never docs 2>&1 )'"},
                {"text": "DONE"},
            ],
            "storyapp/tts-delivery": [
                {"keys": ["Escape"], "enter": False},
                {"text": "继续"},
                {"text": "DONE"},
                {"text": "bash -lc 'git status -sb 2>&1'"},
                {"text": "DONE"},
                {"keys": ["Escape"], "enter": False},
                {"text": "bash -lc 'find backend/src/services/tts -mindepth 1 -maxdepth 1 -printf \"%f\\n\" 2>&1 || ( echo \"[WARN] find 失败，尝试 ls\"; ls -la --group-directories-first --color=never backend/src/services/tts 2>&1 )'"},
                {"text": "DONE"},
            ],
            "storyapp/mongo-launch": [
                {"keys": ["Escape"], "enter": False},
                {"text": "继续"},
                {"text": "DONE"},
            ],
            "storyapp/orchestrator": [
                {"keys": ["Escape"], "enter": False},
                {"text": "bash -lc 'python3 ~/.tmux_agent/scripts/storyapp_digest.py --interval 0 | tail -n 10 2>&1'"},
                {"text": "DONE"},
                {"keys": ["Escape"], "enter": False},
                {"text": "bash -lc 'tail -n 20 ~/.tmux_agent/alerts.log 2>&1'"},
                {"text": "DONE"},
            ],
        }

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
            self._maybe_decompose_requirements(snapshot)
            self._maybe_generate_summary(snapshot)
            self._ensure_dialogue_health(snapshot, now_ts)
            self._detect_and_handle_stall(snapshot, now_ts)
            self._handle_repeated_failures(snapshot, now_ts)
            if self._should_process(snapshot):
                prompt = self._render_prompt(snapshot)
                start_time = time.perf_counter()
                try:
                    decision = self.codex.run(prompt)
                except CodexError as exc:
                    logger.error("Codex execution failed for %s: %s", snapshot.branch, exc)
                    self._record_error(
                        snapshot,
                        str(exc),
                        kind=exc.kind,
                        raw_payload=getattr(exc, "raw", None),
                    )
                    fallback = self._fallback_command_for(snapshot)
                    if fallback is not None:
                        dispatched = self._enqueue_command(fallback, snapshot.branch)
                        if dispatched:
                            snapshot.metadata["fallback_active"] = True
                            self.agent_service.update_status(
                                snapshot.branch,
                                metadata={
                                    "orchestrator_last_command": [
                                        fallback.get("text") or " ".join(fallback.get("keys", []))
                                    ],
                                    "fallback_active": True,
                                },
                            )
                    continue
                except Exception as exc:  # pragma: no cover - unexpected errors
                    logger.exception("Unexpected Codex failure for %s", snapshot.branch)
                    self._record_error(snapshot, str(exc))
                    continue
                metrics.observe_decision_latency(time.perf_counter() - start_time)
                self._handle_decision(snapshot, decision)
            self._update_next_actions(snapshot)
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
        if not lines:
            return ""
        history_limit = max(1, int(self.config.history_lines or 0))
        window = history_limit * 4
        excerpt = "\n".join(lines[-window:]) if window > 0 else data
        return self._sanitize_log_excerpt(excerpt, limit=history_limit)

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

    def _sanitize_log_excerpt(self, text: str, *, limit: int | None = None) -> str:
        if not text:
            return ""
        normalized = _ANSI_ESCAPE_RE.sub("", text)
        normalized = _CONTROL_CHAR_RE.sub("", normalized)
        normalized = normalized.replace("\r", "")
        cleaned_lines: list[str] = []
        for raw_line in normalized.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if any(stripped.startswith(prefix) for prefix in _SPINNER_PREFIXES):
                continue
            if any(snippet in raw_line for snippet in _PROMPT_SNIPPETS):
                continue
            cleaned_lines.append(raw_line.rstrip())
        if limit is not None and limit > 0:
            cleaned_lines = cleaned_lines[-limit:]
        return "\n".join(cleaned_lines).strip("\n")

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

    def _ensure_dialogue_health(self, snapshot: AgentSnapshot, now_ts: int) -> dict[str, Any] | None:
        if not self._stuck_prompt_path or not self._is_codex_dialogue_session(snapshot):
            return None
        last = snapshot.metadata.get("dialogue_health_ts")
        if isinstance(last, (int, float)) and (now_ts - last) < max(60, int(self.config.poll_interval)):
            cached = snapshot.metadata.get("dialogue_health")
            return cached if isinstance(cached, dict) else None
        prompt_template = self._stuck_prompt_path.read_text(encoding="utf-8")
        metadata_json = json.dumps(snapshot.metadata, ensure_ascii=False, indent=2, default=str)
        prompt = prompt_template.format(
            branch=snapshot.branch,
            session=snapshot.session,
            metadata=metadata_json,
            log_excerpt=snapshot.log_excerpt,
        )
        try:
            raw = self.codex.run_raw(prompt)
        except CodexError as exc:
            logger.debug("Dialogue health check failed for %s: %s", snapshot.branch, exc)
            result = {
                "stuck": False,
                "reason": f"model_error:{exc.kind}",
            }
        else:
            result = {
                "stuck": bool(raw.get("stuck")),
                "reason": str(raw.get("reason") or ""),
            }
            suggestion = raw.get("suggestion")
            if isinstance(suggestion, str) and suggestion.strip():
                result["suggestion"] = suggestion.strip()
        updates = {
            "dialogue_health": result,
            "dialogue_health_ts": now_ts,
        }
        self.agent_service.update_status(snapshot.branch, metadata=updates)
        snapshot.metadata.update(updates)
        logger.debug("Dialogue health for %s: %s", snapshot.branch, result)
        return result

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

    def _maybe_decompose_requirements(self, snapshot: AgentSnapshot) -> None:
        requirements_path = snapshot.metadata.get("requirements_doc")
        if not requirements_path:
            return
        try:
            resolved = Path(str(requirements_path)).expanduser()
        except (TypeError, ValueError):
            return
        if not resolved.exists():
            return
        meta_info = snapshot.metadata.get("task_decomposition_meta")
        source_mtime = int(resolved.stat().st_mtime)
        if isinstance(meta_info, dict) and meta_info.get("source_mtime") == source_mtime:
            return
        steps = decompose_requirements(resolved)
        if not steps:
            return
        payload = {
            "task_decomposition": steps,
            "task_decomposition_meta": {
                "source": str(resolved),
                "source_mtime": source_mtime,
            },
        }
        self.agent_service.update_status(snapshot.branch, metadata=payload)
        snapshot.metadata.update(payload)

    def _render_prompt(self, snapshot: AgentSnapshot) -> str:
        current_phase = str(snapshot.metadata.get("phase") or self.config.default_phase)
        prompt_path = self._phase_prompts.get(current_phase, self.config.prompts.command)
        command_template = prompt_path.read_text(encoding="utf-8")
        if self._delegate_mode:
            delegate_path = self.config.prompts.delegate
            if not delegate_path:
                candidate = prompt_path.parent / "command_delegate.md"
            else:
                candidate = delegate_path
            if candidate.exists():
                command_template = candidate.read_text(encoding="utf-8")
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
        had_error = bool(snapshot.metadata.get("orchestrator_error"))
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
        delegate_mode = self._delegate_mode
        if decision.summary:
            meta_payload: dict[str, Any] = {
                "orchestrator_summary": decision.summary,
                "orchestrator_last_command": []
                if delegate_mode
                else [cmd.text for cmd in decision.commands],
                "phase": target_phase,
                "blockers": list(decision.blockers) if decision.blockers else [],
            }
            if had_error:
                meta_payload["orchestrator_error"] = None
                meta_payload["orchestrator_error_payload"] = None
            if delegate_mode and decision.extra:
                actions = decision.extra.get("actions")
                if isinstance(actions, list):
                    meta_payload["delegate_actions"] = actions
            if phase_history_updates is not None:
                meta_payload["phase_history"] = phase_history_updates
            self.agent_service.update_status(
                snapshot.branch,
                status="orchestrated",
                metadata=meta_payload,
            )
            snapshot.metadata.update(meta_payload)
            if had_error:
                snapshot.metadata.pop("orchestrator_error", None)
                snapshot.metadata.pop("orchestrator_error_payload", None)
                had_error = False
        elif decision.phase or decision.blockers or phase_history_updates is not None:
            meta_updates: dict[str, Any] = {}
            if decision.phase:
                meta_updates["phase"] = decision.phase
            if decision.blockers:
                meta_updates["blockers"] = list(decision.blockers)
            if phase_history_updates is not None:
                meta_updates["phase_history"] = phase_history_updates
            if had_error:
                meta_updates["orchestrator_error"] = None
                meta_updates["orchestrator_error_payload"] = None
            if meta_updates:
                self.agent_service.update_status(snapshot.branch, metadata=meta_updates)
                snapshot.metadata.update(meta_updates)
                if had_error:
                    snapshot.metadata.pop("orchestrator_error", None)
                    snapshot.metadata.pop("orchestrator_error_payload", None)
                    had_error = False
        else:
            if phase_history_updates is not None:
                self.agent_service.update_status(
                    snapshot.branch,
                    metadata={"phase_history": phase_history_updates},
                )
                snapshot.metadata["phase_history"] = phase_history_updates
        confirmation_required = decision.requires_confirmation
        pending_payloads: list[dict[str, Any]] = []
        if decision.has_commands and delegate_mode:
            logger.debug(
                "Delegate mode active, skipping direct execution of suggestions: %s",
                [cmd.text or cmd.keys for cmd in decision.commands],
            )
            if decision.commands:
                suggestions = [cmd.text for cmd in decision.commands]
                snapshot.metadata["delegate_suggestions"] = suggestions
                self.agent_service.update_status(
                    snapshot.branch,
                    metadata={"delegate_suggestions": suggestions},
                )
        if decision.has_commands and not delegate_mode:
            sent = 0
            dispatched_texts: list[str] = []
            for command in decision.commands:
                expanded_commands = self._expand_dialogue_command(command)
                if not expanded_commands:
                    continue
                for expanded in expanded_commands:
                    if sent >= self.config.max_commands_per_cycle:
                        break
                    target_session = expanded.session or snapshot.session
                    risk_level = expanded.risk_level
                    requires_manual = confirmation_required or risk_level in {"high", "critical"}
                    if requires_manual:
                        confirmation_required = True
                    meta = {
                        "risk_level": risk_level,
                        "phase": decision.phase
                        or snapshot.metadata.get("phase")
                        or self.config.default_phase,
                    }
                    if expanded.cwd:
                        meta["cwd"] = expanded.cwd
                    if expanded.notes:
                        meta["notes"] = expanded.notes
                    payload = {
                        "text": expanded.text or "",
                        "session": target_session,
                        "enter": expanded.enter,
                        "sender": "orchestrator",
                        "meta": meta,
                    }
                    if expanded.input_mode:
                        meta["input_mode"] = expanded.input_mode
                    if expanded.keys:
                        payload["keys"] = self._normalize_keys(expanded.keys, expanded.input_mode)
                    if not payload["text"]:
                        payload.pop("text")
                    if requires_manual:
                        pending_payloads.append(payload)
                    else:
                        dispatched = self._enqueue_command(payload, snapshot.branch)
                        if dispatched:
                            sent += 1
                            if expanded.text:
                                dispatched_texts.append(expanded.text)
                        else:
                            queued = self._pending_by_branch.get(snapshot.branch)
                            if queued:
                                snapshot.metadata["queued_commands"] = [dict(item) for item in queued]
                            elif "queued_commands" in snapshot.metadata:
                                snapshot.metadata["queued_commands"] = []
                if sent >= self.config.max_commands_per_cycle:
                    break
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
        if not delegate_mode and not decision.has_commands:
            health = self._ensure_dialogue_health(snapshot, int(time.time()))
            should_fallback = True
            if health is not None and not bool(health.get("stuck")):
                should_fallback = False
                logger.debug("Skipping fallback for %s (dialogue healthy)", snapshot.branch)
            if should_fallback:
                fallback_payload = self._fallback_command_for(snapshot)
                if fallback_payload is not None:
                    dispatched = self._enqueue_command(fallback_payload, snapshot.branch)
                    if dispatched:
                        self.agent_service.update_status(
                            snapshot.branch,
                            metadata={
                                "orchestrator_last_command": [
                                    fallback_payload.get("text")
                                    or " ".join(fallback_payload.get("keys", []))
                                ],
                                "fallback_active": True,
                            },
                        )
                        snapshot.metadata["fallback_active"] = True
        elif snapshot.metadata.get("fallback_active"):
            snapshot.metadata.pop("fallback_active", None)
            self.agent_service.update_status(
                snapshot.branch,
                metadata={"fallback_active": False},
            )
        for payload in pending_payloads:
            self._record_pending_confirmation(snapshot.branch, payload)
        queued_view = self._pending_by_branch.get(snapshot.branch)
        if queued_view:
            snapshot.metadata["queued_commands"] = [dict(item) for item in queued_view]
        elif "queued_commands" in snapshot.metadata:
            snapshot.metadata["queued_commands"] = []
        if had_error:
            self.agent_service.update_status(
                snapshot.branch,
                metadata={
                    "orchestrator_error": None,
                    "orchestrator_error_payload": None,
                },
            )
            snapshot.metadata.pop("orchestrator_error", None)
            snapshot.metadata.pop("orchestrator_error_payload", None)
            had_error = False
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

    def _fallback_command_for(self, snapshot: AgentSnapshot) -> dict[str, Any] | None:
        branch = snapshot.branch
        sequence = self._fallback_sequences.get(branch)
        if not sequence:
            return None
        metadata = snapshot.metadata
        idx = metadata.get("fallback_index", 0)
        if not isinstance(idx, int) or idx < 0:
            idx = 0
        if idx >= len(sequence):
            return None
        entry = sequence[idx]
        text = entry.get("text") if isinstance(entry, dict) else str(entry)
        keys = entry.get("keys") if isinstance(entry, dict) else None
        enter_flag = entry.get("enter") if isinstance(entry, dict) and "enter" in entry else True
        if keys and isinstance(keys, list):
            normalized_keys = [str(item) for item in keys if str(item).strip()]
        else:
            normalized_keys = []
        instruction = text.strip() if isinstance(text, str) else ""
        if not instruction and not normalized_keys:
            return None
        payload = {
            "session": snapshot.session,
            "enter": enter_flag if isinstance(enter_flag, bool) else True,
            "sender": "orchestrator",
            "meta": {
                "risk_level": "low",
                "input_mode": "codex_dialogue",
                "fallback": True,
                "fallback_index": idx,
            },
        }
        if instruction:
            payload["text"] = instruction
        if normalized_keys:
            payload["keys"] = normalized_keys
            if "text" not in payload:
                # 默认在发送热键时不自动回车
                payload["enter"] = False
        metadata["fallback_index"] = idx + 1
        self.agent_service.update_status(
            branch,
            metadata={"fallback_index": idx + 1},
        )
        return payload

    def _dispatch_command(
        self,
        payload: dict[str, Any],
        branch: str | None,
        *,
        queued: bool,
    ) -> None:
        now = time.time()
        session = payload.get("session")
        meta = payload.setdefault("meta", {})
        input_mode = meta.get("input_mode")
        keys = payload.get("keys")
        codex_dialogue = isinstance(input_mode, str) and input_mode == "codex_dialogue"
        skip_instrumentation = codex_dialogue or bool(keys)

        command_id = None
        original_text = None
        text = payload.get("text")

        if isinstance(text, str) and text and not skip_instrumentation:
            hardened = self._harden_command_text(text)
            if hardened != text:
                payload["text"] = hardened
        if branch and not skip_instrumentation:
            command_id, original_text = self._ensure_command_metadata(payload)
            self._apply_command_instrumentation(payload, command_id, original_text)
        if self.config.dry_run:
            logger.info("[dry-run] skip dispatch: %s", payload.get("text"))
            metrics.record_command("dry_run")
        else:
            self.bus.append_command(payload)
            if session:
                self._session_busy_until[session] = now + self.config.session_cooldown_seconds
            metrics.record_command("dispatched")
        if branch and command_id and original_text:
            self._track_command_dispatch(
                branch,
                command_id=command_id,
                original_text=original_text,
                session=session,
                queued=queued,
                dispatched_at=now,
                dry_run=self.config.dry_run,
                risk_level=payload.get("meta", {}).get("risk_level"),
            )
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

    def _ensure_command_metadata(self, payload: dict[str, Any]) -> tuple[str, str]:
        meta = payload.setdefault("meta", {})
        command_id = meta.get("command_id")
        if not command_id:
            command_id = f"cmd-{uuid4().hex}"
            meta["command_id"] = command_id
        original_text = meta.get("original_text")
        if not original_text:
            original_text = str(payload.get("text") or "")
            meta["original_text"] = original_text
        return command_id, original_text

    def _apply_command_instrumentation(
        self,
        payload: dict[str, Any],
        command_id: str,
        original_text: str,
    ) -> None:
        meta = payload.setdefault("meta", {})
        if meta.get("instrumented"):
            return
        sentinel = COMMAND_RESULT_SENTINEL
        trailer = (
            f"__tmuxagent_status=$?; printf \"{sentinel} {command_id} %s\\n\" \"$__tmuxagent_status\""
        )
        normalized = original_text.rstrip()
        if not normalized:
            payload["text"] = original_text
        else:
            separator = "" if normalized.endswith(";") else ";"
            payload["text"] = f"{normalized}{separator} {trailer}"
        meta["instrumented"] = True

    def _harden_command_text(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return text
        timeout = getattr(self.config, "command_timeout_seconds", 0) or 0
        if timeout <= 0:
            return stripped
        lowered = stripped.lower()
        if "timeout " in lowered or lowered.startswith("timeout"):
            return stripped
        if "\n" in stripped or stripped.endswith("\\"):
            return stripped
        first_token = stripped.split(None, 1)[0].lower()
        if first_token in {"cd", "export", "alias", "set", "unset", "source"}:
            return stripped
        seconds = max(1, int(round(timeout)))
        return f"timeout {seconds}s {stripped}"

    def _normalize_keys(self, keys: tuple[str, ...], input_mode: str | None) -> list[str]:
        normalized: list[str] = []
        for key in keys:
            token = str(key).strip()
            if not token:
                continue
            if token.lower() in {"c-c", "ctrl+c", "ctrl-c"} and input_mode == "codex_dialogue":
                normalized.append("Escape")
            else:
                normalized.append(token)
        return normalized

    def _is_codex_dialogue_session(self, snapshot: AgentSnapshot) -> bool:
        session_name = snapshot.session or ""
        if session_name.startswith("agent-storyapp-") or session_name.startswith("agent-weather-"):
            return True
        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        template = metadata.get("template") if metadata else None
        return isinstance(template, str) and "storyapp" in template

    def _expand_dialogue_command(self, command: CommandSuggestion) -> list[CommandSuggestion]:
        if command.input_mode != "codex_dialogue":
            return [command]
        text = command.text or ""
        blocks = _CODE_BLOCK_RE.findall(text)
        expanded: list[CommandSuggestion] = []
        include_escape = False
        lowered = text.lower()
        if "esc" in lowered or "escape" in lowered or "按 esc" in text or "按下 esc" in text:
            include_escape = True
        if include_escape:
            expanded.append(
                CommandSuggestion(
                    text=None,
                    session=command.session,
                    enter=False,
                    cwd=command.cwd,
                    risk_level=command.risk_level,
                    notes=command.notes,
                    keys=("Escape",),
                    input_mode=command.input_mode,
                )
            )
        if not blocks:
            if include_escape:
                return expanded
            return [command]
        logger.debug(
            "Expanding dialogue command for %s with %d code blocks",
            command.session,
            len(blocks),
        )
        for block in blocks:
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.lower() == "echo done":
                    stripped = "DONE"
                expanded.append(
                    CommandSuggestion(
                        text=stripped,
                        session=command.session,
                        enter=True,
                        cwd=command.cwd,
                        risk_level=command.risk_level,
                        notes=command.notes,
                        input_mode=command.input_mode,
                    )
                )
        return expanded or [command]
        template_name = str(snapshot.record.template or "")
        if "storyapp" in template_name.lower() or "codex" in template_name.lower():
            return True
        meta_template = snapshot.metadata.get("template")
        if isinstance(meta_template, str) and "storyapp" in meta_template.lower():
            return True
        meta_input_mode = snapshot.metadata.get("input_mode")
        if isinstance(meta_input_mode, str) and meta_input_mode == "codex_dialogue":
            return True
        return False

    def _track_command_dispatch(
        self,
        branch: str,
        *,
        command_id: str,
        original_text: str,
        session: str | None,
        queued: bool,
        dispatched_at: float,
        dry_run: bool,
        risk_level: Any,
    ) -> None:
        session_row = self.state_store.get_agent_session(branch)
        metadata = dict((session_row or {}).get("metadata") or {})
        tracker: list[dict[str, Any]] = []
        raw_tracker = metadata.get("command_tracker")
        if isinstance(raw_tracker, list):
            for item in raw_tracker:
                if isinstance(item, dict) and item.get("command_id") != command_id:
                    tracker.append(dict(item))
        entry = {
            "command_id": command_id,
            "text": original_text,
            "session": session,
            "risk_level": risk_level,
            "queued": queued,
            "dispatched_at": dispatched_at,
            "status": "dry_run" if dry_run else "pending",
        }
        tracker.append(entry)
        tracker = tracker[-self._command_tracker_limit :]
        updates = {"command_tracker": tracker}
        self.agent_service.update_status(branch, metadata=updates)

    def _append_blocker(self, metadata: dict[str, Any], message: str) -> list[str]:
        blockers: list[str] = []
        raw_blockers = metadata.get("blockers")
        if isinstance(raw_blockers, list):
            for item in raw_blockers:
                text = str(item)
                if text:
                    blockers.append(text)
        if message not in blockers:
            blockers.append(message)
        return blockers[-10:]

    def _detect_and_handle_stall(self, snapshot: AgentSnapshot, now_ts: int) -> None:
        timeout = self.config.stall_timeout_seconds
        if timeout <= 0:
            return
        tracker = snapshot.metadata.get("command_tracker")
        pending: list[tuple[dict[str, Any], float]] = []
        if isinstance(tracker, list):
            for item in tracker:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "").lower()
                if status not in {"pending", "dry_run"}:
                    continue
                dispatched_at = item.get("dispatched_at")
                if isinstance(dispatched_at, (int, float)):
                    pending.append((item, float(dispatched_at)))
        if not pending:
            self._stall_counts.pop(snapshot.branch, None)
            return
        oldest_entry, dispatched_ts = min(pending, key=lambda pair: pair[1])
        if dispatched_ts <= 0 or (now_ts - dispatched_ts) < timeout:
            self._stall_counts.pop(snapshot.branch, None)
            return
        stall_count = self._stall_counts.get(snapshot.branch, 0) + 1
        self._stall_counts[snapshot.branch] = stall_count
        wait_seconds = int(now_ts - dispatched_ts)
        command_label = oldest_entry.get("text") or oldest_entry.get("command_id") or snapshot.branch
        message = f"命令 {command_label} 已等待 {wait_seconds}s 未完成，触发第 {stall_count} 次自检"
        blockers = self._append_blocker(snapshot.metadata, message)
        updates = {
            "blockers": blockers,
            "orchestrator_error": message,
            "orchestrator_stall_count": stall_count,
        }
        self.agent_service.update_status(snapshot.branch, metadata=updates)
        snapshot.metadata.update(updates)
        metrics.record_error(snapshot.branch)
        notify_every = max(1, int(self.config.stall_retries_before_notify or 1))
        if stall_count == 1 or stall_count % notify_every == 0:
            self.notifier.send(
                NotificationMessage(
                    title=f"{snapshot.branch} 命令卡顿",
                    body=message,
                    meta={"requires_attention": True, "severity": "warning"},
                )
            )
        self._last_command_at[snapshot.branch] = now_ts - self.config.cooldown_seconds
        for entry, _ in pending:
            session_name = entry.get("session")
            if isinstance(session_name, str):
                self._session_busy_until.pop(session_name, None)

    def _handle_repeated_failures(self, snapshot: AgentSnapshot, now_ts: int) -> None:
        threshold = self.config.failure_alert_threshold
        if threshold <= 0:
            return
        history = snapshot.metadata.get("command_history")
        consecutive_failures = 0
        if isinstance(history, list):
            for item in reversed(history):
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "").lower()
                if status == "failed":
                    consecutive_failures += 1
                elif status in {"succeeded", "success", "ok"}:
                    break
                else:
                    break
        if consecutive_failures >= threshold:
            if self._failure_alert_state.get(snapshot.branch) == consecutive_failures:
                return
            message = f"已连续 {consecutive_failures} 次命令失败，请人工介入"
            blockers = self._append_blocker(snapshot.metadata, message)
            updates = {
                "blockers": blockers,
                "orchestrator_error": message,
                "last_failure_alert": now_ts,
                "orchestrator_failure_streak": consecutive_failures,
            }
            self.agent_service.update_status(snapshot.branch, metadata=updates)
            snapshot.metadata.update(updates)
            self._failure_alert_state[snapshot.branch] = consecutive_failures
            metrics.record_error(snapshot.branch)
            self.notifier.send(
                NotificationMessage(
                    title=f"{snapshot.branch} 命令连续失败",
                    body=message,
                    meta={"requires_attention": True, "severity": "critical"},
                )
            )
            self._last_command_at[snapshot.branch] = now_ts - self.config.cooldown_seconds
        else:
            self._failure_alert_state.pop(snapshot.branch, None)

    def _update_next_actions(self, snapshot: AgentSnapshot) -> None:
        try:
            insights = build_recommendations(snapshot)
        except Exception:  # pragma: no cover - insights should not break orchestrator
            logger.debug("Failed to build decision insights", exc_info=True)
            return
        if not insights:
            return
        existing = snapshot.metadata.get("next_actions")
        if isinstance(existing, dict):
            if existing.get("recommendations") == insights.get("recommendations"):
                return
        self.agent_service.update_status(snapshot.branch, metadata={"next_actions": insights})
        snapshot.metadata["next_actions"] = insights
        recs = insights.get("recommendations") or []
        if not recs:
            return
        top = recs[0]
        priority = str(top.get("priority") or "").lower()
        requires_attention = priority in {"high", "critical"}
        if priority in {"", "low"}:
            return
        self.notifier.send(
            NotificationMessage(
                title=f"{snapshot.branch} 下一步建议",
                body=f"{top.get('title')}: {top.get('detail')}",
                meta={
                    "requires_attention": requires_attention,
                    "priority": priority,
                    "confidence": insights.get("confidence"),
                    "category": "insight",
                },
            )
        )
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

    def _record_error(
        self,
        snapshot: AgentSnapshot,
        message: str,
        *,
        kind: str = "unknown",
        raw_payload: str | None = None,
    ) -> None:
        branch = snapshot.branch
        now = time.time()
        state = self._error_state.get(branch)
        if state and state.get("message") == message and now - state.get("ts", 0.0) < ERROR_SUPPRESSION_WINDOW_SECONDS:
            state["count"] = int(state.get("count", 1)) + 1
        else:
            state = {"message": message, "count": 1}
            self._error_state[branch] = state
        state["ts"] = now

        metadata_update: dict[str, Any] = {"orchestrator_error": message}
        if raw_payload and kind in {"json_parse_error", "invalid_payload_type"}:
            metadata_update["orchestrator_error_payload"] = raw_payload[:500]
        self.agent_service.update_status(branch, metadata=metadata_update)

        metrics.record_error(branch)
        if kind in {"json_parse_error", "invalid_payload_type", "empty_output"}:
            metrics.record_json_parse_failure(branch, kind)
        if kind == "utf8_decode_error":
            metrics.record_utf8_decode_error(branch)

        if state["count"] > ERROR_REPEAT_NOTIFY_THRESHOLD:
            logger.debug(
                "Suppressing repeated orchestrator error for %s (kind=%s, count=%s)",
                branch,
                kind,
                state["count"],
            )
            return

        self.notifier.send(
            NotificationMessage(
                title="Orchestrator 异常",
                body=f"{branch}: {message}",
                meta={"requires_attention": True, "severity": "critical", "kind": kind},
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
