"""Audiobooks in the Spotify skill: what it remembers about books, and the
small choices around one (T1.40).

Spotify keeps the place in each book. This keeps what Spotify doesn't tell
an app: which books played here (a refusal of one of those means the
month's hours ran out; of any other, that the book must be bought), which
ended, the last book and the last music apart (so "pon música" after a book
plays music), where a book was before "desde el principio" (so it can be
undone), and the seconds of book audio per month (for the logs, and the
hours budget when one is configured).
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from huxley_sdk import SkillStorage
    from huxley_skill_spotify.search import BookInfo, Match

# Search returns reading guides next to the book ("El extranjero (Guía de
# lectura): Resumen y análisis"); they are never what was asked for.
_GUIDE = re.compile(
    r"\b(resumen|guia de lectura|analisis|summary|study guide|sinopsis|"
    r"guide de lecture|fiche de lecture)\b"
)
# A resumed book backs up a little so it doesn't start mid-word: 2 s plus a
# tenth of the pause, at most 20 s (the rule the library skill used).
REWIND_BASE_MS = 2_000
REWIND_MAX_MS = 20_000
# This much audio proves a book plays here: a refused one stops after
# 0.3-1.2 s (seen 2026-10-12).
PLAYED_AFTER_MS = 10_000


def fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def is_guide(title: str) -> bool:
    return bool(_GUIDE.search(fold(title)))


def order_books(matches: list[Match], language: str) -> list[Match]:
    """Books before reading guides, the persona's language first, otherwise
    Spotify's order."""

    def key(item: tuple[int, Match]) -> tuple[bool, bool, int]:
        index, match = item
        own = not match.languages or language in match.languages
        return (is_guide(match.title), not own, index)

    return [m for _, m in sorted(enumerate(matches), key=key)]


def rewind_ms(paused_s: float) -> int:
    return int(min(REWIND_MAX_MS, REWIND_BASE_MS + paused_s * 100))


_NUMBER = re.compile(r"\d+")
_CHAPTER_WORDS = ("capitulo", "chapter", "chapitre")


def find_chapter(book: BookInfo, spoken: str) -> int | None:
    """The chapter the user named, by its name, never by index: short parts
    ("Créditos iniciales", "Primera Parte") make the index drift from the
    chapter's number. "5" and "capítulo 5" find "Capítulo 5"."""
    wanted = fold(spoken)
    if not wanted:
        return None
    names = [fold(c.name) for c in book.chapters]
    for i, name in enumerate(names):
        if name == wanted:
            return i
    digits = _NUMBER.findall(wanted)
    if digits:
        number = digits[0]
        word = re.compile(rf"(?<!\d){number}(?!\d)")
        numbered = [i for i, n in enumerate(names) if word.search(n)]
        for i in numbered:
            if any(w in names[i] for w in _CHAPTER_WORDS):
                return i
        return numbered[0] if numbered else None
    for i, name in enumerate(names):
        if wanted in name:
            return i
    return None


def remaining_ms(book: BookInfo, index: int, position_ms: int) -> tuple[int, int]:
    """(left in this chapter, left in the book)."""
    chapter = book.chapters[index]
    in_chapter = max(0, chapter.duration_ms - position_ms)
    after = sum(c.duration_ms for c in book.chapters[index + 1 :])
    return in_chapter, in_chapter + after


def _book_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


@dataclass(frozen=True)
class Place:
    chapter_uri: str
    position_ms: int


class Shelf:
    """The skill's memory of books, in its namespaced storage."""

    def __init__(self, storage: SkillStorage) -> None:
        self._storage = storage

    async def _get(self, key: str) -> Any:
        raw = await self._storage.get_setting(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    async def _set(self, key: str, value: Any) -> None:
        await self._storage.set_setting(key, json.dumps(value, ensure_ascii=False))

    # The last book and the last music, kept apart.
    async def last_book(self) -> dict[str, Any] | None:
        found = await self._get("book:last")
        return found if isinstance(found, dict) and found.get("uri") else None

    async def set_last_book(self, uri: str, title: str, author: str | None) -> None:
        await self._set("book:last", {"uri": uri, "title": title, "author": author})

    async def last_music(self) -> dict[str, Any] | None:
        found = await self._get("music:last")
        return found if isinstance(found, dict) and found.get("uri") else None

    async def set_last_music(self, match: Match) -> None:
        await self._set(
            "music:last",
            {"uri": match.uri, "kind": match.kind, "title": match.title, "by": match.artist},
        )

    # Books that played here: proof they're included in the plan.
    async def played(self, uri: str) -> bool:
        return await self._get(f"book:played:{_book_id(uri)}") is not None

    async def mark_played(self, uri: str, title: str, author: str | None) -> None:
        await self._set(
            f"book:played:{_book_id(uri)}",
            {"uri": uri, "title": title, "author": author, "at": time.time()},
        )

    async def played_books(self, limit: int = 3) -> list[dict[str, Any]]:
        """Books that played here, most recent first."""
        rows = []
        for _, raw in await self._storage.list_settings("book:played:"):
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("uri"):
                rows.append(row)
        rows.sort(key=lambda r: float(r.get("at") or 0), reverse=True)
        return rows[:limit]

    # Books that ended: replaying one isn't a refusal.
    async def finished(self, uri: str) -> bool:
        return await self._get(f"book:finished:{_book_id(uri)}") is not None

    async def mark_finished(self, uri: str) -> None:
        await self._set(f"book:finished:{_book_id(uri)}", time.time())

    async def clear_finished(self, uri: str) -> None:
        await self._storage.delete_setting(f"book:finished:{_book_id(uri)}")

    # Where a book was last heard, and where it was before a restart.
    async def place(self, uri: str) -> Place | None:
        return self._place(await self._get(f"book:place:{_book_id(uri)}"))

    async def set_place(self, uri: str, place: Place) -> None:
        await self._set(
            f"book:place:{_book_id(uri)}",
            {"chapter": place.chapter_uri, "position_ms": place.position_ms},
        )

    async def saved_place(self, uri: str) -> Place | None:
        return self._place(await self._get(f"book:saved:{_book_id(uri)}"))

    async def save_place(self, uri: str, place: Place) -> None:
        await self._set(
            f"book:saved:{_book_id(uri)}",
            {"chapter": place.chapter_uri, "position_ms": place.position_ms},
        )

    @staticmethod
    def _place(found: Any) -> Place | None:
        if isinstance(found, dict) and found.get("chapter"):
            return Place(str(found["chapter"]), int(found.get("position_ms") or 0))
        return None

    # Seconds of book audio heard, per calendar month.
    async def add_seconds(self, seconds: float) -> float:
        key = f"book:seconds:{datetime.now(UTC):%Y-%m}"
        total = float(await self._get(key) or 0) + seconds
        await self._set(key, round(total, 1))
        return total

    async def seconds_this_month(self) -> float:
        return float(await self._get(f"book:seconds:{datetime.now(UTC):%Y-%m}") or 0)
