"""Policy loading and evaluation against tmux pane updates."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from .approvals import ApprovalManager
from .approvals import ApprovalRequest
from .config import ActionSpec
from .config import PipelineConfig
from .config import PolicyConfig
from .config import StageConfig
from .config import TriggerSpec
from .notify import NotificationMessage
from .parser import ParsedMessage
from .state import StageState
from .state import StageStatus
from .state import StateStore
from .tmux import PaneInfo


@dataclass
class CompiledTrigger:
    log_regex: Optional[re.Pattern[str]] = None
    message_type: Optional[str] = None
    after_stage_success: Optional[str] = None

    @classmethod
    def from_spec(cls, spec: TriggerSpec) -> "CompiledTrigger":
        return cls(
            log_regex=re.compile(spec.log_regex) if spec.log_regex else None,
            message_type=spec.message_type,
            after_stage_success=spec.after_stage_success,
        )


@dataclass
class CompiledStage:
    name: str
    triggers: list[CompiledTrigger]
    actions_on_start: list[ActionSpec]
    success_when: list[CompiledTrigger]
    fail_when: list[CompiledTrigger]
    require_approval: bool
    retry_max: int
    ask_human_prompt: Optional[str]
    escalate_code: Optional[str]


@dataclass
class CompiledPipeline:
    name: str
    window_matchers: list[re.Pattern[str]]
    pane_matchers: list[re.Pattern[str]]
    stages: list[CompiledStage]

    def matches(self, pane: PaneInfo) -> bool:
        window_ok = True
        if self.window_matchers:
            window_ok = any(pane.window_name and pattern.search(pane.window_name) for pattern in self.window_matchers)
        pane_ok = True
        if self.pane_matchers:
            pane_ok = any(pane.pane_title and pattern.search(pane.pane_title) for pattern in self.pane_matchers)
        return window_ok and pane_ok


@dataclass
class Action:
    pane_id: str
    command: str
    enter: bool = True


@dataclass
class EvaluationOutcome:
    actions: list[Action]
    notifications: list[NotificationMessage]
    approvals: list[ApprovalRequest]


class PolicyEngine:
    def __init__(
        self,
        policy: PolicyConfig,
        store: StateStore,
        approvals: ApprovalManager,
    ) -> None:
        self.policy = policy
        self.store = store
        self.approvals = approvals
        self.pipelines = [self._compile_pipeline(p) for p in policy.pipelines]

    def _compile_pipeline(self, pipeline: PipelineConfig) -> CompiledPipeline:
        window_patterns = [re.compile(m.window_name) for m in pipeline.match.get("any_of", []) if m.window_name]
        pane_patterns = [re.compile(m.pane_title) for m in pipeline.match.get("any_of", []) if m.pane_title]
        stages = [self._compile_stage(stage) for stage in pipeline.stages]
        return CompiledPipeline(
            name=pipeline.name,
            window_matchers=window_patterns,
            pane_matchers=pane_patterns,
            stages=stages,
        )

    def _compile_stage(self, stage: StageConfig) -> CompiledStage:
        def compile_block(block: Optional[dict[str, list[TriggerSpec]]]) -> list[CompiledTrigger]:
            if not block:
                return []
            triggers = []
            for specs in block.values():
                triggers.extend(CompiledTrigger.from_spec(spec) for spec in specs)
            return triggers

        retry_max = 0
        ask_prompt: Optional[str] = None
        escalate_code: Optional[str] = None
        for entry in stage.on_fail:
            if "retry" in entry:
                retry = entry["retry"] or {}
                retry_max = int(retry.get("max", 0))
            if "ask_human" in entry:
                ask_prompt = entry["ask_human"]
            if "escalate" in entry:
                escalate_code = entry["escalate"]
            if "if_still_fail" in entry:
                payload = entry["if_still_fail"]
                if isinstance(payload, dict) and "ask_human" in payload:
                    ask_prompt = payload["ask_human"]
                if isinstance(payload, dict) and "escalate" in payload:
                    escalate_code = payload["escalate"]
        return CompiledStage(
            name=stage.name,
            triggers=compile_block(stage.triggers),
            actions_on_start=stage.actions_on_start,
            success_when=compile_block(stage.success_when),
            fail_when=compile_block(stage.fail_when),
            require_approval=stage.require_approval,
            retry_max=retry_max,
            ask_human_prompt=ask_prompt,
            escalate_code=escalate_code,
        )

    def evaluate(
        self,
        pane: PaneInfo,
        new_lines: list[str],
        messages: list[ParsedMessage],
    ) -> EvaluationOutcome:
        actions: list[Action] = []
        notifications: list[NotificationMessage] = []
        approvals_needed: list[ApprovalRequest] = []

        for pipeline in self.pipelines:
            if not pipeline.matches(pane):
                continue
            actions, notifications, approvals_needed = self._evaluate_pipeline(
                pipeline, pane, new_lines, messages, actions, notifications, approvals_needed
            )
        return EvaluationOutcome(actions=actions, notifications=notifications, approvals=approvals_needed)

    def _evaluate_pipeline(
        self,
        pipeline: CompiledPipeline,
        pane: PaneInfo,
        new_lines: list[str],
        messages: list[ParsedMessage],
        actions: list[Action],
        notifications: list[NotificationMessage],
        approvals_needed: list[ApprovalRequest],
    ) -> tuple[list[Action], list[NotificationMessage], list[ApprovalRequest]]:
        for stage in pipeline.stages:
            state = self.store.load_stage_state(pane.pane_id, pipeline.name, stage.name)
            if state.status == StageStatus.COMPLETED:
                continue
            if state.status == StageStatus.FAILED:
                # Skip until manual reset.
                continue

            now = int(time.time())

            if state.status in {StageStatus.IDLE, StageStatus.WAITING_TRIGGER}:
                if self._stage_ready(pipeline, stage, pane, new_lines, messages):
                    if stage.require_approval:
                        state.status = StageStatus.WAITING_APPROVAL
                        state.data = {"waiting_since": now, "notified": True}
                        request = self.approvals.ensure_request(pane.pane_id, stage.name)
                        approvals_needed.append(request)
                        notifications.append(
                            NotificationMessage(
                                title=f"Approval required: {pipeline.name}/{stage.name}",
                                body=self._format_approval_request(request, stage.ask_human_prompt),
                            )
                        )
                    else:
                        started = state.data or {}
                        if not started.get("action_sent"):
                            actions.extend(self._stage_actions(pane, stage))
                            started["action_sent"] = True
                            state.data = started
                        state.status = StageStatus.RUNNING
                else:
                    state.status = StageStatus.WAITING_TRIGGER

            elif state.status == StageStatus.WAITING_APPROVAL:
                decision = self.approvals.poll_file_decision(pane.pane_id, stage.name)
                if decision == "approve":
                    actions.extend(self._stage_actions(pane, stage))
                    state.status = StageStatus.RUNNING
                    state.data = {"action_sent": True, "approved_at": now}
                    self.approvals.store.delete_approval_token(pane.pane_id, stage.name)
                elif decision == "reject":
                    state.status = StageStatus.FAILED
                    self.approvals.store.delete_approval_token(pane.pane_id, stage.name)
                else:
                    if not state.data:
                        state.data = {"waiting_since": now}
                    request = self.approvals.ensure_request(pane.pane_id, stage.name)
                    approvals_needed.append(request)
                    if not state.data.get("notified") and stage.ask_human_prompt:
                        notifications.append(
                            NotificationMessage(
                                title=f"Manual decision needed: {pipeline.name}/{stage.name}",
                                body=self._format_approval_request(request, stage.ask_human_prompt),
                            )
                        )
                        state.data["notified"] = True

            elif state.status == StageStatus.RUNNING:
                if self._conditions_met(stage.success_when, pipeline, pane, new_lines, messages):
                    state.status = StageStatus.COMPLETED
                    state.data = {"completed_at": now}
                elif self._conditions_met(stage.fail_when, pipeline, pane, new_lines, messages):
                    if state.retries < stage.retry_max:
                        state.retries += 1
                        state.status = StageStatus.RUNNING
                        actions.extend(self._stage_actions(pane, stage))
                        state.data = {"retry": state.retries, "retry_at": now}
                    elif stage.ask_human_prompt:
                        state.status = StageStatus.WAITING_APPROVAL
                        state.data = {"waiting_since": now, "prompt": stage.ask_human_prompt, "notified": True}
                        request = self.approvals.ensure_request(pane.pane_id, stage.name)
                        approvals_needed.append(request)
                        notifications.append(
                            NotificationMessage(
                                title=f"Manual decision needed: {pipeline.name}/{stage.name}",
                                body=self._format_approval_request(request, stage.ask_human_prompt),
                            )
                        )
                    else:
                        state.status = StageStatus.FAILED
                        state.data = {"failed_at": now, "reason": "fail_condition"}

            self.store.save_stage_state(state)
            # Stop evaluating later stages until this one completes
            if state.status in {StageStatus.IDLE, StageStatus.WAITING_TRIGGER, StageStatus.WAITING_APPROVAL, StageStatus.RUNNING}:
                break
        return actions, notifications, approvals_needed

    def _stage_ready(
        self,
        pipeline: CompiledPipeline,
        stage: CompiledStage,
        pane: PaneInfo,
        new_lines: list[str],
        messages: list[ParsedMessage],
    ) -> bool:
        if not stage.triggers:
            return True
        if self._conditions_met(stage.triggers, pipeline, pane, new_lines, messages):
            return True
        return False

    def _conditions_met(
        self,
        triggers: list[CompiledTrigger],
        pipeline: CompiledPipeline,
        pane: PaneInfo,
        new_lines: list[str],
        messages: list[ParsedMessage],
    ) -> bool:
        if not triggers:
            return False
        for trigger in triggers:
            if trigger.log_regex and any(trigger.log_regex.search(line) for line in new_lines):
                return True
            if trigger.message_type and any(msg.kind == trigger.message_type for msg in messages):
                return True
            if trigger.after_stage_success:
                prev_state = self.store.load_stage_state(pane.pane_id, pipeline.name, trigger.after_stage_success)
                if prev_state.status == StageStatus.COMPLETED:
                    return True
        return False

    def _stage_actions(self, pane: PaneInfo, stage: CompiledStage) -> list[Action]:
        actions: list[Action] = []
        for action in stage.actions_on_start:
            if action.send_keys:
                actions.append(Action(pane_id=pane.pane_id, command=action.send_keys, enter=True))
            if action.shell:
                actions.append(Action(pane_id=pane.pane_id, command=action.shell, enter=False))
        return actions

    def _format_approval_request(self, request: ApprovalRequest, prompt: Optional[str]) -> str:
        body = [prompt or "Approval required"]
        body.append(f"File: `{request.file_path}`")
        if request.approve_url and request.reject_url:
            body.append(f"[Approve]({request.approve_url}) | [Reject]({request.reject_url})")
        return "\n".join(body)
