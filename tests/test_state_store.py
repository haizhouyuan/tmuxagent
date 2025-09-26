import time

from tmux_agent.state import StageState
from tmux_agent.state import StageStatus

HOST = "local"


def test_offset_roundtrip(state_store):
    assert state_store.get_offset(HOST, "%1") == 0
    state_store.set_offset(HOST, "%1", 42)
    assert state_store.get_offset(HOST, "%1") == 42


def test_stage_state_roundtrip(state_store):
    state = state_store.load_stage_state(HOST, "%1", "pipe", "stage")
    assert state.status == StageStatus.IDLE
    state.status = StageStatus.RUNNING
    state.retries = 1
    state.data = {"foo": "bar"}
    state_store.save_stage_state(state)
    loaded = state_store.load_stage_state(HOST, "%1", "pipe", "stage")
    assert loaded.status == StageStatus.RUNNING
    assert loaded.retries == 1
    assert loaded.data["foo"] == "bar"


def test_approval_tokens(state_store):
    state_store.upsert_approval_token(HOST, "%1", "stage", "token", int(time.time()) + 5)
    token_info = state_store.get_approval_token(HOST, "%1", "stage")
    assert token_info[0] == "token"
    state_store.delete_approval_token(HOST, "%1", "stage")
    assert state_store.get_approval_token(HOST, "%1", "stage") is None


def test_agent_session_crud(state_store):
    state_store.upsert_agent_session(
        branch="feature/x",
        worktree_path="/tmp/worktrees/feature-x",
        session_name="agent-feature-x",
        model="claude-sonnet",
        template="python-pro",
        description="QA session",
        pid=1234,
        last_prompt="Run tests",
        status="launching",
        log_path="/tmp/logs/feature-x.log",
        last_output="booting",
        last_output_at=1234567890,
        metadata={"phase": "qa"},
    )

    session = state_store.get_agent_session("feature/x")
    assert session is not None
    assert session["session_name"] == "agent-feature-x"
    assert session["template"] == "python-pro"
    assert session["status"] == "launching"
    assert session["log_path"] == "/tmp/logs/feature-x.log"
    assert session["metadata"]["phase"] == "qa"

    state_store.upsert_agent_session(
        branch="feature/x",
        worktree_path="/tmp/worktrees/feature-x",
        session_name="agent-feature-x",
        model="claude-opus",
        template=None,
        description="deploy",
        pid=None,
        last_prompt=None,
        status="running",
        log_path="/tmp/logs/feature-x.log",
        metadata={"phase": "deploy"},
    )

    session = state_store.get_agent_session("feature/x")
    assert session["model"] == "claude-opus"
    assert session["template"] is None
    assert session["description"] == "deploy"
    assert session["status"] == "running"
    assert session["metadata"]["phase"] == "deploy"

    state_store.upsert_agent_session(
        branch="feature/y",
        worktree_path="/tmp/worktrees/feature-y",
        session_name="agent-feature-y",
    )

    sessions = state_store.list_agent_sessions()
    branches = [item["branch"] for item in sessions]
    assert set(branches) == {"feature/x", "feature/y"}

    state_store.prune_agent_sessions(["feature/x"])
    remaining = state_store.list_agent_sessions()
    assert [item["branch"] for item in remaining] == ["feature/x"]

    state_store.delete_agent_session("feature/x")
    assert state_store.get_agent_session("feature/x") is None
