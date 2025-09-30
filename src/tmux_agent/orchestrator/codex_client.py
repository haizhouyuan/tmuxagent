"""Codex CLI invocation helpers for orchestrator decisions."""
from __future__ import annotations

import json
import subprocess
import os
from dataclasses import dataclass, field
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Sequence


class CodexError(RuntimeError):
    """Raised when Codex CLI execution or parsing fails."""

    def __init__(self, message: str, *, kind: str = "unknown", raw: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.raw = raw



@dataclass(frozen=True)
class CommandSuggestion:
    """Parsed command suggestion emitted by the orchestrator."""

    text: str | None
    session: str | None = None
    enter: bool = True
    cwd: str | None = None
    risk_level: str = "low"
    notes: str | None = None
    keys: tuple[str, ...] = tuple()
    input_mode: str | None = None


@dataclass(frozen=True)
class OrchestratorDecision:
    """Normalized decision payload returned by Codex."""

    summary: str | None
    commands: tuple[CommandSuggestion, ...]
    notify: str | None
    requires_confirmation: bool
    phase: str | None
    blockers: tuple[str, ...]
    extra: dict[str, Any] = field(default_factory=dict)

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
            data = self._load_json(stdout)
        except CodexError:
            # Allow raw text summaries (fallback)
            return stdout.strip()
        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        raise CodexError("Codex summary response missing 'summary' field")

    def run_raw(self, prompt: str) -> dict[str, Any]:
        """Run Codex CLI and return raw JSON dict (for auxiliary tools)."""

        stdout = self._run_cli(prompt)
        data = self._load_json(stdout)
        if not isinstance(data, dict):
            raise CodexError(
                "Codex 输出不是 JSON 对象",
                kind="invalid_payload_type",
                raw=stdout,
            )
        return data

    @staticmethod
    def _default_env() -> Mapping[str, str]:
        return {"LC_ALL": "en_US.UTF-8"}

    def _run_cli(self, prompt: str) -> str:
        payload = (prompt or "").strip()
        if prompt and not prompt.endswith("\n\n"):
            payload = f"{payload}\n\n"
        try:
            env = {**os.environ, **self._env, **self._default_env()}
            proc = subprocess.run(
                self._executable,
                input=payload,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - real CLI scenario
            raise CodexError(
                f"Codex CLI timed out after {self._timeout:.0f}s",
                kind="timeout",
            ) from exc
        except FileNotFoundError as exc:  # pragma: no cover - requires missing binary
            raise CodexError(
                "Codex executable not found. 请确认已安装 codex CLI 并在 PATH 中可访问，"
                "或在 orchestrator 配置中指定正确的 codex.bin 路径。",
                kind="missing_executable",
            ) from exc
        if proc.returncode != 0:
            raise CodexError(
                f"Codex CLI failed with code {proc.returncode}: {proc.stderr.strip()}",
                kind="non_zero_exit",
                raw=proc.stdout,
            )
        return proc.stdout

    @staticmethod
    def _parse_json(payload: str) -> OrchestratorDecision:
        data = CodexClient._load_json(payload)
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
            raw_text = item.get("text")
            text = (raw_text if isinstance(raw_text, str) else "").strip()
            enter = bool(item.get("enter", True))
            session = item.get("session")
            cwd = item.get("cwd")
            risk_level = str(item.get("risk_level", "low")).lower()
            notes = item.get("notes")
            keys_raw = item.get("keys")
            keys: tuple[str, ...] = tuple()
            if isinstance(keys_raw, str):
                key_text = keys_raw.strip()
                if key_text:
                    keys = (key_text,)
            elif isinstance(keys_raw, Iterable):
                collected: list[str] = []
                for candidate in keys_raw:
                    if not isinstance(candidate, str):
                        candidate = str(candidate)
                    normalized = candidate.strip()
                    if normalized:
                        collected.append(normalized)
                keys = tuple(collected)
            input_mode = item.get("input_mode")
            if isinstance(input_mode, str):
                input_mode = input_mode.strip() or None
            else:
                input_mode = None
            if not text and not keys:
                continue
            commands.append(
                CommandSuggestion(
                    text=text or None,
                    session=session,
                    enter=enter,
                    cwd=cwd,
                    risk_level=risk_level,
                    notes=notes if isinstance(notes, str) else None,
                    keys=keys,
                    input_mode=input_mode,
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
        known_keys = {
            "summary",
            "commands",
            "notify",
            "requires_confirmation",
            "phase",
            "blockers",
        }
        extras = {k: v for k, v in data.items() if k not in known_keys}
        return OrchestratorDecision(
            summary=summary if isinstance(summary, str) else None,
            notify=notify if isinstance(notify, str) else None,
            commands=tuple(commands),
            requires_confirmation=requires_confirmation,
            phase=phase_value,
            blockers=blockers,
            extra=extras,
        )

    @classmethod
    def _load_json(cls, payload: str) -> dict[str, Any]:
        # First try to parse as JSONL format (codex exec output)
        jsonl_result = cls._parse_jsonl_format(payload)
        if jsonl_result is not None:
            return jsonl_result

        # Fallback to original JSON parsing logic
        normalized = cls._normalize_payload(payload)
        attempts = [normalized]
        extracted = cls._extract_json_segment(normalized)
        if extracted not in attempts:
            attempts.append(extracted)
        for candidate in attempts:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
            raise CodexError(
                "Codex 输出不是 JSON 对象，请检查提示模板。",
                kind="invalid_payload_type",
                raw=candidate,
            )
        snippet = cls._summarize_payload(attempts[-1])
        raise CodexError(
            f"Codex 输出无法解析为 JSON：{snippet}",
            kind="json_parse_error",
            raw=attempts[-1],
        )

    @classmethod
    def _parse_jsonl_format(cls, payload: str) -> dict[str, Any] | None:
        """Extract JSON from JSONL format output from codex exec --json."""

        lines = payload.strip().split("\n")
        last_reasoning: str | None = None
        last_command_output: str | None = None
        last_error: str | None = None
        assistant_fragments: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            msg = data.get("msg")
            if isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "agent_message":
                    message = msg.get("message", "")
                    if isinstance(message, str) and message.strip():
                        parsed = cls._interpret_assistant_message(message)
                        if parsed is not None:
                            return parsed
                elif msg_type == "agent_reasoning":
                    text = msg.get("text")
                    if isinstance(text, str) and text.strip():
                        last_reasoning = text.strip()
                elif msg_type in {"agent_error", "task_failed"}:
                    detail = (
                        msg.get("error")
                        or msg.get("message")
                        or msg.get("text")
                        or str(msg)
                    )
                    if isinstance(detail, str) and detail.strip():
                        last_error = detail.strip()
                elif msg_type == "exec_command_end":
                    output = (
                        msg.get("formatted_output")
                        or msg.get("aggregated_output")
                        or msg.get("stderr")
                        or msg.get("stdout")
                    )
                    if isinstance(output, str) and output.strip():
                        last_command_output = output.strip()
                    exit_code = msg.get("exit_code")
                    if (
                        isinstance(exit_code, int)
                        and exit_code != 0
                        and (last_error or last_command_output)
                    ):
                        detail = last_error or last_command_output
                        if isinstance(detail, str) and detail:
                            last_error = f"命令退出码 {exit_code}: {detail.splitlines()[-1].strip()}"
                continue

            event_type = data.get("type")
            if not isinstance(event_type, str):
                continue
            if event_type == "item.completed":
                item = data.get("item") or {}
                if not isinstance(item, dict):
                    continue
                item_type = item.get("item_type") or item.get("type")
                text = item.get("text")
                if isinstance(item_type, str) and isinstance(text, str):
                    if item_type in {"assistant_message", "output_text", "assistant"}:
                        parsed = cls._interpret_assistant_message(text)
                        if parsed is not None:
                            return parsed
                    elif item_type in {"reasoning", "analysis"}:
                        stripped = text.strip()
                        if stripped:
                            last_reasoning = stripped
                continue
            if event_type in {"response.output_text.delta", "response.output_text.stream.delta"}:
                delta = data.get("delta") or {}
                if isinstance(delta, dict):
                    piece = delta.get("text")
                    if isinstance(piece, str) and piece:
                        assistant_fragments.append(piece)
                continue
            if event_type in {"response.output_text.done", "response.completed"}:
                if assistant_fragments:
                    message_text = "".join(assistant_fragments)
                    parsed = cls._interpret_assistant_message(message_text)
                    if parsed is not None:
                        return parsed
                continue
            if event_type in {"error", "response.error"}:
                detail = data.get("detail") or data.get("error") or data.get("message")
                if isinstance(detail, str) and detail.strip():
                    last_error = detail.strip()

        if last_error:
            summary = f"Codex 执行失败：{last_error}"
            return {
                "summary": summary,
                "commands": [],
                "requires_confirmation": True,
                "notify": summary,
            }
        if last_reasoning:
            summary = last_reasoning
            if last_command_output:
                last_line = last_command_output.splitlines()[-1].strip()
                if last_line and last_line not in summary:
                    summary = f"{summary}\n最后输出: {last_line}"
            return {
                "summary": summary,
                "commands": [],
                "requires_confirmation": False,
            }
        if last_command_output:
            return {
                "summary": last_command_output,
                "commands": [],
                "requires_confirmation": False,
            }
        return None

    @staticmethod
    def _sanitize_message_text(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = CodexClient._strip_code_fence(cleaned)
        return cleaned.strip()

    @classmethod
    def _interpret_assistant_message(cls, message: str) -> dict[str, Any] | None:
        cleaned = cls._sanitize_message_text(message)
        if not cleaned:
            return None
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            summary = cleaned.strip()
            if not summary:
                return None
            return {
                "summary": summary,
                "commands": [],
                "requires_confirmation": False,
            }
        if isinstance(data, dict):
            return data
        raise CodexError(
            "Codex 输出不是 JSON 对象，请检查提示模板。",
            kind="invalid_payload_type",
            raw=cleaned,
        )

    @staticmethod
    def _normalize_payload(payload: str) -> str:
        text = (payload or "").strip()
        if not text:
            raise CodexError("Codex 返回空输出", kind="empty_output", raw=payload)
        if text.startswith("```"):
            text = CodexClient._strip_code_fence(text)
        return text.strip()

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        trimmed = text[3:]
        newline_index = trimmed.find("\n")
        if newline_index != -1:
            trimmed = trimmed[newline_index + 1 :]
        if trimmed.endswith("```"):
            trimmed = trimmed[:-3]
        return trimmed

    @staticmethod
    def _extract_json_segment(text: str) -> str:
        candidates: list[str] = []
        for open_char, close_char in (("{", "}"), ("[", "]")):
            start = text.find(open_char)
            end = text.rfind(close_char)
            if start != -1 and end != -1 and end > start:
                candidates.append(text[start : end + 1])
        for candidate in candidates:
            try:
                json.loads(candidate)
            except json.JSONDecodeError:
                continue
            else:
                return candidate
        return text

    @staticmethod
    def _summarize_payload(payload: str, limit: int = 200) -> str:
        compact = " ".join(payload.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


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
