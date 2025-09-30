from pathlib import Path

from tmux_agent.agents.templates import load_templates
from tmux_agent.agents.templates import parse_template


def test_parse_template_with_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "demo.md"
    path.write_text(
        """---
name: python-pro
description: Python 专家
model: sonnet
---
Here is the body.
""",
        encoding="utf-8",
    )
    spec = parse_template(path)
    assert spec.name == "python-pro"
    assert spec.description == "Python 专家"
    assert spec.model == "sonnet"
    assert "Here is the body" in spec.body


def test_load_templates(tmp_path: Path) -> None:
    (tmp_path / "first.md").write_text("---\nname: a\n---\nbody", encoding="utf-8")
    (tmp_path / "second.md").write_text("content", encoding="utf-8")
    templates = load_templates(tmp_path)
    assert set(templates.keys()) == {"a", "second"}
