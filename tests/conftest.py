import os
from pathlib import Path

import pytest

from tmux_agent.state import StateStore


@pytest.fixture()
def state_store(tmp_path: Path) -> StateStore:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clear_wecom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WECOM_WEBHOOK", raising=False)
