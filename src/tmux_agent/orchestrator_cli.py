"""CLI entrypoint for orchestrator background service."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .agents.service import AgentService
from .config import AgentConfig
from .config import load_agent_config
from .local_bus import LocalBus
from .notify import Notifier
from .state import StateStore
from .orchestrator.codex_client import CodexClient
from .orchestrator.config import load_orchestrator_config
from .orchestrator.service import OrchestratorService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tmux agent orchestrator")
    parser.add_argument("--config", type=Path, help="Agent config file", required=True)
    parser.add_argument(
        "--orchestrator-config",
        type=Path,
        default=None,
        help="Orchestrator config TOML (default: .tmuxagent/orchestrator.toml)",
    )
    parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO"))
    return parser


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    agent_config = load_agent_config(args.config)
    orchestrator_config = load_orchestrator_config(args.orchestrator_config)

    state_store = StateStore(agent_config.expanded_sqlite_path())
    bus = LocalBus(agent_config.expanded_bus_dir())
    notifier = Notifier(channel=agent_config.notify_channel, bus=bus)

    agent_service = AgentService(config=None, adapter=None, state_store=state_store)

    executable = [orchestrator_config.codex.bin, *orchestrator_config.codex.extra_args]
    codex = CodexClient(
        executable=executable,
        env=orchestrator_config.codex.env,
        timeout=orchestrator_config.codex.timeout,
    )

    service = OrchestratorService(
        agent_service=agent_service,
        state_store=state_store,
        bus=bus,
        notifier=notifier,
        codex=codex,
        config=orchestrator_config,
    )
    try:
        service.run_forever()
    finally:
        state_store.close()
        agent_service.close()


if __name__ == "__main__":  # pragma: no cover - CLI bootstrap
    main()
