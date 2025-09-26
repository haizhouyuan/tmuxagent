"""Codex CLI invocation helpers for orchestrator decisions."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Iterable
from typing import Mapping
from typing import Sequence


class CodexError(RuntimeError):
    """Raised when Codex CLI execution or parsing fails."""



@dataclass(frozen=True)
class CommandSuggestion:
    """Parsed command suggestion emitted by the orchestrator."""

    text: str
    session: str | None = None
    enter: bool = True
    cwd: str | None = None
    risk_level: str = "low"
    notes: str | None = None


@dataclass(frozen=True)
class OrchestratorDecision:
    """Normalized decision payload returned by Codex."""

    summary: str | None
    commands: tuple[CommandSuggestion, ...]
    notify: str | None
    requires_confirmation: bool
    phase: str | None
    blockers: tuple[str, ...]

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

        stdout = self._run_cli(prompt)
        return self._parse_json(stdout)

    def run_summary(self, prompt: str) -> str:
        """Invoke Codex to obtain a textual/JSON summary."""

        stdout = self._run_cli(prompt)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Allow raw text summaries (fallback)
            return stdout.strip()
        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        raise CodexError("Codex summary response missing 'summary' field")

    @staticmethod
    def _default_env() -> Mapping[str, str]:
        return {"LC_ALL": "en_US.UTF-8"}

    def _run_cli(self, prompt: str) -> str:
        try:
            proc = subprocess.run(
                self._executable,
                input=prompt,
                env={**self._env, **self._default_env()},
                text=True,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:  # pragma: no cover - requires missing binary
            raise CodexError(
                "Codex executable not found. 请确认已安装 codex CLI 并在 PATH 中可访问，"
                "或在 orchestrator 配置中指定正确的 codex.bin 路径。"
            ) from exc
        if proc.returncode != 0:
            raise CodexError(
                f"Codex CLI failed with code {proc.returncode}: {proc.stderr.strip()}"
            )
        return proc.stdout

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
            cwd = item.get("cwd")
            risk_level = str(item.get("risk_level", "low")).lower()
            notes = item.get("notes")
            commands.append(
                CommandSuggestion(
                    text=text,
                    session=session,
                    enter=enter,
                    cwd=cwd,
                    risk_level=risk_level,
                    notes=notes if isinstance(notes, str) else None,
                )
            )
        blockers_raw = data.get("blockers") or []
        if isinstance(blockers_raw, str):
            blockers = tuple(blockers_raw.splitlines())
        elif isinstance(blockers_raw, Iterable):
            blockers = tuple(str(item) for item in blockers_raw)
        else:
            blockers = tuple()
        phase = data.get("phase")
        if isinstance(phase, str):
            phase_value = phase.strip()
        else:
            phase_value = None
        return OrchestratorDecision(
            summary=summary if isinstance(summary, str) else None,
            notify=notify if isinstance(notify, str) else None,
            commands=tuple(commands),
            requires_confirmation=requires_confirmation,
            phase=phase_value,
            blockers=blockers,
        )


class FakeCodexClient(CodexClient):  # pragma: no cover - testing utility
    def __init__(self, decisions: Iterable[OrchestratorDecision]):
        self._decisions = list(decisions)
        super().__init__(executable=["true"], env={}, timeout=0)

    def run(self, prompt: str) -> OrchestratorDecision:  # type: ignore[override]
        if not self._decisions:
            raise RuntimeError("No fake decisions remaining")
        return self._decisions.pop(0)

    def run_summary(self, prompt: str) -> str:  # type: ignore[override]
        # When used in tests without explicit summary decisions, fall back to static text.
        if self._decisions:
            decision = self._decisions[0]
            if decision.summary:
                return decision.summary
        return ""
