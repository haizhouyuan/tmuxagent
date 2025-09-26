"""CLI helper to run the local agent portal."""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from tmux_agent.bus_server import build_app_from_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tmux-agent mobile portal")
    parser.add_argument("--config", type=Path, required=True, help="Path to agent config YAML")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="HTTP port")
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Optional auth token required via X-Auth-Token header",
    )
    args = parser.parse_args()

    app = build_app_from_config(args.config, auth_token=args.token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
