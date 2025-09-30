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
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8701, help="Port to listen on")
    parser.add_argument(
        "--templates",
        type=Path,
        default=None,
        help="Optional override for dashboard templates",
    )
    parser.add_argument(
        "--approval-dir",
        type=Path,
        default=None,
        help="Directory where approval request files are stored",
    )
    parser.add_argument("--tmux-bin", default="tmux", help="tmux binary to use")
    parser.add_argument(
        "--tmux-socket",
        default=None,
        help="Optional tmux socket name (equivalent to tmux -L)",
    )
    parser.add_argument(
        "--capture-lines",
        type=int,
        default=200,
        help="Number of lines to capture per pane when rendering output",
    )
    args = parser.parse_args()

    config = DashboardConfig(
        db_path=args.db,
        template_path=args.templates,
        approval_dir=args.approval_dir,
        tmux_bin=args.tmux_bin,
        tmux_socket=args.tmux_socket,
        capture_lines=args.capture_lines,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
