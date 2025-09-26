"""Supervisor script that keeps the orchestrator running."""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


def main() -> None:  # pragma: no cover - utility wrapper
    parser = argparse.ArgumentParser(description="Run orchestrator with restart guard")
    parser.add_argument("--config", type=Path, required=True, help="Agent config path")
    parser.add_argument(
        "--orchestrator-config",
        type=Path,
        default=None,
        help="Optional orchestrator config path",
    )
    parser.add_argument("--sleep", type=float, default=5.0, help="Delay before restart (seconds)")
    args = parser.parse_args()

    cmd = [
        "tmux-agent-orchestrator",
        "--config",
        str(args.config),
    ]
    if args.orchestrator_config:
        cmd += ["--orchestrator-config", str(args.orchestrator_config)]

    while True:
        proc = subprocess.run(cmd)
        if proc.returncode == 0:
            break
        time.sleep(args.sleep)


if __name__ == "__main__":  # pragma: no cover
    main()
