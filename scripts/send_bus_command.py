#!/usr/bin/env python3
"""Append commands to the tmux-agent local bus.

This utility lets you向指定 tmux session 注入简单输入（例如“继续”），
以便减少人工值守。命令会写入 `~/.tmux_agent/bus/commands.jsonl`，
由正在运行的 tmux-agent 读取并发送到目标 pane。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable


def ensure_bus_file(bus_dir: Path) -> Path:
    bus_dir.mkdir(parents=True, exist_ok=True)
    commands_path = bus_dir / "commands.jsonl"
    if not commands_path.exists():
        commands_path.touch()
    return commands_path


def append_command(commands_path: Path, *, session: str, text: str, enter: bool, sender: str) -> None:
    payload = {
        "id": f"auto-{int(time.time() * 1000)}",
        "ts": time.time(),
        "session": session,
        "text": text,
        "enter": enter,
        "meta": {"sender": sender},
    }
    with commands_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a text command to tmux-agent bus")
    parser.add_argument(
        "--session",
        dest="sessions",
        action="append",
        required=True,
        help="tmux session name（例如 agent-storyapp-tts，可多次指定）",
    )
    parser.add_argument(
        "--text",
        default="继续",
        help="要发送的文本内容，默认“继续”",
    )
    parser.add_argument(
        "--no-enter",
        action="store_true",
        help="发送后不自动回车（默认会回车）",
    )
    parser.add_argument(
        "--bus-dir",
        default="~/.tmux_agent/bus",
        help="bus 目录，默认 ~/.tmux_agent/bus",
    )
    parser.add_argument(
        "--sender",
        default="auto",
        help="写入 meta.sender 字段，方便追踪来源",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bus_dir = Path(args.bus_dir).expanduser()
    commands_path = ensure_bus_file(bus_dir)

    sessions: Iterable[str] = args.sessions
    for session in sessions:
        append_command(
            commands_path,
            session=session,
            text=args.text,
            enter=not args.no_enter,
            sender=args.sender,
        )


if __name__ == "__main__":
    main()
