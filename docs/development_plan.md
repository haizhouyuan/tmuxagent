# Development Plan: tmux-agent MVP with Reliability Enhancements

## Objectives
- Provide a lightweight tmux orchestration agent that can monitor tmux panes, interpret AI agent output, and drive follow-up commands according to YAML policies.
- Deliver reliability and safety enhancements identified during review: state persistence, deterministic approval flow with token-based security, and hardened command dispatch.
- Supply tests and documentation that allow the system to run on a NAS-hosted environment with 3–5 active sessions.

## Scope
1. **Core Agent Runtime**
   - Discover and monitor configured tmux panes via local shell execution (SSH support abstracted for future work).
   - Incrementally capture pane output, parse structured markers (`### SENTRY {json}`), and match free-form heuristics.
   - Execute configured actions (`send-keys`, `shell`) with deduplication and idempotency safeguards.
2. **Policy Engine**
   - Load YAML pipelines (principles + stages) with triggers, success/failure conditions, retries, and escalation hooks.
   - Maintain per-pane, per-stage state machine with statuses: `IDLE`, `WAITING_TRIGGER`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`.
3. **Approval Workflow**
   - Support file-based approvals (`~/.tmux_sentry/approvals`).
   - Provide secure token generation/validation for webhook approvals and render actionable notifications when `PUBLIC_BASE_URL` is set.
4. **Notifications**
   - Implement stdout and WeCom webhook integrations (extensible plug-in style) while keeping credentials outside repo.
5. **Persistence Layer**
   - SQLite-backed storage for pipeline progress, last processed pane offsets, and pending approval tokens.
   - Graceful recovery on restart.
6. **CLI + Config**
   - Entry point `tmux_agent.main` with options for config paths, dry-run, and poll interval.
   - Typed configuration models with validation (pydantic).
7. **Testing & Tooling**
   - Unit tests for parsers, policy evaluation, approvals, and persistence.
   - Integration-style test that simulates tmux output via fixture.

## Deliverables
- Python package `tmux_agent` with modules:
  - `config.py` – schema & loading helpers.
  - `tmux.py` – adapter for tmux commands (mockable interface).
  - `parser.py` – message parsing utilities.
  - `state.py` – persistence and state models.
  - `policy.py` – evaluation logic and state machine.
  - `approvals.py` – token, file I/O, and webhook helpers.
  - `notify.py` – extensible notification dispatch.
  - `runner.py` – main loop orchestrating polling cycle.
  - `main.py` – CLI entrypoint.
- Example configuration files in `examples/` (hosts, policy templates).
- Documentation updates:
  - `README.md` quickstart.
  - `docs/development_plan.md` (this document).
  - `docs/testing.md` listing test commands and scenarios.
- Test suite under `tests/` with pytest, plus CI-ready instructions (local execution requirement only).

## Work Breakdown Structure
1. **Project Bootstrap (0.5 day)**
   - Initialize Python package & pyproject (poetry or bare `setuptools + pip`).
   - Add formatting/test tooling (ruff + pytest configuration).
2. **Configuration & Models (0.5 day)**
   - Implement config loading, validation, and environment variable expansion.
   - Draft example YAML files.
3. **Pane Monitoring Layer (1 day)**
   - Build tmux adapter with capture/send abstractions.
   - Implement incremental buffer reader with cursor tracking stored in SQLite.
4. **Parser & Policy Engine (1.5 day)**
   - `parser.py`: structured marker detection, fallback heuristics, exit-code placeholder.
   - `policy.py`: trigger evaluation, retries, state transitions, action queue with deduplication.
5. **Persistence Layer (0.5 day)**
   - SQLite models/tables for panes, stages, approvals, and offsets.
   - Recovery logic on startup.
6. **Approvals & Notifications (0.5 day)**
   - Implement file watcher + token generation/validation.
   - Notification dispatcher with stdout + WeCom; stub for Server酱.
7. **Main Loop & CLI (0.5 day)**
   - Compose runner orchestrating poll-scan-evaluate-act cycle.
   - CLI options for poll interval, dry run, log level.
8. **Testing (1 day)**
   - Unit tests for parser, approvals, policy transitions, persistence failure cases.
   - Integration test using fake tmux adapter and sample policy.
9. **Documentation & Packaging (0.5 day)**
   - README instructions, sample usage, security checklist.
   - `docs/testing.md` and final polish.
10. **Final QA (0.5 day)**
    - Run `pytest`, lint, and optional static checks.
    - Prepare PR summary and changelog.

## Risk & Mitigation
- **tmux availability in CI** – use adapter abstraction; tests rely on fake adapter. Provide manual verification script for real environment.
- **Credential leakage** – never store secrets; highlight environment variable usage.
- **State corruption** – guard SQLite operations with migrations and transactions; add sanity checks when loading.

## Testing Strategy
- `pytest` with coverage threshold ≥80% for core modules.
- Golden log fixtures to ensure parser stability.
- Approval token round-trip tests ensure TTL and signature validation.
- Runner smoke test covering trigger → approval → action path with simulated time.

## Acceptance Criteria
- Running `python -m tmux_agent.main --config examples/hosts.yaml --policy examples/policy.yaml --dry-run` works with fake adapter in tests.
- Restarting the agent resumes stage progress from SQLite state.
- Approval tokens validated both via file and webhook, with log output confirming decision.
- Notifications routed to stdout by default; WeCom path tested via mocked HTTP client.
- Documentation gives NAS operator all steps to deploy safely.
