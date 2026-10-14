"""His books, for the page (T1.45): the curated shelf the Spotify skill
recommends from (`<persona>/books/shelf.json`, written by its build
script) and the skill's per-book summaries in the persona DB
(`spotify:book:summary:<id>`, docs/skills/spotify.md). Read only; a
missing or damaged file or value is skipped, never a raise."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

SUMMARY_PREFIX = "spotify:book:summary:"
SHELF_FILE = "books/shelf.json"


@dataclass(frozen=True)
class Shelf:
    categories: tuple[str, ...] = ()
    books: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def read_shelf(path: Path) -> Shelf | None:
    """The verified shelf, or None (no file yet, or one that can't be
    read: the page then says so)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    books = tuple(
        b
        for b in data.get("books") or []
        if isinstance(b, dict)
        and isinstance(b.get("title"), str)
        and isinstance(b.get("uri"), str)
    )
    named = [str(c) for c in data.get("categories") or [] if isinstance(c, str)]
    # A book's category the list forgot still shows, after the named ones.
    extra = sorted({str(b.get("category")) for b in books if b.get("category")} - set(named))
    return Shelf(tuple(named + extra), books)


def parse_summary(value: object) -> dict[str, Any] | None:
    """A summary value from the settings table: JSON text (possibly JSON
    encoded twice, if the storage wrapped a string). None if it isn't a
    summary with a uri and a title."""
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("uri"), str) or not isinstance(value.get("title"), str):
        return None
    return value
