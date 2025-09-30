from pathlib import Path

import pytest

from tmux_agent.agents.session import AgentSessionManager
from tmux_agent.tmux import FakeTmuxAdapter


@pytest.fixture()
def adapter() -> FakeTmuxAdapter:
    return FakeTmuxAdapter()


def test_spawn_and_send(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    manager = AgentSessionManager(adapter)
    worktree = tmp_path / "repo"
    worktree.mkdir()

    log_file = tmp_path / "logs" / "feature.log"
    result = manager.spawn(
        "feature/demo",
        worktree,
        "echo start",
        env={"AI_AGENT_BRANCH": "feature/demo"},
        log_path=log_file,
    )
    assert result.session_name == "agent-feature-demo"

    panes = adapter.panes_for_session(result.session_name)
    assert panes
    pane_id = panes[0].pane_id
    pane_buffer = adapter._panes[pane_id]
    assert "AI_AGENT_BRANCH" in pane_buffer
    assert "[PIPE_APPEND]" in pane_buffer
    assert log_file.exists()

    manager.send("feature/demo", "ls")
    assert "ls" in adapter._panes[pane_id]

    tail = manager.capture_tail("feature/demo")
    assert "echo start" in tail

    manager.kill("feature/demo")
    assert not adapter.session_exists(result.session_name)


def test_ensure_spawned_reuses_session(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    manager = AgentSessionManager(adapter)
    worktree = tmp_path / "repo"
    worktree.mkdir()

    log_path = tmp_path / "logs" / "bugfix.log"
    result = manager.spawn("bugfix/demo", worktree, "printf hi", log_path=log_path)
    assert adapter.session_exists(result.session_name)

    again = manager.ensure_spawned("bugfix/demo", worktree, "echo hi", log_path=log_path)
    assert again.session_name == result.session_name
    pane_id = again.pane_id
    buffer = adapter._panes[pane_id]
    assert "printf hi" in buffer
    assert "echo hi" in buffer
    assert buffer.count("[PIPE_APPEND]") == 1
