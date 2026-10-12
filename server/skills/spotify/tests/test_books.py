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
import json
from typing import TYPE_CHECKING, Any

from huxley_sdk import AudioStream
from huxley_skill_spotify import skill as skill_module
from huxley_skill_spotify.books import Place, Shelf
from huxley_skill_spotify.search import BookInfo, Chapter, Match
from huxley_skill_spotify.skill import SpotifySkill

from .conftest import FakeSearch, FakeSoloist, PcmServer, Recorded, make_ctx

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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
SONG = Match(uri="spotify:track:sinti", kind="track", title="Sin Ti", artist="Los Panchos")


class BookSoloist(FakeSoloist):
    """FakeSoloist that knows one book's chapters (see the module doc)."""

    def __init__(self, resume: int = 1, position: int = 60_000) -> None:
        super().__init__()
        self.resume = resume
        self.resume_position = position
        self.index: int | None = None
        self.skips_ignored = False

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
        if command == "play" and fields.get("uri") == BOOK_INFO.uri and self.start_playing:
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
) -> tuple[SpotifySkill, BookSoloist, Recorded, Shelf]:
    client = client or BookSoloist()
    recorded = Recorded()
    search = FakeSearch(match, others, books={BOOK_INFO.uri: BOOK_INFO})
    skill = SpotifySkill(client=client, search=search)  # type: ignore[arg-type]
    await skill.setup(make_ctx(tmp_path, recorded, config={"pcm": pcm.address, **(config or {})}))
    assert skill._shelf is not None
    return skill, client, recorded, skill._shelf  # type: ignore[return-value]


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

    async def test_naming_the_author_is_enough(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "el libro de garcía márquez"})
        assert isinstance(result.side_effect, AudioStream)

    async def test_no_title_continues_the_last_book(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, shelf = await _skill(tmp_path, pcm_server)
        none_yet = await skill.handle("play_book", {})
        assert _out(none_yet)["reason"] == "nothing_started"
        assert "ningún libro empezado" in _out(none_yet)["then"]

        await shelf.set_last_book(BOOK.uri, BOOK.title, BOOK.artist)
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
        skill, _, _, shelf = await _skill(tmp_path, pcm_server, others=[GUIDE, CORONEL])
        await shelf.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
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

    async def test_no_query_lists_books_listened_to_here(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _, shelf = await _skill(tmp_path, pcm_server)
        none = await skill.handle("find_books", {})
        assert _out(none)["reason"] == "books_none"
        assert "¿De qué autor o de qué tema busco?" in _out(none)["then"]

        await shelf.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        listed = await skill.handle("find_books", {})
        assert [b["title"] for b in _out(listed)["books"]] == [BOOK.title]
        assert skill._search.queries == []  # type: ignore[union-attr]


class TestRefusals:
    async def _refused(
        self, tmp_path: Path, pcm: PcmServer, **kwargs: Any
    ) -> tuple[Recorded, FakeSoloist]:
        pcm.hold_open.set()
        skill, client, recorded, shelf = await _skill(tmp_path, pcm, **kwargs)
        if kwargs.get("config", {}).get("played_before"):
            await shelf.mark_played(BOOK.uri, BOOK.title, BOOK.artist)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"  # Spotify stops it at once
        await _settle()
        return recorded, client

    async def test_a_book_never_played_here_must_be_bought(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        recorded, client = await self._refused(tmp_path, pcm_server)
        assert [key for _, key in recorded.injected] == ["spotify:book_must_buy"]
        prompt = recorded.injected[0][0]
        assert "NO está sonando" in prompt
        assert "«Ese libro hay que comprarlo aparte; no lo puedo poner." in prompt
        assert "pause" not in client.commands()

    async def test_a_book_that_played_here_means_the_hours_ran_out(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        recorded, _ = await self._refused(tmp_path, pcm_server, config={"played_before": True})
        assert [key for _, key in recorded.injected] == ["spotify:book_no_hours"]
        assert "«Este mes ya no quedan horas para escuchar libros." in recorded.injected[0][0]

    async def test_the_configured_budget_spent_means_the_hours_ran_out(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded, shelf = await _skill(
            tmp_path, pcm_server, config={"book_hours_per_month": 10}
        )
        await shelf.add_seconds(10 * 3600)
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
        skill, client, recorded, shelf = await _skill(tmp_path, pcm_server)
        client.resume = 3  # the last chapter
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        async for _ in result.side_effect.factory():  # type: ignore[union-attr]
            client.state.status = "idle"
        await _settle()

        assert [key for _, key in recorded.injected] == ["spotify:book_finished"]
        assert "«Se terminó Cien años de soledad. ¿Busco otro libro?»" in recorded.injected[0][0]
        assert await shelf.finished(BOOK.uri)
        again = await skill.handle("play_book", {"title": "cien años de soledad"})
        assert _out(again)["reason"] == "finished_replay"
        assert "¿Lo pongo desde el principio?" in _out(again)["then"]

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


class TestFromTheBeginning:
    async def test_early_in_the_book_it_starts_over_without_asking(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, shelf = await _skill(tmp_path, pcm_server)
        await shelf.set_place(BOOK.uri, Place(CHAPTERS[1].uri, 60_000))
        result = await skill.handle(
            "play_book", {"title": "cien años de soledad", "from_beginning": True}
        )
        await _take(result.side_effect, 2)  # type: ignore[arg-type]
        assert client.index == 0 and client.state.position_ms == 0
        assert await shelf.saved_place(BOOK.uri) == Place(CHAPTERS[1].uri, 60_000)

    async def test_deep_in_the_book_it_asks_book_or_chapter_and_can_undo(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, shelf = await _skill(tmp_path, pcm_server)
        await shelf.set_place(BOOK.uri, Place(CHAPTERS[3].uri, 300_000))
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
    async def test_pon_musica_after_a_book_plays_the_last_music(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _, shelf = await _skill(tmp_path, pcm_server, match=SONG)
        song = await skill.handle("play_music", {"query": "sin ti"})
        await _take(song.side_effect, 2)  # type: ignore[arg-type]
        assert (await shelf.last_music() or {}).get("uri") == SONG.uri

        client.load(1, 60_000)  # then a book played
        again = await skill.handle("play_music", {})
        await _take(again.side_effect, 1)  # type: ignore[arg-type]
        assert _plays(client)[-1]["uri"] == SONG.uri

    async def test_a_book_heard_ten_seconds_is_remembered(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "PLAYED_AFTER_MS", 100)
        pcm_server.hold_open.set()
        skill, _, _, shelf = await _skill(tmp_path, pcm_server)
        result = await skill.handle("play_book", {"title": "cien años de soledad"})
        await _take(result.side_effect, 3)  # type: ignore[arg-type]
        assert await shelf.played(BOOK.uri)
        assert (await shelf.last_book() or {}).get("uri") == BOOK.uri
        assert await shelf.seconds_this_month() > 0
