"""Async mpv IPC client using JSON protocol over Unix socket.

mpv is launched in --idle mode with --input-ipc-server. Commands are sent
as JSON over the socket, and responses/events are read back asynchronously.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()


class MpvError(Exception):
    """Raised when mpv returns an error response."""


class MpvClient:
    """Async client for mpv's JSON IPC protocol.

    Manages the mpv process lifecycle and provides typed commands for
    media playback control.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._receive_task: asyncio.Task[None] | None = None
        self._event_callbacks: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def start(self) -> None:
        """Launch mpv in idle mode and connect to its IPC socket."""
        self._process = await asyncio.create_subprocess_exec(
            "mpv",
            "--idle",
            "--no-video",
            "--no-terminal",
            f"--input-ipc-server={self._socket_path}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Wait for the socket to become available
        for _ in range(50):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.1)
        else:
            msg = f"mpv IPC socket not available at {self._socket_path}"
            raise TimeoutError(msg)

        self._receive_task = asyncio.create_task(self._receive_loop())
        await logger.ainfo("mpv_started", socket=self._socket_path)

    async def stop(self) -> None:
        """Terminate mpv and clean up."""
        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None

        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None

        # Cancel pending futures
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    # --- Playback commands ---

    async def loadfile(self, path: str | Path) -> None:
        """Load and play a file."""
        await self._command("loadfile", str(path))

    async def pause(self) -> None:
        await self._set_property("pause", True)

    async def resume(self) -> None:
        await self._set_property("pause", False)

    async def seek(self, seconds: float) -> None:
        """Seek relative to current position (negative = rewind)."""
        await self._command("seek", seconds, "relative")

    async def seek_absolute(self, seconds: float) -> None:
        """Seek to an absolute position."""
        await self._command("seek", seconds, "absolute")

    async def stop_playback(self) -> None:
        await self._command("stop")

    # --- Property access ---

    async def get_position(self) -> float:
        """Get current playback position in seconds."""
        result = await self._get_property("playback-time")
        return float(result) if result is not None else 0.0

    async def get_duration(self) -> float:
        """Get total duration in seconds."""
        result = await self._get_property("duration")
        return float(result) if result is not None else 0.0

    async def get_paused(self) -> bool:
        result = await self._get_property("pause")
        return bool(result)

    async def set_volume(self, percent: int) -> None:
        await self._set_property("volume", percent)

    # --- Event observation ---

    def subscribe_event(self, event_name: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to mpv events (e.g., 'end-file'). Returns a queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_callbacks.setdefault(event_name, []).append(queue)
        return queue

    # --- Internal ---

    async def _command(self, *args: Any) -> Any:
        """Send a command and wait for the response."""
        request_id = self._next_id()
        msg = {"command": list(args), "request_id": request_id}
        return await self._send_and_wait(msg, request_id)

    async def _get_property(self, name: str) -> Any:
        request_id = self._next_id()
        msg = {"command": ["get_property", name], "request_id": request_id}
        try:
            return await self._send_and_wait(msg, request_id)
        except MpvError:
            return None

    async def _set_property(self, name: str, value: Any) -> None:
        request_id = self._next_id()
        msg = {"command": ["set_property", name, value], "request_id": request_id}
        await self._send_and_wait(msg, request_id)

    async def _send_and_wait(self, msg: dict[str, Any], request_id: int) -> Any:
        if not self._writer:
            msg_err = "Not connected to mpv"
            raise MpvError(msg_err)

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        data = json.dumps(msg) + "\n"
        self._writer.write(data.encode())
        await self._writer.drain()

        try:
            return await asyncio.wait_for(future, timeout=5.0)
        except TimeoutError:
            self._pending.pop(request_id, None)
            raise

    async def _receive_loop(self) -> None:
        """Read JSON lines from mpv socket, route to pending futures or event queues."""
        assert self._reader is not None
        while True:
            line = await self._reader.readline()
            if not line:
                break

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Response to a command
            if "request_id" in data:
                rid = data["request_id"]
                future = self._pending.pop(rid, None)
                if future and not future.done():
                    if data.get("error") != "success":
                        future.set_exception(MpvError(data.get("error", "unknown")))
                    else:
                        future.set_result(data.get("data"))

            # Event notification
            if "event" in data:
                event_name = data["event"]
                for queue in self._event_callbacks.get(event_name, []):
                    await queue.put(data)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id
