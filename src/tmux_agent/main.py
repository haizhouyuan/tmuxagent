"""CLI entry point for tmux-agent."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .approvals import ApprovalManager
from .config import AgentConfig
from .config import HostConfig
from .config import load_agent_config
from .config import load_policy
from .notify import Notifier
from .runner import HostRuntime
from .runner import Runner
from .state import StateStore
from .tmux import TmuxAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tmux agent orchestrator")
    parser.add_argument("--config", type=Path, required=True, help="Path to agent config YAML")
    parser.add_argument("--policy", type=Path, required=True, help="Path to policy YAML")
    parser.add_argument("--dry-run", action="store_true", help="Do not send keys, only log")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument("--approval-secret", type=str, default=os.getenv("APPROVAL_SECRET"), help="Secret used to sign approval tokens")
    parser.add_argument("--public-base-url", type=str, default=os.getenv("PUBLIC_BASE_URL"), help="Base URL for approval callbacks")
    parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    agent_config = load_agent_config(args.config)
    policy_config = load_policy(args.policy)

    state_store = StateStore(agent_config.expanded_sqlite_path())
    notifier = Notifier(channel=agent_config.notify_channel)
    approval_manager = ApprovalManager(
        store=state_store,
        approval_dir=agent_config.expanded_approval_dir(),
        secret=args.approval_secret,
        base_url=args.public_base_url,
    )

    adapters = [HostRuntime(host=host, adapter=_build_adapter(agent_config, host)) for host in agent_config.hosts]
    runner = Runner(
        agent_config=agent_config,
        policy=policy_config,
        state_store=state_store,
        notifier=notifier,
        approval_manager=approval_manager,
        adapters=adapters,
        dry_run=args.dry_run,
    )

    try:
        if args.once:
            runner.run_once()
        else:
            runner.run_forever()
    finally:
        state_store.close()


def _build_adapter(agent_config: AgentConfig, host: HostConfig) -> TmuxAdapter:
    return TmuxAdapter(
        tmux_bin=agent_config.tmux_bin,
        socket=host.tmux.socket,
        ssh=host.ssh,
    )


if __name__ == "__main__":  # pragma: no cover - CLI bootstrap
    main()
