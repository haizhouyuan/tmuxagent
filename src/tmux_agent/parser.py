"""Parsing utilities for agent output and tmux logs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List


@dataclass
class ParsedMessage:
    kind: str
    payload: Dict[str, Any]
    raw: str


SUCCESS_KEYWORDS = re.compile(r"\b(done|success|passed|completed)\b", re.IGNORECASE)
ERROR_KEYWORDS = re.compile(r"\b(error|failed|exception)\b", re.IGNORECASE)


def parse_messages(lines: Iterable[str]) -> List[ParsedMessage]:
    messages: list[ParsedMessage] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("### SENTRY"):
            payload_str = line[len("### SENTRY"):].strip()
            parsed = _parse_json(payload_str)
            if parsed:
                messages.append(ParsedMessage(kind=parsed.get("type", "UNKNOWN"), payload=parsed, raw=line))
                continue
        if line.startswith("{") and line.endswith("}"):
            parsed = _parse_json(line)
            if parsed:
                messages.append(ParsedMessage(kind=parsed.get("type", "UNKNOWN"), payload=parsed, raw=line))
                continue
        if ERROR_KEYWORDS.search(line):
            messages.append(ParsedMessage(kind="ERROR", payload={"text": line}, raw=line))
        elif SUCCESS_KEYWORDS.search(line):
            messages.append(ParsedMessage(kind="STATUS", payload={"ok": True, "text": line}, raw=line))
    return messages


def _parse_json(text: str) -> Dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None
