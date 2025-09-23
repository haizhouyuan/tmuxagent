import time
from pathlib import Path

import pytest

from tmux_agent.approvals import ApprovalManager


@pytest.fixture()
def manager(state_store, tmp_path: Path) -> ApprovalManager:
    return ApprovalManager(state_store, tmp_path, secret="secret", base_url="https://example")


def test_file_decision(manager: ApprovalManager):
    path = manager.approval_file("%1", "stage")
    path.write_text("approve")
    decision = manager.poll_file_decision("%1", "stage")
    assert decision == "approve"
    assert not path.exists()


def test_token_roundtrip(manager: ApprovalManager):
    request = manager.ensure_request("%1", "stage")
    assert request.token is not None
    pane, stage = manager.resolve_token(request.token)
    assert pane == "%1"
    assert stage == "stage"


def test_expired_token_cleanup(state_store, tmp_path: Path):
    mgr = ApprovalManager(state_store, tmp_path, secret="secret")
    token = mgr._make_token("%1", "stage")
    past = int(time.time()) - 10
    state_store.upsert_approval_token("%1", "stage", token, past)
    assert state_store.get_approval_token("%1", "stage") is None
