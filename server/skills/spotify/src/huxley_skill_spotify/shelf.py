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


class Judge:
    """Exclusions and scores for one look at the listener's memory. Each
    book's other editions are found once: recomputing them per call made
    ranking the whole shelf take 1.6-2.4 s on a Mac with 10-20 books
    remembered, and it grows as he listens (T1.45 review)."""

    def __init__(self, shelf: Shelf, listener: Listener) -> None:
        self.shelf = shelf
        self.listener = listener
        self._names = {i: _author_sets(a) for i, a in listener.authors.items()}
        self._cores = {i: core_title(t) for i, t in listener.titles.items()}
        self._eq: dict[str, frozenset[str]] = {}
        opinions = listener.opinions
        self._liked = {i for i, o in opinions.items() if o == "liked"}
        self._disliked = {i for i, o in opinions.items() if o == "disliked"}
        self._liked_categories = {
            b.category for b in shelf.books if self.equivalents(b) & self._liked
        }

    def _same_book_as(self, title: str, author: str, book_id: str) -> bool:
        names = _author_sets(author)
        theirs = self._names.get(book_id, [])
        if not any(a <= b or b <= a for a in names for b in theirs):
            return False
        return closeness(core_title(title), self._cores.get(book_id, "")) >= TITLE_MATCH

    def ids_for(self, title: str, author: str, uri: str) -> frozenset[str]:
        """The ids in memory that are this book: its own, and other
        editions (same author, close title; the shelf's edition may not
        be the one he already played, T1.45 critic)."""
        own = uri.rsplit(":", 1)[-1]
        return frozenset(
            {own} | {i for i in self._cores if i != own and self._same_book_as(title, author, i)}
        )

    def equivalents(self, book: ShelfBook) -> frozenset[str]:
        found = self._eq.get(book.uri)
        if found is None:
            found = self._eq[book.uri] = self.ids_for(book.title, book.author, book.uri)
        return found

    def excluded_ids(self, ids: frozenset[str]) -> str | None:
        for book_id in sorted(ids):
            why = self.listener.excluded(book_id)
            if why is not None:
                return why
        return None

    def excluded(self, book: ShelfBook) -> str | None:
        """Why a shelf book is never offered (any edition of it counts)."""
        return self.excluded_ids(self.equivalents(book))

    def _by(self, author: str, ids: set[str] | frozenset[str]) -> bool:
        names = _author_sets(author)
        return any(a <= b or b <= a for i in ids for a in names for b in self._names.get(i, []))

    def score(self, book: ShelfBook) -> int:
        """Liked author +2, finished author +1, liked category +1,
        disliked author -3."""
        total = 0
        if self._by(book.author, self._liked):
            total += 2
        if self._by(book.author, self.listener.ended):
            total += 1
        if book.category in self._liked_categories:
            total += 1
        if self._by(book.author, self._disliked):
            total -= 3
        return total


def _author_sets(author: str) -> list[frozenset[str]]:
    return [frozenset(n) for n in _author_names(author)]


def equivalents(book: ShelfBook, listener: Listener) -> frozenset[str]:
    return Judge(Shelf((book.category,), (book,)), listener).equivalents(book)


def excluded(book: ShelfBook, listener: Listener) -> str | None:
    """Why a shelf book is never offered (any edition of it counts)."""
    return Judge(Shelf((book.category,), (book,)), listener).excluded(book)


def score(book: ShelfBook, listener: Listener, shelf: Shelf) -> int:
    return Judge(shelf, listener).score(book)


def rank(
    books: list[ShelfBook],
    listener: Listener,
    shelf: Shelf,
    *,
    bonus: dict[str, int] | None = None,
    judge: Judge | None = None,
) -> tuple[list[ShelfBook], int]:
    """(books to offer, best first; how many were excluded). Ties go to
    the book offered least recently, then shelf order."""
    judge = judge or Judge(shelf, listener)
    order = {b.uri: i for i, b in enumerate(shelf.books)}
    kept = [b for b in books if judge.excluded(b) is None]
    extra = bonus or {}
    scores = {b.uri: judge.score(b) + extra.get(b.uri, 0) for b in kept}
    kept.sort(
        key=lambda b: (
            -scores[b.uri],
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


def theme_words(shelf: Shelf) -> frozenset[str]:
    """Every word of the shelf's categories and tags."""
    words: set[str] = set()
    for category in shelf.categories:
        words |= _content(category)
    for book in shelf.books:
        for tag in book.tags:
            words |= _content(tag)
    return frozenset(words)


def only_theme(shelf: Shelf, said: str) -> bool:
    """Every word said (beyond request words) names a kind of book on the
    shelf: "algo de misterio", "novela histórica de amor". A title that
    contains one ("Las aventuras de Tom Sawyer", "Breve historia del
    tiempo") is not a theme (T1.45 review)."""
    words = _content(said)
    return bool(words) and words <= theme_words(shelf)


def is_theme(shelf: Shelf, said: str) -> bool:
    """What was said is a kind of book on the shelf, not a title or an
    author: "pon algo de misterio" (play_book sends it to the shelf)."""
    if match_title(shelf, said) is not None or match_author(shelf, said) is not None:
        return False
    if not only_theme(shelf, said):
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


def rotation_order(shelf: Shelf) -> list[ShelfBook]:
    """The shelf in the order "recomiéndame un libro" walks it: one book of
    each kind in turn (the kinds in the shelf's order, each kind's books in
    theirs), so three in a row are of three kinds and an author rarely
    comes twice running (Mario, 2026-10-14: no choosing a kind first)."""
    queues = {c: [b for b in shelf.books if b.category == c] for c in shelf.categories}
    order: list[ShelfBook] = []
    while any(queues.values()):
        for c in shelf.categories:
            if queues[c]:
                order.append(queues[c].pop(0))
    order.extend(b for b in shelf.books if b.category not in queues)
    return order


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
