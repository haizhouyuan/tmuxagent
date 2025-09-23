import yaml

from tmux_agent.approvals import ApprovalManager
from tmux_agent.config import AgentConfig
from tmux_agent.config import PolicyConfig
from tmux_agent.notify import MockNotifier
from tmux_agent.runner import HostRuntime
from tmux_agent.runner import Runner
from tmux_agent.tmux import FakeTmuxAdapter


HOST_NAME = "local"


def make_agent_config(tmp_path):
    data = {
        "poll_interval_ms": 10,
        "tmux_bin": "tmux",
        "sqlite_path": str(tmp_path / "state.db"),
        "approval_dir": str(tmp_path / "approvals"),
        "notify": "stdout",
        "hosts": [
            {
                "name": HOST_NAME,
                "tmux": {
                    "socket": "default",
                    "session_filters": ["proj:storyapp"],
                    "pane_name_patterns": ["^codex"],
                    "capture_lines": 2000,
                },
            }
        ],
    }
    return AgentConfig.model_validate(data)


def make_policy():
    raw = yaml.safe_load(
        """
principles: []
pipelines:
  - name: demo
    match:
      any_of:
        - window_name: "^agent:codex-ci$"
    stages:
      - name: lint
        triggers:
          any_of:
            - log_regex: "run lint"
        actions_on_start:
          - send_keys: "npm run lint"
        success_when:
          any_of:
            - log_regex: "lint ok"
      - name: build
        triggers:
          any_of:
            - after_stage_success: "lint"
        require_approval: true
        actions_on_start:
          - send_keys: "npm run build"
        success_when:
          any_of:
            - log_regex: "build ok"
"""
    )
    return PolicyConfig.model_validate(raw)


def test_runner_flow(state_store, tmp_path, monkeypatch):
    agent_config = make_agent_config(tmp_path)
    policy = make_policy()
    approval_manager = ApprovalManager(
        state_store,
        tmp_path / "approvals",
        secret="secret",
    )
    notifier = MockNotifier()

    adapter = FakeTmuxAdapter({"%1": "run lint\n"})
    adapter.set_meta("%1", "proj:storyapp", "agent:codex-ci", "codex:ci")

    runtime = HostRuntime(host=agent_config.hosts[0], adapter=adapter)
    runner = Runner(
        agent_config=agent_config,
        policy=policy,
        state_store=state_store,
        notifier=notifier,
        approval_manager=approval_manager,
        adapters=[runtime],
        dry_run=False,
    )

    runner.run_once()
    assert "[SENT:npm run lint" in adapter._panes["%1"]  # lint command queued

    adapter.append_output("%1", "lint ok\n")
    runner.run_once()
    build_state = state_store.load_stage_state(HOST_NAME, "%1", "demo", "build")
    assert build_state.status.value == "WAITING_APPROVAL"

    approval_path = approval_manager.approval_file(HOST_NAME, "%1", "build")
    approval_path.write_text("approve")
    runner.run_once()
    assert "[SENT:npm run build" in adapter._panes["%1"]

    adapter.append_output("%1", "build ok\n")
    runner.run_once()
    build_state = state_store.load_stage_state(HOST_NAME, "%1", "demo", "build")
    assert build_state.status.value == "COMPLETED"
