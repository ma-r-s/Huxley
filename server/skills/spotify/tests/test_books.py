"""Audiobooks in the Spotify skill (T1.40): every request an elderly blind
listener makes of a book, and every way a book stops, said in exact words.

`BookSoloist` follows Soloist as measured on 2026-10-12: playing a book
resumes at its saved chapter; `skip_next` goes to the next chapter at 0:00;
`skip_prev` at a chapter's start goes back one, mid-chapter restarts it;
`seek` moves within the chapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from huxley_sdk import AudioStream
from huxley_skill_spotify import skill as skill_module
from huxley_skill_spotify.books import BookMemory, Place, capped_position
from huxley_skill_spotify.search import BookInfo, Chapter, Match
from huxley_skill_spotify.skill import SpotifySkill

from .conftest import FakeSearch, FakeSoloist, PcmServer, Recorded, make_ctx, write_shelf

if TYPE_CHECKING:
    from pathlib import Path

CHAPTERS = (
    Chapter("spotify:episode:c0", "Créditos iniciales", 17_000),
    Chapter("spotify:episode:c1", "Capítulo 1", 2_110_000),
    Chapter("spotify:episode:c2", "Capítulo 2", 2_191_000),
    Chapter("spotify:episode:c3", "Capítulo 3", 2_000_000),
)
BOOK_INFO = BookInfo(
    uri="spotify:show:cien",
    title="Cien años de soledad",
    author="Gabriel García Márquez",
    description="",
    chapters=CHAPTERS,
)
BOOK = Match(
    uri="spotify:show:cien",
    kind="audiobook",
    title="Cien años de soledad",
    artist="Gabriel García Márquez",
    languages=("es",),
    about="La historia de siete generaciones de los Buendía. Y mucho más.",
)
CORONEL = Match(
    uri="spotify:show:coronel",
    kind="audiobook",
    title="El coronel no tiene quien le escriba",
    artist="Gabriel García Márquez",
    languages=("es",),
)
GUIDE = Match(
    uri="spotify:show:guia",
    kind="audiobook",
    title="Cien años de soledad (Guía de lectura): Resumen y análisis",
    artist="ResumenExpress",
    languages=("es",),
)
# Spotify marks a shortened reading in its edition note only.
ABRIDGED = Match(
    uri="spotify:show:corto",
    kind="audiobook",
    title="Cien años de soledad",
    artist="Gabriel García Márquez",
    languages=("es",),
    edition="Abridged edition",
)
SONG = Match(uri="spotify:track:sinti", kind="track", title="Sin Ti", artist="Los Panchos")


PEDRO = Match(
    uri="spotify:show:pedro",
    kind="audiobook",
    title="Pedro Páramo",
    artist="Juan Rulfo",
    languages=("es",),
)


@pytest.fixture(autouse=True)
def _quick_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A book check waits 1.6 s for Spotify to refuse; tests don't."""
    monkeypatch.setattr(skill_module, "PROBE_OK_S", 0.15)


class BookSoloist(FakeSoloist):
    """FakeSoloist that knows one book's chapters (see the module doc)."""

    def __init__(self, resume: int = 1, position: int = 60_000) -> None:
        super().__init__()
        self.resume = resume
        self.resume_position = position
        self.index: int | None = None
        self.skips_ignored = False
        # Books Spotify won't play: "playing", then idle after `refuse_after`.
        self.refused: set[str] = set()
        self.refuse_after = 0.01

    def on_chapter(self, index: int, position: int = 0) -> None:
        self.index = index
        chapter = CHAPTERS[index]
        self.state.item = {
            "uri": chapter.uri,
            "decorations": {
                "identity": {"name": chapter.name},
                "playback": {"duration_ms": chapter.duration_ms},
            },
        }
        self.state.position_ms = position
        self.state.speed = 0.0

    def load(self, index: int, position: int, status: str = "paused") -> None:
        """The book as a press leaves it: loaded, paused mid-chapter."""
        self.state.context_uri = BOOK_INFO.uri
        self.state.status = status
        self.on_chapter(index, position)

    def send(self, command: str, *, generation: int | None = None, **fields: Any) -> None:
        super().send(command, generation=generation, **fields)
        if command == "play" and fields.get("uri") in self.refused:
            # As measured: "playing" first, with its first chapter and the
            # position moving, then Spotify stops it.
            self.state.item = {"uri": "spotify:episode:refused-first-chapter"}
            self.state.speed = 1.0
            asyncio.get_running_loop().call_later(
                self.refuse_after, lambda: asyncio.ensure_future(self._update(status="idle"))
            )
        elif command == "play" and fields.get("uri") == BOOK_INFO.uri and self.start_playing:
            self.on_chapter(self.resume, self.resume_position)
        elif self.skips_ignored or self.index is None:
            return
        elif command == "skip_next" and self.index + 1 < len(CHAPTERS):
            self.state.status = "playing"
            self.on_chapter(self.index + 1)
        elif command == "skip_prev":
            self.state.status = "playing"
            at_start = (self.state.position_ms or 0) < 3_000
            self.on_chapter(self.index - 1 if at_start and self.index > 0 else self.index)
        elif command == "seek":
            self.state.position_ms = int(fields["position_ms"])


async def _skill(
    tmp_path: Path,
    pcm: PcmServer,
    *,
    match: Match | None = BOOK,
    others: list[Match] | None = None,
    client: FakeSoloist | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[SpotifySkill, BookSoloist, Recorded, BookMemory]:
    client = client or BookSoloist()
    recorded = Recorded()
    search = FakeSearch(match, others, books={BOOK_INFO.uri: BOOK_INFO})
    skill = SpotifySkill(client=client, search=search)  # type: ignore[arg-type]
    await skill.setup(make_ctx(tmp_path, recorded, config={"pcm": pcm.address, **(config or {})}))
    assert skill._memory is not None
    return skill, client, recorded, skill._memory  # type: ignore[return-value]


def _events_named(skill: SpotifySkill, name: str) -> list[dict[str, Any]]:
    calls = [*skill._logger.ainfo.call_args_list, *skill._logger.awarning.call_args_list]  # type: ignore[union-attr]
    return [c.kwargs for c in calls if c.args and c.args[0] == name]


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _take(stream: AudioStream, count: int) -> list[bytes]:
    """Run the stream until `count` chunks arrived, then stop it (a press)."""
    got: list[bytes] = []
    enough = asyncio.Event()

    async def consume() -> None:
        async for chunk in stream.factory():
            got.append(chunk)
            if len(got) >= count:
                enough.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(enough.wait(), 3.0)
    finally:
        # Also on a timeout: a stream left running never lets the PCM
        # server fixture close.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await _settle()
    return got


def _out(result: Any) -> dict[str, Any]:
    return json.loads(result.output)  # type: ignore[no-any-return]


def _plays(client: FakeSoloist) -> list[dict[str, Any]]:
    return [c for c in client.sent if c["command"] == "play"]


class TestAskingForABook:
    async def test_a_title_plays_the_book_where_it_was_left(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})

        assert _out(result)["playing"] == "Cien años de soledad"
        assert skill._search.queries == [("cien años de soledad", "audiobook")]  # type: ignore[union-attr]
        # A book keeps its narrator's level (T1.47).
        assert result.side_effect.loudness_key == f"spotify:book:{BOOK.uri}"  # type: ignore[union-attr]
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert _plays(client)[0]["uri"] == BOOK_INFO.uri
        # Spotify's place is kept: no seek, no chapter moves.
        assert "seek" not in client.commands() and "skip_prev" not in client.commands()

    async def test_reading_guides_are_never_picked(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server, match=GUIDE, others=[BOOK])
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        assert _out(result)["playing"] == "Cien años de soledad"

    async def test_an_abridged_edition_is_never_picked(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server, match=ABRIDGED, others=[BOOK])
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert _plays(client)[0]["uri"] == BOOK.uri

    async def test_only_guides_is_not_found(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server, match=GUIDE)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        assert _out(result)["reason"] == "book_not_found"
        assert "«No encontré ese libro en Spotify. ¿Busco otro?»" in _out(result)["then"]
        assert result.side_effect is None

    async def test_a_far_title_is_confirmed_before_spending_hours(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        asked = await skill.handle("play_book", {"title": "la casa grande"})

        assert _out(asked)["reason"] == "ask_which"
        assert "¿Es Cien años de soledad, de Gabriel García Márquez?" in _out(asked)["then"]
        assert asked.side_effect is None and _plays(client) == []
        # "Sí": the model passes the title it was asked about.
        yes = await skill.handle("play_book", {"title": "Cien años de soledad"})
        assert isinstance(yes.side_effect, AudioStream)
        assert len(skill._search.queries) == 1  # type: ignore[union-attr]

    async def test_the_author_named_with_a_wrong_title_is_still_confirmed(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle(
            "play_book", {"title": "crónica de una muerte anunciada de garcía márquez"}
        )
        assert _out(result)["reason"] == "ask_which"

    async def test_naming_the_author_is_enough(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "el libro de garcía márquez"})
        assert isinstance(result.side_effect, AudioStream)

    async def test_no_title_continues_the_last_book(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _skill(tmp_path, pcm_server)
        none_yet = await skill.handle("play_book", {})
        assert _out(none_yet)["reason"] == "nothing_started"
        assert "ningún libro empezado" in _out(none_yet)["then"]

        await memory.set_last_book(BOOK.uri, BOOK.title, BOOK.artist)
        result = await skill.handle("play_book", {})
        assert _out(result)["playing"] == "Cien años de soledad"
        assert skill._search.queries == []  # type: ignore[union-attr]

    async def test_play_music_kind_audiobook_goes_to_the_book_path(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_music", {"query": "cien años", "kind": "audiobook"})
        assert _out(result)["kind"] == "audiobook"
        assert skill._search.queries == [("cien años", "audiobook")]  # type: ignore[union-attr]


class TestRecommendations:
    async def test_a_search_reads_three_numbered_with_length_and_about(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _skill(tmp_path, pcm_server, others=[GUIDE, CORONEL])
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        result = await skill.handle("find_books", {"query": "garcía márquez"})

        books = _out(result)["books"]
        assert [b["title"] for b in books] == [BOOK.title, CORONEL.title]  # no guide
        assert books[0]["n"] == 1 and books[0]["listened_here"] is True
        assert books[0]["length"] == "unas 2 horas"
        assert books[0]["about"] == "La historia de siete generaciones de los Buendía."
        assert books[1]["listened_here"] is False and "length" not in books[1]
        assert "numerados" in _out(result)["then"]

    async def test_el_segundo_is_the_second_book_read(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server, others=[CORONEL])
        await skill.handle("find_books", {"query": "garcía márquez"})
        result = await skill.handle("play_book", {"title": "2"})
        assert _out(result)["playing"] == CORONEL.title
        by_title = await skill.handle("play_book", {"title": "el coronel no tiene quien"})
        assert _out(by_title)["playing"] == CORONEL.title
        assert len(skill._search.queries) == 1  # type: ignore[union-attr]

    async def test_el_segundo_said_literally_is_still_the_second(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server, others=[CORONEL])
        await skill.handle("find_books", {"query": "garcía márquez"})
        assert _out(await skill.handle("play_book", {"title": "el segundo"}))["playing"] == (
            CORONEL.title
        )
        assert _out(await skill.handle("play_book", {"title": "el último"}))["playing"] == (
            CORONEL.title
        )
        assert len(skill._search.queries) == 1  # type: ignore[union-attr]

    async def test_only_books_spotify_will_play_are_offered(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """Mario: a recommendation must be something he can play. Unknown
        books are tried for a moment; what Spotify refuses is left out and
        remembered; what was loaded is put back, paused."""
        skill, client, _, memory = await _skill(tmp_path, pcm_server, others=[CORONEL, PEDRO])
        client.refused = {CORONEL.uri}
        client.load(1, 60_000)  # the book he paused to ask
        result = await skill.handle("find_books", {"query": "novela latinoamericana"})

        assert [b["title"] for b in _out(result)["books"]] == [BOOK.title, PEDRO.title]
        assert await memory.unavailable(CORONEL.uri)
        assert await memory.playable(PEDRO.uri)
        assert [c.get("uri") for c in _plays(client)][-1] == BOOK_INFO.uri  # put back
        assert client.commands()[-1] == "pause"

    async def test_a_known_book_refused_too_means_no_hours_not_unavailable(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, memory = await _skill(tmp_path, pcm_server, match=CORONEL)
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        client.refused = {CORONEL.uri, BOOK.uri}  # every book: the hours ran out
        result = await skill.handle("find_books", {"query": "coronel"})

        assert _out(result)["reason"] == "no_hours"
        assert "«Este mes ya no quedan horas para escuchar libros." in _out(result)["then"]
        assert not await memory.unavailable(CORONEL.uri)  # not filed as unavailable

    async def test_no_extra_check_when_another_book_just_played(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """A refusal needs the "known book" check only when nothing in the
        round played: that check cost Mario 1.6 s per search."""
        skill, client, _, memory = await _skill(
            tmp_path, pcm_server, match=CORONEL, others=[PEDRO]
        )
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        client.refused = {CORONEL.uri}
        await skill.handle("find_books", {"query": "novela"})
        tried = [c["uri"] for c in client.sent if c["command"] == "play" and "uri" in c]
        assert BOOK_INFO.uri not in tried[:-1]  # only the put-back, if anything
        assert await memory.unavailable(CORONEL.uri)

    async def test_books_already_known_are_not_tried_again(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, memory = await _skill(tmp_path, pcm_server, others=[CORONEL])
        await memory.mark_ok(BOOK.uri, BOOK.title, BOOK.artist)
        await memory.mark_unavailable(CORONEL.uri)
        result = await skill.handle("find_books", {"query": "garcía márquez"})
        assert [b["title"] for b in _out(result)["books"]] == [BOOK.title]
        assert _plays(client) == []  # nothing tried, nothing to put back

    async def test_nothing_playable_is_said(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server, match=CORONEL)
        client.refused = {CORONEL.uri}
        result = await skill.handle("find_books", {"query": "coronel"})
        assert _out(result)["reason"] == "none_available"

    async def test_a_refused_book_is_not_offered_again(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server, match=CORONEL)
        client.refused = {CORONEL.uri}
        stream = (
            await skill.handle("play_book", {"title": "el coronel no tiene quien le escriba"})
        ).side_effect
        async for _ in stream.factory():  # type: ignore[union-attr]
            pass
        await _settle()
        assert await memory.unavailable(CORONEL.uri)

    async def test_no_query_lists_books_listened_to_here(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _skill(tmp_path, pcm_server)
        none = await skill.handle("find_books", {})
        assert _out(none)["reason"] == "books_none"
        assert "¿De qué autor o de qué tema busco?" in _out(none)["then"]

        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        listed = await skill.handle("find_books", {})
        assert [b["title"] for b in _out(listed)["books"]] == [BOOK.title]
        # Not "the books available": Mario heard it that way (2026-10-12).
        assert "no todos los que hay" in _out(listed)["then"]
        assert skill._search.queries == []  # type: ignore[union-attr]


class TestRefusals:
    async def _refused(
        self, tmp_path: Path, pcm: PcmServer, **kwargs: Any
    ) -> tuple[Recorded, FakeSoloist]:
        pcm.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm, **kwargs)
        if kwargs.get("config", {}).get("played_before"):
            await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"  # Spotify stops it at once
        await _settle()
        return recorded, client

    async def test_a_book_never_played_here_is_not_available(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        recorded, client = await self._refused(tmp_path, pcm_server)
        assert [key for _, key in recorded.injected] == ["spotify:book_unavailable"]
        prompt = recorded.injected[0][0]
        assert "NO está sonando" in prompt
        assert "«Ese libro no está disponible para escuchar. ¿Busco otro libro?»" in prompt
        assert "pause" not in client.commands()

    async def test_a_book_that_played_here_means_the_hours_ran_out(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        recorded, _ = await self._refused(tmp_path, pcm_server, config={"played_before": True})
        assert [key for _, key in recorded.injected] == ["spotify:book_no_hours"]
        assert "«Este mes ya no quedan horas para escuchar libros." in recorded.injected[0][0]

    async def test_a_book_refused_after_a_recent_play_is_unavailable_not_hours(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """Siddhartha, 2026-10-12: a check had said it played, Spotify then
        refused it, and the refusal was read as "no hours left" while other
        books played fine. A book that played lately means the hours are
        there: it's this book."""
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        await memory.mark_ok(BOOK.uri, BOOK.title, BOOK.artist)  # the check's "ok"
        skill._hours_ok_at = time.monotonic()  # another book played a moment ago
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_unavailable"]
        assert await memory.unavailable(BOOK.uri)
        assert not await memory.playable(BOOK.uri)  # the wrong "ok" is gone

    async def test_a_known_book_refused_is_explained_by_another_that_plays(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """No recent play to go by: after the stream ends, a book known to
        play is tried. It plays, so the hours are there: this book isn't."""
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        await memory.mark_played(PEDRO.uri, PEDRO.title, PEDRO.artist)  # the control
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        await asyncio.sleep(0.3)  # the control check after the stream
        assert [key for _, key in recorded.injected] == ["spotify:book_unavailable"]
        assert PEDRO.uri in [c.get("uri") for c in _plays(client)]
        assert client.commands()[-1] == "pause"  # the control is not left playing
        assert await memory.unavailable(BOOK.uri)

    async def test_a_known_book_refused_and_the_control_too_is_the_hours(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        await memory.mark_played(PEDRO.uri, PEDRO.title, PEDRO.artist)
        client.refused = {PEDRO.uri}
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        await asyncio.sleep(0.3)
        assert [key for _, key in recorded.injected] == ["spotify:book_no_hours"]
        assert not await memory.unavailable(BOOK.uri)

    async def test_a_check_is_not_fooled_by_the_first_playing(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spotify says "playing" before it decides; a refusal can come a
        moment later. Only a book still playing at the end is offered."""
        monkeypatch.setattr(skill_module, "PROBE_OK_S", 0.6)
        skill, client, _, memory = await _skill(tmp_path, pcm_server, match=CORONEL)
        client.refused = {CORONEL.uri}
        client.refuse_after = 0.45  # past the old early "ok" and its 0.35 s confirm
        result = await skill.handle("find_books", {"query": "coronel"})
        assert _out(result)["reason"] == "none_available"
        assert not await memory.playable(CORONEL.uri)

    async def test_the_configured_budget_spent_means_the_hours_ran_out(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(
            tmp_path, pcm_server, config={"book_hours_per_month": 10}
        )
        await memory.add_seconds(10 * 3600)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_no_hours"]


class TestWhenABookStops:
    async def test_the_end_of_the_last_chapter_is_said_and_remembered(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "REFUSED_S", 0.0)
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        client.resume, client.resume_position = 3, 2_000_000 - 5_000  # its last seconds
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()

        assert [key for _, key in recorded.injected] == ["spotify:book_finished"]
        assert "«Se terminó Cien años de soledad. ¿Busco otro libro?»" in recorded.injected[0][0]
        assert await memory.finished(BOOK.uri)
        again = await skill.handle("play_book", {"title": "cien años de soledad"})
        assert _out(again)["reason"] == "finished_replay"
        assert "¿Lo pongo desde el principio?" in _out(again)["then"]

    async def test_a_stop_early_in_the_last_chapter_is_a_cut_not_the_end(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "REFUSED_S", 0.0)
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        client.resume, client.resume_position = 3, 120_000  # 2 min into 33
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_cut"]
        assert not await memory.finished(BOOK.uri)

    async def test_hours_running_out_on_a_resume_is_said_as_hours(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """Not only a fresh start: the press's resume, "sigue" or a chapter
        move can be the play Spotify refuses (critic round 2, B1)."""
        pcm_server.hold_open.set()
        skill, client, recorded, memory = await _skill(tmp_path, pcm_server)
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        client.load(2, 600_000)
        result = await skill.handle("music_control", {"action": "resume"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_no_hours"]

    async def test_sigue_after_a_cut_starts_the_book_again(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """A bare play to an idle Soloist does nothing for 10 s (critic B2)."""
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(2, 600_000, status="idle")
        result = await skill.handle("music_control", {"action": "resume"})
        assert _out(result)["action"] == "restart"
        await _take(result.side_effect, 1)  # type: ignore[arg-type]
        assert _plays(client)[-1].get("uri") == BOOK_INFO.uri

    async def test_a_book_paused_from_a_phone_is_said(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "PAUSED_GRACE_S", 0.05)
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "paused"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_patience"]

    async def test_stopping_mid_book_is_a_cut(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "REFUSED_S", 0.0)
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_cut"]
        assert "«Se cortó el libro. ¿Lo vuelvo a poner?»" in recorded.injected[0][0]

    async def test_spotify_leaving_the_book_after_the_last_chapter_is_the_end(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(tmp_path, pcm_server)
        client.resume = 3
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.context_uri = "spotify:show:something-else"  # autoplay
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_finished"]
        assert client.commands()[-1] == "pause"

    async def test_another_device_taking_the_book_is_said_as_a_book(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        client.activate()
        n = 0
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            n += 1
            if n == 2:
                client.state.is_active = False
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_moved"]

    async def test_a_long_call_leaves_the_book_paused_and_says_so(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, recorded, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await result.side_effect.on_patience_expired()  # type: ignore[union-attr]
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:book_patience"]

    async def test_after_an_hour_without_a_press_it_pauses_at_the_next_chapter(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(
            tmp_path, pcm_server, config={"book_sleep_minutes": 0.0001}
        )
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        n = 0
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            n += 1
            await asyncio.sleep(0.01)
            if n == 2:
                client.on_chapter(2)  # the chapter ends, the next begins
        await _settle()
        assert recorded.injected == []  # he may be asleep: no voice
        assert client.commands()[-1] == "pause"


class TestChapters:
    async def test_next_moves_to_the_next_chapter(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 600_000)
        result = await skill.handle("music_control", {"action": "next"})
        assert _out(result)["chapter"] == "Capítulo 2"
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 2
        assert "skip_next" in client.commands()

    async def test_previous_mid_chapter_still_goes_to_the_previous_chapter(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """Mid-chapter, Soloist's skip_prev only restarts the chapter: seek
        to its start first."""
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(2, 600_000)
        result = await skill.handle("music_control", {"action": "previous"})
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 1

    async def test_the_ends_of_the_book_are_said(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(0, 1_000)
        first = await skill.handle("music_control", {"action": "previous"})
        assert "«Ya es el primer capítulo.»" in _out(first)["then"]
        assert first.side_effect is None  # the book comes back after the answer
        client.load(3, 1_000)
        last = await skill.handle("music_control", {"action": "next"})
        assert "«Ya es el último capítulo.»" in _out(last)["then"]

    async def test_a_chapter_by_its_number_is_found_by_its_name(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 5_000)
        result = await skill.handle("book_control", {"action": "go_to_chapter", "chapter": "3"})
        assert _out(result)["chapter"] == "Capítulo 3"
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 3

        missing = await skill.handle("book_control", {"action": "go_to_chapter", "chapter": "9"})
        assert "«Ese libro no tiene un capítulo 9.»" in _out(missing)["then"]

    async def test_a_chapter_said_in_words_is_found(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 5_000)
        result = await skill.handle(
            "book_control", {"action": "go_to_chapter", "chapter": "capítulo tres"}
        )
        assert _out(result)["chapter"] == "Capítulo 3"

    async def test_a_press_mid_move_finishes_the_move_on_resume(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The move is silent; a press during it ("¿qué pasó?") must not
        leave the book on whatever chapter it had reached (critic M1)."""
        monkeypatch.setattr(skill_module, "NAV_SETTLE_S", 5.0)
        monkeypatch.setattr(skill_module, "SKIP_GAP_S", 0.0)
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 5_000)
        client.skips_ignored = True  # the move is still going when he presses
        stream = (
            await skill.handle("book_control", {"action": "go_to_chapter", "chapter": "3"})
        ).side_effect

        async def run_until_pressed() -> None:
            async for _ in stream.factory():  # type: ignore[union-attr]
                pass

        task = asyncio.create_task(run_until_pressed())
        await asyncio.sleep(0.3)
        task.cancel()  # the press
        with contextlib.suppress(asyncio.CancelledError):
            await task
        client.skips_ignored = False
        client.load(1, 5_000)
        await _take(stream, 1)  # type: ignore[arg-type]  # the turn ends: resume
        assert client.index == 3

    async def test_a_chapter_that_cant_be_reached_is_said(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "NAV_SETTLE_S", 0.05)
        monkeypatch.setattr(skill_module, "SKIP_GAP_S", 0.0)
        pcm_server.hold_open.set()
        skill, client, recorded, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 5_000)
        client.skips_ignored = True
        result = await skill.handle("book_control", {"action": "go_to_chapter", "chapter": "3"})
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert [key for _, key in recorded.injected] == ["spotify:book_nav_failed"]


class TestMovingWithinAChapter:
    async def test_back_and_forward_seek_inside_the_chapter(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 60_000)
        back = await skill.handle("book_control", {"action": "back"})
        await _take(back.side_effect, 1)  # type: ignore[arg-type]
        seeks = [c["position_ms"] for c in client.sent if c["command"] == "seek"]
        assert seeks == [30_000]  # and no extra resume rewind on top

        client.load(1, 2_100_000)
        forward = await skill.handle("book_control", {"action": "forward", "seconds": 300})
        await _take(forward.side_effect, 1)  # type: ignore[arg-type]
        seeks = [c["position_ms"] for c in client.sent if c["command"] == "seek"]
        assert seeks[-1] == 2_109_000  # stays inside the chapter

    async def test_restart_chapter_seeks_to_its_start(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(2, 900_000)
        result = await skill.handle("book_control", {"action": "restart_chapter"})
        await _take(result.side_effect, 1)  # type: ignore[arg-type]
        assert [c["position_ms"] for c in client.sent if c["command"] == "seek"] == [0]

    async def test_with_no_book_on_it_says_so(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("book_control", {"action": "back"})
        assert "«No hay ningún libro puesto. ¿Qué libro busco?»" in _out(result)["then"]

    async def test_a_resumed_book_backs_up_a_little(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_book", {"title": "cien años de soledad"})).side_effect
        await _take(stream, 2)  # type: ignore[arg-type]  # then a press pauses it
        client.load(1, 60_000)
        await _take(stream, 1)  # type: ignore[arg-type]  # the turn ends: it resumes
        seek = [c for c in client.sent if c["command"] == "seek"][-1]
        assert 57_000 <= seek["position_ms"] <= 58_000  # 2 s and a tenth of the pause


class TestAPlaceNeverRunsAhead:
    """Soloist plays on when nobody reads its capture. A walkie switched off
    mid-book kept the stream "playing" 16 min before the server noticed,
    and the place jumped 16 min past what was heard (2026-10-12)."""

    async def _listen_then_go(
        self, tmp_path: Path, pcm_server: PcmServer, *, ran_ahead_ms: int
    ) -> tuple[BookSoloist, BookMemory]:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_book", {"title": "cien años de soledad"})).side_effect
        run = stream.factory()  # type: ignore[union-attr]
        chunks = 0
        async for _ in run:
            chunks += 1
            if chunks == 3:
                break
        # Nobody reads the capture any more; Soloist's clock runs on.
        assert client.state.position_ms is not None
        client.state.position_ms += ran_ahead_ms
        await run.aclose()  # the server finally notices: the stream is cancelled
        await _settle()
        return client, memory

    async def test_a_listener_gone_long_ago_keeps_the_place_heard(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        client, memory = await self._listen_then_go(tmp_path, pcm_server, ran_ahead_ms=900_000)
        place = await memory.place(BOOK.uri)
        assert place is not None and place.chapter_uri == CHAPTERS[1].uri
        # 60 s in, at most 300 ms sent, then 10 s back: not 16 min on.
        assert 50_000 <= place.position_ms <= 50_300
        # And Soloist goes back there too: a plain resume starts from it.
        order = client.commands()
        assert order[-1] == "seek" and "pause" in order[-3:]
        assert client.sent[-1]["position_ms"] == place.position_ms

    async def test_an_ordinary_stop_keeps_soloists_place(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        client, memory = await self._listen_then_go(tmp_path, pcm_server, ran_ahead_ms=1_000)
        place = await memory.place(BOOK.uri)
        assert place is not None and place.position_ms == 61_000
        assert "seek" not in client.commands()


def test_capped_position() -> None:
    assert capped_position(65_000, 60_000) is None  # within the slack
    assert capped_position(65_001, 60_000) == 50_000
    assert capped_position(900_000, 4_000) == 0  # never before the start


class TestNoOldAudioAfterASeek:
    async def test_a_resume_seeks_after_play_not_before(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """A seek sent before "play" landed after Soloist had already played
        about a second from its buffer: Mario heard the old spot, a cut, then
        the new one (2026-10-12). Play first, then seek, silent until it lands."""
        pcm_server.hold_open.set()
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_book", {"title": "cien años de soledad"})).side_effect
        await _take(stream, 2)  # type: ignore[arg-type]
        client.load(1, 60_000)
        client.sent.clear()
        await _take(stream, 1)  # type: ignore[arg-type]  # the resume after a turn
        order = client.commands()
        assert order.index("play") < order.index("seek")

    async def test_back_seeks_after_play_too(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(1, 60_000)
        result = await skill.handle("book_control", {"action": "back"})
        await _take(result.side_effect, 1)  # type: ignore[arg-type]
        order = client.commands()
        assert order.index("play") < order.index("seek")


class TestFromTheBeginning:
    async def test_early_in_the_book_it_starts_over_without_asking(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server)
        await memory.set_place(BOOK.uri, Place(CHAPTERS[1].uri, 60_000))
        result = await skill.handle(
            "play_book", {"title": "cien años de soledad", "from_beginning": True}
        )
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 0 and client.state.position_ms == 0
        assert await memory.saved_place(BOOK.uri) == Place(CHAPTERS[1].uri, 60_000)

    async def test_deep_in_the_book_it_asks_book_or_chapter_and_can_undo(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server)
        await memory.set_place(BOOK.uri, Place(CHAPTERS[3].uri, 300_000))
        asked = await skill.handle(
            "play_book", {"title": "cien años de soledad", "from_beginning": True}
        )
        assert "«¿Desde el principio del libro, o solo de este capítulo?»" in _out(asked)["then"]
        assert asked.side_effect is None

        client.resume, client.resume_position = 3, 300_000
        sure = await skill.handle(
            "play_book",
            {"title": "cien años de soledad", "from_beginning": True, "confirmed": True},
        )
        await _take(sure.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 0

        client.load(0, 4_000)
        undo = await skill.handle("book_control", {"action": "go_back_to_saved_place"})
        await _take(undo.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 3 and client.state.position_ms == 300_000


class TestStartingOverAFinishedBook:
    async def test_a_finished_book_starts_over_without_a_second_question(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server)
        await memory.mark_finished(BOOK.uri)
        await memory.set_place(BOOK.uri, Place(CHAPTERS[3].uri, 1_990_000))
        client.resume, client.resume_position = 3, 1_990_000
        result = await skill.handle(
            "play_book", {"title": "cien años de soledad", "from_beginning": True}
        )
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 0
        assert not await memory.finished(BOOK.uri)
        assert await memory.saved_place(BOOK.uri) is None  # nothing worth undoing to


class TestWhatsPlaying:
    async def test_a_book_says_book_chapter_and_whats_left(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _skill(tmp_path, pcm_server)
        client.load(2, 2_191_000 - 20 * 60_000)
        result = await skill.handle("whats_playing", {})
        then = _out(result)["then"]
        assert "«Es Cien años de soledad, de Gabriel García Márquez." in then
        assert "Va en Capítulo 2: faltan 20 minutos para terminar el capítulo" in then
        assert "y 53 minutos para terminar el libro.»" in then
        assert result.side_effect is None


class TestMusicAndBooksApart:
    async def test_a_book_is_counted_when_spotify_wont_give_its_chapters(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-10-14: Spotify answered 429 on /audiobooks for 19 hours; the
        skill took the book for music and the month's listening stopped
        moving on Mario's page."""
        monkeypatch.setattr(skill_module, "PLAYED_AFTER_MS", 100)
        pcm_server.hold_open.set()
        skill, _, _, memory = await _skill(tmp_path, pcm_server)
        skill._search.books.clear()  # type: ignore[union-attr]  # every lookup fails
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await _take(result.side_effect, 3)  # type: ignore[arg-type]
        await _settle()
        assert await memory.played(BOOK.uri)
        assert (await memory.last_book() or {}).get("uri") == BOOK.uri
        assert await memory.seconds_this_month() > 0
        (ended,) = _events_named(skill, "spotify.stream_ended")
        assert ended["book"] == "Cien años de soledad" and ended["book_info"] is False
        assert _events_named(skill, "spotify.book_without_chapters")

    async def test_a_resume_without_chapters_never_writes_an_empty_title(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1.45 review: a resume while Spotify won't give chapters, with a
        different last book in memory, saved the book under "". It takes
        the title from the shelf, and writes nothing when nobody knows it."""
        monkeypatch.setattr(skill_module, "PLAYED_AFTER_MS", 100)
        pcm_server.hold_open.set()
        path = write_shelf(tmp_path / "shelf.json")
        skill, client, _, memory = await _skill(
            tmp_path, pcm_server, config={"book_shelf": str(path)}
        )
        skill._search.books.clear()  # type: ignore[union-attr]
        await memory.set_last_book("spotify:show:otro", "Otro libro", "Alguien")
        client.state.context_uri = "spotify:show:sabueso"  # the shelf's Baskerville
        await _take(skill._stream(), 3)  # a resume: no request
        await _settle()
        assert (await memory.last_book() or {}).get("title") == "El sabueso de los Baskerville"
        client.state.context_uri = "spotify:show:nadie"  # nobody knows this one
        await _take(skill._stream(), 3)
        await _settle()
        assert (await memory.last_book() or {}).get("title") == "El sabueso de los Baskerville"
        assert not await memory.played("spotify:show:nadie")

    async def test_pon_musica_after_a_book_plays_the_last_music(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, memory = await _skill(tmp_path, pcm_server, match=SONG)
        song = await skill.handle("play_music", {"query": "sin ti"})
        await _take(song.side_effect, 2)  # type: ignore[arg-type]
        assert (await memory.last_music() or {}).get("uri") == SONG.uri

        client.load(1, 60_000)  # then a book played
        again = await skill.handle("play_music", {})
        await _take(again.side_effect, 1)  # type: ignore[arg-type]
        assert _plays(client)[-1]["uri"] == SONG.uri

    async def test_a_book_heard_ten_seconds_is_remembered(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "PLAYED_AFTER_MS", 100)
        pcm_server.hold_open.set()
        skill, _, _, memory = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await _take(result.side_effect, 3)  # type: ignore[arg-type]
        assert await memory.played(BOOK.uri)
        assert (await memory.last_book() or {}).get("uri") == BOOK.uri
        assert await memory.seconds_this_month() > 0


def test_track_chapters_are_said_as_chapters() -> None:
    """Some books name chapters "Track 2": heard as "Va en Track 2"."""
    from huxley_skill_spotify import lines

    assert lines.chapter_name("es", "Track 2") == "el capítulo 2"
    assert lines.chapter_name("en", "track 12") == "chapter 12"
    assert lines.chapter_name("es", "Capítulo 3") == "Capítulo 3"
    assert lines.chapter_name("es", "Créditos iniciales") == "Créditos iniciales"


async def test_the_log_says_which_chapter_of_how_many(
    tmp_path: Path, pcm_server: PcmServer
) -> None:
    """huxley-watch's phone app shows a book's progress from these (T1.46)."""
    pcm_server.hold_open.set()
    skill, client, _, _ = await _skill(tmp_path, pcm_server)
    stream = (await skill.handle("play_book", {"title": "cien años de soledad"})).side_effect
    run = stream.factory()  # type: ignore[union-attr]
    chunks = 0
    async for _ in run:
        chunks += 1
        if chunks == 3:
            break
    client.load(2, 60_000)  # the listener reached chapter 2
    await run.aclose()
    await _settle()
    logged = {c.args[0]: c.kwargs for c in skill._log().ainfo.call_args_list}  # type: ignore[attr-defined]
    started, ended = logged["spotify.play_started"], logged["spotify.stream_ended"]
    assert (started["book"], started["chapters"]) == ("Cien años de soledad", 4)
    assert started["chapter"] == CHAPTERS[started["chapter_index"]].name
    assert (ended["chapter"], ended["chapter_index"], ended["chapters"]) == ("Capítulo 2", 2, 4)


# --- The shelf (T1.45) ---

SABUESO = "spotify:show:sabueso"
ORIENT = "spotify:show:orient"
DIEZ = "spotify:show:diez"
ESCARLATA = "spotify:show:escarlata"
MISTERIO = [SABUESO, ORIENT, DIEZ, ESCARLATA]
SUMMARY_KEYS = {
    "uri",
    "title",
    "author",
    "chapters",
    "chapter",
    "position_ms",
    "last_at",
    "finished",
    "opinion",
    "source",
}


async def _shelf_skill(
    tmp_path: Path,
    pcm: PcmServer,
    *,
    match: Match | None = BOOK,
    others: list[Match] | None = None,
    known: list[str] | None = None,
) -> tuple[SpotifySkill, BookSoloist, Recorded, BookMemory]:
    """A skill with the test shelf; `known` books are already known to
    play (no check before offering them)."""
    path = write_shelf(tmp_path / "shelf.json")
    skill, client, recorded, memory = await _skill(
        tmp_path, pcm, match=match, others=others, config={"book_shelf": str(path)}
    )
    for uri in MISTERIO if known is None else known:
        await memory.mark_ok(uri, "", None)
    return skill, client, recorded, memory


def _events(skill: SpotifySkill, name: str) -> list[dict[str, Any]]:
    calls = [*skill._logger.ainfo.call_args_list, *skill._logger.awarning.call_args_list]  # type: ignore[union-attr]
    return [c.kwargs for c in calls if c.args and c.args[0] == name]


def _said(result: Any) -> str:
    """The exact line inside a `then`."""
    then = _out(result)["then"]
    return then[then.index("«") + 1 : then.index("»")]


class TestTheShelf:
    async def test_no_subject_names_the_kinds_of_book_without_searching(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("find_books", {})
        assert _said(result) == (
            "Tengo libros de misterio y suspenso, novela latinoamericana, cuentos e historia "
            "y memorias. ¿De cuál busco?"
        )
        assert skill._search.queries == []  # type: ignore[union-attr]
        more = await skill.handle("find_books", {"more": True})
        assert _said(more) == "También tengo libros de aventuras. ¿De cuál busco?"

    async def test_his_book_in_progress_is_offered_with_the_kinds(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        await memory.set_last_book(PEDRO.uri, PEDRO.title, PEDRO.artist)
        result = await skill.handle("find_books", {})
        assert "También quedó a medias Pedro Páramo." in _said(result)
        assert _out(await skill.handle("play_book", {"title": "ese"}))["playing"] == "Pedro Páramo"

    async def test_a_theme_offers_three_from_the_shelf_in_the_skills_words(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("find_books", {"query": "algo de misterio"})
        assert _said(result) == (
            "De misterio y suspenso tengo tres. Uno, El sabueso de los Baskerville, de Arthur "
            "Conan Doyle: Sherlock Holmes y un perro que aterra a una familia. Dos, Asesinato "
            "en el Orient Express, de Agatha Christie: Un crimen en un tren detenido por la "
            "nieve. Tres, Diez negritos, de Agatha Christie: Diez invitados en una isla, y uno "
            "a uno van muriendo. ¿Cuál pongo, o busco otros?"
        )
        assert [b["n"] for b in _out(result)["books"]] == [1, 2, 3]
        assert skill._search.queries == []  # type: ignore[union-attr]
        assert _plays(client) == []  # all known to play: nothing checked
        (browse,) = _events(skill, "spotify.shelf_browse")
        assert browse["mode"] == "category" and browse["offset"] == 0

    async def test_el_segundo_plays_the_second_book_offered(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        await skill.handle("find_books", {"query": "misterio"})
        result = await skill.handle("play_book", {"title": "el segundo"})
        assert _out(result)["playing"] == "Asesinato en el Orient Express"
        assert (_events(skill, "spotify.book_requested")[0]["source"]) == "list"

    async def test_otros_then_none_left(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        await skill.handle("find_books", {"query": "misterio"})
        more = await skill.handle("find_books", {"more": True})
        assert _said(more) == (
            "Tengo uno más de misterio y suspenso. Estudio en escarlata, de Arthur Conan "
            "Doyle: De qué trata Estudio en escarlata. ¿Lo pongo, o busco otros?"
        )
        done = await skill.handle("find_books", {"more": True})
        assert _said(done).startswith("Ya no tengo más de misterio y suspenso. También tengo")
        assert skill._search.queries == []  # type: ignore[union-attr]

    async def test_a_book_spotify_refuses_is_left_out_and_remembered(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, memory = await _shelf_skill(tmp_path, pcm_server, known=[])
        client.refused = {ORIENT}
        result = await skill.handle("find_books", {"query": "misterio"})
        assert [b["title"] for b in _out(result)["books"]] == [
            "El sabueso de los Baskerville",
            "Diez negritos",
            "Estudio en escarlata",
        ]
        assert await memory.unavailable(ORIENT)
        again = await skill.handle("find_books", {"query": "misterio"})
        assert ORIENT not in [b["title"] for b in _out(again)["books"]]

    async def test_nothing_fits_names_the_kinds_of_book(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("find_books", {"query": "dragones"})
        assert _said(result) == (
            "En la repisa no tengo libros de eso. Tengo libros de misterio y suspenso, novela "
            "latinoamericana, cuentos e historia y memorias. ¿De cuál busco?"
        )
        assert _events(skill, "spotify.shelf_nothing")[0]["reason"] == "no_match"

    async def test_an_author_not_on_the_shelf_gets_only_their_own_books(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        allende = Match(
            uri="spotify:show:espiritus",
            kind="audiobook",
            title="La casa de los espíritus",
            artist="Isabel Allende",
            languages=("es",),
            about="Author(s): Isabel Allende\nNarrator(s): X\n\nLa saga de los Trueba. Y más.",
        )
        other = Match(
            uri="spotify:show:otro",
            kind="audiobook",
            title="Mujeres del alma mía",
            artist="Otra Persona",
            languages=("es",),
        )
        skill, _, _, _ = await _shelf_skill(
            tmp_path, pcm_server, match=other, others=[allende], known=[allende.uri]
        )
        result = await skill.handle("find_books", {"query": "isabel allende"})
        assert _said(result) == (
            "De Isabel Allende encontré uno. La casa de los espíritus, de Isabel Allende: La "
            "saga de los Trueba. ¿Lo pongo, o busco otros?"
        )

    async def test_a_shelf_title_plays_the_curated_edition_without_asking(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "el coronel no tiene quien le escriba"})
        assert _out(result)["playing"] == "El coronel no tiene quien le escriba"
        assert isinstance(result.side_effect, AudioStream)
        assert skill._search.queries == []  # type: ignore[union-attr]
        (requested,) = _events(skill, "spotify.book_requested")
        assert requested["source"] == "shelf" and requested["uri"] == "spotify:show:coronel"

    async def test_his_own_edition_of_a_shelf_book_continues_and_isnt_offered(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        await memory.mark_played(
            "spotify:show:cien-otra",
            "Cien años de soledad (Edición conmemorativa)",
            "Gabriel García Márquez",
        )
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        assert _events(skill, "spotify.book_requested")[0]["uri"] == "spotify:show:cien-otra"
        await memory.mark_ok("spotify:show:coronel", "", None)
        await memory.mark_ok(PEDRO.uri, "", None)
        offer = await skill.handle("find_books", {"query": "novela latinoamericana"})
        assert "Cien años de soledad" not in [b["title"] for b in _out(offer)["books"]]
        assert result.side_effect is not None

    async def test_a_theme_asked_to_play_is_offered_from_the_shelf(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "algo de misterio"})
        assert result.side_effect is None
        assert _said(result).startswith("De misterio y suspenso tengo tres.")
        assert skill._search.queries == []  # type: ignore[union-attr]
        assert _events(skill, "spotify.book_redirected") == [{"query": "algo de misterio"}]

    async def test_a_garbled_title_is_still_searched_and_confirmed(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """T1.45 critic: only a shelf category or tag is sent to the shelf;
        a title it doesn't know still gets the search and "¿es ...?"."""
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "la casa grande"})
        assert _out(result)["reason"] == "ask_which"
        assert skill._search.queries == [("la casa grande", "audiobook")]  # type: ignore[union-attr]

    async def test_like_a_shelf_book(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("find_books", {"like": "el sabueso de los baskerville"})
        titles = [b["title"] for b in _out(result)["books"]]
        assert titles[0] == "Estudio en escarlata"  # same author, category, tags
        assert "El sabueso de los Baskerville" not in titles
        assert _said(result).startswith("Parecidos a El sabueso de los Baskerville tengo")
        (browse,) = _events(skill, "spotify.shelf_browse")
        assert browse["like_source"] == "shelf"

    async def test_like_a_book_not_on_the_shelf_looks_it_up_once(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        poirot = Match(
            uri="spotify:show:poirot",
            kind="audiobook",
            title="Muerte en el Nilo",
            artist="Agatha Christie",
            languages=("es",),
            about="Hércules Poirot investiga un crimen a bordo de un barco.",
        )
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server, match=poirot)
        result = await skill.handle("find_books", {"like": "muerte en el nilo"})
        titles = [b["title"] for b in _out(result)["books"]]
        assert titles[:2] == ["Asesinato en el Orient Express", "Diez negritos"]
        assert skill._search.queries == [("muerte en el nilo", "audiobook")]  # type: ignore[union-attr]

    async def test_opinions_move_the_offers(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        await skill.handle("find_books", {"query": "misterio"})
        liked = await skill.handle("book_opinion", {"opinion": "liked", "title": "el tercero"})
        assert _said(liked) == "Anotado que Diez negritos gustó. Buscaré más libros así."
        disliked = await skill.handle(
            "book_opinion", {"opinion": "disliked", "title": "El sabueso de los Baskerville"}
        )
        assert _said(disliked) == (
            "Anotado que El sabueso de los Baskerville no gustó; no lo vuelvo a ofrecer."
        )
        again = await skill.handle("find_books", {"query": "misterio"})
        titles = [b["title"] for b in _out(again)["books"]]
        # Christie (liked) first; the disliked book gone; its author last.
        assert titles == [
            "Asesinato en el Orient Express",
            "Diez negritos",
            "Estudio en escarlata",
        ]
        assert _events(skill, "spotify.book_opinion")[0]["opinion"] == "liked"

    async def test_an_opinion_with_no_title_is_the_book_on(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _, memory = await _shelf_skill(tmp_path, pcm_server)
        none = await skill.handle("book_opinion", {"opinion": "liked"})
        assert _said(none) == "No sé de qué libro. ¿Cuál fue?"
        client.load(1, 60_000)
        result = await skill.handle("book_opinion", {"opinion": "liked"})
        assert _said(result) == "Anotado que Cien años de soledad gustó. Buscaré más libros así."
        summary = await memory.summary(BOOK.uri)
        assert summary is not None and summary["opinion"] == "liked"
        assert summary["source"] == "shelf" and summary["last_at"] == 0.0  # never heard here

    async def test_listened_lists_the_books_started_here(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        await memory.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        result = await skill.handle("find_books", {"listened": True})
        assert [b["title"] for b in _out(result)["books"]] == [BOOK.title]

    async def test_a_missing_shelf_turns_offers_back_to_open_search(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(
            tmp_path, pcm_server, config={"book_shelf": str(tmp_path / "none.json")}
        )
        assert _events(skill, "spotify.shelf_missing")
        await skill.handle("find_books", {"query": "garcía márquez"})
        assert skill._search.queries == [("garcía márquez", "audiobook")]  # type: ignore[union-attr]

    async def test_a_broken_shelf_keeps_the_last_good_one(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        path = tmp_path / "shelf.json"
        path.write_text("{ broken", encoding="utf-8")
        later = time.time() + 5
        import os

        os.utime(path, (later, later))
        result = await skill.handle("find_books", {"query": "misterio"})
        assert len(_out(result)["books"]) == 3
        (invalid,) = _events(skill, "spotify.shelf_invalid")
        assert invalid["kept"] == 11

    async def test_asking_for_books_keeps_what_played_paused(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """2026-10-14: after every list of books the last book came back,
        "¿Cuál pongo?" included. Choosing a book ends the held stream; the
        book keeps its place for "sigue con el libro"."""
        from huxley_sdk import CancelMedia

        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        for args in ({}, {"query": "misterio"}, {"more": True}):
            result = await skill.handle("find_books", args)
            assert isinstance(result.side_effect, CancelMedia), args
        # Nothing to choose from ("no tengo libros de eso"): what was
        # playing comes back as after any question (T1.45 review).
        nothing = await skill.handle("find_books", {"query": "dragones"})
        assert nothing.side_effect is None

    async def test_any_book_is_choosing_not_continuing(self) -> None:
        """ "Ponme un libro" resumed the last book instead of offering one."""
        from huxley_skill_spotify import tooltext

        for lang, words in (
            ("es", "ponme un libro"),
            ("en", "put on a book"),
            ("fr", "mets un livre"),
        ):
            text = tooltext.TEXT[lang]["play_book"]
            assert words in text and "`find_books`" in text, lang

    async def test_otros_after_an_offer_is_more_and_an_opinion_comes_first(self) -> None:
        """Hearing bench, 2026-10-14: "otros" after an offer played the
        second book, and a bare "me gustó" recorded nothing. The model's
        obedience is checked on the bench; this pins the words it reads."""
        from huxley_skill_spotify import tooltext

        for lang, others in (("es", "'Otros'"), ("en", "'Others'"), ("fr", "'D'autres'")):
            text = tooltext.TEXT[lang]["play_book"]
            assert others in text and "`more`" in text, lang
        assert "antes de contestar" in tooltext.TEXT["es"]["book_opinion"]
        assert "before answering" in tooltext.TEXT["en"]["book_opinion"]
        assert "avant de répondre" in tooltext.TEXT["fr"]["book_opinion"]

    # --- the review round (T1.45) ---

    async def test_a_title_with_a_theme_word_is_searched_not_redirected(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """ "Las aventuras de Tom Sawyer" contains the adventures category's
        word; it is a title, so it is searched (it used to loop on an offer
        of adventure books)."""
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "las aventuras de tom sawyer"})
        assert skill._search.queries == [("las aventuras de tom sawyer", "audiobook")]  # type: ignore[union-attr]
        assert _events(skill, "spotify.book_redirected") == []
        assert _out(result)["reason"] == "ask_which"  # the fake search knows another book
        found = await skill.handle("find_books", {"query": "breve historia del tiempo"})
        assert not _said(found).startswith("De historia y memorias")

    async def test_a_book_he_named_is_offered_in_his_own_edition(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        await memory.mark_played(
            "spotify:show:cien-otra", "Cien años de soledad", "Gabriel García Márquez"
        )
        await skill.handle("find_books", {"query": "cien años de soledad"})
        await skill.handle("play_book", {"title": "ese"})
        assert _events(skill, "spotify.book_requested")[0]["uri"] == "spotify:show:cien-otra"

    async def test_el_dos_picks_the_second_as_the_offer_numbers_them(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(tmp_path, pcm_server)
        await skill.handle("find_books", {"query": "misterio"})
        result = await skill.handle("play_book", {"title": "el dos"})
        assert _out(result)["playing"] == "Asesinato en el Orient Express"

    async def test_otros_after_one_book_offers_books_like_it(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(
            tmp_path,
            pcm_server,
            known=["spotify:show:pedro", "spotify:show:cien", "spotify:show:coronel"],
        )
        one = await skill.handle("find_books", {"query": "pedro páramo"})
        assert _said(one).endswith("¿Lo pongo, o busco otros?")
        more = await skill.handle("find_books", {"more": True})
        assert _said(more).startswith("Parecidos a Pedro Páramo tengo")

    async def test_an_off_shelf_authors_finished_book_isnt_offered_again(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        paula = Match(
            uri="spotify:show:paula",
            kind="audiobook",
            title="Paula",
            artist="Isabel Allende",
            languages=("es",),
        )
        casa = Match(
            uri="spotify:show:espiritus",
            kind="audiobook",
            title="La casa de los espíritus",
            artist="Isabel Allende",
            languages=("es",),
        )
        skill, _, _, memory = await _shelf_skill(
            tmp_path, pcm_server, match=paula, others=[casa], known=[paula.uri, casa.uri]
        )
        await memory.mark_played(paula.uri, paula.title, paula.artist)
        await memory.mark_finished(paula.uri)
        result = await skill.handle("find_books", {"query": "isabel allende"})
        assert [b["title"] for b in _out(result)["books"]] == ["La casa de los espíritus"]

    async def test_more_with_a_new_subject_starts_a_fresh_offer(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _shelf_skill(
            tmp_path, pcm_server, known=["spotify:show:ficciones", "spotify:show:selva"]
        )
        await skill.handle("find_books", {"query": "misterio"})
        result = await skill.handle("find_books", {"query": "cuentos", "more": True})
        assert _said(result).startswith("De cuentos tengo dos.")
        assert _events(skill, "spotify.shelf_more_new_subject")[0]["query"] == "cuentos"


class TestTheBookSummary:
    """The page's contract (T1.45): `book:summary:<id>` in the skill's
    storage, so `spotify:book:summary:<id>` in the database, JSON text."""

    async def test_a_place_writes_the_summary_in_the_page_format(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        before = time.time()
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        await _settle()
        raw = await skill._ctx.storage.get_setting("book:summary:cien")  # type: ignore[union-attr]
        assert raw is not None
        summary = json.loads(raw)
        assert set(summary) == SUMMARY_KEYS
        assert summary["uri"] == "spotify:show:cien"
        assert summary["title"] == "Cien años de soledad"
        assert summary["author"] == "Gabriel García Márquez"
        assert summary["chapters"] == 4 and summary["chapter"] == 1
        assert isinstance(summary["position_ms"], int) and summary["position_ms"] >= 60_000
        assert isinstance(summary["last_at"], float) and summary["last_at"] >= before
        assert summary["finished"] is False and summary["opinion"] is None
        assert summary["source"] == "shelf"
        assert await memory.summary(BOOK.uri) == summary

    async def test_the_end_and_a_place_landing_together_both_stay(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """At a book's end `mark_finished` and the stream's last place are
        spawned together: neither may overwrite the other."""
        skill, _, _, memory = await _shelf_skill(tmp_path, pcm_server)
        label = skill._label(BOOK.uri, BOOK.title, BOOK.artist)
        await asyncio.gather(
            memory.set_place(
                BOOK.uri, Place(CHAPTERS[3].uri, 1_990_000), label, chapter=3, chapters=4
            ),
            memory.mark_finished(BOOK.uri, label),
        )
        summary = await memory.summary(BOOK.uri)
        assert summary is not None
        assert summary["finished"] is True and summary["chapter"] == 3
        assert await memory.ended(BOOK.uri)
        # A start from the beginning clears "finished", not "ended".
        await memory.clear_finished(BOOK.uri, label)
        assert (await memory.summary(BOOK.uri) or {})["finished"] is False
        assert await memory.ended(BOOK.uri)

    async def test_books_heard_before_get_a_summary_at_setup(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        from huxley_sdk.testing import _NoopSkillStorage

        storage = _NoopSkillStorage()
        earlier = BookMemory(storage)
        await earlier.mark_played(PEDRO.uri, PEDRO.title, PEDRO.artist)
        await earlier.set_place(PEDRO.uri, Place("spotify:episode:p3", 90_000))
        path = write_shelf(tmp_path / "shelf.json")
        recorded = Recorded()
        skill = SpotifySkill(client=BookSoloist(), search=FakeSearch(None))  # type: ignore[arg-type]
        ctx = make_ctx(
            tmp_path, recorded, config={"pcm": pcm_server.address, "book_shelf": str(path)}
        )
        await skill.setup(dataclasses.replace(ctx, storage=storage))
        summary = await BookMemory(storage).summary(PEDRO.uri)
        assert summary is not None and set(summary) == SUMMARY_KEYS
        assert summary["position_ms"] == 90_000 and summary["chapter"] is None
        assert summary["source"] == "shelf" and summary["last_at"] > 0
