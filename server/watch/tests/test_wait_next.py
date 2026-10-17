"""The wait between cycles: cut short when the walkie comes back while it
is marked offline (2026-10-17: a four-minute visit fell between cycles)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from huxley_watch.__main__ import QUICK_S, wait_next


class FakeStop:
    def __init__(self, clock: list[float], stop_at: float | None = None) -> None:
        self.clock = clock
        self.stop_at = stop_at
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stop_at is not None and self.clock[0] >= self.stop_at

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.clock[0] += seconds
        return self.is_set()


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


def _watcher(open_ids: set[str]) -> Any:
    cfg = SimpleNamespace(interval_s=300.0, health_url=None, server_port=8765)
    return SimpleNamespace(cfg=cfg, state=SimpleNamespace(open=open_ids), log=FakeLog())


def test_no_offline_alert_waits_the_whole_interval() -> None:
    clock = [0.0]
    stop = FakeStop(clock)
    asked: list[str] = []
    wait_next(stop, _watcher(set()), lambda url: asked.append(url) or True, lambda: clock[0])  # type: ignore[arg-type]
    assert stop.waits == [300.0] and asked == []


def test_walkie_back_cuts_the_wait_short() -> None:
    clock = [0.0]
    stop = FakeStop(clock)
    answers = iter([False, None, True])
    watcher = _watcher({"walkie.offline"})
    wait_next(stop, watcher, lambda url: next(answers), lambda: clock[0])  # type: ignore[arg-type]
    assert stop.waits == [QUICK_S, QUICK_S, QUICK_S]
    assert watcher.log.events == [("watch.walkie_back_early", {"waited_s": 45})]


def test_still_offline_ends_at_the_interval() -> None:
    clock = [0.0]
    stop = FakeStop(clock)
    asked: list[str] = []

    def connected(url: str) -> bool:
        asked.append(url)
        return False

    wait_next(stop, _watcher({"walkie.offline"}), connected, lambda: clock[0])  # type: ignore[arg-type]
    assert sum(stop.waits) == 300.0
    assert set(asked) == {"http://127.0.0.1:8765/health"}


def test_a_stop_ends_the_wait() -> None:
    clock = [0.0]
    stop = FakeStop(clock, stop_at=20.0)
    wait_next(stop, _watcher({"walkie.offline"}), lambda url: False, lambda: clock[0])  # type: ignore[arg-type]
    assert clock[0] < 300.0
