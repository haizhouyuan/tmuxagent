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


def read_prompt() -> str:
    payload = sys.stdin.read()
    if not payload.strip():
        raise SystemExit("No prompt provided on stdin")
    return payload


def call_deepseek(api_key: str, model: str, prompt: str, timeout: float) -> str:
    request_body = {
        "model": model,
        "messages": [
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

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and "content" in message:
                return str(message["content"])
            delta = first.get("delta")
            if isinstance(delta, dict) and "content" in delta:
                return str(delta["content"])
    if "output_text" in payload:
        return str(payload["output_text"])
    raise RuntimeError(f"Unexpected DeepSeek response format: {payload}")


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
