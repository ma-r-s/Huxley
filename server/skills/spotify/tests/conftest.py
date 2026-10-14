"""Fixtures for huxley-skill-spotify.

- `FakeSoloist`: the skill's Soloist client with the socket replaced by
  a recorder. `play` flips the cached state to "playing" the way the
  real Soloist's events would, unless a test says otherwise.
- `pcm_server`: a real TCP server on localhost standing in for the
  container's PCM capture, serving canned chunks.
- `make_ctx`: a SkillContext whose inject_turn and background_task are
  recorders.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from huxley_sdk.testing import make_test_context
from huxley_skill_spotify.capture import CHUNK_BYTES
from huxley_skill_spotify.search import SearchError
from huxley_skill_spotify.soloist import SoloistClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from huxley_sdk import SkillContext
    from huxley_skill_spotify.search import BookInfo, Match


class FakeSoloist(SoloistClient):
    """Starts paired but inactive, as a freshly started Soloist is; it
    turns active a moment after playback starts (tests call `activate`)."""

    def __init__(self) -> None:
        logger = MagicMock()
        super().__init__("ws://fake", logger)
        self.sent: list[dict[str, Any]] = []
        self.start_playing = True
        self.state.connected = True
        self.state.logged_in = True
        self.state.is_active = False

    def send(self, command: str, *, generation: int | None = None, **fields: Any) -> None:
        self.sent.append({"command": command, "generation": generation, **fields})
        if command == "play" and self.start_playing:
            self.state.status = "playing"
            if "uri" in fields:
                self.state.context_uri = fields["uri"]
        if command == "pause":
            self.state.status = "paused"

    def activate(self) -> None:
        self.state.is_active = True

    async def run(self) -> None:
        return None

    def commands(self) -> list[str]:
        return [c["command"] for c in self.sent]


class FakeSearch:
    """`match` is the best result; `others` the runners-up. `books` are
    what `book(uri)` answers (chapters and all)."""

    def __init__(
        self,
        match: Match | None,
        others: list[Match] | None = None,
        books: dict[str, BookInfo] | None = None,
    ) -> None:
        self.match = match
        self.others = others or []
        self.books = books or {}
        self.queries: list[tuple[str, str]] = []

    async def book(self, uri: str) -> BookInfo:
        if uri not in self.books:
            raise SearchError(f"no book {uri}")
        return self.books[uri]

    async def candidates(self, query: str, kind: str = "any", *, limit: int = 3) -> list[Match]:
        self.queries.append((query, kind))
        if self.match is None:
            return []
        return [self.match, *self.others][:limit]

    async def aclose(self) -> None:
        return None


@dataclasses.dataclass
class PcmServer:
    host: str
    port: int
    chunks: list[bytes]
    # Set to keep the connection open after the canned chunks.
    hold_open: asyncio.Event
    connections: int = 0

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@pytest.fixture
async def pcm_server() -> AsyncIterator[PcmServer]:
    server_state = PcmServer("127.0.0.1", 0, [bytes([1]) * CHUNK_BYTES] * 3, asyncio.Event())

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        server_state.connections += 1
        try:
            for chunk in server_state.chunks:
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0)
            if server_state.hold_open.is_set():
                while True:
                    writer.write(bytes(CHUNK_BYTES))
                    await writer.drain()
                    await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    server_state.port = server.sockets[0].getsockname()[1]
    async with server:
        yield server_state
        server.close()


@dataclasses.dataclass
class Recorded:
    injected: list[tuple[str, str | None]] = dataclasses.field(default_factory=list)
    tasks: list[str] = dataclasses.field(default_factory=list)


def make_ctx(
    tmp_path: Path, recorded: Recorded, *, config: dict[str, Any] | None = None
) -> SkillContext:
    base = make_test_context(
        name="spotify", config=config or {}, persona_data_dir=tmp_path, language="es"
    )

    async def inject_turn(prompt: str, /, *, dedup_key: str | None = None, **_: Any) -> None:
        recorded.injected.append((prompt, dedup_key))

    def background_task(name: str, factory: Callable[[], Any], **_: Any) -> MagicMock:
        recorded.tasks.append(name)
        return MagicMock()

    return dataclasses.replace(base, inject_turn=inject_turn, background_task=background_task)


# A small shelf (T1.45), shaped like server/personas/abuelos/books/shelf.json.
SHELF_CATEGORIES = [
    "misterio y suspenso",
    "novela latinoamericana",
    "cuentos",
    "historia y memorias",
    "aventuras",
]


def shelf_book(
    title: str, author: str, uri: str, category: str, tags: list[str], pitch: str = ""
) -> dict[str, Any]:
    return {
        "title": title,
        "author": author,
        "narrator": "Alguien",
        "uri": uri,
        "language": "es",
        "category": category,
        "tags": tags,
        "pitch": pitch or f"De qué trata {title}.",
        "verified": "2026-10-13",
    }


SHELF_BOOKS = [
    shelf_book(
        "Cien años de soledad",
        "Gabriel García Márquez",
        "spotify:show:cien",
        "novela latinoamericana",
        ["familia", "colombia"],
        "La historia de los Buendía en Macondo.",
    ),
    shelf_book(
        "El coronel no tiene quien le escriba",
        "Gabriel García Márquez",
        "spotify:show:coronel",
        "novela latinoamericana",
        ["colombia"],
    ),
    shelf_book(
        "Pedro Páramo", "Juan Rulfo", "spotify:show:pedro", "novela latinoamericana", ["méxico"]
    ),
    shelf_book(
        "El sabueso de los Baskerville",
        "Arthur Conan Doyle",
        "spotify:show:sabueso",
        "misterio y suspenso",
        ["detectives", "inglaterra"],
        "Sherlock Holmes y un perro que aterra a una familia.",
    ),
    shelf_book(
        "Asesinato en el Orient Express",
        "Agatha Christie",
        "spotify:show:orient",
        "misterio y suspenso",
        ["detectives", "crimen", "viajes"],
        "Un crimen en un tren detenido por la nieve.",
    ),
    shelf_book(
        "Diez negritos",
        "Agatha Christie",
        "spotify:show:diez",
        "misterio y suspenso",
        ["crimen"],
        "Diez invitados en una isla, y uno a uno van muriendo.",
    ),
    shelf_book(
        "Estudio en escarlata",
        "Arthur Conan Doyle",
        "spotify:show:escarlata",
        "misterio y suspenso",
        ["detectives", "inglaterra"],
    ),
    shelf_book(
        "Ficciones", "Jorge Luis Borges", "spotify:show:ficciones", "cuentos", ["fantástico"]
    ),
    shelf_book(
        "Cuentos de la selva",
        "Horacio Quiroga",
        "spotify:show:selva",
        "cuentos",
        ["animales", "naturaleza"],
    ),
    shelf_book(
        "Relato de un náufrago",
        "Gabriel García Márquez",
        "spotify:show:naufrago",
        "historia y memorias",
        ["mar", "periodismo", "colombia"],
    ),
    shelf_book(
        "La vuelta al mundo en ochenta días",
        "Julio Verne",
        "spotify:show:vuelta",
        "aventuras",
        ["viajes"],
    ),
]


def shelf_data(books: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "built": "2026-10-13",
        "market": "CO",
        "categories": SHELF_CATEGORIES,
        "books": SHELF_BOOKS if books is None else books,
    }


def write_shelf(path: Path, books: list[dict[str, Any]] | None = None) -> Path:
    import json

    path.write_text(json.dumps(shelf_data(books), ensure_ascii=False), encoding="utf-8")
    return path
