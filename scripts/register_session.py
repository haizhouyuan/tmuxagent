#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tmux_agent.state import StateStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register existing tmux agent session")
    parser.add_argument("branch", help="Logical branch name，例如 storyapp/tts-delivery")
    parser.add_argument("worktree", help="工作区路径")
    parser.add_argument("session", help="tmux 会话名，例如 agent-storyapp-tts-delivery")
    parser.add_argument("--model", default="gpt-5-codex high")
    parser.add_argument("--template", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--log", default=None, help="日志文件路径（可选）")
    parser.add_argument("--metadata", default=None, help="JSON 字符串附加元信息")
    parser.add_argument("--state-db", default="~/.tmux_agent/state.db")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state_path = Path(args.state_db).expanduser()
    store = StateStore(state_path)
    try:
        metadata = json.loads(args.metadata) if args.metadata else {}
        now = int(time.time())
        store.upsert_agent_session(
            branch=args.branch,
            worktree_path=args.worktree,
            session_name=args.session,
            model=args.model,
            template=args.template,
            description=args.description,
            status="orchestrated",
            log_path=args.log,
            metadata=metadata,
            last_output=None,
            last_output_at=now,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
