"""Codex CLI invocation helpers for orchestrator decisions."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Iterable
from typing import Mapping
from typing import Sequence


@dataclass(frozen=True)
class CommandSuggestion:
    """Parsed command suggestion emitted by the orchestrator."""

    text: str
    session: str | None = None
    enter: bool = True


@dataclass(frozen=True)
class OrchestratorDecision:
    """Normalized decision payload returned by Codex."""

    summary: str | None
    commands: tuple[CommandSuggestion, ...]
    notify: str | None
    requires_confirmation: bool

    @property
    def has_commands(self) -> bool:
        return bool(self.commands)


class CodexClient:
    """Simple wrapper around the Codex CLI for structured outputs."""

    def __init__(
        self,
        *,
        executable: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
    ) -> None:
        self._executable = list(executable)
        self._env = dict(env)
        self._timeout = timeout

    def run(self, prompt: str) -> OrchestratorDecision:
        """Execute Codex CLI and parse the JSON response."""

        proc = subprocess.run(
            self._executable,
            input=prompt,
            env={**self._env, **self._default_env()},
            text=True,
            capture_output=True,
            timeout=self._timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Codex CLI failed with code {proc.returncode}: {proc.stderr.strip()}"
            )
        return self._parse_json(proc.stdout)

    @staticmethod
    def _default_env() -> Mapping[str, str]:
        return {"LC_ALL": "en_US.UTF-8"}

    @staticmethod
    def _parse_json(payload: str) -> OrchestratorDecision:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Codex output is not valid JSON: {payload}") from exc
        summary = data.get("summary")
        notify = data.get("notify")
        requires_confirmation = bool(data.get("requires_confirmation"))
        commands_raw = data.get("commands") or []
        if not isinstance(commands_raw, Iterable):
            raise ValueError("Codex output 'commands' must be a list")
        commands: list[CommandSuggestion] = []
        for item in commands_raw:
            if not isinstance(item, dict):
                continue
            text = (item.get("text") or "").strip()
            if not text:
                continue
            enter = bool(item.get("enter", True))
            session = item.get("session")
            commands.append(CommandSuggestion(text=text, session=session, enter=enter))
        return OrchestratorDecision(
            summary=summary if isinstance(summary, str) else None,
            notify=notify if isinstance(notify, str) else None,
            commands=tuple(commands),
            requires_confirmation=requires_confirmation,
        )


class FakeCodexClient(CodexClient):  # pragma: no cover - testing utility
    def __init__(self, decisions: Iterable[OrchestratorDecision]):
        self._decisions = list(decisions)
        super().__init__(executable=["true"], env={}, timeout=0)

    def run(self, prompt: str) -> OrchestratorDecision:  # type: ignore[override]
        if not self._decisions:
            raise RuntimeError("No fake decisions remaining")
        return self._decisions.pop(0)
