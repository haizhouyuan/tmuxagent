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


def test_all_of_trigger_group(state_store, tmp_path):
    policy = load_policy(
        """
principles: []
pipelines:
  - name: demo
    match:
      any_of:
        - window_name: "^agent:codex-ci$"
    stages:
      - name: double
        actions_on_start:
          - send_keys: "echo start"
        success_when:
          all_of:
            - log_regex: "first marker"
            - log_regex: "second marker"
"""
    )
    approvals = ApprovalManager(state_store, tmp_path / "approvals")
    engine = PolicyEngine(policy, state_store, approvals)
    pane = PaneInfo(
        pane_id="%1",
        session_name="proj:storyapp",
        window_name="agent:codex-ci",
        pane_title="codex:ci",
    )

    outcome = engine.evaluate(HOST, pane, [], [])
    assert outcome.actions[0].command == "echo start"
    stage_state = state_store.load_stage_state(HOST, "%1", "demo", "double")
    assert stage_state.status == StageStatus.RUNNING

    engine.evaluate(HOST, pane, ["first marker"], [])
    stage_state = state_store.load_stage_state(HOST, "%1", "demo", "double")
    assert stage_state.status == StageStatus.RUNNING

    engine.evaluate(HOST, pane, ["first marker", "second marker"], [])
    stage_state = state_store.load_stage_state(HOST, "%1", "demo", "double")
    assert stage_state.status == StageStatus.COMPLETED


def test_escalation_notification(state_store, tmp_path):
    policy = load_policy(
        """
principles: []
pipelines:
  - name: demo
    match:
      any_of:
        - window_name: "^agent:codex-ci$"
    stages:
      - name: deploy
        success_when:
          any_of:
            - log_regex: "deploy ok"
        fail_when:
          any_of:
            - log_regex: "deploy failed"
        on_fail:
          - escalate: "deploy_failure"
"""
    )
    approvals = ApprovalManager(state_store, tmp_path / "approvals")
    engine = PolicyEngine(policy, state_store, approvals)
    pane = PaneInfo(
        pane_id="%1",
        session_name="proj:storyapp",
        window_name="agent:codex-ci",
        pane_title="codex:ci",
    )

    engine.evaluate(HOST, pane, [], [])
    state = state_store.load_stage_state(HOST, "%1", "demo", "deploy")
    assert state.status == StageStatus.RUNNING

    outcome = engine.evaluate(HOST, pane, ["deploy failed"], [])
    state = state_store.load_stage_state(HOST, "%1", "demo", "deploy")
    assert state.status == StageStatus.FAILED
    assert outcome.notifications
    titles = [note.title for note in outcome.notifications]
    assert any("Escalation" in title for title in titles)
