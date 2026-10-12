"""`server.tx.audio_stats`: once a second while audio flows, the server
says how much audio it handed to the client and how much the network
hasn't taken yet. With the firmware's `ws.rx.stats` it tells a slow
network from a slow source without guessing (clients/firmware F-0022).
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import AsyncMock, patch

import structlog

from huxley.server import server as server_mod
from huxley.server.server import AudioServer, _ConnInfo, _sock_unsent


class _Transport:
    def __init__(self, sock: socket.socket | None) -> None:
        self._sock = sock

    def get_write_buffer_size(self) -> int:
        return 123

    def get_extra_info(self, name: str) -> Any:
        return self._sock if name == "socket" else None


class _WS:
    def __init__(self, sock: socket.socket | None = None) -> None:
        self.transport = _Transport(sock)
        self.sent: list[str] = []

    async def send(self, msg: str) -> None:
        self.sent.append(msg)


def _server_with_conn(ws: _WS) -> AudioServer:
    srv = AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
    )
    srv._conn = _ConnInfo(
        conn_id="c1",
        remote="test",
        connected_at_ms=0,
        requested_persona=None,
        requested_language=None,
        ws=ws,  # type: ignore[arg-type]
    )
    return srv


async def test_one_line_per_window_with_audio_ms_and_backlog() -> None:
    srv = _server_with_conn(_WS())
    now = [0]
    with (
        patch.object(server_mod, "_now_ms", lambda: now[0]),
        structlog.testing.capture_logs() as logs,
    ):
        # 3 x 100 ms of PCM: the third send crosses the 1 s window.
        for t in (1000, 1400, 2100):
            now[0] = t
            await srv.send_audio(b"\0" * 4800)
    stats = [e for e in logs if e["event"] == "server.tx.audio_stats"]
    assert len(stats) == 1
    s = stats[0]
    assert s["window_ms"] == 1100
    assert s["audio_ms"] == 300
    assert s["sends"] == 3
    assert s["write_buffer"] == 123
    assert s["sock_unsent"] is None  # fake transport has no socket


async def test_quiet_until_a_window_passes() -> None:
    srv = _server_with_conn(_WS())
    with (
        patch.object(server_mod, "_now_ms", lambda: 5000),
        structlog.testing.capture_logs() as logs,
    ):
        await srv.send_audio(b"\0" * 4800)
    assert not [e for e in logs if e["event"] == "server.tx.audio_stats"]


def test_sock_unsent_reads_the_kernel_send_buffer() -> None:
    """A real connected socket on this platform: the kernel count is a
    number (0 once the peer has everything)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname())
    peer, _ = listener.accept()
    try:
        unsent = _sock_unsent(_WS(client))  # type: ignore[arg-type]
        assert isinstance(unsent, int)
        assert unsent >= 0
    finally:
        for s in (client, peer, listener):
            s.close()
