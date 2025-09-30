import subprocess
from pathlib import Path

import pytest

from tmux_agent.agent_cli import main
from tmux_agent.agents.config import write_default_config
from tmux_agent.tmux import FakeTmuxAdapter


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=path, check=True)
    (path / "README.md").write_text("demo", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_spawn_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    config_path = write_default_config(repo)
    content = config_path.read_text(encoding="utf-8").replace(
        "~/.tmux_agent/state.db", str(tmp_path / "state.db")
    )
    config_path.write_text(content, encoding="utf-8")

    template_path = config_path.parent / "templates" / "qa.md"
    template_path.write_text(
        """---
name: qa-template
description: QA checklist
model: sonnet
---
List open bugs.
""",
        encoding="utf-8",
    )

    fake_adapter = FakeTmuxAdapter()
    monkeypatch.setattr(
        "tmux_agent.agents.service.TmuxAdapter",
        lambda tmux_bin="tmux", socket=None: fake_adapter,
    )

    monkeypatch.chdir(repo)

    exit_code = main(["spawn", "feature/cli", "--template", "qa-template", "--task", "Validate"])
    assert exit_code == 0
    assert fake_adapter.session_exists("agent-feature-cli")

    from tmux_agent.state import StateStore

    store = StateStore(tmp_path / "state.db")
    try:
        sessions = store.list_agent_sessions()
    finally:
        store.close()

    target = next(row for row in sessions if row["branch"] == "feature/cli")
    assert target["template"] == "qa-template"
    assert target["model"] == "sonnet high"
    assert target["status"] == "launching"
    assert Path(target["log_path"]).exists()
    assert target["metadata"]["source"] == "cli"
