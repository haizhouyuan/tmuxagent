from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tmux_agent.dashboard.app import create_app
from tmux_agent.dashboard.config import DashboardConfig
from tmux_agent.state import StageState
from tmux_agent.state import StageStatus
from tmux_agent.state import StateStore
from tmux_agent.tmux import FakeTmuxAdapter


def _seed_state(db_path):
    store = StateStore(db_path)
    try:
        first = StageState(
            host="local",
            pane_id="%1",
            pipeline="demo",
            stage="lint",
            status=StageStatus.RUNNING,
            data={"action_sent": True},
        )
        first.updated_at = int(datetime.now(tz=timezone.utc).timestamp())
        store.save_stage_state(first)

        second = StageState(
            host="local",
            pane_id="%1",
            pipeline="demo",
            stage="build",
            status=StageStatus.WAITING_APPROVAL,
            data={"waiting_since": 1234567890},
        )
        store.save_stage_state(second)
    finally:
        store.close()


def _install_fake_tmux(monkeypatch, adapter: FakeTmuxAdapter) -> None:
    def _factory(*_args, **_kwargs):
        return adapter

    monkeypatch.setattr("tmux_agent.dashboard.app.TmuxAdapter", _factory)


def _make_fake_adapter() -> FakeTmuxAdapter:
    adapter = FakeTmuxAdapter({"%1": "hello\n", "%2": "other\n"})
    adapter.set_meta("%1", "sess1", "win1", "title1")
    adapter.set_meta("%2", "sess2", "win2", "title2")
    return adapter


def test_overview_api(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=db_path))
    client = TestClient(app)

    response = client.get("/api/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["RUNNING"] == 1
    assert payload["summary"]["WAITING_APPROVAL"] == 1
    assert any(stage["stage"] == "lint" for stage in payload["stages"])


def test_index_page_renders_state(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=db_path))
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "demo" in response.text
    assert "WAITING_APPROVAL" in response.text


def test_api_allows_submitting_decision(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    approvals_dir = tmp_path / "approvals"
    _seed_state(db_path)
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    config = DashboardConfig(db_path=db_path, approval_dir=approvals_dir)
    app = create_app(config)
    client = TestClient(app)

    response = client.post(
        "/api/approvals/local/%1/build",
        json={"decision": "approve"},
    )
    assert response.status_code == 200
    approval_file = approvals_dir / "local" / "pct1__build.txt"
    assert approval_file.read_text(encoding="utf-8").strip() == "approve"

    response = client.post(
        "/approvals/reject",
        data={"host": "local", "pane_id": "%1", "stage": "build"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert approval_file.read_text(encoding="utf-8").strip() == "reject"


def test_panes_api_and_send(tmp_path, monkeypatch):
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=tmp_path / "state.db"))
    client = TestClient(app)

    list_response = client.get("/api/panes")
    assert list_response.status_code == 200
    panes = list_response.json()
    assert any(pane["pane_id"] == "%1" for pane in panes)

    send_resp = client.post(
        "/api/panes/%1/send",
        json={"input": "echo hi"},
    )
    assert send_resp.status_code == 200
    # Fake adapter appends marker to pane output
    assert "[SENT:echo hi\n]" in fake._panes["%1"]

    tail_resp = client.get("/api/panes/%1/tail")
    assert tail_resp.status_code == 200
    assert any("echo hi" in line for line in tail_resp.json()["lines"])


def test_panes_page_renders(tmp_path, monkeypatch):
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=tmp_path / "state.db"))
    client = TestClient(app)

    response = client.get("/panes")
    assert response.status_code == 200
    assert "form" in response.text
