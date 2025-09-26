"""Requirement document decomposition helpers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_FILE_LINE = re.compile(r"^[`']?([\w./-]+\.[a-zA-Z0-9]+)[`']?$")


def decompose_requirements(path: Path) -> list[dict[str, Any]]:
    """Convert a markdown requirements document into ordered steps."""

    steps: list[dict[str, Any]] = []
    if not path.exists():
        return steps

    current_section: str | None = None
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("### "):
            current_section = stripped[4:].strip()
            i += 1
            continue
        match = _FILE_LINE.match(stripped)
        if match:
            steps.append(
                {
                    "type": "create_file",
                    "target": match.group(1),
                    "section": current_section,
                    "description": f"创建 {match.group(1)}",
                }
            )
            i += 1
            continue
        if stripped.startswith("```bash"):
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].strip())
                i += 1
            commands = [cmd for cmd in block if cmd and not cmd.startswith("#")]
            for cmd in commands:
                steps.append(
                    {
                        "type": "run_command",
                        "command": cmd,
                        "section": current_section,
                        "description": f"执行 {cmd}",
                    }
                )
            i += 1
            continue
        if stripped.startswith("```"):
            language = stripped.strip("`").strip()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            if language:
                steps.append(
                    {
                        "type": "code_block",
                        "language": language,
                        "lines": len(block),
                        "section": current_section,
                        "description": f"编写 {language} 代码块",
                    }
                )
            i += 1
            continue
        bullet = stripped.lstrip("-*")
        if bullet != stripped:
            summary = bullet.strip()
            if summary:
                steps.append(
                    {
                        "type": "note",
                        "section": current_section,
                        "description": summary,
                    }
                )
        i += 1
    return steps


__all__ = ["decompose_requirements"]
