"""High-level service coordinating worktrees, tmux sessions, and state."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import MutableMapping

from ..state import StateStore
from ..tmux import TmuxAdapter
from .config import FleetConfig
from .config import load_fleet_config
from .session import AgentSessionManager
from .templates import TemplateSpec
from .templates import load_templates
from .worktree import AgentWorktreeManager


@dataclass(frozen=True)
class AgentRecord:
    branch: str
    session_name: str
    worktree_path: Path
    model: str
    template: str | None
    description: str | None
    status: str
    log_path: Path | None
    metadata: Mapping[str, Any]
    last_output: str | None
    last_output_at: int | None


class AgentService:
    """Facade that orchestrates worktrees, tmux sessions, and persistence."""

    def __init__(
        self,
        config: FleetConfig | None = None,
        *,
        adapter: TmuxAdapter | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self.config = config or load_fleet_config()
        self._templates = load_templates(self.config.templates_dir)
        self._worktree_mgr = AgentWorktreeManager(
            self.config.repo_root,
            self.config.worktree_root,
        )
        self._adapter = adapter or TmuxAdapter(
            tmux_bin=self.config.tmux_bin,
            socket=self.config.tmux_socket,
        )
        self._sessions = AgentSessionManager(self._adapter, prefix=self.config.tmux_prefix)
        self._store = state_store or StateStore(self.config.state_db)

    # Lifecycle --------------------------------------------------------
    def close(self) -> None:
        self._store.close()

    # Templates --------------------------------------------------------
    def list_templates(self) -> Mapping[str, TemplateSpec]:
        return dict(self._templates)

    # Spawn ------------------------------------------------------------
    def spawn_agent(
        self,
        *,
        branch: str,
        task: str | None = None,
        template_name: str | None = None,
        model: str | None = None,
        command_template: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> AgentRecord:
        template = self._resolve_template(template_name)
        resolved_model = self._resolve_model(model, template)
        command = self._render_command(command_template, resolved_model)

        worktree_path = self._worktree_mgr.create(branch)
        session_name = self._sessions.session_name(branch)
        log_path = self._log_path_for_session(session_name)

        env: MutableMapping[str, str] = {
            "AI_AGENT_BRANCH": branch,
            "AI_AGENT_MODEL": resolved_model,
            "AI_AGENT_SESSION": session_name,
        }
        if template is not None:
            env["AI_AGENT_TEMPLATE"] = template.name
        if task:
            env["AI_AGENT_TASK"] = task
        if extra_env:
            env.update(extra_env)

        spawn_result = self._sessions.ensure_spawned(
            branch,
            worktree_path,
            command,
            env=dict(env),
            log_path=log_path,
        )

        if template is not None:
            self._sessions.send(branch, template.body, enter=True)
        if task:
            self._sessions.send(branch, task, enter=True)

        combined_metadata = self._build_metadata(template, metadata)
        self._store.upsert_agent_session(
            branch=branch,
            worktree_path=str(worktree_path),
            session_name=spawn_result.session_name,
            model=resolved_model,
            template=template.name if template else None,
            description=task,
            last_prompt=template.body if template else task,
            status="launching",
            log_path=str(log_path) if log_path else None,
            metadata=combined_metadata,
        )

        return AgentRecord(
            branch=branch,
            session_name=spawn_result.session_name,
            worktree_path=worktree_path,
            model=resolved_model,
            template=template.name if template else None,
            description=task,
            status="launching",
            log_path=log_path,
            metadata=combined_metadata,
            last_output=None,
            last_output_at=None,
        )

    # Session state ----------------------------------------------------
    def list_agents(self) -> list[AgentRecord]:
        rows = self._store.list_agent_sessions()
        records: list[AgentRecord] = []
        for row in rows:
            records.append(
                AgentRecord(
                    branch=row["branch"],
                    session_name=row["session_name"],
                    worktree_path=Path(row["worktree_path"]),
                    model=row.get("model") or self.config.default_model,
                    template=row.get("template"),
                    description=row.get("description"),
                    status=row.get("status", "unknown"),
                    log_path=Path(row["log_path"]) if row.get("log_path") else None,
                    metadata=row.get("metadata", {}),
                    last_output=row.get("last_output"),
                    last_output_at=row.get("last_output_at"),
                )
            )
        return records

    def update_status(
        self,
        branch: str,
        *,
        status: str | None = None,
        last_output: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        session = self._store.get_agent_session(branch)
        if not session:
            raise ValueError(f"未找到代理会话: {branch}")
        merged_meta = dict(session.get("metadata") or {})
        if metadata:
            merged_meta.update(metadata)
        self._store.upsert_agent_session(
            branch=branch,
            worktree_path=session["worktree_path"],
            session_name=session["session_name"],
            model=session.get("model"),
            template=session.get("template"),
            description=session.get("description"),
            last_prompt=session.get("last_prompt"),
            status=status or session.get("status"),
            log_path=session.get("log_path"),
            last_output=last_output or session.get("last_output"),
            last_output_at=int(time.time()) if last_output else session.get("last_output_at"),
            metadata=merged_meta,
        )

    def capture_recent_output(self, branch: str, lines: int = 200) -> str:
        output = self._sessions.capture_tail(branch, lines)
        self.update_status(branch, last_output=output)
        return output

    # Command helpers --------------------------------------------------
    def send(self, branch: str, text: str, *, enter: bool = True) -> None:
        self._sessions.send(branch, text, enter=enter)

    def kill(self, branch: str) -> None:
        self._sessions.kill(branch)
        self._store.delete_agent_session(branch)

    # Internals --------------------------------------------------------
    def _resolve_template(self, template_name: str | None) -> TemplateSpec | None:
        if not template_name:
            return None
        template = self._templates.get(template_name)
        if not template:
            available = ", ".join(sorted(self._templates)) or "无"
            raise ValueError(f"未找到模板 {template_name}，可用模板: {available}")
        return template

    def _resolve_model(self, model: str | None, template: TemplateSpec | None) -> str:
        candidate = model or (template.model if template and template.model else None) or self.config.default_model
        if "high" not in candidate:
            candidate = f"{candidate} high".strip()
        return candidate

    def _render_command(self, command_template: str | None, model: str) -> str:
        template = command_template or self.config.command_template
        return template.format(model=model)

    def _log_path_for_session(self, session_name: str) -> Path:
        log_dir = self.config.repo_root / ".tmuxagent" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{session_name}.log"

    def _build_metadata(
        self,
        template: TemplateSpec | None,
        metadata: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {}
        if template:
            payload.update(
                {
                    "template_description": template.description,
                    "template_path": str(template.path),
                }
            )
        if metadata:
            payload.update(metadata)
        return payload


__all__ = ["AgentService", "AgentRecord"]
