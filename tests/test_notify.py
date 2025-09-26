import pytest

import httpx

from tmux_agent.notify import NotificationMessage, Notifier


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("HTTP error", request=None, response=None)


class FakeClient:
    def __init__(self, token_payload, send_payload):
        self._token_payload = token_payload
        self._send_payload = send_payload
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        if self._token_payload is None:
            raise AssertionError("unexpected token fetch")
        self.requests.append(("get", url, params))
        return FakeResponse(self._token_payload)

    def post(self, url, params=None, json=None, data=None):
        self.requests.append(("post", url, params, json if json is not None else data))
        payload = self._send_payload
        if payload is None:
            payload = {"errcode": 0, "errmsg": "ok"}
        return FakeResponse(payload)


class SequencedClientFactory:
    def __init__(self, entries):
        self._entries = list(entries)
        self.instances = []
        self._index = 0

    def __call__(self, *args, **kwargs):
        if self._index < len(self._entries):
            entry = self._entries[self._index]
        else:
            entry = {"token": None, "send": None}
        self._index += 1
        client = FakeClient(entry.get("token"), entry.get("send"))
        self.instances.append(client)
        return client


def _set_required_env(monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "ww963453c57ce43b45")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    monkeypatch.setenv("WECOM_APP_SECRET", "secret-value")
    monkeypatch.delenv("WECOM_TOPARTY", raising=False)
    monkeypatch.delenv("WECOM_TOTAG", raising=False)
    monkeypatch.delenv("WECOM_TOUSER", raising=False)


def test_wecom_app_missing_env(monkeypatch):
    for key in [
        "WECOM_CORP_ID",
        "WECOM_AGENT_ID",
        "WECOM_APP_SECRET",
        "WECOM_TOUSER",
        "WECOM_TOPARTY",
        "WECOM_TOTAG",
    ]:
        monkeypatch.delenv(key, raising=False)
    notifier = Notifier(channel="wecom_app")
    notifier.send(NotificationMessage(title="T", body="B"))
    assert "wecom_app" in notifier._disabled_channels


def test_wecom_app_token_error(monkeypatch):
    _set_required_env(monkeypatch)
    factory = SequencedClientFactory([
        {"token": {"errcode": 40013, "errmsg": "invalid corpid"}, "send": None}
    ])
    monkeypatch.setattr(httpx, "Client", factory)
    notifier = Notifier(channel="wecom_app")
    with pytest.raises(RuntimeError):
        notifier.send(NotificationMessage(title="告警", body="token"))
    assert factory.instances
    assert factory.instances[0].requests[0][0] == "get"


def test_wecom_app_send_success_with_caching(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("WECOM_TOUSER", "xiaozhang|xiaoli")
    entries = [
        {
            "token": {"errcode": 0, "access_token": "token123", "expires_in": 7200},
            "send": {"errcode": 0, "errmsg": "ok"},
        },
        {
            "token": None,
            "send": {"errcode": 0, "errmsg": "ok"},
        },
    ]
    factory = SequencedClientFactory(entries)
    monkeypatch.setattr(httpx, "Client", factory)

    notifier = Notifier(channel="wecom_app")
    message = NotificationMessage(title="调度提醒", body="CI 构建待审批")
    notifier.send(message)
    notifier.send(message)

    assert len(factory.instances) == 2
    first_requests = factory.instances[0].requests
    second_requests = factory.instances[1].requests
    assert any(call[0] == "get" for call in first_requests)
    assert all(call[0] != "get" for call in second_requests)
    send_call = second_requests[-1]
    assert send_call[0] == "post"
    assert send_call[2] == {"access_token": "token123"}
    payload = send_call[3]
    assert payload["touser"] == "xiaozhang|xiaoli"
    assert payload["agentid"] == 1000002
    assert payload["markdown"]["content"].startswith("**调度提醒**")


def test_local_bus_channel(tmp_path):
    from tmux_agent.local_bus import LocalBus

    bus = LocalBus(tmp_path / 'bus')
    notifier = Notifier(channel='local_bus', bus=bus)
    notifier.send(NotificationMessage(title='测试', body='本地总线'))
    items = bus.recent_notifications(limit=5)
    assert any(item['title'] == '测试' for item in items)


def test_multi_channel_includes_bus(tmp_path, monkeypatch):
    from tmux_agent.local_bus import LocalBus

    sent: list[str] = []

    def fake_wecom(message):
        sent.append(message.title)

    bus = LocalBus(tmp_path / 'bus')
    notifier = Notifier(channel='wecom,local_bus', bus=bus)
    monkeypatch.setenv('WECOM_WEBHOOK', 'https://example.test/webhook')
    monkeypatch.setattr(Notifier, '_send_wecom', lambda self, msg: fake_wecom(msg))

    message = NotificationMessage(title='复合通知', body='hello')
    notifier.send(message)

    assert '复合通知' in sent
    records = bus.recent_notifications(limit=5)
    assert any(rec['title'] == '复合通知' for rec in records)


def test_wecom_channel_missing_env(monkeypatch):
    monkeypatch.delenv('WECOM_WEBHOOK', raising=False)
    notifier = Notifier(channel='wecom')
    notifier.send(NotificationMessage(title='T', body='B'))
    assert 'wecom' in notifier._disabled_channels
