import yaml

from tmux_agent.approvals import ApprovalManager
from tmux_agent.config import PolicyConfig
from tmux_agent.policy import PolicyEngine
from tmux_agent.state import StageStatus
from tmux_agent.tmux import PaneInfo

HOST = "local"


def load_policy(yaml_str: str) -> PolicyConfig:
    data = yaml.safe_load(yaml_str)
    return PolicyConfig.model_validate(data)


def make_policy() -> PolicyConfig:
    return load_policy(
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


def test_policy_flow(state_store, tmp_path):
    policy = make_policy()
    approvals = ApprovalManager(state_store, tmp_path / "approvals", secret="secret", base_url="https://example")
    engine = PolicyEngine(policy, state_store, approvals)
    pane = PaneInfo(
        pane_id="%1",
        session_name="proj:storyapp",
        window_name="agent:codex-ci",
        pane_title="codex:ci",
    )

    outcome = engine.evaluate(HOST, pane, ["run lint"], [])
    assert outcome.actions[0].command == "npm run lint"
    lint_state = state_store.load_stage_state(HOST, "%1", "demo", "lint")
    assert lint_state.status == StageStatus.RUNNING

    outcome = engine.evaluate(HOST, pane, ["lint ok"], [])
    lint_state = state_store.load_stage_state(HOST, "%1", "demo", "lint")
    assert lint_state.status == StageStatus.COMPLETED

    outcome = engine.evaluate(HOST, pane, [], [])
    build_state = state_store.load_stage_state(HOST, "%1", "demo", "build")
    assert build_state.status == StageStatus.WAITING_APPROVAL
    assert outcome.approvals
    request = outcome.approvals[0]
    request.file_path.write_text("approve")

    outcome = engine.evaluate(HOST, pane, [], [])
    build_state = state_store.load_stage_state(HOST, "%1", "demo", "build")
    assert build_state.status == StageStatus.RUNNING
    assert outcome.actions[0].command == "npm run build"

    outcome = engine.evaluate(HOST, pane, ["build ok"], [])
    build_state = state_store.load_stage_state(HOST, "%1", "demo", "build")
    assert build_state.status == StageStatus.COMPLETED
