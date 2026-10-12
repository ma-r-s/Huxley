"""A peer that vanishes mid-send is dropped by the kernel within a bound.

2026-10-12: the walkie was switched off mid-reply; with unacknowledged
audio in the socket, the kernel retransmitted for ~15 min and the
websockets keepalive (its ping waits behind the full buffer) never fired,
so the server believed the walkie present and a proactive turn would
have been said into a dead socket (T1.44).
"""

from __future__ import annotations

import socket
import sys
from typing import TYPE_CHECKING, Any

from huxley.server import server as srv

if TYPE_CHECKING:
    import pytest


class FakeSock:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self.fail = fail

    def setsockopt(self, level: int, opt: int, value: int) -> None:
        if self.fail:
            raise OSError("not supported")
        self.calls.append((level, opt, value))


def test_linux_sets_tcp_user_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "TCP_USER_TIMEOUT", 18, raising=False)
    sock = FakeSock()
    assert srv.bound_unacked_data(sock) == "tcp_user_timeout"
    assert sock.calls == [(socket.IPPROTO_TCP, 18, srv.UNACKED_DATA_TIMEOUT_MS)]


def test_macos_falls_back_to_rxt_conndroptime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(socket, "TCP_USER_TIMEOUT", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    sock = FakeSock()
    assert srv.bound_unacked_data(sock, 30_000) == "tcp_rxt_conndroptime"
    assert sock.calls == [(socket.IPPROTO_TCP, 0x80, 30)]


def test_failures_and_missing_socket_are_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "TCP_USER_TIMEOUT", 18, raising=False)
    assert srv.bound_unacked_data(FakeSock(fail=True)) is None
    assert srv.bound_unacked_data(None) is None


def test_a_real_socket_accepts_the_option() -> None:
    """On this machine's real TCP stack the call succeeds (Linux or macOS)."""
    a = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        used: Any = srv.bound_unacked_data(a)
        assert used in ("tcp_user_timeout", "tcp_rxt_conndroptime")
    finally:
        a.close()
