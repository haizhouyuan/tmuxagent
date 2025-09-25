"""Utilities to summarize pane output using the Claude CLI."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from typing import Literal
from typing import Optional

from .panes import PaneService

ANSI_PATTERN = re.compile(r"\x1B\[[0-9;?]*[ -/]*[@-~]")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PROMPT_DEFAULT = (
    "请阅读以下终端输出，提炼当前状态、关键错误/风险、影响范围，并给出按优先级排序的下一步可执行操作。"
    "若存在命令建议，请直接给出可复制的命令。"
)
DEFAULT_MODEL = os.getenv("TMUX_AGENT_CLAUDE_MODEL", "claude-sonnet-4-20250514")


@dataclass
class SummaryOptions:
    prompt: str = PROMPT_DEFAULT
    lines: int = 5000
    max_chars: int = 600_000
    model: str = DEFAULT_MODEL
    max_turns: int = 2
    output_format: Literal["json", "text"] = "json"


@dataclass
class SummaryResult:
    pane_id: str
    summary: str
    model: str
    duration_ms: Optional[int]
    cost_usd: Optional[float]
    raw: dict[str, Any] | None = None


class PaneSummaryError(RuntimeError):
    """Raised when the Claude CLI fails to produce a summary."""


class PaneSummarizer:
    """Orchestrates capturing pane output and invoking the Claude CLI."""

    def __init__(
        self,
        *,
        cli_path: str = "claude",
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._cli_path = cli_path
        self._default_model = default_model
        if shutil.which(cli_path) is None:
            raise PaneSummaryError(
                f"无法找到 Claude CLI ({cli_path})，请先安装并登录 claude 命令行工具。"
            )

    def summarize(
        self,
        pane_service: PaneService,
        pane_id: str,
        *,
        options: SummaryOptions | None = None,
    ) -> SummaryResult:
        opts = options or SummaryOptions()
        capture = pane_service.capture(pane_id)
        cleaned = self._prepare_text(capture.content, opts.lines, opts.max_chars)
        if not cleaned.strip():
            raise PaneSummaryError("目标 pane 没有可总结的输出。")

        payload = self._invoke_claude(
            cleaned,
            prompt=opts.prompt,
            model=opts.model or self._default_model,
            output_format=opts.output_format,
            max_turns=opts.max_turns,
        )
        return SummaryResult(
            pane_id=pane_id,
            summary=payload["summary"],
            model=payload["model"],
            duration_ms=payload.get("duration_ms"),
            cost_usd=payload.get("total_cost_usd"),
            raw=payload.get("raw"),
        )

    @staticmethod
    def _prepare_text(text: str, lines: int, max_chars: int) -> str:
        stripped = ANSI_PATTERN.sub("", text)
        stripped = CONTROL_PATTERN.sub("", stripped)
        if lines and lines > 0:
            stripped = "\n".join(stripped.splitlines()[-lines:])
        if max_chars and max_chars > 0 and len(stripped) > max_chars:
            stripped = stripped[-max_chars:]
        return stripped

    def _invoke_claude(
        self,
        content: str,
        *,
        prompt: str,
        model: str,
        output_format: Literal["json", "text"],
        max_turns: int,
    ) -> dict[str, Any]:
        env = {
            "CLAUDE_TELEMETRY_MODE": "disabled",
            "CLAUDE_SHELL_AUTO_UPDATE": "0",
            **os.environ,
        }

        cmd = [
            self._cli_path,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--max-turns",
            str(max_turns),
            "--model",
            model,
        ]

        start = time.perf_counter()
        proc = subprocess.run(
            cmd,
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0:
            message = stderr or stdout or f"Claude CLI 退出码 {proc.returncode}"
            raise PaneSummaryError(message)

        json_payload = self._parse_cli_json(stdout)
        if not json_payload:
            raise PaneSummaryError(stdout or "未能解析 Claude CLI 输出")

        model_used = json_payload.get("model")
        if not model_used and "modelUsage" in json_payload:
            model_usage = json_payload["modelUsage"]
            if isinstance(model_usage, dict):
                model_used = next(iter(model_usage.keys()), model)
        model_used = model_used or model

        result_text = json_payload.get("result")
        if not isinstance(result_text, str):
            raise PaneSummaryError("Claude CLI 未返回文本结果")

        payload: dict[str, Any] = {
            "summary": result_text.strip(),
            "model": model_used,
            "duration_ms": json_payload.get("duration_ms", duration_ms),
            "total_cost_usd": json_payload.get("total_cost_usd"),
            "raw": json_payload,
        }
        return payload

    @staticmethod
    def _parse_cli_json(output: str) -> dict[str, Any] | None:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        return None

    @property
    def default_model(self) -> str:
        return self._default_model
