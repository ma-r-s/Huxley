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

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from huxley_sdk import SkillStorage
    from huxley_skill_spotify.search import BookInfo, Match

# Search returns reading guides next to the book ("El extranjero (Guía de
# lectura): Resumen y análisis"); they are never what was asked for.
_GUIDE = re.compile(
    r"\b(resumen|guia de lectura|analisis|summary|study guide|sinopsis|"
    r"guide de lecture|fiche de lecture|lekturehilfe|abreviada|abreviado|abridged)\b"
)
# The same, in the first part of a synopsis: only phrases a real book's
# blurb wouldn't use ("un analisis de la sociedad" is a novel's blurb).
_GUIDE_BLURB = re.compile(
    r"\b(guia de lectura|resumen y analisis|resumen del libro|study guide|summary of|"
    r"guide de lecture|fiche de lecture|lekturehilfe|version abreviada|edicion abreviada|"
    r"abridged)\b"
)
# A resumed book backs up a little so it doesn't start mid-word: 2 s plus a
# tenth of the pause, at most 20 s (the rule the library skill used).
REWIND_BASE_MS = 2_000
REWIND_MAX_MS = 20_000
# This much audio proves a book plays here: a refused one stops after
# 0.3-1.2 s (seen 2026-10-12).
PLAYED_AFTER_MS = 10_000
# Soloist plays on when nobody reads its capture: a walkie switched off
# mid-book kept a stream "playing" for 16 min before the server noticed,
# and the place jumped 16 min past what was heard (2026-10-12). A place
# more than this ahead of the audio actually sent is not believed...
AHEAD_SLACK_MS = 5_000
# ...and goes back to this far before the last audio sent, as audiobook
# apps do: some of it was still on its way when the listener went.
AHEAD_REWIND_MS = 10_000


def fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def is_guide(title: str, about: str = "", edition: str = "") -> bool:
    """A reading guide, summary or abridgement, by its title, the start of
    its synopsis, or Spotify's edition note ("Abridged edition"; the shelf
    build met 14 among the editions of shelf books, 2026-10-13)."""
    return bool(
        _GUIDE.search(fold(title))
        or _GUIDE_BLURB.search(fold(about[:300]))
        or fold(edition).startswith("abridged")
    )


def order_books(matches: list[Match], language: str) -> list[Match]:
    """Books before reading guides, the persona's language first, otherwise
    Spotify's order."""

    def key(item: tuple[int, Match]) -> tuple[bool, bool, int]:
        index, match = item
        own = not match.languages or language in match.languages
        return (is_guide(match.title, edition=match.edition), not own, index)

    return [m for _, m in sorted(enumerate(matches), key=key)]


def rewind_ms(paused_s: float) -> int:
    return int(min(REWIND_MAX_MS, REWIND_BASE_MS + paused_s * 100))


def capped_position(position_ms: int, sent_until_ms: int) -> int | None:
    """The place to keep when Soloist's position ran more than
    AHEAD_SLACK_MS past the audio sent (which reaches `sent_until_ms` in
    the same chapter); None when it didn't."""
    if position_ms <= sent_until_ms + AHEAD_SLACK_MS:
        return None
    return max(0, sent_until_ms - AHEAD_REWIND_MS)


_NUMBER = re.compile(r"\d+")
_CHAPTER_WORDS = ("capitulo", "chapter", "chapitre")
# The tool passes the chapter "as said": "capítulo cinco".
_NUMBER_WORDS = {
    w: i + 1
    for words in (
        "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce "
        "quince dieciseis diecisiete dieciocho diecinueve veinte veintiuno veintidos "
        "veintitres veinticuatro veinticinco veintiseis veintisiete veintiocho "
        "veintinueve treinta",
        "one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty",
        "un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize",
    )
    for i, w in enumerate(words.split())
}
_NUMBER_WORDS["primero"] = _NUMBER_WORDS["primer"] = _NUMBER_WORDS["first"] = 1
# A place kept before "desde el principio" is for undoing it soon after.
SAVED_PLACE_DAYS = 30
# A book Spotify refused is checked again after this long.
UNAVAILABLE_DAYS = 30


def find_chapter(book: BookInfo, spoken: str) -> int | None:
    """The chapter the user named, by its name, never by index: short parts
    ("Créditos iniciales", "Primera Parte") make the index drift from the
    chapter's number. "5" and "capítulo 5" find "Capítulo 5"."""
    wanted = fold(spoken)
    if not wanted:
        return None
    wanted = " ".join(str(_NUMBER_WORDS.get(w, w)) for w in wanted.split())
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


@dataclass(frozen=True)
class Label:
    """How a book is named in its summary: the shelf's own title and
    author for a shelf book, Spotify's otherwise."""

    title: str
    author: str | None
    source: str  # "shelf" or "search"


# A started book not heard for this long, never finished, and not the
# last one: left. Not offered again, not counted as in progress.
ABANDONED_DAYS = 21


@dataclass(frozen=True)
class Listener:
    """What the memory knows, as plain data for ranking (shelf.py). Keys
    are book ids (the last part of the show URI)."""

    # Started here (10 s heard): id -> when last heard (epoch s).
    started: dict[str, float] = field(default_factory=dict)
    # Finished at some point (survives a start from the beginning).
    ended: frozenset[str] = frozenset()
    # id -> "liked" / "disliked"
    opinions: dict[str, str] = field(default_factory=dict)
    # Spotify refused it here lately.
    refused: frozenset[str] = frozenset()
    # id -> when last offered (epoch s).
    offered: dict[str, float] = field(default_factory=dict)
    # The last book listened to.
    last_book: str | None = None
    # id -> author and id -> title, for every book the memory names (on
    # the shelf or not): another edition of a shelf book is that book.
    authors: dict[str, str] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)

    def abandoned(self, book_id: str, now: float) -> bool:
        heard = self.started.get(book_id)
        return (
            heard is not None
            and book_id not in self.ended
            and book_id != self.last_book
            and now - heard >= ABANDONED_DAYS * 86_400
        )

    def excluded(self, book_id: str) -> str | None:
        """Why a book is never offered, or None. Every started book is
        out: finished, in progress, or abandoned."""
        if book_id in self.refused:
            return "refused"
        if self.opinions.get(book_id) == "disliked":
            return "disliked"
        if book_id in self.ended:
            return "finished"
        if book_id in self.started:
            return "started"
        return None


OPINIONS = ("liked", "disliked")


class BookMemory:
    """The skill's memory of books, in its namespaced storage."""

    def __init__(self, storage: SkillStorage) -> None:
        self._storage = storage
        # add_seconds reads then writes: two streams ending together
        # (a press right after a resume) must not lose one's seconds.
        self._seconds_lock = asyncio.Lock()
        # The summary is read, merged and written from spawned tasks (a
        # place and the end of a book land together): one at a time.
        self._summary_lock = asyncio.Lock()

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

    # Books a quick check found Spotify will play here, or won't (T1.40):
    # recommendations only offer books that can be listened to.
    async def mark_ok(self, uri: str, title: str, author: str | None) -> None:
        await self._set(
            f"book:ok:{_book_id(uri)}",
            {"uri": uri, "title": title, "author": author, "at": time.time()},
        )

    async def playable(self, uri: str) -> bool:
        """It played here, or a check found it plays: it's included."""
        return await self.played(uri) or await self._get(f"book:ok:{_book_id(uri)}") is not None

    async def any_playable(self, besides: str) -> str | None:
        """A book known to play, other than `besides`: the control that
        tells "this book isn't available" from "no hours left"."""
        for prefix in ("book:played:", "book:ok:"):
            for _, raw in await self._storage.list_settings(prefix):
                try:
                    uri = json.loads(raw).get("uri")
                except (ValueError, AttributeError):
                    continue
                if uri and uri != besides:
                    return str(uri)
        return None

    async def mark_unavailable(self, uri: str) -> None:
        """Spotify refused it here: a check's earlier "ok" was wrong."""
        await self._set(f"book:no:{_book_id(uri)}", time.time())
        await self._storage.delete_setting(f"book:ok:{_book_id(uri)}")

    async def unavailable(self, uri: str) -> bool:
        """Spotify refused it here within the last month (a plan can change)."""
        at = await self._get(f"book:no:{_book_id(uri)}")
        return isinstance(at, int | float) and time.time() - at < UNAVAILABLE_DAYS * 86_400

    # Books that ended: replaying one isn't a refusal. `finished` is the
    # book as it is now (a start from the beginning clears it); `ended`
    # remembers it was finished once, so it isn't offered again.
    async def finished(self, uri: str) -> bool:
        return await self._get(f"book:finished:{_book_id(uri)}") is not None

    async def mark_finished(self, uri: str, label: Label | None = None) -> None:
        now = time.time()
        await self._set(f"book:finished:{_book_id(uri)}", now)
        await self._set(f"book:ended:{_book_id(uri)}", now)
        await self.update_summary(uri, label, finished=True)

    async def clear_finished(self, uri: str, label: Label | None = None) -> None:
        await self._storage.delete_setting(f"book:finished:{_book_id(uri)}")
        await self.update_summary(uri, label, finished=False)

    async def ended(self, uri: str) -> bool:
        return await self._get(f"book:ended:{_book_id(uri)}") is not None or await self.finished(
            uri
        )

    # What the listener said of a book (T1.45).
    async def set_opinion(self, uri: str, opinion: str, label: Label) -> None:
        await self._set(
            f"book:opinion:{_book_id(uri)}",
            {
                "uri": uri,
                "title": label.title,
                "author": label.author,
                "opinion": opinion,
                "at": time.time(),
            },
        )
        await self.update_summary(uri, label, opinion=opinion)

    async def opinion(self, uri: str) -> str | None:
        found = await self._get(f"book:opinion:{_book_id(uri)}")
        value = found.get("opinion") if isinstance(found, dict) else None
        return str(value) if value in OPINIONS else None

    async def mark_offered(self, uris: list[str]) -> None:
        now = time.time()
        for uri in uris:
            await self._set(f"book:offered:{_book_id(uri)}", now)

    # Where a book was last heard, and where it was before a restart.
    async def place(self, uri: str) -> Place | None:
        return self._place(await self._get(f"book:place:{_book_id(uri)}"))

    async def set_place(
        self,
        uri: str,
        place: Place,
        label: Label | None = None,
        *,
        chapter: int | None = None,
        chapters: int | None = None,
    ) -> None:
        await self._set(
            f"book:place:{_book_id(uri)}",
            {"chapter": place.chapter_uri, "position_ms": place.position_ms},
        )
        if label is not None:
            await self.update_summary(
                uri,
                label,
                chapter=chapter,
                chapters=chapters,
                position_ms=place.position_ms,
                last_at=time.time(),
            )

    async def saved_place(self, uri: str) -> Place | None:
        found = await self._get(f"book:saved:{_book_id(uri)}")
        if isinstance(found, dict) and time.time() - float(found.get("at") or 0) > (
            SAVED_PLACE_DAYS * 86_400
        ):
            return None
        return self._place(found)

    async def save_place(self, uri: str, place: Place) -> None:
        await self._set(
            f"book:saved:{_book_id(uri)}",
            {"chapter": place.chapter_uri, "position_ms": place.position_ms, "at": time.time()},
        )

    @staticmethod
    def _place(found: Any) -> Place | None:
        if isinstance(found, dict) and found.get("chapter"):
            return Place(str(found["chapter"]), int(found.get("position_ms") or 0))
        return None

    # Seconds of book audio heard, per calendar month.
    async def add_seconds(self, seconds: float) -> float:
        async with self._seconds_lock:
            key = f"book:seconds:{datetime.now(UTC):%Y-%m}"
            total = float(await self._get(key) or 0) + seconds
            await self._set(key, round(total, 1))
            return total

    async def seconds_this_month(self) -> float:
        return float(await self._get(f"book:seconds:{datetime.now(UTC):%Y-%m}") or 0)

    # The page's view of each book (T1.45): `book:summary:<id>`, so the
    # database key is `spotify:book:summary:<id>` and the value is JSON
    # text. Written whenever a book's place, finished state or opinion
    # changes; huxley-watch reads it and never writes.
    async def update_summary(self, uri: str, label: Label | None, **changes: Any) -> None:
        async with self._summary_lock:
            key = f"book:summary:{_book_id(uri)}"
            found = await self._get(key)
            if not isinstance(found, dict):
                if label is None:
                    return  # nothing to name it by: written on its first play
                played = await self._get(f"book:played:{_book_id(uri)}")
                found = {
                    "uri": uri,
                    "title": label.title,
                    "author": label.author,
                    "chapters": None,
                    "chapter": None,
                    "position_ms": None,
                    # 0: never heard here (an opinion on an offered book).
                    "last_at": float(played.get("at") or 0) if isinstance(played, dict) else 0.0,
                    "finished": await self.finished(uri),
                    "opinion": await self.opinion(uri),
                    "source": label.source,
                }
            elif label is not None:
                found.update(title=label.title, author=label.author, source=label.source)
            for name, value in changes.items():
                if value is not None or name in ("opinion",):
                    found[name] = value
            await self._set(key, found)

    async def summary(self, uri: str) -> dict[str, Any] | None:
        found = await self._get(f"book:summary:{_book_id(uri)}")
        return found if isinstance(found, dict) else None

    async def _rows(self, prefix: str) -> dict[str, Any]:
        rows: dict[str, Any] = {}
        for key, raw in await self._storage.list_settings(prefix):
            try:
                rows[key.removeprefix(prefix)] = json.loads(raw)
            except ValueError:
                continue
        return rows

    async def listener(self) -> Listener:
        """Everything ranking needs, in one read of each kind of key."""
        now = time.time()
        played = {
            i: r for i, r in (await self._rows("book:played:")).items() if isinstance(r, dict)
        }
        summaries = await self._rows("book:summary:")
        opinions = await self._rows("book:opinion:")
        authors: dict[str, str] = {}
        titles: dict[str, str] = {}
        started: dict[str, float] = {}
        for book_id, row in played.items():
            summary = summaries.get(book_id)
            heard = float(summary.get("last_at") or 0) if isinstance(summary, dict) else 0.0
            started[book_id] = max(float(row.get("at") or 0), heard)
        for source in (played, summaries, opinions):
            for book_id, row in source.items():
                if not isinstance(row, dict):
                    continue
                if row.get("author"):
                    authors.setdefault(book_id, str(row["author"]))
                if row.get("title"):
                    titles.setdefault(book_id, str(row["title"]))
        ended = set(await self._rows("book:ended:")) | set(await self._rows("book:finished:"))
        refused = {
            i
            for i, at in (await self._rows("book:no:")).items()
            if isinstance(at, int | float) and now - at < UNAVAILABLE_DAYS * 86_400
        }
        offered = {
            i: float(at)
            for i, at in (await self._rows("book:offered:")).items()
            if isinstance(at, int | float)
        }
        last = await self.last_book()
        return Listener(
            started=started,
            ended=frozenset(ended),
            opinions={
                i: str(r["opinion"])
                for i, r in opinions.items()
                if isinstance(r, dict) and r.get("opinion") in OPINIONS
            },
            refused=frozenset(refused),
            offered=offered,
            last_book=_book_id(str(last["uri"])) if last else None,
            authors=authors,
            titles=titles,
        )
