"""Notification adapters."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Sequence

import httpx

from .local_bus import LocalBus


logger = logging.getLogger(__name__)


@dataclass
class NotificationMessage:
    title: str
    body: str
    meta: Mapping[str, Any] | None = None


class Notifier:
    def __init__(self, channel: str = "stdout", *, bus: LocalBus | None = None) -> None:
        channels = [item.strip() for item in channel.split(',') if item.strip()]
        if not channels:
            raise ValueError("notification channel must not be empty")
        self.channels: Sequence[str] = channels
        self.bus = bus
        self._wecom_app_token: str | None = None
        self._wecom_app_token_expiry: float = 0.0
        self._disabled_channels: set[str] = set()

    def send(self, message: NotificationMessage) -> None:
        for channel in self.channels:
            self._dispatch(channel, message)

    # Dispatch helpers -----------------------------------------------------
    def _dispatch(self, channel: str, message: NotificationMessage) -> None:
        if channel in self._disabled_channels:
            return
        if channel == "stdout":
            print(f"[NOTIFY] {message.title}\n{message.body}")
            return
        if channel == "serverchan":
            self._send_serverchan(message)
            return
        if channel == "wecom":
            self._send_wecom(message)
            return
        if channel == "wecom_app":
            self._send_wecom_app(message)
            return
        if channel == "local_bus":
            self._send_local_bus(message)
            return
        raise ValueError(f"Unknown notification channel: {channel}")

    def _send_local_bus(self, message: NotificationMessage) -> None:
        if not self.bus:
            raise RuntimeError("local bus channel requested but LocalBus instance is not configured")
        payload = {
            "title": message.title,
            "body": message.body,
            "source": "notifier",
        }
        if message.meta:
            payload["meta"] = dict(message.meta)
        self.bus.append_notification(payload)

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
            self._disable_channel_once("wecom", "WECOM_WEBHOOK environment variable not set")
            return
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{message.title}**\n\n{message.body}",
            },
        }
        with httpx.Client(timeout=5.0) as client:
            response = client.post(webhook, json=payload)
            response.raise_for_status()

    def _send_wecom_app(self, message: NotificationMessage) -> None:
        corp_id = os.getenv("WECOM_CORP_ID", "").strip()
        agent_id = os.getenv("WECOM_AGENT_ID", "").strip()
        app_secret = os.getenv("WECOM_APP_SECRET", "").strip()
        if not corp_id or not agent_id or not app_secret:
            self._disable_channel_once(
                "wecom_app",
                "WECOM_CORP_ID, WECOM_AGENT_ID, and WECOM_APP_SECRET must be set",
            )
            return

        touser = os.getenv("WECOM_TOUSER")
        if touser is None or not touser.strip():
            touser = "@all"
        else:
            touser = touser.strip()
        toparty = os.getenv("WECOM_TOPARTY", "").strip()
        totag = os.getenv("WECOM_TOTAG", "").strip()

        with httpx.Client(timeout=5.0) as client:
            token = self._ensure_wecom_app_token(client, corp_id, app_secret)
            payload: dict[str, Any] = {
                "touser": touser,
                "agentid": int(agent_id) if agent_id.isdigit() else agent_id,
                "msgtype": "markdown",
                "markdown": {
                    "content": f"**{message.title}**\n\n{message.body}",
                },
                "safe": 0,
            }
            if toparty:
                payload["toparty"] = toparty
            if totag:
                payload["totag"] = totag
            response = client.post(
                "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                params={"access_token": token},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("errcode") != 0:
                raise RuntimeError("WeCom send message failed: " + str(data))

    def _ensure_wecom_app_token(self, client: httpx.Client, corp_id: str, app_secret: str) -> str:
        now = time.time()
        if self._wecom_app_token and now < self._wecom_app_token_expiry:
            return self._wecom_app_token
        response = client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": app_secret},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") != 0 or not data.get("access_token"):
            raise RuntimeError("WeCom gettoken failed: " + str(data))
        token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 7200))
        self._wecom_app_token = token
        self._wecom_app_token_expiry = now + max(expires_in - 60, 60)
        return token

    def _disable_channel_once(self, channel: str, reason: str) -> None:
        if channel in self._disabled_channels:
            return
        logger.warning("Notification channel '%s' disabled: %s", channel, reason)
        self._disabled_channels.add(channel)
        if self.bus:
            payload = {
                "title": "通知通道已降级",
                "body": f"channel={channel} disabled: {reason}",
                "source": "notifier",
                "meta": {
                    "severity": "warning",
                    "kind": "channel_disabled",
                    "channel": channel,
                },
            }
            try:
                self.bus.append_notification(payload)
            except Exception:  # pragma: no cover - best effort
                logger.debug("Failed to append local bus notification for channel disable")


class MockNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__(channel="stdout")
        self.sent: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> None:
        self.sent.append(message)
