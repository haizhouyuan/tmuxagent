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
from tmux_agent.orchestrator.service import OrchestratorService
from tmux_agent.state import StateStore


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
        assert commands, "command should be appended to bus"
        assert commands[0]["text"] == "echo hello"
        assert commands[0]["session"] == "agent-storyapp-feature-x"

        session = store.get_agent_session("storyapp/feature-x")
        assert session is not None
        metadata = session["metadata"]
        assert metadata.get("orchestrator_summary") == "ok"
        assert metadata.get("phase") == "executing"
        assert metadata.get("blockers") == ["waiting on secrets"]
        assert metadata.get("orchestrator_last_command") == ["echo hello", "echo skip"]
        assert "orchestrator_heartbeat" in metadata
        assert metadata.get("history_summaries") == ["ok"]
        assert metadata.get("pending_confirmation") == ["echo hello"]

        notifications = _read_jsonl(bus.notifications_path)
        assert notifications, "notification should be written"
        assert notifications[-1]["body"] == "please confirm"

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
        service.run_once()
        commands_after = _read_jsonl(bus.commands_path)
        assert len(commands_after) == len(commands), "no new command during cooldown"

        service._last_command_at["storyapp/feature-x"] = time.time() - 3600
        service.run_once()
        commands_final = _read_jsonl(bus.commands_path)
        assert commands_final[-1]["text"] == "echo second"
    finally:
        service.agent_service.close()
        store.close()
