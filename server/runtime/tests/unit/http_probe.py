"""Plain-HTTP helpers for tests that hit `GET /health` on a live server.

Raw asyncio streams instead of an HTTP client library: the request is
one line, and the test sees exactly the bytes the server wrote.
"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from typing import Any


@dataclass
class HttpReply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


def free_port() -> int:
    """A port that was free a moment ago (small race with the server's bind)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


async def http_get(
    port: int, path: str, *, headers: dict[str, str] | None = None, timeout_s: float = 2.0
) -> HttpReply:
    """One GET to 127.0.0.1:`port`, read until the server closes."""
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{extra}\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout_s)
    finally:
        writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return HttpReply(status=int(lines[0].split()[1]), headers=headers, body=body)


async def wait_for_http(port: int, path: str = "/health", timeout_s: float = 5.0) -> HttpReply:
    """Poll until the server answers `path` (it is still binding)."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        try:
            return await http_get(port, path)
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.02)


async def until(cond: Any, timeout_s: float = 5.0) -> None:
    """Wait for a condition. structlog's async log calls hop to a thread,
    so a fixed number of event-loop yields is not a reliable barrier."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)
