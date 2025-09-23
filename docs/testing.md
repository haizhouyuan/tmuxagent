# Testing Guide

## Automated Tests
1. Install dev dependencies: `pip install -e .[dev]`.
2. Run `pytest` to execute unit and integration tests with coverage.

## Manual Verification (optional)
1. Prepare a tmux session with a pane running a mock agent that emits `### SENTRY {...}` markers.
2. Start the agent in dry-run mode:
   ```bash
   python -m tmux_agent.main --config hosts.yaml --policy policy.yaml --dry-run
   ```
3. Trigger stages by typing commands in the monitored pane and observe logged state transitions.
4. For approval-required stages, create files in `~/.tmux_agent/approvals/` with `approve` or `reject` to ensure the workflow resumes appropriately.
5. Set `SENTRY_NOTIFY_CHANNEL=wecom` and `WECOM_WEBHOOK=...` to validate notification delivery (mocked in automated tests).
