"""Utilities to relay local bus notifications to external channels."""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import load_agent_config
from .local_bus import LocalBus
from .notify import NotificationMessage
from .notify import Notifier
from .state import StateStore

logger = logging.getLogger(__name__)


@dataclass
class RelayResult:
    processed: int
    new_offset: int


class NotificationRelay:
    """Relay notifications written to the local bus to a ``Notifier`` instance."""

    def __init__(
        self,
        *,
        bus: LocalBus,
        state_store: StateStore,
        notifier: Notifier,
        offset_name: str = "notifications:relay",
        require_attention: bool = True,
    ) -> None:
        self.bus = bus
        self.state_store = state_store
        self.notifier = notifier
        self.offset_name = offset_name
        self.require_attention = require_attention

    def run_once(self) -> RelayResult:
        offset = self.state_store.get_bus_offset(self.offset_name)
        entries, new_offset = self.bus.read_notifications(offset)
        processed = 0
        for entry in entries:
            meta = entry.get("meta") or {}
            if self.require_attention and not meta.get("requires_attention"):
                continue
            title = (entry.get("title") or "通知").strip() or "通知"
            body = (entry.get("body") or "").strip()
            self.notifier.send(NotificationMessage(title=title, body=body, meta=meta))
            processed += 1
        if new_offset != offset:
            self.state_store.set_bus_offset(self.offset_name, new_offset)
        return RelayResult(processed=processed, new_offset=new_offset)

    def run_forever(self, interval: float = 2.0) -> None:
        logger.info("Starting notification relay (interval %.1fs)", interval)
        try:
            while True:
                result = self.run_once()
                if result.processed:
                    logger.debug("Relayed %d notifications", result.processed)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Notification relay interrupted")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Relay local bus notifications to external channels")
    parser.add_argument("--config", type=Path, required=True, help="Path to agent config YAML")
    parser.add_argument(
        "--channel",
        type=str,
        default="wecom",
        help="Notifier channels, comma separated (default: wecom)",
    )
    parser.add_argument(
        "--offset-name",
        type=str,
        default="notifications:relay",
        help="Identifier used to persist read offsets (default: notifications:relay)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval when running continuously (seconds)",
    )
    parser.add_argument(
        "--require-attention",
        action="store_true",
        default=True,
        help="Only forward notifications tagged with requires_attention",
    )
    parser.add_argument("--once", action="store_true", help="Process pending notifications once and exit")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (default: INFO)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    agent_config = load_agent_config(args.config)
    bus = LocalBus(agent_config.expanded_bus_dir())
    state_store = StateStore(agent_config.expanded_sqlite_path())
    notifier = Notifier(channel=args.channel)

    relay = NotificationRelay(
        bus=bus,
        state_store=state_store,
        notifier=notifier,
        offset_name=args.offset_name,
        require_attention=args.require_attention,
    )

    try:
        if args.once:
            result = relay.run_once()
            logger.info("Relayed %d notifications", result.processed)
        else:
            relay.run_forever(interval=args.interval)
    finally:
        state_store.close()

    return 0


__all__ = ["NotificationRelay", "RelayResult", "build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
