"""The audiobook shelf (T1.45): the only books the skill ever offers.

Spotify's Web API has no recommendations for an app like ours, and its
search answers a theme with study guides and obscure titles. So offers
come from a curated list of well-known books, each edition checked
against Spotify by `huxley-spotify-shelf` (`shelf_build.py`), which
writes the JSON this module reads. Open search still plays a book asked
for by name; it never suggests one.

Pure functions plus one small file watcher: loading and validating the
JSON, matching what was said to a category, tag, author or title, and
ranking the books by what the listener did (`Listener`, built from the
skill's memory).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from huxley_skill_spotify.books import Listener, fold
from huxley_skill_spotify.search import title_score

if TYPE_CHECKING:
    from pathlib import Path

SHELF_VERSION = 1
# What was said matches a shelf title (letters, SequenceMatcher).
TITLE_MATCH = 0.8
# ... a category or a tag.
TERM_MATCH = 0.85
# Words around a request that name neither a theme nor a book.
_NOISE = frozenset(
    [
        "un",
        "una",
        "unos",
        "unas",
        "el",
        "la",
        "los",
        "las",
        "lo",
        "de",
        "del",
        "al",
        "y",
        "e",
        "o",
        "u",
        "en",
        "para",
        "con",
        "por",
        "que",
        "sobre",
        "algo",
        "alguno",
        "alguna",
        "otro",
        "otra",
        "otros",
        "otras",
        "mas",
        "buen",
        "bueno",
        "buena",
        "tipo",
        "genero",
        "libro",
        "libros",
        "audiolibro",
        "audiolibros",
        "novela",
        "novelas",
        "pon",
        "ponme",
        "quiero",
        "escuchar",
        "leer",
        "a",
        "an",
        "the",
        "some",
        "any",
        "and",
        "or",
        "in",
        "on",
        "for",
        "with",
        "by",
        "of",
        "about",
        "kind",
        "book",
        "books",
        "audiobook",
        "audiobooks",
        "novel",
        "novels",
        "play",
        "put",
        "me",
        "read",
        "listen",
        "le",
        "les",
        "des",
        "du",
        "au",
        "et",
        "ou",
        "dans",
        "pour",
        "avec",
        "livre",
        "livres",
        "roman",
        "romans",
        "mets",
        "moi",
        "lire",
    ]
)
# Joiners in "<title> de <author>".
_JOINERS = frozenset({"de", "del", "by", "of", "von", "di", "da"})
_ARTICLES = frozenset({"el", "la", "los", "las", "un", "una", "the", "le", "les", "a", "l"})
# Words of a request around a title.
_REQUEST = frozenset(
    {"pon", "ponme", "quiero", "escuchar", "libro", "audiolibro", "play", "book", "audiobook"}
    | {"mets", "livre"}
)


class ShelfError(ValueError):
    """The shelf file can't be used as a whole."""


@dataclass(frozen=True)
class ShelfBook:
    title: str
    author: str
    uri: str
    category: str
    pitch: str
    tags: tuple[str, ...] = ()
    narrator: str | None = None
    language: str = "es"
    verified: str | None = None

    @property
    def book_id(self) -> str:
        return self.uri.rsplit(":", 1)[-1]


@dataclass(frozen=True)
class Shelf:
    categories: tuple[str, ...]
    books: tuple[ShelfBook, ...]
    market: str | None = None
    built: str | None = None
    # Entries dropped by validation (logged with the load).
    skipped: int = 0

    def by_uri(self, uri: str) -> ShelfBook | None:
        return next((b for b in self.books if b.uri == uri), None)


def parse_shelf(data: Any) -> Shelf:
    """Validate the built JSON. A bad entry is skipped (counted); a file
    with the wrong shape, version or no usable book is a ShelfError."""
    if not isinstance(data, dict):
        raise ShelfError("not a JSON object")
    if data.get("version") != SHELF_VERSION:
        raise ShelfError(f"version {data.get('version')!r}, expected {SHELF_VERSION}")
    raw_categories = data.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ShelfError("no categories")
    categories = tuple(str(c).strip() for c in raw_categories if str(c).strip())
    raw_books = data.get("books")
    if not isinstance(raw_books, list):
        raise ShelfError("no books list")
    books: list[ShelfBook] = []
    seen: set[str] = set()
    skipped = 0
    for entry in raw_books:
        book = _book(entry, categories)
        if book is None or book.uri in seen:
            skipped += 1
            continue
        seen.add(book.uri)
        books.append(book)
    if not books:
        raise ShelfError("no valid books")
    market = data.get("market")
    built = data.get("built")
    return Shelf(
        categories=tuple(c for c in categories if any(b.category == c for b in books)),
        books=tuple(books),
        market=str(market) if market else None,
        built=str(built) if built else None,
        skipped=skipped,
    )


def _book(entry: Any, categories: tuple[str, ...]) -> ShelfBook | None:
    if not isinstance(entry, dict):
        return None
    text = {k: str(entry.get(k) or "").strip() for k in ("title", "author", "uri", "category")}
    if not all(text.values()) or not text["uri"].startswith("spotify:show:"):
        return None
    if text["category"] not in categories:
        return None
    tags = entry.get("tags") or []
    if not isinstance(tags, list):
        return None
    narrator = str(entry.get("narrator") or "").strip() or None
    verified = str(entry.get("verified") or "").strip() or None
    return ShelfBook(
        title=text["title"],
        author=text["author"],
        uri=text["uri"],
        category=text["category"],
        pitch=" ".join(str(entry.get("pitch") or "").split()),
        tags=tuple(str(t).strip() for t in tags if str(t).strip()),
        narrator=narrator,
        language=str(entry.get("language") or "es"),
        verified=verified,
    )


def load_shelf(path: Path) -> Shelf:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ShelfError(f"invalid JSON: {exc}") from exc
    return parse_shelf(data)


@dataclass
class ShelfChange:
    """What a refresh found, for the skill to log."""

    event: str  # "loaded", "invalid", "missing"
    error: str | None = None


@dataclass
class ShelfFile:
    """The shelf file, read again when its mtime or size changes. An
    invalid file keeps the last good shelf; a missing one turns the shelf
    off (None) until it's back."""

    path: Path
    shelf: Shelf | None = None
    _stamp: tuple[float, int] | None = field(default=None, repr=False)
    _missing_said: bool = field(default=False, repr=False)

    def refresh(self) -> ShelfChange | None:
        try:
            stat = self.path.stat()
        except OSError:
            self.shelf = None
            self._stamp = None
            if self._missing_said:
                return None
            self._missing_said = True
            return ShelfChange("missing")
        self._missing_said = False
        stamp = (stat.st_mtime, stat.st_size)
        if stamp == self._stamp:
            return None
        self._stamp = stamp
        try:
            self.shelf = load_shelf(self.path)
        except (OSError, ShelfError) as exc:
            return ShelfChange("invalid", error=str(exc))
        return ShelfChange("loaded")


def _author_names(author: str) -> list[list[str]]:
    """Each author of "A, B" as its folded words, in order."""
    return [_words(a) for a in fold(author).split(",") if _words(a)]


def same_author(a: str, b: str) -> bool:
    """One author's words all in the other's: "García Márquez" and
    "Gabriel García Márquez" are the same."""
    for left in map(frozenset, _author_names(a)):
        for right in map(frozenset, _author_names(b)):
            if left <= right or right <= left:
                return True
    return False


def closeness(heard: str, title: str) -> float:
    """How close a title is to what was heard: by letters (digits said as
    words still match, "80 días" and "ochenta días"), or by letters and
    digits ("1984" has no letters at all)."""

    def alnum(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", fold(text))

    a, b = alnum(heard), alnum(title)
    digits = SequenceMatcher(None, a, b).ratio() if a and b else 0.0
    return max(title_score(heard, title), digits)


def core_title(title: str) -> str:
    """A title without its subtitle or edition note: what's before the
    first "(" or ":"."""
    return fold(re.split(r"[(:\[]", title)[0]) or fold(title)


def same_book(title_a: str, author_a: str, title_b: str, author_b: str) -> bool:
    """Two editions of one book: the same author and a close title. The
    shelf's edition may not be the one he already played (T1.45 critic)."""
    return same_author(author_a, author_b) and (
        closeness(core_title(title_a), core_title(title_b)) >= TITLE_MATCH
    )


def equivalents(book: ShelfBook, listener: Listener) -> set[str]:
    """The ids in memory that are this book: its own, and other editions."""
    found = {book.book_id}
    for book_id, title in listener.titles.items():
        author = listener.authors.get(book_id, "")
        if book_id != book.book_id and same_book(book.title, book.author, title, author):
            found.add(book_id)
    return found


def excluded(book: ShelfBook, listener: Listener) -> str | None:
    """Why a shelf book is never offered (any edition of it counts)."""
    for book_id in sorted(equivalents(book, listener)):
        why = listener.excluded(book_id)
        if why is not None:
            return why
    return None


def score(book: ShelfBook, listener: Listener, shelf: Shelf) -> int:
    """Liked author +2, finished author +1, liked category +1, disliked
    author -3."""
    total = 0
    liked = [i for i, o in listener.opinions.items() if o == "liked"]
    disliked = [i for i, o in listener.opinions.items() if o == "disliked"]
    if any(same_author(book.author, listener.authors.get(i, "")) for i in liked):
        total += 2
    if any(same_author(book.author, listener.authors.get(i, "")) for i in listener.ended):
        total += 1
    liked_categories = {b.category for b in shelf.books if equivalents(b, listener) & set(liked)}
    if book.category in liked_categories:
        total += 1
    if any(same_author(book.author, listener.authors.get(i, "")) for i in disliked):
        total -= 3
    return total


def rank(
    books: list[ShelfBook],
    listener: Listener,
    shelf: Shelf,
    *,
    bonus: dict[str, int] | None = None,
) -> tuple[list[ShelfBook], int]:
    """(books to offer, best first; how many were excluded). Ties go to
    the book offered least recently, then shelf order."""
    order = {b.uri: i for i, b in enumerate(shelf.books)}
    kept = [b for b in books if excluded(b, listener) is None]
    extra = bonus or {}
    kept.sort(
        key=lambda b: (
            -(score(b, listener, shelf) + extra.get(b.uri, 0)),
            listener.offered.get(b.book_id, 0.0),
            order.get(b.uri, 0),
        )
    )
    return kept, len(books) - len(kept)


# --- Matching what was said ---


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", fold(text)) if w]


def _stem(word: str) -> str:
    """Plural to singular, roughly: "historias" -> "historia"."""
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _content(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in _NOISE}


def match_category(shelf: Shelf, said: str) -> str | None:
    """The category sharing the most words with what was said ("algo de
    misterio" finds "misterio y suspenso"); ties go to the first."""
    wanted = _content(said)
    best: tuple[int, str] | None = None
    for category in shelf.categories:
        shared = len(wanted & _content(category))
        if shared and (best is None or shared > best[0]):
            best = (shared, category)
    return best[1] if best is not None else None


def match_tag(shelf: Shelf, said: str) -> str | None:
    """A tag whose every word was said ("ciencia ficción", "amor")."""
    wanted = _content(said)
    for book in shelf.books:
        for tag in book.tags:
            words = _content(tag)
            if words and words <= wanted:
                return tag
    return None


def match_author(shelf: Shelf, said: str) -> str | None:
    """A shelf author named by surname: the name's last word in what was
    said ("garcía márquez", "borges", "conan doyle")."""
    words = set(_words(said))
    for book in shelf.books:
        for name in _author_names(book.author):
            if len(name[-1]) > 2 and name[-1] in words:
                return book.author
    return None


def author_named(said: str, author: str) -> bool:
    """A search result's author was named: one of its authors' surname
    (the name's last word) is in what was said."""
    words = set(_words(said))
    return any(len(n[-1]) > 2 and n[-1] in words for n in _author_names(author))


def heard_title(said: str, author: str) -> str:
    """What was said minus the author's words and request words."""
    drop = set(_words(author)) | _REQUEST
    words = [w for w in _words(said) if w not in drop]
    while words and words[-1] in _JOINERS:
        words.pop()
    while words and words[0] in _ARTICLES:
        words.pop(0)
    return " ".join(words)


def _bare_title(title: str) -> str:
    words = _words(core_title(title))
    while words and words[0] in _ARTICLES:
        words.pop(0)
    return " ".join(words)


def title_matches(said: str, title: str, author: str) -> bool:
    """What was said names this title (TITLE_MATCH or closer)."""
    heard = heard_title(said, author)
    return bool(heard) and closeness(heard, _bare_title(title)) >= TITLE_MATCH


def match_title(shelf: Shelf, said: str) -> ShelfBook | None:
    """The shelf book whose title was said, the closest one."""
    best: tuple[float, ShelfBook] | None = None
    for book in shelf.books:
        heard = heard_title(said, book.author)
        if not heard:
            continue
        found = closeness(heard, _bare_title(book.title))
        if best is None or found > best[0]:
            best = (found, book)
    return best[1] if best is not None and best[0] >= TITLE_MATCH else None


def is_theme(shelf: Shelf, said: str) -> bool:
    """What was said is a kind of book on the shelf, not a title or an
    author: "pon algo de misterio" (play_book sends it to the shelf)."""
    if match_title(shelf, said) is not None or match_author(shelf, said) is not None:
        return False
    return match_category(shelf, said) is not None or match_tag(shelf, said) is not None


def synopsis_words(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _words(text[:600]) if len(w) > 3 and w not in _NOISE)


def similar(
    shelf: Shelf,
    *,
    author: str,
    category: str | None,
    tags: tuple[str, ...],
    words: frozenset[str] = frozenset(),
    besides: str = "",
) -> dict[str, int]:
    """Likeness to a book: same category +2, each shared tag +1, same
    author +1, and each word of a synopsis found in a book's tags or
    category +1. Only books with some likeness are returned."""
    found: dict[str, int] = {}
    for book in shelf.books:
        if book.uri == besides:
            continue
        points = 0
        if category and book.category == category:
            points += 2
        points += len(set(book.tags) & set(tags))
        if author and same_author(book.author, author):
            points += 1
        if words:
            terms = {w for t in (book.category, *book.tags) for w in _content(t) if len(w) > 3}
            points += len(terms & words)
        if points:
            found[book.uri] = points
    return found


def spoken_list(items: list[str], lang: str) -> str:
    """'a, b y c'; Spanish says 'e' before an 'i' sound ('cuentos e
    historia'), French 'et', English 'and'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    head, last = items[:-1], items[-1]
    if lang == "es":
        first = fold(last)[:2]
        joiner = (
            "e" if first.startswith("i") or (first == "hi" and fold(last)[2:3] != "e") else "y"
        )
    elif lang == "fr":
        joiner = "et"
    else:
        joiner = "and"
    return f"{', '.join(head)} {joiner} {last}"
