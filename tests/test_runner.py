import types
import yaml

from tmux_agent.approvals import ApprovalManager
from tmux_agent.config import AgentConfig
from tmux_agent.config import PolicyConfig
from tmux_agent.constants import COMMAND_RESULT_SENTINEL
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


def test_runner_executes_shell_action(state_store, tmp_path, monkeypatch):
    agent_config = make_agent_config(tmp_path)

    raw = yaml.safe_load(
        """
principles: []
pipelines:
  - name: demo
    match:
      any_of:
        - window_name: "^agent:codex-ci$"
    stages:
      - name: actions
        triggers:
          any_of:
            - log_regex: "shell please"
        actions_on_start:
          - send_keys: "say hello"
          - shell: "echo remote"
        success_when:
          any_of:
            - log_regex: "done"
"""
    )
    policy = PolicyConfig.model_validate(raw)

    approval_manager = ApprovalManager(state_store, tmp_path / "approvals")
    notifier = MockNotifier()

    adapter = FakeTmuxAdapter({"%1": "shell please\n"})
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

    calls: list[tuple[list[str], bool, dict]] = []

    def fake_run(cmd, check=True, **kwargs):
        calls.append((cmd, check, kwargs))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    runner.run_once()

    assert "[SENT:say hello\n" in adapter._panes["%1"]
    stage_state = state_store.load_stage_state(HOST_NAME, "%1", "demo", "actions")
    assert stage_state.status.value == "RUNNING"
    assert len(calls) == 1
    assert calls[0][0] == ["bash", "-lc", "echo remote"]

    adapter.append_output("%1", "done\n")
    runner.run_once()
    stage_state = state_store.load_stage_state(HOST_NAME, "%1", "demo", "actions")
    assert stage_state.status.value == "COMPLETED"


def test_runner_process_bus_command(state_store, tmp_path):
    from tmux_agent.local_bus import LocalBus

    agent_config = make_agent_config(tmp_path)
    policy = make_policy()
    approval_manager = ApprovalManager(
        state_store,
        tmp_path / "approvals",
        secret="secret",
    )
    notifier = MockNotifier()
    bus = LocalBus(tmp_path / 'bus')

    adapter = FakeTmuxAdapter({"%1": ""})
    adapter.set_meta("%1", "proj:storyapp", "agent:codex-ci", "codex:ci")

    runtime = HostRuntime(host=agent_config.hosts[0], adapter=adapter)
    runner = Runner(
        agent_config=agent_config,
        policy=policy,
        state_store=state_store,
        notifier=notifier,
        approval_manager=approval_manager,
        adapters=[runtime],
        bus=bus,
        dry_run=False,
    )

    bus.append_command({"text": "hello orchestrator", "session": "proj:storyapp", "sender": "test"})
    runner.run_once()

    assert any(blob.startswith('[SENT:hello orchestrator') for blob in adapter._panes.values())
    assert any(msg.title == '指令已注入' for msg in notifier.sent)


def test_runner_records_command_results(state_store, tmp_path):
    agent_config = make_agent_config(tmp_path)
    policy = make_policy()
    approval_manager = ApprovalManager(state_store, tmp_path / "approvals", secret="secret")
    notifier = MockNotifier()

    adapter = FakeTmuxAdapter({"%1": ""})
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

    branch = "storyapp/feature-z"
    session_name = "proj:storyapp"
    state_store.upsert_agent_session(
        branch=branch,
        worktree_path=str(tmp_path),
        session_name=session_name,
        model="gpt-5-codex",
        template="orchestrator",
        description="demo",
        status="running",
        log_path=str(tmp_path / "log.txt"),
        metadata={
            "command_tracker": [
                {
                    "command_id": "cmd-test",
                    "text": "echo hi",
                    "status": "pending",
                    "dispatched_at": 1,
                }
            ]
        },
    )

    agent_record = state_store.find_agent_by_session(session_name)
    assert agent_record is not None
    sentinel_line = f"{COMMAND_RESULT_SENTINEL} cmd-test 1"
    runner._process_command_results(agent_record, ["some output", sentinel_line])

    session = state_store.get_agent_session(branch)
    assert session is not None
    metadata = session["metadata"]
    tracker = metadata.get("command_tracker")
    assert isinstance(tracker, list) and tracker[-1]["status"] == "failed"
    assert metadata.get("last_command_result", {}).get("exit_code") == 1
    history = metadata.get("command_history")
    assert isinstance(history, list) and history[-1]["command_id"] == "cmd-test"
