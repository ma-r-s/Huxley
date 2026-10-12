"""Client for Spotify Soloist's local WebSocket API.

Soloist is Spotify's official headless Spotify Connect client. It runs
beside the server (a systemd unit on Linux, a container on a Mac; see
`dev/`) and the skill controls it over `ws://127.0.0.1:<port>`.

This client keeps one connection open (reconnecting with backoff),
caches the latest auth and playback state from Soloist's events, and
sends commands in order from a queue, so callers never wait on the
socket. That matters for a stream factory's `finally`, which runs
inside the coordinator's cancel path and must not block on a network
round trip.

Generations: each playback session the skill starts takes a new
generation number. A command tagged with an older generation is dropped
when it reaches the front of the queue, so a late `pause` from a stream
that was replaced can't stop the one that replaced it.

Protocol reference:
https://developer.spotify.com/documentation/soloist/reference/websocket-api
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import websockets

if TYPE_CHECKING:
    from collections.abc import Callable

    from huxley_sdk import SkillLogger


@dataclass
class PlayerState:
    """What Soloist last told us. `connected` is our socket, not Spotify's."""

    connected: bool = False
    logged_in: bool = False
    is_active: bool = False
    device_name: str | None = None
    # idle | playing | paused | buffering
    status: str = "idle"
    item: dict[str, Any] | None = None
    # What is loaded: a track, album, playlist or artist URI.
    context_uri: str | None = None
    # Where playback was at Soloist's wall clock `position_at_ms`, moving
    # at `speed` (0 while paused). Interpolate with `position_now`.
    position_ms: int | None = None
    position_at_ms: int | None = None
    speed: float = 0.0
    last_error: str | None = None
    # Bumped on every `error` event, so a waiter can tell a new error
    # from one it already saw.
    error_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def position_now(state: PlayerState, now_ms: float | None = None) -> int | None:
    """The playback position now, from the last anchor Soloist sent."""
    if state.position_ms is None:
        return None
    if not state.speed or state.position_at_ms is None:
        return state.position_ms
    now = time.time() * 1000 if now_ms is None else now_ms
    return int(state.position_ms + max(0.0, now - state.position_at_ms) * state.speed)


def item_uri(item: dict[str, Any] | None) -> str | None:
    uri = (item or {}).get("uri")
    return str(uri) if uri else None


def item_duration_ms(item: dict[str, Any] | None) -> int | None:
    """A track's or chapter's length, at `decorations.playback.duration_ms`."""
    duration = ((item or {}).get("decorations") or {}).get("playback", {}).get("duration_ms")
    return int(duration) if isinstance(duration, int | float) else None


def _position_fields(position: Any) -> dict[str, Any]:
    if not isinstance(position, dict) or "position_ms" not in position:
        return {}
    return {
        "position_ms": int(position.get("position_ms") or 0),
        "position_at_ms": int(position.get("timestamp_ms") or time.time() * 1000),
        "speed": float(position.get("speed") or 0.0),
    }


def _identity(entity: dict[str, Any]) -> str | None:
    name = (entity.get("decorations") or {}).get("identity", {}).get("name")
    return str(name) if name else None


def describe_item(item: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """(title, artists) from a Soloist track item.

    Soloist's shape (observed 2026-10-10): the title is at
    `decorations.identity.name`, artists at
    `decorations.creators[].entity.decorations.identity.name`. Plain
    `name` / `artists` are accepted too.
    """
    if not item:
        return None, None
    decorations = item.get("decorations") or {}
    title = _identity(item) or item.get("name") or item.get("title")
    names = [
        n
        for creator in decorations.get("creators") or []
        if (n := _identity((creator or {}).get("entity") or {}))
    ]
    if not names:
        for artist in item.get("artists") or item.get("artist_names") or []:
            name = artist.get("name") if isinstance(artist, dict) else artist
            if name:
                names.append(str(name))
    return (str(title) if title else None), (", ".join(names) or None)


class SoloistClient:
    """One reconnecting WebSocket to Soloist, with a state cache and an
    ordered, non-blocking command queue."""

    def __init__(self, url: str, logger: SkillLogger) -> None:
        self._url = url
        self._log = logger
        self.state = PlayerState()
        self._queue: asyncio.Queue[tuple[int | None, dict[str, Any]]] = asyncio.Queue()
        self._generation = 0
        self._changed = asyncio.Condition()

    @property
    def generation(self) -> int:
        return self._generation

    def new_generation(self) -> int:
        """Start a playback session. Older-generation commands still
        queued are dropped from now on."""
        self._generation += 1
        return self._generation

    def send(self, command: str, *, generation: int | None = None, **fields: Any) -> None:
        """Queue a command. Never blocks; safe from a `finally`."""
        self._queue.put_nowait((generation, {"type": "command", "command": command, **fields}))

    async def wait_until(self, predicate: Callable[[PlayerState], bool], seconds: float) -> bool:
        """Wait until `predicate(state)` holds, at most `seconds`."""
        try:
            async with asyncio.timeout(seconds), self._changed:
                await self._changed.wait_for(lambda: predicate(self.state))
        except TimeoutError:
            return False
        return True

    async def run(self) -> None:
        """Background task: connect, pump events in and commands out,
        reconnect with backoff when the socket drops. Runs until cancelled."""
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._url, open_timeout=5) as ws:
                    backoff = 1.0
                    await self._update(connected=True)
                    await self._log.ainfo("spotify.ws_state", connected=True, url=self._url)
                    # Seed the cache; play/pause decisions read it.
                    await ws.send(json.dumps({"type": "command", "command": "get_auth_state"}))
                    await ws.send(json.dumps({"type": "command", "command": "get_state"}))
                    sender = asyncio.create_task(self._send_loop(ws))
                    try:
                        async for raw in ws:
                            await self._on_message(raw)
                    finally:
                        sender.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await sender
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                await self._log.awarning(
                    "spotify.ws_state",
                    connected=False,
                    url=self._url,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                    retry_in_s=backoff,
                )
            await self._update(connected=False, is_active=False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _send_loop(self, ws: Any) -> None:
        while True:
            generation, message = await self._queue.get()
            if generation is not None and generation < self._generation:
                await self._log.adebug(
                    "spotify.command_dropped_stale",
                    command=message["command"],
                    generation=generation,
                    current=self._generation,
                )
                continue
            await ws.send(json.dumps(message))
            await self._log.adebug("spotify.command_sent", command=message["command"])

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            await self._log.awarning("spotify.bad_message", raw=str(raw)[:200])
            return
        kind = event.get("type")
        auth_before = (self.state.logged_in, self.state.is_active)
        status_before = self.state.status
        match kind:
            case "auth_state":
                await self._update(
                    logged_in=bool(event.get("logged_in")),
                    is_active=bool(event.get("is_active")),
                    device_name=event.get("device_name"),
                )
            case "playback_state":
                await self._update(
                    status=str(event.get("status") or self.state.status),
                    item=event.get("item", self.state.item),
                    is_active=bool(event.get("is_active", self.state.is_active)),
                    context_uri=(event.get("context") or {}).get("uri", self.state.context_uri),
                    **_position_fields(event.get("position")),
                )
            case "position_sync":
                await self._update(**_position_fields(event.get("position")))
            case "playback_changed":
                await self._update(status=str(event.get("status") or self.state.status))
            case "context_changed":
                await self._update(context_uri=(event.get("context") or {}).get("uri"))
            case "track_changed":
                await self._update(item=event.get("item"))
                title, artists = describe_item(event.get("item"))
                await self._log.ainfo("spotify.track", title=title, artists=artists)
            case "device_changed":
                await self._update(
                    is_active=bool(event.get("is_active")),
                    device_name=event.get("device_name", self.state.device_name),
                )
            case "error":
                message = str(event.get("message") or "unknown")
                await self._update(last_error=message, error_count=self.state.error_count + 1)
                await self._log.awarning("spotify.soloist_error", message=message[:200])
            case _:
                await self._log.adebug("spotify.event", type=kind)
        if (self.state.logged_in, self.state.is_active) != auth_before:
            await self._log.ainfo(
                "spotify.auth_state",
                logged_in=self.state.logged_in,
                is_active=self.state.is_active,
            )
        if self.state.status != status_before:
            await self._log.ainfo(
                "spotify.playback", status=self.state.status, context=self.state.context_uri
            )

    async def _update(self, **changes: Any) -> None:
        async with self._changed:
            for key, value in changes.items():
                setattr(self.state, key, value)
            self._changed.notify_all()
