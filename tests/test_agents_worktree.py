import subprocess
from pathlib import Path

import pytest

from tmux_agent.agents.worktree import AgentWorktreeManager


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("demo", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_worktree_create_and_list(git_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    manager = AgentWorktreeManager(git_repo, worktree_root)

    path = manager.create("feature/demo")
    assert path.exists()
    assert path.parent == worktree_root.resolve()

    infos = manager.list()
    assert any(info.branch == "refs/heads/feature/demo" for info in infos)

    # 创建已存在分支不会报错
    again = manager.create("feature/demo")
    assert again == path
    assert manager.exists("feature/demo")
