"""Template parsing utilities for Claude/Codex subagents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import Iterator
from typing import Optional

import yaml


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    description: str | None
    model: str | None
    body: str
    path: Path


def load_templates(directory: Path) -> dict[str, TemplateSpec]:
    templates: dict[str, TemplateSpec] = {}
    if not directory.exists():
        return templates
    for path in directory.glob("*.md"):
        spec = parse_template(path)
        templates[spec.name] = spec
    return templates


def parse_template(path: Path) -> TemplateSpec:
    text = path.read_text(encoding="utf-8")
    meta, body = _split_front_matter(text)
    name = meta.get("name") or path.stem
    description = meta.get("description") if isinstance(meta, dict) else None
    model = meta.get("model") if isinstance(meta, dict) else None
    cleaned_body = body.strip()
    return TemplateSpec(name=name, description=description, model=model, body=cleaned_body, path=path)


def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index: Optional[int] = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = idx
            break
    if end_index is None:
        return {}, text
    front = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    meta = yaml.safe_load(front) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body
