"""Notification adapters."""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass
class NotificationMessage:
    title: str
    body: str


class Notifier:
    def __init__(self, channel: str = "stdout") -> None:
        self.channel = channel

    def send(self, message: NotificationMessage) -> None:
        if self.channel == "stdout":
            print(f"[NOTIFY] {message.title}\n{message.body}")
            return
        if self.channel == "serverchan":
            self._send_serverchan(message)
            return
        if self.channel == "wecom":
            self._send_wecom(message)
            return
        raise ValueError(f"Unknown notification channel: {self.channel}")

    def _send_serverchan(self, message: NotificationMessage) -> None:
        key = os.getenv("SENTRY_SERVERCHAN_KEY", "").strip()
        if not key:
            raise RuntimeError("SENTRY_SERVERCHAN_KEY environment variable not set")
        url = f"https://sctapi.ftqq.com/{key}.send"
        data = {
            "title": message.title,
            "desp": message.body,
        }
        with httpx.Client(timeout=5.0) as client:
            response = client.post(url, data=data)
            response.raise_for_status()

    def _send_wecom(self, message: NotificationMessage) -> None:
        webhook = os.getenv("WECOM_WEBHOOK", "").strip()
        if not webhook:
            raise RuntimeError("WECOM_WEBHOOK environment variable not set")
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{message.title}**\n\n{message.body}",
            },
        }
        with httpx.Client(timeout=5.0) as client:
            response = client.post(webhook, json=payload)
            response.raise_for_status()


class MockNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__(channel="stdout")
        self.sent: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> None:
        self.sent.append(message)
