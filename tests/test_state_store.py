import time

from tmux_agent.state import StageState
from tmux_agent.state import StageStatus


def test_offset_roundtrip(state_store):
    assert state_store.get_offset("%1") == 0
    state_store.set_offset("%1", 42)
    assert state_store.get_offset("%1") == 42


def test_stage_state_roundtrip(state_store):
    state = state_store.load_stage_state("%1", "pipe", "stage")
    assert state.status == StageStatus.IDLE
    state.status = StageStatus.RUNNING
    state.retries = 1
    state.data = {"foo": "bar"}
    state_store.save_stage_state(state)
    loaded = state_store.load_stage_state("%1", "pipe", "stage")
    assert loaded.status == StageStatus.RUNNING
    assert loaded.retries == 1
    assert loaded.data["foo"] == "bar"


def test_approval_tokens(state_store):
    state_store.upsert_approval_token("%1", "stage", "token", int(time.time()) + 5)
    token_info = state_store.get_approval_token("%1", "stage")
    assert token_info[0] == "token"
    state_store.delete_approval_token("%1", "stage")
    assert state_store.get_approval_token("%1", "stage") is None
