from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tmux_agent.agents.service import AgentService
from tmux_agent.local_bus import LocalBus
from tmux_agent.notify import Notifier
from tmux_agent.orchestrator.codex_client import CommandSuggestion
from tmux_agent.orchestrator.codex_client import OrchestratorDecision
from tmux_agent.orchestrator.codex_client import FakeCodexClient
from tmux_agent.orchestrator.config import CodexConfig
from tmux_agent.orchestrator.config import OrchestratorConfig
from tmux_agent.orchestrator.config import PromptConfig
from tmux_agent.orchestrator.config import TaskSpec
from tmux_agent.orchestrator.replay import summarize_events
from tmux_agent.orchestrator.service import OrchestratorService
from tmux_agent.state import StateStore
from tmux_agent.constants import COMMAND_RESULT_SENTINEL


def _append_agent(store: StateStore, branch: str, session: str, log_path: Path) -> None:
    store.upsert_agent_session(
        branch=branch,
        worktree_path=str(log_path.parent),
        session_name=session,
        model="gpt-5-codex",
        template="orchestrator",
        description="demo task",
        last_prompt="initial",
        status="idle",
        log_path=str(log_path),
        metadata={"priority": "high"},
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_orchestrator_injects_commands(tmp_path):
    log_path = tmp_path / "logs" / "agent-story.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    _append_agent(store, "storyapp/feature-x", "agent-storyapp-feature-x", log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("{{ \"summary\": \"auto-summary\" }}", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=10.0,
        max_commands_per_cycle=2,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=summary_path),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        notify_only_on_confirmation=True,
        command_timeout_seconds=0,
    )

    decisions = [
        OrchestratorDecision(
            summary="ok",
            notify="please confirm",
            requires_confirmation=True,
            phase="executing",
            blockers=("waiting on secrets",),
            commands=(
                CommandSuggestion(text="echo hello", risk_level="high", cwd="/tmp/work"),
                CommandSuggestion(text="echo skip"),
            ),
        )
    ]

    codex = FakeCodexClient(decisions)
    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()

        commands = _read_jsonl(bus.commands_path)
        assert commands == [], "commands should wait for manual confirmation"

        session = store.get_agent_session("storyapp/feature-x")
        assert session is not None
        metadata = session["metadata"]
        assert metadata.get("orchestrator_summary") == "ok"
        assert metadata.get("phase") == "executing"
        assert metadata.get("blockers") == ["waiting on secrets"]
        assert metadata.get("orchestrator_last_command") == ["echo hello", "echo skip"]
        assert "orchestrator_heartbeat" in metadata
        assert metadata.get("history_summaries") == ["ok"]
        pending = metadata.get("pending_confirmation")
        assert isinstance(pending, list) and len(pending) == 2

        notifications = _read_jsonl(bus.notifications_path)
        assert notifications, "notification should be written"
        assert any(item.get("body") == "please confirm" for item in notifications)
        assert any(item.get("meta", {}).get("requires_attention") for item in notifications)

        codex._decisions = [
            OrchestratorDecision(
                summary="second",
                notify=None,
                requires_confirmation=False,
                phase="verifying",
                blockers=(),
                commands=(CommandSuggestion(text="echo second"),),
            )
        ]

        bus.append_confirmation({"branch": "storyapp/feature-x", "action": "approve"})
        service.run_once()
        commands_after = _read_jsonl(bus.commands_path)
        assert len(commands_after) == 2
        assert commands_after[0]["text"].startswith("echo hello")
        assert commands_after[1]["text"].startswith("echo skip")
        assert COMMAND_RESULT_SENTINEL in commands_after[0]["text"]
        assert COMMAND_RESULT_SENTINEL in commands_after[1]["text"]
        assert commands_after[0]["meta"].get("command_id")

        session = store.get_agent_session("storyapp/feature-x")
        metadata = session["metadata"]
        assert metadata.get("pending_confirmation") == []
        assert metadata.get("phase") == "executing"
        tracker = metadata.get("command_tracker")
        assert isinstance(tracker, list) and tracker, "command tracker should capture executed commands"
        assert tracker[-1].get("status") == "pending"

        service._last_command_at["storyapp/feature-x"] = time.time() - 3600
        service._session_busy_until.clear()
        service.run_once()
        commands_final = _read_jsonl(bus.commands_path)
        assert commands_final[-1]["text"].startswith("echo second")
        assert COMMAND_RESULT_SENTINEL in commands_final[-1]["text"]

        session = store.get_agent_session("storyapp/feature-x")
        metadata = session["metadata"]
        assert metadata.get("phase") == "verifying"
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_respects_dependencies(tmp_path):
    log_path = tmp_path / "logs" / "dep.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("dep line\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    base_branch = "storyapp/base"
    _append_agent(store, base_branch, "agent-storyapp-base", log_path)
    store.upsert_agent_session(
        branch=base_branch,
        worktree_path=str(log_path.parent),
        session_name="agent-storyapp-base",
        model="gpt-5-codex",
        template="orchestrator",
        description="base",
        last_prompt="",
        status="idle",
        log_path=str(log_path),
        metadata={"phase": "planning"},
    )

    target_log = tmp_path / "logs" / "target.log"
    target_log.write_text("target line\n", encoding="utf-8")
    _append_agent(store, "storyapp/feature-y", "agent-storyapp-feature-y", target_log)
    store.upsert_agent_session(
        branch="storyapp/feature-y",
        worktree_path=str(target_log.parent),
        session_name="agent-storyapp-feature-y",
        model="gpt-5-codex",
        template="orchestrator",
        description="y",
        last_prompt="",
        status="idle",
        log_path=str(target_log),
        metadata={"depends_on": [base_branch]},
    )

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=10.0,
        max_commands_per_cycle=2,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        phase_prompts={},
        default_phase="planning",
        completion_phase="done",
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        notify_only_on_confirmation=True,
        command_timeout_seconds=0,
    )

    decisions = [
        OrchestratorDecision(
            summary="base ok",
            notify=None,
            requires_confirmation=False,
            phase="planning",
            blockers=(),
            commands=(),
        ),
        OrchestratorDecision(
            summary="wait",
            notify=None,
            requires_confirmation=False,
            phase="executing",
            blockers=(),
            commands=(CommandSuggestion(text="echo dependent"),),
        ),
    ]

    codex = FakeCodexClient(decisions)
    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        commands = _read_jsonl(bus.commands_path)
        assert commands == []

        session = store.get_agent_session("storyapp/feature-y")
        assert session["metadata"].get("blockers") == [base_branch]

        store.upsert_agent_session(
            branch=base_branch,
            worktree_path=str(log_path.parent),
            session_name="agent-storyapp-base",
            model="gpt-5-codex",
            template="orchestrator",
            description="base",
            last_prompt="",
            status="idle",
            log_path=str(log_path),
            metadata={"phase": "done"},
        )

        service.run_once()
        commands_after = _read_jsonl(bus.commands_path)
        assert commands_after[-1]["text"].startswith("echo dependent")
        assert COMMAND_RESULT_SENTINEL in commands_after[-1]["text"]
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_detects_stall(tmp_path):
    log_path = tmp_path / "logs" / "agent-stall.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("boot\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/stall"
    session_name = "agent-storyapp-stall"
    _append_agent(store, branch, session_name, log_path)
    now = time.time()
    store.upsert_agent_session(
        branch=branch,
        worktree_path=str(log_path.parent),
        session_name=session_name,
        model="gpt-5-codex",
        template="orchestrator",
        description="stall",
        last_prompt="",
        status="idle",
        log_path=str(log_path),
        metadata={
            "command_tracker": [
                {
                    "command_id": "cmd-old",
                    "text": "echo stuck",
                    "status": "pending",
                    "dispatched_at": now - 600,
                    "session": session_name,
                }
            ]
        },
    )

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        stall_timeout_seconds=1.0,
        stall_retries_before_notify=1,
        failure_alert_threshold=3,
        command_timeout_seconds=0,
    )

    decisions = [
        OrchestratorDecision(
            summary=None,
            notify=None,
            requires_confirmation=False,
            phase=None,
            blockers=(),
            commands=(),
        )
    ]

    codex = FakeCodexClient(decisions)
    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        session = store.get_agent_session(branch)
        metadata = session["metadata"]
        assert metadata.get("orchestrator_stall_count") == 1
        blockers = metadata.get("blockers") or []
        assert any("echo stuck" in blocker for blocker in blockers)
        notifications = _read_jsonl(bus.notifications_path)
        assert notifications, "stall detection should emit notification"
        assert any("命令卡顿" in item.get("title", "") for item in notifications)
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_flags_repeated_failures(tmp_path):
    log_path = tmp_path / "logs" / "agent-fail.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("boot\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/failure"
    session_name = "agent-storyapp-failure"
    _append_agent(store, branch, session_name, log_path)
    store.upsert_agent_session(
        branch=branch,
        worktree_path=str(log_path.parent),
        session_name=session_name,
        model="gpt-5-codex",
        template="orchestrator",
        description="failure",
        last_prompt="",
        status="idle",
        log_path=str(log_path),
        metadata={
            "command_history": [
                {"command_id": "cmd-a", "status": "failed", "exit_code": 1},
                {"command_id": "cmd-b", "status": "failed", "exit_code": 1},
            ]
        },
    )

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        stall_timeout_seconds=999,
        failure_alert_threshold=2,
        command_timeout_seconds=0,
    )

    codex = FakeCodexClient(
        [
            OrchestratorDecision(
                summary=None,
                notify=None,
                requires_confirmation=False,
                phase=None,
                blockers=(),
                commands=(),
            )
        ]
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        session = store.get_agent_session(branch)
        metadata = session["metadata"]
        assert metadata.get("orchestrator_failure_streak") == 2
        blockers = metadata.get("blockers") or []
        assert any("连续" in blocker for blocker in blockers)
        notifications = _read_jsonl(bus.notifications_path)
        assert notifications
        assert any("命令连续失败" in item.get("title", "") for item in notifications)
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_updates_next_actions(tmp_path):
    log_path = tmp_path / "logs" / "agent-insight.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("boot\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/insight"
    session_name = "agent-storyapp-insight"
    _append_agent(store, branch, session_name, log_path)
    now = time.time()
    store.upsert_agent_session(
        branch=branch,
        worktree_path=str(log_path.parent),
        session_name=session_name,
        model="gpt-5-codex",
        template="orchestrator",
        description="insight",
        last_prompt="",
        status="idle",
        log_path=str(log_path),
        metadata={
            "phase": "planning",
            "phase_plan": ["planning", "executing", "verifying"],
            "phase_history": ["planning"],
            "blockers": ["等待依赖"],
            "command_tracker": [
                {
                    "command_id": "cmd-pending",
                    "text": "npm test",
                    "status": "pending",
                    "dispatched_at": now - 120,
                    "session": session_name,
                }
            ],
            "command_history": [
                {"command_id": "cmd-last", "status": "failed", "exit_code": 1, "text": "npm lint"}
            ],
        },
    )

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        stall_timeout_seconds=999,
        failure_alert_threshold=5,
        command_timeout_seconds=0,
    )

    codex = FakeCodexClient(
        [
            OrchestratorDecision(
                summary=None,
                notify=None,
                requires_confirmation=False,
                phase="planning",
                blockers=(),
                commands=(),
            )
        ]
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        session = store.get_agent_session(branch)
        metadata = session["metadata"]
        next_actions = metadata.get("next_actions")
        assert isinstance(next_actions, dict)
        recs = next_actions.get("recommendations") or []
        assert recs, "should produce recommendations"
        titles = [item.get("title") for item in recs]
        assert "解除 blocker" in titles
        notifications = _read_jsonl(bus.notifications_path)
        assert any("下一步建议" in item.get("title", "") for item in notifications)
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_decomposes_requirements(tmp_path):
    log_path = tmp_path / "logs" / "agent-doc.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("boot\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/doc"
    session_name = "agent-storyapp-doc"
    _append_agent(store, branch, session_name, log_path)
    store.upsert_agent_session(
        branch=branch,
        worktree_path=str(log_path.parent),
        session_name=session_name,
        model="gpt-5-codex",
        template="orchestrator",
        description="doc",
        last_prompt="",
        status="idle",
        log_path=str(log_path),
        metadata={"requirements_doc": "docs/weather_bot_end_to_end_plan.md"},
    )

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        command_timeout_seconds=0,
    )

    codex = FakeCodexClient(
        [
            OrchestratorDecision(
                summary=None,
                notify=None,
                requires_confirmation=False,
                phase="planning",
                blockers=(),
                commands=(),
            )
        ]
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        session = store.get_agent_session(branch)
        metadata = session["metadata"]
        decomposition = metadata.get("task_decomposition")
        assert isinstance(decomposition, list) and decomposition, "task decomposition should be populated"
        first_step = decomposition[0]
        assert "description" in first_step
        meta_info = metadata.get("task_decomposition_meta")
        assert isinstance(meta_info, dict) and meta_info.get("source")
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_applies_task_plan(tmp_path):
    log_path = tmp_path / "logs" / "plan.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("planning\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/plan"
    _append_agent(store, branch, "agent-storyapp-plan", log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=5.0,
        session_cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        tasks=[
            TaskSpec(
                branch=branch,
                title="功能联调",
                depends_on=["storyapp/base"],
                responsible="lily",
                phases=["planning", "executing", "verifying", "done"],
                tags=["high-priority"],
            )
        ],
        command_timeout_seconds=0,
    )

    codex = FakeCodexClient(
        [
            OrchestratorDecision(
                summary=None,
                notify=None,
                requires_confirmation=False,
                phase="planning",
                blockers=(),
                commands=(),
            )
        ]
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=config,
    )

    try:
        service.run_once()
        session = store.get_agent_session(branch)
        assert session is not None
        metadata = session["metadata"]
        assert metadata.get("depends_on") == ["storyapp/base"]
        assert metadata.get("responsible") == "lily"
        assert metadata.get("phase_plan") == ["planning", "executing", "verifying", "done"]
        assert metadata.get("orchestrator_task") == "功能联调"
        assert metadata.get("tags") == ["high-priority"]
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_respects_session_cooldown_queue(tmp_path):
    log_path = tmp_path / "logs" / "queue.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("queue\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/queue"
    session_name = "agent-storyapp-queue"
    _append_agent(store, branch, session_name, log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=1.0,
        session_cooldown_seconds=30.0,
        max_commands_per_cycle=5,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        command_timeout_seconds=0,
    )

    decision = OrchestratorDecision(
        summary=None,
        notify=None,
        requires_confirmation=False,
        phase="executing",
        blockers=(),
        commands=(
            CommandSuggestion(text="echo first"),
            CommandSuggestion(text="echo second"),
        ),
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=FakeCodexClient([decision]),
        config=config,
    )

    try:
        service.run_once()
        commands = _read_jsonl(bus.commands_path)
        assert len(commands) == 1
        assert commands[0]["text"].startswith("echo first")
        assert COMMAND_RESULT_SENTINEL in commands[0]["text"]

        session = store.get_agent_session(branch)
        assert session is not None
        metadata = session["metadata"]
        queued = metadata.get("queued_commands")
        assert queued and queued[0]["text"].startswith("echo second")

        service._session_busy_until.clear()
        service._flush_queued_commands()

        commands_after = _read_jsonl(bus.commands_path)
        assert len(commands_after) == 2
        assert commands_after[-1]["text"].startswith("echo second")
        assert COMMAND_RESULT_SENTINEL in commands_after[-1]["text"]

        session = store.get_agent_session(branch)
        assert session["metadata"].get("queued_commands") == []
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_dry_run_skips_dispatch(tmp_path):
    log_path = tmp_path / "logs" / "dry.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("dry-run\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/dry"
    _append_agent(store, branch, "agent-storyapp-dry", log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=1.0,
        session_cooldown_seconds=5.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        dry_run=True,
        command_timeout_seconds=0,
    )

    decision = OrchestratorDecision(
        summary=None,
        notify=None,
        requires_confirmation=False,
        phase="executing",
        blockers=(),
        commands=(CommandSuggestion(text="echo dry"),),
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=FakeCodexClient([decision]),
        config=config,
    )

    try:
        service.run_once()
        commands = _read_jsonl(bus.commands_path)
        assert commands == []
        session = store.get_agent_session(branch)
        assert session is not None
        assert session["metadata"].get("orchestrator_last_command") == ["echo dry"]
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_applies_timeout_wrapper(tmp_path):
    log_path = tmp_path / "logs" / "timeout.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("boot\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/timeout"
    session_name = "agent-storyapp-timeout"
    _append_agent(store, branch, session_name, log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=1.0,
        session_cooldown_seconds=1.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None),
        command_timeout_seconds=30,
    )

    decision = OrchestratorDecision(
        summary=None,
        notify=None,
        requires_confirmation=False,
        phase="executing",
        blockers=(),
        commands=(CommandSuggestion(text="ls"),),
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=FakeCodexClient([decision]),
        config=config,
    )

    try:
        service.run_once()
        commands = _read_jsonl(bus.commands_path)
        assert len(commands) == 1
        dispatched = commands[0]["text"]
        assert dispatched.startswith("timeout 30s ls")
        assert COMMAND_RESULT_SENTINEL in dispatched
    finally:
        service.agent_service.close()
        store.close()


def test_orchestrator_delegate_mode_skips_dispatch(tmp_path):
    log_path = tmp_path / "logs" / "delegate.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("hello\n", encoding="utf-8")

    store = StateStore(tmp_path / "state.db")
    branch = "storyapp/delegate"
    session_name = "agent-storyapp-delegate"
    _append_agent(store, branch, session_name, log_path)

    bus = LocalBus(tmp_path / "bus")
    notifier = Notifier(channel="local_bus", bus=bus)

    prompt_path = tmp_path / "command.md"
    prompt_path.write_text("JSON ONLY\n{log_excerpt}\n", encoding="utf-8")
    delegate_path = tmp_path / "delegate.md"
    delegate_path.write_text("delegated {log_excerpt}", encoding="utf-8")

    config = OrchestratorConfig(
        poll_interval=0.1,
        cooldown_seconds=1.0,
        session_cooldown_seconds=1.0,
        max_commands_per_cycle=1,
        history_lines=5,
        prompts=PromptConfig(command=prompt_path, summary=None, delegate=delegate_path),
        codex=CodexConfig(bin="codex", extra_args=[], timeout=5.0, env={}),
        command_timeout_seconds=45.0,
        delegate_to_codex=True,
    )

    decision = OrchestratorDecision(
        summary="handled internally",
        notify="",
        requires_confirmation=False,
        phase="executing",
        blockers=(),
        commands=(CommandSuggestion(text="echo should not run"),),
    )

    service = OrchestratorService(
        agent_service=AgentService(state_store=store),
        state_store=store,
        bus=bus,
        notifier=notifier,
        codex=FakeCodexClient([decision]),
        config=config,
    )

    try:
        service.run_once()
        commands = _read_jsonl(bus.commands_path)
        assert commands == []
        session = store.get_agent_session(branch)
        assert session is not None
        metadata = session["metadata"]
        assert metadata.get("orchestrator_summary") == "handled internally"
        assert metadata.get("delegate_suggestions") == ["echo should not run"]
    finally:
        service.agent_service.close()
        store.close()


def test_replay_summarize_events():
    events = [
        {"event": "command", "payload": {"text": "echo 1"}, "ts": 100.0},
        {"event": "queued", "ts": 101.0},
        {"event": "pending_confirmation", "ts": 102.0},
        {"event": "confirmation", "ts": 103.0},
    ]
    summary = summarize_events(events)
    assert summary["counts"]["command"] == 1
    assert summary["max_queue_depth"] >= 1
    assert summary["samples"] == 4
