from datetime import datetime, timezone

from fastapi.testclient import TestClient
import base64

from tmux_agent.dashboard.app import create_app
from tmux_agent.dashboard.config import DashboardConfig
from tmux_agent.state import StageState
from tmux_agent.state import StageStatus
from tmux_agent.state import StateStore


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


def test_overview_api(tmp_path):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    app = create_app(DashboardConfig(db_path=db_path))
    client = TestClient(app)

    response = client.get("/api/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["RUNNING"] == 1
    assert payload["summary"]["WAITING_APPROVAL"] == 1
    assert any(stage["stage"] == "lint" for stage in payload["stages"])


def test_index_page_renders_state(tmp_path):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    app = create_app(DashboardConfig(db_path=db_path))
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "demo" in response.text
    assert "WAITING_APPROVAL" in response.text


def test_endpoints_require_basic_auth_when_configured(tmp_path):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    config = DashboardConfig(db_path=db_path, username="admin", password="secret")
    app = create_app(config)
    client = TestClient(app)

    # Missing credentials -> 401
    response = client.get("/api/overview")
    assert response.status_code == 401

    credentials = base64.b64encode(b"admin:secret").decode("ascii")
    headers = {"Authorization": f"Basic {credentials}"}
    ok_response = client.get("/api/overview", headers=headers)
    assert ok_response.status_code == 200

    html_response = client.get("/", headers=headers)
    assert html_response.status_code == 200
    assert "WAITING_APPROVAL" in html_response.text
