#!/usr/bin/env python3
"""Lightweight Codex-compatible CLI wrapper for DeepSeek Responses API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


API_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a tool that must answer strictly in JSON. "
    "Do not add any explanations, code fences, or extra text outside the JSON."
)


def read_prompt() -> str:
    payload = sys.stdin.read()
    if not payload.strip():
        raise SystemExit("No prompt provided on stdin")
    return payload


def call_deepseek(api_key: str, model: str, prompt: str, timeout: float) -> str:
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.2,
    }
    data = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - depends on runtime errors
        details = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"DeepSeek API error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise RuntimeError(f"DeepSeek returned non-JSON payload: {body}") from exc

    response_text: str | None = None
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    response_text = content
            if response_text is None:
                delta = first.get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        response_text = content
    if response_text is None and "output_text" in payload:
        candidate = payload["output_text"]
        if isinstance(candidate, str):
            response_text = candidate
    if response_text is None:
        raise RuntimeError(f"Unexpected DeepSeek response format: {payload}")

    text = response_text.strip()
    if not text:
        raise RuntimeError("DeepSeek returned empty response")

    # Ensure plain JSON without code fences or commentary
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`")
    if "```" in text:
        start = text.find("```json")
        if start != -1:
            start = text.find("\n", start)
            end = text.find("```", start + 1)
            if start != -1 and end != -1:
                text = text[start + 1 : end].strip()

    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek response is not valid JSON: {text}") from exc

    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex-compatible DeepSeek CLI")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek model name")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request timeout in seconds (default: 120)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("Environment variable DEEPSEEK_API_KEY is required")

    prompt = read_prompt()
    response_text = call_deepseek(api_key, args.model, prompt, args.timeout)
    sys.stdout.write(response_text.rstrip("\n") + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
