# tmux-agent

A lightweight orchestration agent for tmux-based AI development environments. The agent watches pane output, interprets structured `### SENTRY {json}` markers, evaluates YAML-defined policies, and performs follow-up actions with human-in-the-loop approvals.

## Features
- Configurable tmux pane discovery and incremental log capture.
- YAML policy engine with stage triggers, retries, approvals, and escalation hooks.
- Persistent state backed by SQLite for restart resilience.
- File-based and webhook-driven approval flow with signed tokens.
- Notification adapters (stdout 默认，可选 Server酱 或 WeCom webhook).

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Copy and adjust configuration
cp examples/hosts.example.yaml hosts.yaml
cp examples/policy.example.yaml policy.yaml

python -m tmux_agent.main --config hosts.yaml --policy policy.yaml
```

Run `python -m tmux_agent.main --help` for CLI options.

## Testing
```bash
pip install -e .[dev]
pytest
```

See `docs/testing.md` for detailed scenarios and manual verification steps.
