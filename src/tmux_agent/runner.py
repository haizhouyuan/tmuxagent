"""Main orchestration loop for the tmux agent."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from typing import Iterable

import shlex
import subprocess

from .approvals import ApprovalManager
from .config import AgentConfig
from .config import HostConfig
from .config import PolicyConfig
from .constants import COMMAND_HISTORY_LIMIT
from .constants import COMMAND_RESULT_SENTINEL
from . import metrics
from .local_bus import LocalBus
from .notify import Notifier
from .notify import NotificationMessage
from .parser import parse_messages
from .policy import EvaluationOutcome
from .policy import PolicyEngine
from .state import StateStore
from .tmux import CaptureResult
from .tmux import PaneInfo
from .tmux import TmuxAdapter

logger = logging.getLogger(__name__)


@dataclass
class HostRuntime:
    host: HostConfig
    adapter: TmuxAdapter

    @property
    def name(self) -> str:
        return self.host.name


class Runner:
    _RESULT_PATTERN = re.compile(
        rf"{COMMAND_RESULT_SENTINEL}\s+(?P<command_id>\S+)\s+(?P<exit_code>-?\d+)"
    )

    def __init__(
        self,
        agent_config: AgentConfig,
        policy: PolicyConfig,
        state_store: StateStore,
        notifier: Notifier,
        approval_manager: ApprovalManager,
        adapters: Iterable[HostRuntime],
        bus: LocalBus | None = None,
        dry_run: bool = False,
    ) -> None:
        self.agent_config = agent_config
        self.policy = policy
        self.state_store = state_store
        self.notifier = notifier
        self.approval_manager = approval_manager
        self.adapters = list(adapters)
        self.bus = bus
        self.dry_run = dry_run
        self.policy_engine = PolicyEngine(policy, state_store, approval_manager)

    def _poll_interval_seconds(self) -> float:
        base = self.agent_config.poll_interval_ms
        host_intervals = [
            hr.host.tmux.poll_interval_ms
            for hr in self.adapters
            if hr.host.tmux.poll_interval_ms is not None
        ]
        effective_ms = min([base, *host_intervals]) if host_intervals else base
        return max(effective_ms, 100) / 1000.0  # clamp to avoid zero

    def run_forever(self) -> None:
        interval = self._poll_interval_seconds()
        logger.info("Starting tmux agent with poll interval %.2fs", interval)
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Received interrupt, shutting down")

    def run_once(self) -> None:
        self.state_store.expire_tokens()
        if self.bus:
            self._process_bus_commands()
        for runtime in self.adapters:
            panes = runtime.adapter.list_panes()
            for pane in panes:
                if not self._pane_matches(runtime.host, pane):
                    continue
                capture = runtime.adapter.capture_pane(
                    pane.pane_id,
                    runtime.host.tmux.capture_lines,
                )
                new_lines = self._diff_pane(runtime.name, pane, capture)
                if new_lines:
                    agent_record = self.state_store.find_agent_by_session(pane.session_name)
                    if agent_record:
                        self._process_command_results(agent_record, new_lines)
                        snippet = "\n".join(new_lines[-20:])
                        self.state_store.update_agent_runtime(
                            agent_record["branch"],
                            status="running",
                            last_output=snippet,
                        )
                messages = parse_messages(new_lines) if new_lines else []
                outcome = self.policy_engine.evaluate(runtime.name, pane, new_lines, messages)
                self._handle_outcome(runtime, pane, outcome)

    def _pane_matches(self, host: HostConfig, pane: PaneInfo) -> bool:
        session_filters = host.tmux.session_filters
        if session_filters and not any(
            re.search(pattern, pane.session_name) for pattern in session_filters
        ):
            return False
        if host.tmux.pane_name_patterns and not pane.matches_patterns(host.tmux.pane_name_patterns):
            return False
        return True

    def _diff_pane(self, host: str, pane: PaneInfo, capture: CaptureResult) -> list[str]:
        buffer_text = capture.content
        prev_offset = self.state_store.get_offset(host, pane.pane_id)
        if prev_offset > len(buffer_text):
            prev_offset = 0  # pane cleared or history truncated
        new_slice = buffer_text[prev_offset:]
        self.state_store.set_offset(host, pane.pane_id, len(buffer_text))
        if not new_slice:
            return []
        return new_slice.splitlines()

    def _process_bus_commands(self) -> None:
        offset = self.state_store.get_bus_offset('commands')
        commands, new_offset = self.bus.read_commands(offset) if self.bus else ([], offset)
        if not commands:
            return
        for payload in commands:
            try:
                self._execute_command(payload)
            except Exception as exc:  # pragma: no cover - defensive
                self._notify_error(f"命令执行失败: {payload}", exc)
        if self.bus and new_offset != offset:
            self.state_store.set_bus_offset('commands', new_offset)

    def _execute_command(self, payload: dict[str, Any]) -> None:
        text = (payload.get('text') or payload.get('command') or '').strip()
        if not text:
            return
        target_host = payload.get('host')
        session = payload.get('session')
        pane_id = payload.get('pane_id')
        enter = payload.get('enter', True)
        runtime: HostRuntime | None = None
        if target_host:
            for candidate in self.adapters:
                if candidate.name == target_host:
                    runtime = candidate
                    break
        if runtime is None and self.adapters:
            runtime = self.adapters[0]
        if runtime is None:
            raise RuntimeError('no tmux adapter available for command execution')

        pane_target = None
        if pane_id:
            pane_target = pane_id
        elif session:
            panes = runtime.adapter.panes_for_session(session)
            if panes:
                pane_target = panes[0].pane_id
        else:
            panes = runtime.adapter.list_panes()
            if panes:
                pane_target = panes[0].pane_id
        if not pane_target:
            raise RuntimeError(f"未找到可用窗格用于执行命令 (session={session!r})")

        if not self.dry_run:
            runtime.adapter.send_keys(pane_target, text, enter=enter)

        self.notifier.send(
            NotificationMessage(
                title='指令已注入',
                body=f"{payload.get('sender', 'local')} -> {session or pane_target}: {text}",
            )
        )

    def _process_command_results(self, agent_record: dict[str, Any], lines: list[str]) -> None:
        matches: list[tuple[str, int, str]] = []
        for line in lines:
            match = self._RESULT_PATTERN.search(line)
            if match:
                command_id = match.group('command_id')
                exit_code = int(match.group('exit_code'))
                matches.append((command_id, exit_code, line))
        if not matches:
            return
        branch = agent_record.get('branch')
        if not branch:
            return
        session_row = self.state_store.get_agent_session(branch)
        session_metadata = dict((session_row or {}).get('metadata') or {})
        tracker: list[dict[str, Any]] = []
        raw_tracker = session_metadata.get('command_tracker')
        if isinstance(raw_tracker, list):
            for item in raw_tracker:
                if isinstance(item, dict):
                    tracker.append(dict(item))
        history: list[dict[str, Any]] = []
        raw_history = session_metadata.get('command_history')
        if isinstance(raw_history, list):
            for item in raw_history:
                if isinstance(item, dict):
                    history.append(dict(item))
        last_result: dict[str, Any] | None = None
        now = int(time.time())
        for command_id, exit_code, line in matches:
            status = 'succeeded' if exit_code == 0 else 'failed'
            tracker_entry = None
            for item in tracker:
                if item.get('command_id') == command_id:
                    tracker_entry = item
                    break
            if tracker_entry is None:
                tracker_entry = {
                    'command_id': command_id,
                    'text': None,
                    'session': agent_record.get('session_name'),
                    'risk_level': None,
                    'dispatched_at': None,
                }
                tracker.append(tracker_entry)
            tracker_entry['status'] = status
            tracker_entry['exit_code'] = exit_code
            tracker_entry['completed_at'] = now
            tracker_entry['result_marker'] = line
            history.append(
                {
                    'command_id': command_id,
                    'text': tracker_entry.get('text'),
                    'exit_code': exit_code,
                    'status': status,
                    'completed_at': now,
                }
            )
            last_result = {
                'command_id': command_id,
                'exit_code': exit_code,
                'status': status,
                'completed_at': now,
            }
            if exit_code == 0:
                metrics.record_command_success(branch)
            else:
                metrics.record_command_failure(branch)
        tracker = tracker[-COMMAND_HISTORY_LIMIT:]
        history = history[-COMMAND_HISTORY_LIMIT:]
        updates: dict[str, Any] = {
            'command_tracker': tracker,
            'command_history': history,
        }
        if last_result is not None:
            updates['last_command_result'] = last_result
        self.state_store.update_agent_runtime(branch, metadata=updates)
        merged_metadata = dict(session_metadata)
        merged_metadata.update(updates)
        agent_record['metadata'] = merged_metadata

    def _notify_error(self, context: str, exc: Exception) -> None:
        try:
            self.notifier.send(
                NotificationMessage(
                    title='命令处理异常',
                    body=f"{context}\n{exc}",
                )
            )
        except Exception:  # pragma: no cover - avoid cascading failures
            pass

    def _handle_outcome(self, runtime: HostRuntime, pane: PaneInfo, outcome: EvaluationOutcome) -> None:
        for request in outcome.approvals:
            logger.info(
                "Approval required for %s/%s on %s -> %s",
                request.pane_id,
                request.stage,
                request.host,
                request.file_path,
            )
        for note in outcome.notifications:
            try:
                self.notifier.send(note)
            except Exception as exc:  # pragma: no cover - notification failures are non-critical
                logger.error("Failed to send notification: %s", exc)
        for action in outcome.actions:
            if action.host != runtime.name:
                logger.debug(
                    "Skipping action for host %s while processing %s", action.host, runtime.name
                )
                continue
            logger.info(
                "Action %s (%s) on %s/%s",
                action.command,
                action.kind,
                runtime.name,
                action.pane_id,
            )
            if self.dry_run:
                continue
            try:
                if action.kind == "send_keys":
                    runtime.adapter.send_keys(action.pane_id, action.command, enter=action.enter)
                elif action.kind == "shell":
                    self._execute_shell_action(runtime, action)
                else:  # pragma: no cover - defensive branch
                    logger.error("Unknown action kind %s", action.kind)
            except Exception as exc:  # pragma: no cover
                logger.error(
                    "Failed to execute action %s for pane %s on %s: %s",
                    action.kind,
                    action.pane_id,
                    runtime.name,
                    exc,
                )

    def _execute_shell_action(self, runtime: HostRuntime, action: Action) -> None:
        host_cfg = runtime.host
        ssh_cfg = host_cfg.ssh
        if ssh_cfg:
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={ssh_cfg.timeout}",
                "-p",
                str(ssh_cfg.port),
            ]
            if ssh_cfg.key_path:
                ssh_cmd += ["-i", ssh_cfg.key_path]
            target = f"{ssh_cfg.user}@{ssh_cfg.host}" if ssh_cfg.user else ssh_cfg.host
            remote_command = f"bash -lc {shlex.quote(action.command)}"
            ssh_cmd.extend([target, remote_command])
            subprocess.run(ssh_cmd, check=True)
        else:
            subprocess.run(["bash", "-lc", action.command], check=True)
