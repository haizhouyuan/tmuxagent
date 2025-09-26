from pathlib import Path

import pytest

from tmux_agent.local_bus import LocalBus
from tmux_agent.notify import NotificationMessage, Notifier
from tmux_agent.notify_bridge import NotificationRelay
from tmux_agent.state import StateStore


class StubNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__(channel="stdout")
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> None:  # type: ignore[override]
        self.messages.append(message)


@pytest.fixture()
def bus(tmp_path: Path) -> LocalBus:
    return LocalBus(tmp_path / "bus")


@pytest.fixture()
def state_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.db")
    yield store
    store.close()


def test_relay_sends_notifications_once(bus: LocalBus, state_store: StateStore) -> None:
    notifier = StubNotifier()
    relay = NotificationRelay(bus=bus, state_store=state_store, notifier=notifier, require_attention=False)

    bus.append_notification({"title": "测试", "body": "首次"})
    result = relay.run_once()

    assert result.processed == 1
    assert notifier.messages[0].title == "测试"
    assert notifier.messages[0].body == "首次"

    # second run without new entries should not duplicate
    result = relay.run_once()
    assert result.processed == 0
    assert len(notifier.messages) == 1


def test_relay_updates_offset(bus: LocalBus, state_store: StateStore) -> None:
    notifier = StubNotifier()
    relay = NotificationRelay(
        bus=bus,
        state_store=state_store,
        notifier=notifier,
        offset_name="notifications:bridge",
        require_attention=False,
    )

    bus.append_notification({"title": "A", "body": "1"})
    relay.run_once()

    offset = state_store.get_bus_offset("notifications:bridge")
    assert offset > 0

    bus.append_notification({"title": "B", "body": "2"})
    relay.run_once()

    titles = [msg.title for msg in notifier.messages]
    assert titles == ["A", "B"]


def test_relay_requires_attention_flag(bus: LocalBus, state_store: StateStore) -> None:
    notifier = StubNotifier()
    relay = NotificationRelay(bus=bus, state_store=state_store, notifier=notifier, require_attention=True)

    bus.append_notification({"title": "ignore", "body": "no meta"})
    relay.run_once()
    assert notifier.messages == []

    bus.append_notification({"title": "ok", "body": "meta", "meta": {"requires_attention": True}})
    relay.run_once()
    assert [msg.title for msg in notifier.messages] == ["ok"]
