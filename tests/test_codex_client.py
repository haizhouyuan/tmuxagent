from __future__ import annotations

import pytest

from tmux_agent.orchestrator.codex_client import CodexClient, CodexError


def _parse(payload: str):
    return CodexClient._parse_json(payload)


def test_parse_json_with_code_fence():
    payload = """```json\n{\n  \"summary\": \"ok\",\n  \"commands\": [],\n  \"requires_confirmation\": false\n}\n```"""
    decision = _parse(payload)
    assert decision.summary == "ok"
    assert decision.commands == ()


def test_parse_json_with_wrapped_text():
    payload = "Result follows:\n{\n  \"summary\": \"ok\",\n  \"commands\": [{\n    \"text\": \"echo hi\"\n  }]\n}\nThanks"
    decision = _parse(payload)
    assert decision.summary == "ok"
    assert decision.commands[0].text == "echo hi"


def test_parse_json_invalid_payload_raises_codex_error():
    with pytest.raises(CodexError) as exc:
        _parse("not json at all")
    assert exc.value.kind == "json_parse_error"


def test_parse_json_non_object_payload():
    with pytest.raises(CodexError) as exc:
        _parse("[1, 2, 3]")
    assert exc.value.kind == "invalid_payload_type"


def test_parse_jsonl_agent_message_with_code_block():
    payload = (
        "{\"id\":\"0\",\"msg\":{\"type\":\"agent_message\","
        "\"message\":\"```json\\n{\\n  \\\"summary\\\": \\\"ok\\\"\\n}\\n```\"}}"
    )
    decision = _parse(payload)
    assert decision.summary == "ok"
    assert decision.commands == ()


def test_parse_jsonl_without_agent_message_uses_reasoning():
    payload = (
        "{\"id\":\"0\",\"msg\":{\"type\":\"agent_reasoning\","
        "\"text\":\"Investigating config\"}}"
    )
    decision = _parse(payload)
    assert decision.summary.startswith("Investigating config")
    assert decision.commands == ()
    assert decision.requires_confirmation is False


def test_parse_jsonl_task_failed_produces_error_decision():
    payload = (
        "{\"id\":\"0\",\"msg\":{\"type\":\"task_failed\","
        "\"error\":\"Model aborted\"}}"
    )
    decision = _parse(payload)
    assert "Model aborted" in decision.summary
    assert decision.commands == ()
    assert decision.requires_confirmation is True
    assert decision.notify == decision.summary
