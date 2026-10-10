"""SoloistClient against a real local WebSocket server; search against a
mocked Web API (pytest-httpx)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import websockets

from huxley_skill_spotify.search import SEARCH_URL, TOKEN_URL, SearchError, SpotifySearch
from huxley_skill_spotify.soloist import SoloistClient, describe_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pytest_httpx import HTTPXMock


def _logger() -> MagicMock:
    log = MagicMock()
    for method in ("ainfo", "adebug", "awarning", "aerror", "aexception"):
        setattr(log, method, AsyncMock())
    return log


class FakeSoloistServer:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.connection: Any = None
        self.changed = asyncio.Condition()

    async def handler(self, ws: Any) -> None:
        async with self.changed:
            self.connection = ws
            self.changed.notify_all()
        async for raw in ws:
            async with self.changed:
                self.received.append(json.loads(raw))
                self.changed.notify_all()

    async def emit(self, event: dict[str, Any]) -> None:
        await self.connection.send(json.dumps(event))

    def commands(self) -> list[str]:
        return [m["command"] for m in self.received]


@pytest.fixture
async def soloist() -> AsyncIterator[tuple[FakeSoloistServer, SoloistClient]]:
    fake = FakeSoloistServer()
    async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        client = SoloistClient(f"ws://127.0.0.1:{port}", _logger())
        task = asyncio.create_task(client.run())
        assert await client.wait_until(lambda s: s.connected, 2.0)
        try:
            yield fake, client
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _until(fake: FakeSoloistServer, predicate: Any, seconds: float = 2.0) -> None:
    async with asyncio.timeout(seconds), fake.changed:
        await fake.changed.wait_for(predicate)


class TestSoloistClient:
    async def test_connect_seeds_state_queries(
        self, soloist: tuple[FakeSoloistServer, SoloistClient]
    ) -> None:
        fake, _ = soloist
        await _until(fake, lambda: len(fake.received) >= 2)
        assert fake.commands()[:2] == ["get_auth_state", "get_state"]

    async def test_events_update_cached_state(
        self, soloist: tuple[FakeSoloistServer, SoloistClient]
    ) -> None:
        fake, client = soloist
        await _until(fake, lambda: fake.connection is not None)
        await fake.emit(
            {"type": "auth_state", "logged_in": True, "is_active": True, "device_name": "Huxley"}
        )
        await fake.emit({"type": "playback_changed", "status": "playing"})
        await fake.emit({"type": "track_changed", "item": {"name": "Sin Ti"}})
        await fake.emit({"type": "error", "message": "nope"})
        assert await client.wait_until(lambda s: s.error_count == 1, 2.0)
        state = client.state
        assert (state.logged_in, state.is_active, state.device_name) == (True, True, "Huxley")
        assert state.status == "playing"
        assert state.item == {"name": "Sin Ti"}
        assert state.last_error == "nope"

    async def test_commands_go_out_in_order(
        self, soloist: tuple[FakeSoloistServer, SoloistClient]
    ) -> None:
        fake, client = soloist
        gen = client.new_generation()
        client.send("set_volume", volume=100, generation=gen)
        client.send("play", uri="spotify:track:x", generation=gen)
        client.send("add_to_queue", uri="spotify:track:y", generation=gen)
        await _until(fake, lambda: "add_to_queue" in fake.commands())
        sent = [m for m in fake.received if m["command"] not in ("get_auth_state", "get_state")]
        assert [m["command"] for m in sent] == ["set_volume", "play", "add_to_queue"]
        assert sent[1] == {"type": "command", "command": "play", "uri": "spotify:track:x"}

    async def test_stale_generation_pause_is_dropped(
        self, soloist: tuple[FakeSoloistServer, SoloistClient]
    ) -> None:
        fake, client = soloist
        await _until(fake, lambda: len(fake.received) >= 2)
        old = client.new_generation()
        # Hold the queue: the old stream's pause and the new stream's play
        # land together, as when a song switch races a cancel.
        client.send("pause", generation=old)
        new = client.new_generation()
        client.send("play", uri="spotify:track:new", generation=new)
        await _until(fake, lambda: "play" in fake.commands())
        await asyncio.sleep(0.05)
        assert "pause" not in fake.commands()

    async def test_reconnects_after_the_socket_drops(
        self, soloist: tuple[FakeSoloistServer, SoloistClient]
    ) -> None:
        fake, client = soloist
        await _until(fake, lambda: fake.connection is not None)
        first = fake.connection
        await first.close()
        assert await client.wait_until(lambda s: not s.connected, 2.0)
        await _until(fake, lambda: fake.connection is not first, seconds=5.0)
        assert await client.wait_until(lambda s: s.connected, 5.0)


def test_describe_item_tolerates_shapes() -> None:
    assert describe_item({"name": "A", "artists": [{"name": "X"}, {"name": "Y"}]}) == ("A", "X, Y")
    assert describe_item({"name": "A", "artist_names": ["X"]}) == ("A", "X")
    assert describe_item(None) == (None, None)


# --- Search -----------------------------------------------------------------


def _token(httpx_mock: HTTPXMock, token: str = "t1") -> None:
    httpx_mock.add_response(
        url=TOKEN_URL, method="POST", json={"access_token": token, "expires_in": 3600}
    )


def _artist(artist_id: str, name: str) -> dict[str, Any]:
    return {"id": artist_id, "name": name}


class TestSearch:
    async def test_artist_name_plays_their_tracks_only(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        panchos = _artist("p", "Los Panchos")
        httpx_mock.add_response(
            method="GET",
            json={
                "artists": {"items": [panchos]},
                "tracks": {"items": [{"uri": "spotify:track:cover", "name": "x", "artists": []}]},
            },
        )
        httpx_mock.add_response(
            method="GET",
            json={
                "tracks": {
                    "items": [
                        {"uri": "spotify:track:1", "name": "Sin Ti", "artists": [panchos]},
                        {"uri": "spotify:track:2", "name": "Otro", "artists": [_artist("o", "O")]},
                        {"uri": "spotify:track:3", "name": "Rayito", "artists": [panchos]},
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", market="CO", http=httpx.AsyncClient())

        match = await search.find("los panchos")

        assert match is not None
        assert (match.kind, match.uri, match.queue) == (
            "artist",
            "spotify:track:1",
            ("spotify:track:3",),
        )
        searches = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert searches[0].url.params["market"] == "CO"
        assert searches[1].url.params["q"] == 'artist:"Los Panchos"'

    async def test_song_query_plays_the_track(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "artists": {"items": [_artist("p", "Los Panchos")]},
                "tracks": {
                    "items": [
                        {
                            "uri": "spotify:track:t",
                            "name": "Sin Ti",
                            "artists": [_artist("p", "Los Panchos")],
                        }
                    ]
                },
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        match = await search.find("Sin ti Los Panchos")
        assert match is not None
        assert (match.kind, match.title, match.artist) == ("track", "Sin Ti", "Los Panchos")

    async def test_null_playlists_are_skipped(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "playlists": {
                    "items": [None, {"uri": "spotify:playlist:p", "name": "Boleros", "owner": {}}]
                }
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        match = await search.find("boleros", "playlist")
        assert match is not None and match.uri == "spotify:playlist:p"

    async def test_token_is_reused_then_renewed_on_401(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock, "t1")
        httpx_mock.add_response(method="GET", json={"tracks": {"items": []}})
        httpx_mock.add_response(method="GET", status_code=401)
        _token(httpx_mock, "t2")
        httpx_mock.add_response(method="GET", json={"tracks": {"items": []}})
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        assert await search.find("a", "track") is None
        assert await search.find("b", "track") is None

        auth = [
            r.headers.get("authorization")
            for r in httpx_mock.get_requests(url=None)
            if r.method == "GET"
        ]
        assert auth == ["Bearer t1", "Bearer t1", "Bearer t2"]

    async def test_quota_or_outage_raises_search_error(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(method="GET", status_code=429)
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        with pytest.raises(SearchError, match="429"):
            await search.find("x", "track")

    def test_search_url_is_the_web_api(self) -> None:
        assert SEARCH_URL == "https://api.spotify.com/v1/search"
