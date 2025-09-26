from fastapi.testclient import TestClient

from tmux_agent.bus_server import create_app
from tmux_agent.config import AgentConfig
from tmux_agent.local_bus import LocalBus


def make_config(tmp_path):
    data = {
        "poll_interval_ms": 1000,
        "tmux_bin": "tmux",
        "sqlite_path": str(tmp_path / "state.db"),
        "approval_dir": str(tmp_path / "approvals"),
        "bus_dir": str(tmp_path / "bus"),
        "notify": "local_bus",
        "hosts": [
            {
                "name": "local",
                "tmux": {
                    "socket": "default",
                    "capture_lines": 2000,
                },
            }
        ],
    }
    return AgentConfig.model_validate(data)


def test_portal_notifications_and_commands(tmp_path):
    config = make_config(tmp_path)
    bus = LocalBus(config.expanded_bus_dir())
    app = create_app(config=config, bus=bus, auth_token=None)
    client = TestClient(app)

    bus.append_notification({"title": "测试", "body": "调度"})

    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"]
    assert data["items"][0]["title"] == "测试"

    resp = client.post("/api/commands", json={"text": "hello orchestrator", "session": "agent-storyapp-orchestrator"})
    assert resp.status_code == 200

    commands, _ = bus.read_commands(0)
    assert any(cmd["text"] == "hello orchestrator" for cmd in commands)

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
