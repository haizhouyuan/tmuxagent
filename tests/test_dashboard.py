from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tmux_agent.dashboard.app import create_app
from tmux_agent.dashboard.config import DashboardConfig
from tmux_agent.dashboard.summary import SummaryResult
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


def _seed_agent_session(db_path, branch: str, session_name: str, metadata: dict | None = None) -> None:
    store = StateStore(db_path)
    try:
        store.upsert_agent_session(
            branch=branch,
            worktree_path=f"/worktrees/{branch}",
            session_name=session_name,
            model="claude-sonnet",
            template="qa-template",
            description="检查构建输出",
            last_prompt="请扫描异常",
            metadata=metadata or {},
        )
    finally:
        store.close()


def _install_fake_tmux(monkeypatch, adapter: FakeTmuxAdapter) -> None:
    def _factory(*_args, **_kwargs):
        return adapter

    monkeypatch.setattr("tmux_agent.dashboard.app.TmuxAdapter", _factory)


def _make_fake_adapter() -> FakeTmuxAdapter:
    adapter = FakeTmuxAdapter({"%1": "hello\n", "%2": "other\n"})
    adapter.set_full_meta("%1", session="sess1", window="win1", title="title1", active=True, width=120, height=32)
    adapter.set_full_meta("%2", session="sess2", window="win2", title="title2", active=False, width=100, height=28)
    return adapter


def test_overview_api(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=db_path, template_path=TEMPLATE_DIR))
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
    app = create_app(DashboardConfig(db_path=db_path, template_path=TEMPLATE_DIR))
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "调度控制板" in response.text
    assert "实时 Pane" in response.text
    assert "%1" in response.text


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
    first = next(item for item in panes if item["pane_id"] == "%1")
    assert first["is_active"] is True
    assert first["width"] == 120
    assert first["activity"]["status"] in {"RUNNING", "IDLE"}
    assert first["activity"]["project"] == "others"
    assert "tail_excerpt" in first
    assert "hello" in first["tail_excerpt"]

    send_resp = client.post(
        "/api/panes/%1/send",
        json={"input": "echo hi"},
    )
    assert send_resp.status_code == 200
    # Fake adapter appends marker to pane output
    assert "[SENT:echo hi\n]" in fake._panes["%1"]

    send_keys_resp = client.post(
        "/api/panes/%1/send",
        json={"keys": ["C-c"], "enter": False},
    )
    assert send_keys_resp.status_code == 200
    assert "[SENT:C-c]" in fake._panes["%1"]

    enter_only_resp = client.post(
        "/api/panes/%1/send",
        json={"enter": True},
    )
    assert enter_only_resp.status_code == 200
    assert "[SENT:\n]" in fake._panes["%1"]

    tail_resp = client.get("/api/panes/%1/tail")
    assert tail_resp.status_code == 200
    tail_payload = tail_resp.json()
    assert tail_payload["session"] == "sess1"
    assert any("echo hi" in line for line in tail_payload["lines"])
    assert tail_payload["activity"]["status"] in {"RUNNING", "IDLE"}
    assert tail_payload["activity"]["project"] == "others"
    assert "tail_excerpt" in tail_payload
    assert "[SENT:" in tail_payload["tail_excerpt"]


def test_dashboard_api_includes_stage_and_panes(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    fake = _make_fake_adapter()
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=db_path, template_path=TEMPLATE_DIR))
    client = TestClient(app)

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert "board" in payload
    assert payload["board"]
    assert "projects" in payload
    assert payload["projects"]
    first_pane = next(item for item in payload["panes"] if item["pane_id"] == "%1")
    assert first_pane["lines"]
    assert first_pane["is_active"] is True
    pane_activity = payload["pane_activity"]
    assert pane_activity["%1"]["status"] in {"RUNNING", "IDLE"}
    assert pane_activity["%1"]["project"] == "others"
    assert "tail_excerpt" in pane_activity["%1"]


def test_agent_sessions_reflected_in_dashboard(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    fake = FakeTmuxAdapter({"%10": "booting agent\n"})
    fake.set_full_meta("%10", session="agent-demo", window="win", title="agent-demo", active=True, width=120, height=30)
    _install_fake_tmux(monkeypatch, fake)
    _seed_agent_session(
        db_path,
        branch="demo",
        session_name="agent-demo",
        metadata={
            "phase": "executing",
            "orchestrator_summary": "同步中",
            "queued_commands": [{"text": "echo second", "session": "agent-demo"}],
        },
    )
    app = create_app(DashboardConfig(db_path=db_path, template_path=TEMPLATE_DIR))
    client = TestClient(app)

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    projects = payload["projects"]
    assert any(project["name"] == "agents" for project in projects)
    agent_sessions = payload.get("agent_sessions")
    assert agent_sessions and agent_sessions[0]["branch"] == "demo"
    pane_activity = payload["pane_activity"]["%10"]
    assert pane_activity["project"] == "agents"
    assert pane_activity["agent_info"]["template"] == "qa-template"
    orchestrator = payload.get("orchestrator")
    assert orchestrator and orchestrator[0]["phase"] == "executing"
    assert orchestrator[0]["summary"] == "同步中"
    assert orchestrator[0]["queued_commands"]


def test_pane_summary_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    _seed_state(db_path)
    fake = _make_fake_adapter()

    class StubSummarizer:
        def __init__(self, *_args, **_kwargs) -> None:
            self.default_model = "claude-sonnet-4-20250514"

        def summarize(self, provider, pane_id: str, *, options=None) -> SummaryResult:
            return SummaryResult(
                pane_id=pane_id,
                summary="summarized",
                model="claude-sonnet-4-20250514",
                duration_ms=123,
                cost_usd=0.01,
                raw={"mock": True},
            )

    monkeypatch.setattr("tmux_agent.dashboard.app.PaneSummarizer", lambda *args, **kwargs: StubSummarizer())
    _install_fake_tmux(monkeypatch, fake)
    app = create_app(DashboardConfig(db_path=db_path))
    client = TestClient(app)

    response = client.post("/api/panes/%1/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "summarized"
    assert data["pane_id"] == "%1"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "src" / "tmux_agent" / "dashboard" / "templates"
