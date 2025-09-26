"""Heuristics for generating next-action recommendations."""
from __future__ import annotations

import time
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from .service import AgentSnapshot


def build_recommendations(snapshot: AgentSnapshot) -> dict[str, Any]:
    """Generate structured next actions based on current metadata."""

    metadata = snapshot.metadata
    recommendations: list[dict[str, Any]] = []

    blockers = _string_list(metadata.get("blockers"))
    if blockers:
        recommendations.append(
            {
                "title": "解除 blocker",
                "detail": blockers[0],
                "priority": "high",
            }
        )

    pending_commands = _pending_commands(metadata)
    if pending_commands:
        rec = pending_commands[0]
        command_label = rec.get("text") or rec.get("command_id")
        recommendations.append(
            {
                "title": "等待命令完成",
                "detail": f"{command_label} 执行中",
                "priority": "medium",
            }
        )

    next_phase = _next_phase(metadata)
    if next_phase:
        recommendations.append(
            {
                "title": "推进阶段",
                "detail": f"准备切换到阶段 {next_phase}",
                "priority": "medium",
            }
        )

    last_failure = _recent_failure(metadata)
    if last_failure:
        recommendations.append(
            {
                "title": "复核失败命令",
                "detail": last_failure,
                "priority": "high",
            }
        )

    if not recommendations:
        recommendations.append(
            {
                "title": "保持监控",
                "detail": "暂无自动建议，保持日志巡检",
                "priority": "low",
            }
        )

    confidence = min(1.0, 0.3 + 0.2 * len(recommendations))
    evidence = {
        "phase": metadata.get("phase"),
        "blockers": blockers[:3],
        "pending_commands": [rec.get("command_id") for rec in pending_commands[:3]],
        "phase_plan": _string_list(metadata.get("phase_plan"))[:4],
    }

    return {
        "generated_at": int(time.time()),
        "confidence": round(confidence, 2),
        "recommendations": recommendations,
        "evidence": evidence,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item)
        if text:
            result.append(text)
    return result


def _pending_commands(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    tracker = metadata.get("command_tracker")
    if not isinstance(tracker, list):
        return []
    pending: list[dict[str, Any]] = []
    for item in tracker:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status in {"pending", "dry_run"}:
            pending.append(item)
    pending.sort(key=lambda item: item.get("dispatched_at") or 0)
    return pending


def _next_phase(metadata: dict[str, Any]) -> str | None:
    phase_plan = _string_list(metadata.get("phase_plan"))
    if not phase_plan:
        return None
    current = str(metadata.get("phase") or "")
    history = _string_list(metadata.get("phase_history"))
    for phase in phase_plan:
        if phase == current:
            continue
        if phase not in history:
            return phase
    return None


def _recent_failure(metadata: dict[str, Any]) -> str | None:
    history = metadata.get("command_history")
    if not isinstance(history, list):
        return None
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status == "failed":
            command_id = item.get("command_id")
            text = item.get("text")
            if text:
                return f"{text} 退出码 {item.get('exit_code')}"
            if command_id:
                return f"{command_id} 退出码 {item.get('exit_code')}"
            return "最近命令失败"
        if status in {"succeeded", "success", "ok"}:
            break
    return None


__all__ = ["build_recommendations"]
