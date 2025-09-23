"""Main orchestration loop for the tmux agent."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

import re

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

    def run_forever(self) -> None:
        interval = self.agent_config.poll_interval_ms / 1000.0
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
                capture = runtime.adapter.capture_pane(pane.pane_id, runtime.host.capture_lines)
                new_lines = self._diff_pane(pane, capture)
                messages = parse_messages(new_lines) if new_lines else []
                outcome = self.policy_engine.evaluate(pane, new_lines, messages)
                self._handle_outcome(runtime.adapter, pane, outcome)

    def _pane_matches(self, host: HostConfig, pane: PaneInfo) -> bool:
        # Filter by session names if provided
        if host.session_filters and not any(re.search(pattern, pane.session_name) for pattern in host.session_filters):
            return False
        if host.pane_name_patterns and not pane.matches_patterns(host.pane_name_patterns):
            return False
        return True

    def _diff_pane(self, pane: PaneInfo, capture: CaptureResult) -> list[str]:
        buffer_text = capture.content
        prev_offset = self.state_store.get_offset(pane.pane_id)
        if prev_offset > len(buffer_text):
            prev_offset = 0  # pane cleared or history truncated
        new_slice = buffer_text[prev_offset:]
        self.state_store.set_offset(pane.pane_id, len(buffer_text))
        if not new_slice:
            return []
        return new_slice.splitlines()

    def _handle_outcome(self, adapter: TmuxAdapter, pane: PaneInfo, outcome: EvaluationOutcome) -> None:
        for request in outcome.approvals:
            logger.info(
                "Approval required for %s/%s -> %s",
                pane.pane_id,
                request.stage,
                request.file_path,
            )
        for note in outcome.notifications:
            try:
                self.notifier.send(note)
            except Exception as exc:  # pragma: no cover - notification failures are non-critical
                logger.error("Failed to send notification: %s", exc)
        for action in outcome.actions:
            logger.info("Action %s on pane %s", action.command, pane.pane_id)
            if self.dry_run:
                continue
            try:
                adapter.send_keys(action.pane_id, action.command, enter=action.enter)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to send keys to pane %s: %s", pane.pane_id, exc)
