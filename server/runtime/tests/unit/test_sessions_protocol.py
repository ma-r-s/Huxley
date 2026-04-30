"""T1.12 — protocol-layer tests for session history retrieval.

Exercises `AudioServer._dispatch` directly with the new inbound message
types and asserts the corresponding callbacks fire with the right
arguments. The send-side (`send_sessions_list`,
`send_session_detail`, `send_session_deleted`) is exercised by checking
the JSON shape it serializes.

Storage-layer behavior (what the data looks like, migration, etc.)
lives in `test_storage.py`. End-to-end (PWA → server → storage → PWA)
is left to manual browser smoke per the T1.12 DoD.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from huxley.server.server import AudioServer


def _make_server(
    on_list_sessions: Any = None,
    on_get_session: Any = None,
    on_delete_session: Any = None,
) -> AudioServer:
    return AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
        on_language_select=AsyncMock(),
        on_list_sessions=on_list_sessions,
        on_get_session=on_get_session,
        on_delete_session=on_delete_session,
    )


class TestInboundDispatch:
    async def test_list_sessions_dispatches_to_callback(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_list_sessions=cb)
        await server._dispatch(json.dumps({"type": "list_sessions"}))
        cb.assert_awaited_once_with()

    async def test_list_sessions_unwired_is_silent(self) -> None:
        # No callback wired — should log + drop without raising.
        server = _make_server(on_list_sessions=None)
        await server._dispatch(json.dumps({"type": "list_sessions"}))

    async def test_get_session_dispatches_with_id(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_get_session=cb)
        await server._dispatch(json.dumps({"type": "get_session", "id": 42}))
        cb.assert_awaited_once_with(42)

    async def test_get_session_rejects_missing_id(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_get_session=cb)
        await server._dispatch(json.dumps({"type": "get_session"}))
        cb.assert_not_awaited()

    async def test_get_session_rejects_non_int_id(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_get_session=cb)
        await server._dispatch(json.dumps({"type": "get_session", "id": "not-a-number"}))
        cb.assert_not_awaited()

    async def test_delete_session_dispatches_with_id(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_delete_session=cb)
        await server._dispatch(json.dumps({"type": "delete_session", "id": 7}))
        cb.assert_awaited_once_with(7)

    async def test_delete_session_rejects_non_int_id(self) -> None:
        cb = AsyncMock()
        server = _make_server(on_delete_session=cb)
        await server._dispatch(json.dumps({"type": "delete_session", "id": None}))
        cb.assert_not_awaited()


class TestOutboundSerialization:
    """Senders convert the app's pre-serialized dicts into the wire
    shape documented in `docs/protocol.md`. The `_send` no-ops when no
    client is connected — these tests stub it to capture the payload."""

    async def test_send_sessions_list_shape(self) -> None:
        server = _make_server()
        captured: list[dict[str, Any]] = []

        async def fake_send(msg: dict[str, Any]) -> None:
            captured.append(msg)

        server._send = fake_send  # type: ignore[method-assign]
        sessions = [
            {
                "id": 1,
                "started_at": "2026-04-30 10:00:00",
                "ended_at": "2026-04-30 10:05:00",
                "last_turn_at": "2026-04-30 10:04:50",
                "turn_count": 4,
                "preview": "hola",
                "summary": "saludo breve",
            },
        ]
        await server.send_sessions_list(sessions)
        assert captured == [{"type": "sessions_list", "sessions": sessions}]

    async def test_send_session_detail_shape(self) -> None:
        server = _make_server()
        captured: list[dict[str, Any]] = []

        async def fake_send(msg: dict[str, Any]) -> None:
            captured.append(msg)

        server._send = fake_send  # type: ignore[method-assign]
        turns = [
            {"idx": 0, "role": "user", "text": "hola"},
            {"idx": 1, "role": "assistant", "text": "hola, ¿cómo estás?"},
        ]
        await server.send_session_detail(42, turns)
        assert captured == [{"type": "session_detail", "id": 42, "turns": turns}]

    async def test_send_session_deleted_shape(self) -> None:
        server = _make_server()
        captured: list[dict[str, Any]] = []

        async def fake_send(msg: dict[str, Any]) -> None:
            captured.append(msg)

        server._send = fake_send  # type: ignore[method-assign]
        await server.send_session_deleted(99)
        assert captured == [{"type": "session_deleted", "id": 99}]


class TestProtocolStaysAtVersion2:
    """T1.12 added new message types but did NOT bump the protocol
    version (rejected the bump in the critic round — ESP32 has zero
    use for sessions). Old clients ignore unknown types; new clients
    can talk to old servers (degraded — empty list — but not broken).
    Pin the constant so this can't drift silently."""

    def test_protocol_version_unchanged(self) -> None:
        from huxley.server.server import PROTOCOL_VERSION

        assert PROTOCOL_VERSION == 2
