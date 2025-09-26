"""Replay utility for orchestrator audit logs."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable
from typing import Iterator
from typing import Mapping


DEFAULT_LOG_PATH = Path(".tmuxagent/logs/orchestrator-actions.jsonl")


def _iter_events(path: Path) -> Iterator[dict]:
    if not path.exists():
        return iter(())
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _filter_events(events: Iterable[dict], branch: str | None) -> list[dict]:
    if not branch:
        return list(events)
    return [event for event in events if event.get("branch") == branch]


def summarize_events(events: Iterable[dict]) -> Mapping[str, object]:
    counter: Counter[str] = Counter()
    last_command: str | None = None
    last_summary: str | None = None
    first_ts: float | None = None
    last_ts: float | None = None
    queued: int = 0
    max_queue: int = 0

    for event in events:
        event_type = event.get("event") or "unknown"
        counter[event_type] += 1
        ts = float(event.get("ts", 0))
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        if event_type == "command":
            payload = event.get("payload") or {}
            last_command = payload.get("text") or last_command
        if event_type == "queued":
            queued += 1
            max_queue = max(max_queue, queued)
        if event_type == "confirmation":
            queued = max(0, queued - 1)
        if event_type == "pending_confirmation":
            queued += 1
            max_queue = max(max_queue, queued)
        if event_type == "summary":
            last_summary = event.get("summary")

    duration = None
    if first_ts is not None and last_ts is not None:
        duration = max(0.0, last_ts - first_ts)

    return {
        "counts": dict(counter),
        "last_command": last_command,
        "last_summary": last_summary,
        "max_queue_depth": max_queue,
        "duration_seconds": duration,
        "samples": counter.total(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay orchestrator audit log")
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to orchestrator-actions.jsonl (default: .tmuxagent/logs/orchestrator-actions.jsonl)",
    )
    parser.add_argument("--branch", type=str, default=None, help="Optional branch filter")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of entries processed")
    args = parser.parse_args(argv)

    events = list(_iter_events(args.log))
    if args.limit is not None and args.limit > 0:
        events = events[-args.limit :]
    filtered = _filter_events(events, args.branch)
    summary = summarize_events(filtered)

    if not filtered:
        target = args.branch or "all branches"
        print(f"No orchestrator events found for {target} in {args.log}")
        return 0

    counts = summary["counts"]
    print(f"Replayed {summary['samples']} events from {args.log}")
    print("Event counts:")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    if summary["duration_seconds"]:
        start_ts = filtered[0].get("ts")
        end_ts = filtered[-1].get("ts")
        if start_ts:
            start_dt = datetime.fromtimestamp(float(start_ts))
            print(f"Start: {start_dt.isoformat()}")
        if end_ts:
            end_dt = datetime.fromtimestamp(float(end_ts))
            print(f"End:   {end_dt.isoformat()}")
        print(f"Duration: {summary['duration_seconds']:.2f}s")
    if summary["last_command"]:
        print(f"Last command: {summary['last_command']}")
    if summary["last_summary"]:
        print(f"Last summary: {summary['last_summary']}")
    if summary["max_queue_depth"]:
        print(f"Max queue depth: {summary['max_queue_depth']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
