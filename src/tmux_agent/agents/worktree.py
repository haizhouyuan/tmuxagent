"""Git worktree helpers for agent sessions."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import List
from typing import Optional


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str


class AgentWorktreeManager:
    """Manage git worktrees for agent branches."""

    def __init__(self, repo_root: Path, worktree_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not (self.repo_root / ".git").exists():
            raise ValueError(f"{self.repo_root} 不是一个 Git 仓库")
        if worktree_root is None:
            worktree_root = self.repo_root / "worktrees"
        self.worktree_root = Path(worktree_root).resolve()
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def create(self, branch: str, *, path: Path | None = None) -> Path:
        target_path = path or self._default_path(branch)
        if target_path.exists():
            return target_path

        if self._branch_exists(branch):
            self._run_git(["worktree", "add", str(target_path), branch])
        else:
            self._run_git(["worktree", "add", str(target_path), "-b", branch])
        return target_path

    def remove(self, path: Path, *, force: bool = False) -> None:
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        try:
            self._run_git(args)
        finally:
            if force and path.exists():
                shutil.rmtree(path, ignore_errors=True)

    def list(self) -> List[WorktreeInfo]:
        output = self._run_git(["worktree", "list", "--porcelain"], capture_output=True)
        infos: list[WorktreeInfo] = []
        current_path: Optional[Path] = None
        for raw in output.splitlines():
            line = raw.strip()
            if line.startswith("worktree "):
                current_path = Path(line[9:]).resolve()
            elif line.startswith("branch ") and current_path is not None:
                branch = line[7:]
                infos.append(WorktreeInfo(path=current_path, branch=branch))
                current_path = None
        return infos

    def exists(self, branch: str) -> bool:
        safe = self._default_path(branch)
        if safe.exists():
            return True
        return any(info.branch == branch for info in self.list())

    # ------------------------------------------------------------------
    def _default_path(self, branch: str) -> Path:
        safe_branch = branch.replace("/", "-").replace(":", "-")
        return self.worktree_root / safe_branch

    def _branch_exists(self, branch: str) -> bool:
        try:
            self._run_git(["show-ref", "--verify", f"refs/heads/{branch}"])
        except subprocess.CalledProcessError:
            return False
        return True

    def _run_git(self, args: Iterable[str], capture_output: bool = False) -> str:
        cmd = ["git", *args]
        result = subprocess.run(
            cmd,
            cwd=self.repo_root,
            check=True,
            text=True,
            capture_output=capture_output,
        )
        return result.stdout if capture_output else ""
