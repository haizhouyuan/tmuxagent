"""CLI entry point for the dashboard server."""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_app
from .config import DashboardConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="tmux-agent dashboard server")
    parser.add_argument("--db", type=Path, default=Path("~/.tmux_agent/state.db"), help="Path to SQLite state database")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8700, help="Port to listen on")
    parser.add_argument(
        "--templates",
        type=Path,
        default=None,
        help="Optional override for dashboard templates",
    )
    parser.add_argument(
        "--approval-dir",
        type=Path,
        default=Path("~/.tmux_agent/approvals"),
        help="Path to approval directory (defaults to ~/.tmux_agent/approvals)",
    )
    parser.add_argument("--username", type=str, default=None, help="Basic auth username (optional)")
    parser.add_argument("--password", type=str, default=None, help="Basic auth password (optional)")
    args = parser.parse_args()

    config = DashboardConfig(
        db_path=args.db,
        template_path=args.templates,
        approval_dir=args.approval_dir,
        username=args.username,
        password=args.password,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
