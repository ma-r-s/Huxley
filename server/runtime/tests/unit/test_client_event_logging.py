"""`client_event` telemetry keeps the client's own fields in the log.

The firmware sends `huxley.firmware_log` with `level: "W" | "E" | "I"`.
Logged as kwargs, that `level` was overwritten by structlog's
`add_log_level` ("info"), so board warnings and errors read as info in
the server log (2026-10-12). A field named `event` crashed the handler.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import structlog

from huxley.server.server import AudioServer, client_event_log_fields


def _server() -> AudioServer:
    return AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
        on_language_select=AsyncMock(),
    )


def _client_event(event: str, data: dict[str, object]) -> str:
    return json.dumps({"type": "client_event", "event": event, "data": data})


async def test_firmware_level_survives_the_log_pipeline() -> None:
    server = _server()
    # The production chain's add_log_level is what clobbered the field.
    with structlog.testing.capture_logs(processors=[structlog.processors.add_log_level]) as logs:
        await server._dispatch(
            _client_event(
                "huxley.firmware_log",
                {"level": "E", "tag": "hux_net", "line": "ws.closed", "ts": 5},
            )
        )
    [entry] = [e for e in logs if e["event"] == "client.huxley.firmware_log"]
    assert entry["client_level"] == "E"
    assert entry["level"] == "info"  # the server's own level, untouched
    assert entry["tag"] == "hux_net"
    assert entry["line"] == "ws.closed"


async def test_reserved_keys_do_not_crash_and_skills_get_raw_data() -> None:
    server = _server()
    handler = AsyncMock()
    server.register_client_event_subscriber("demo", "demo.thing", handler)
    data = {"event": "x", "timestamp": 1, "logger": "l", "conn_id": "c", "ok": True}
    with structlog.testing.capture_logs() as logs:
        await server._dispatch(_client_event("demo.thing", data))
    [entry] = [e for e in logs if e["event"] == "client.demo.thing"]
    assert entry["client_event"] == "x"
    assert entry["client_timestamp"] == 1
    assert entry["client_logger"] == "l"
    assert entry["client_conn_id"] == "c"
    assert entry["ok"] is True
    handler.assert_awaited_once_with(data)


def test_unreserved_keys_pass_through() -> None:
    assert client_event_log_fields({"tag": "a", "line": "b"}) == {"tag": "a", "line": "b"}
