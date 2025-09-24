"""Main orchestration loop for the tmux agent."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

import re
import shlex
import subprocess

from .approvals import ApprovalManager
from .config import AgentConfig
from .config import HostConfig
from .config import PolicyConfig
from .notify import Notifier
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
    def __init__(
        self,
        agent_config: AgentConfig,
        policy: PolicyConfig,
        state_store: StateStore,
        notifier: Notifier,
        approval_manager: ApprovalManager,
        adapters: Iterable[HostRuntime],
        dry_run: bool = False,
    ) -> None:
        self.agent_config = agent_config
        self.policy = policy
        self.state_store = state_store
        self.notifier = notifier
        self.approval_manager = approval_manager
        self.adapters = list(adapters)
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
