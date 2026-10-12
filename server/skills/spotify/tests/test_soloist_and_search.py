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

from huxley_skill_spotify.search import (
    SEARCH_URL,
    TOKEN_URL,
    SearchError,
    SpotifySearch,
    heard_title,
    title_score,
)
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
    soloist_shape = {
        "uri": "spotify:track:t",
        "decorations": {
            "identity": {"name": "Sin Ti"},
            "creators": [
                {"entity": {"decorations": {"identity": {"name": "Los Panchos"}}}},
                {"entity": {"decorations": {"identity": {"name": "Los Tres Reyes"}}}},
            ],
        },
    }
    assert describe_item(soloist_shape) == ("Sin Ti", "Los Panchos, Los Tres Reyes")
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
    async def test_artist_name_plays_the_artist(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "artists": {
                    "items": [{"uri": "spotify:artist:p", "name": "Los Panchos", "id": "p"}]
                },
                "tracks": {"items": [{"uri": "spotify:track:cover", "name": "x", "artists": []}]},
            },
        )
        search = SpotifySearch("id", "secret", market="CO", http=httpx.AsyncClient())

        match = await search.find("los panchos")

        assert match is not None
        assert (match.kind, match.uri, match.title, match.artist) == (
            "artist",
            "spotify:artist:p",
            "Los Panchos",
            "Los Panchos",
        )
        (request,) = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert request.url.params["market"] == "CO"

    async def test_artist_kind_searches_artists(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={"artists": {"items": [None, {"uri": "spotify:artist:t", "name": "Trío"}]}},
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        match = await search.find("trio", "artist")
        assert match is not None and match.uri == "spotify:artist:t"
        (request,) = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert request.url.params["type"] == "artist"

    async def test_audiobook_kind_searches_books_and_reads_authors(
        self, httpx_mock: HTTPXMock
    ) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "audiobooks": {
                    "items": [
                        {
                            "uri": "spotify:show:1oVPQwdET6CETd7KFZAghH",
                            "name": "Cien años de soledad",
                            "authors": [{"name": "Gabriel García Márquez"}],
                            "narrators": [{"name": "Juan Pablo Raba"}],
                        }
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", market="CO", http=httpx.AsyncClient())

        match = await search.find("cien años de soledad", "audiobook")

        assert match is not None
        assert (match.kind, match.uri, match.title, match.artist) == (
            "audiobook",
            "spotify:show:1oVPQwdET6CETd7KFZAghH",
            "Cien años de soledad",
            "Gabriel García Márquez",
        )
        (request,) = [r for r in httpx_mock.get_requests() if r.method == "GET"]
        assert request.url.params["type"] == "audiobook"

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

    async def test_candidates_are_the_runners_up_without_repeats(
        self, httpx_mock: HTTPXMock
    ) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "artists": {
                    "items": [
                        {"uri": "spotify:artist:a", "name": "Gilberto A"},
                        None,
                        {"uri": "spotify:artist:a", "name": "Gilberto A"},
                        {"uri": "spotify:artist:b", "name": "Gilberto B"},
                        {"uri": "spotify:artist:c", "name": "Gilberto C"},
                        {"uri": "spotify:artist:d", "name": "Gilberto D"},
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        found = await search.candidates("gilberto", "artist")

        assert [m.uri for m in found] == [
            "spotify:artist:a",
            "spotify:artist:b",
            "spotify:artist:c",
        ]

    async def test_song_and_artist_puts_the_song_first(self, httpx_mock: HTTPXMock) -> None:
        """A song AND an artist must play the song, not the artist
        (2026-10-12: a song request played the artist instead)."""
        _token(httpx_mock)
        flim = {"name": "Flim", "artists": [_artist("x", "Aphex Twin")]}
        httpx_mock.add_response(
            method="GET",
            json={
                "artists": {"items": [_artist("x", "Aphex Twin") | {"uri": "spotify:artist:x"}]},
                "tracks": {
                    "items": [
                        flim | {"uri": "spotify:track:1"},
                        flim | {"uri": "spotify:track:2"},  # the same song again
                    ]
                },
                "albums": {"items": [{"uri": "spotify:album:1", "name": "Come to Daddy"}]},
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        found = await search.candidates("flim aphex twin")

        assert [(m.kind, m.title) for m in found] == [
            ("track", "Flim"),
            ("album", "Come to Daddy"),
            ("artist", "Aphex Twin"),
        ]

    async def test_empty_query_asks_spotify_nothing(self, httpx_mock: HTTPXMock) -> None:
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        assert await search.candidates("  ") == []
        assert httpx_mock.get_requests() == []

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

    async def test_token_without_access_token_is_a_search_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"error": "invalid_client"})
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        with pytest.raises(SearchError, match="access_token"):
            await search.find("x", "track")

    async def test_quota_or_outage_raises_search_error(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(method="GET", status_code=429)
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())
        with pytest.raises(SearchError, match="429"):
            await search.find("x", "track")

    def test_search_url_is_the_web_api(self) -> None:
        assert SEARCH_URL == "https://api.spotify.com/v1/search"


def _rh(name: str, uri: str) -> dict[str, Any]:
    return {"uri": uri, "name": name, "artists": [_artist("r", "Radiohead")]}


class TestHeardTitle:
    """2026-10-12: "Pon Weird Fishes de Radiohead" reached play_music as
    "irfixes Radiohead"; Spotify ranked "Let Down" first and Weird Fishes
    second, and Let Down played."""

    async def test_garbled_title_plays_the_song_it_sounds_like(
        self, httpx_mock: HTTPXMock
    ) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "tracks": {
                    "items": [
                        _rh("Let Down", "spotify:track:ld"),
                        _rh("Weird Fishes / Arpeggi", "spotify:track:wf"),
                        _rh("High and Dry", "spotify:track:hd"),
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        found = await search.candidates("irfixes Radiohead", "track")

        assert [m.title for m in found] == ["Weird Fishes / Arpeggi", "Let Down", "High and Dry"]
        assert len([r for r in httpx_mock.get_requests() if r.method == "GET"]) == 1

    async def test_title_missing_from_the_top_five_is_found_among_the_artists(
        self, httpx_mock: HTTPXMock
    ) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={"tracks": {"items": [_rh("Let Down", "spotify:track:ld")]}},
        )
        httpx_mock.add_response(
            method="GET",
            json={
                "tracks": {
                    "items": [
                        _rh("Creep", "spotify:track:cr"),
                        _rh("Weird Fishes / Arpeggi", "spotify:track:wf"),
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        found = await search.candidates("nuyer fiches de Radiohead", "track")

        assert found[0].title == "Weird Fishes / Arpeggi"
        second = [r for r in httpx_mock.get_requests() if r.method == "GET"][1]
        assert second.url.params["q"] == 'artist:"Radiohead"'

    async def test_a_clear_title_keeps_spotifys_order(self, httpx_mock: HTTPXMock) -> None:
        _token(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            json={
                "tracks": {
                    "items": [
                        {
                            "uri": "spotify:track:a",
                            "name": "Sin Ti",
                            "artists": [_artist("p", "Los Panchos")],
                        },
                        {
                            "uri": "spotify:track:b",
                            "name": "Sin Ti",
                            "artists": [_artist("q", "Other")],
                        },
                    ]
                }
            },
        )
        search = SpotifySearch("id", "secret", http=httpx.AsyncClient())

        found = await search.candidates("sin ti", "track")

        assert [m.uri for m in found] == ["spotify:track:a", "spotify:track:b"]
        assert len([r for r in httpx_mock.get_requests() if r.method == "GET"]) == 1

    def test_scores(self) -> None:
        assert heard_title("irfixes Radiohead", "Radiohead") == "irfixes"
        assert heard_title("Weird Fishes de Radiohead", "Radiohead") == "weird fishes"
        assert title_score("irfixes", "Weird Fishes / Arpeggi") >= 0.6
        assert title_score("irfixes", "Let Down") < 0.5


def test_position_moves_only_while_playing() -> None:
    """A paused or buffering player stays at its anchor even if a speed
    came with it; a track change forgets the old track's position."""
    from huxley_skill_spotify.soloist import PlayerState, position_now

    state = PlayerState(status="playing", position_ms=10_000, position_at_ms=1_000, speed=1.0)
    assert position_now(state, now_ms=4_000) == 13_000
    state.status = "paused"
    assert position_now(state, now_ms=4_000) == 10_000
    state.status = "buffering"
    assert position_now(state, now_ms=4_000) == 10_000
