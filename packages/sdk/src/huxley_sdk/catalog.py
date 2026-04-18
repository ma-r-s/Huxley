"""Searchable per-skill catalog of personal-content items.

The first SDK primitive aimed at the framework's load-bearing differentiator:
"LLM understands rough natural-language intent and dispatches to user-installable
custom tools, including for personal content."

Personal-content skills (audiobooks, radio, contacts, recipes, ...) repeatedly
need the same shape: index a small set of items by string fields, fuzzy-match
user phrases against them, and inject baseline awareness into the LLM's system
prompt. Without a primitive, every skill reinvents the matching code (with
different bugs each time, e.g. accent-insensitivity).

A Catalog is constructed once per skill via `ctx.catalog()` (see
`SkillContext.catalog`). Skills `upsert` items at `setup()` time, then call
`search()` to resolve user phrases and `as_prompt_lines()` to build the
prompt-context contribution.

See `docs/triage.md` T1.1 for the full design rationale and Gate-2 critic
notes that drove the final API shape.

## Usage

```python
class AudiobooksSkill:
    async def setup(self, ctx: SkillContext) -> None:
        self._catalog = ctx.catalog()
        for book in scan_library():
            await self._catalog.upsert(
                id=book["id"],
                fields={"title": book["title"], "author": book["author"]},
                payload={"path": book["path"], "duration": book["duration"]},
            )

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name == "play_audiobook":
            hits = await self._catalog.search(args["book_id"], limit=1)
            if not hits:
                return ToolResult(output='{"playing": false, "message": "..."}')
            book_path = hits[0].payload["path"]
            ...

    def prompt_context(self) -> str:
        return self._catalog.as_prompt_lines(
            limit=50,
            header="Biblioteca de audiolibros disponibles",
            line=lambda h: f'- "{h.fields["title"]}" por {h.fields["author"]}',
        )
```

## Why async over a synchronous in-memory backend

Today the backend is in-memory dict + per-query SequenceMatcher. The async
methods (`upsert`, `search`) shield the public API from a future swap to
SQLite FTS5 (or any other backend) when a skill genuinely needs persistence
or 10k+-item scale. The swap will be a backend change, not an API change.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


def _fold(text: str) -> str:
    """Normalize for accent-insensitive comparison.

    Lowercase + NFKD-decompose + strip combining marks. Symmetric: applied to
    both stored fields and incoming queries before scoring, so "garcía" and
    "garcia" compare equal. Language-agnostic enough for a future English
    persona without rework.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _score(query: str, candidate: str) -> float:
    """SequenceMatcher ratio between folded query and folded candidate.

    Chosen over token-set Jaccard because:
    1. It preserves the existing audiobooks fuzzy behavior exactly (drop-in
       refactor — see Gate 2 critic finding #1)
    2. It handles typos and missing accents gracefully ("naufrago" still
       matches "náufrago")
    3. It doesn't get dominated by short Spanish stopwords ("el", "la", "de")

    Both args must already be folded (call `_fold` on raw strings).
    """
    if not query or not candidate:
        return 0.0
    return SequenceMatcher(None, query, candidate).ratio()


@dataclass(frozen=True, slots=True)
class Hit:
    """One result from `Catalog.search`.

    `score` is in [0, 1]; higher is better. Hits are returned sorted by
    descending score; ties broken by the order of `upsert`. `fields` is the
    same dict the skill provided; `payload` is the skill's opaque attached
    data (typically file paths, IDs, durations — anything the skill needs
    when acting on a hit).
    """

    id: str
    score: float
    fields: dict[str, str]
    payload: dict[str, Any]


@dataclass
class _Item:
    """Internal storage row. Pre-folded fields cached for fast scoring."""

    id: str
    fields: dict[str, str]
    folded_fields: dict[str, str]
    payload: dict[str, Any]
    insert_order: int


class Catalog:
    """In-memory searchable catalog of skill-defined items.

    Construct via `ctx.catalog()` from the skill's `setup()` method; keep the
    reference on the skill for the rest of its lifetime. Calling
    `ctx.catalog()` multiple times returns independent catalogs (the
    framework does not cache); skills are responsible for holding onto the
    instance they want to use.
    """

    def __init__(self) -> None:
        self._items: dict[str, _Item] = {}
        self._next_order: int = 0

    async def upsert(
        self,
        id: str,
        fields: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Insert or replace an item.

        `fields` are the strings searched and rendered into prompt lines.
        `payload` is opaque to the catalog — the skill stores whatever it
        needs to act on a search hit (file paths, IDs, durations, etc.).
        Re-`upsert`ing an existing id replaces the row in place but keeps
        its original insertion order (so the prompt-line ordering is
        stable across reindex).
        """
        existing = self._items.get(id)
        order = existing.insert_order if existing is not None else self._next_order
        if existing is None:
            self._next_order += 1
        self._items[id] = _Item(
            id=id,
            fields=dict(fields),
            folded_fields={k: _fold(v) for k, v in fields.items()},
            payload=dict(payload) if payload is not None else {},
            insert_order=order,
        )

    async def search(self, query: str, limit: int = 10) -> list[Hit]:
        """Return the top `limit` items matching `query`.

        Empty query returns an empty list. Score per item = max ratio
        across all fields (so a hit that matches one field strongly beats
        one that matches several fields weakly). Sorted by descending
        score; ties broken by insertion order. Hits with score 0 are
        excluded.
        """
        if not query:
            return []
        folded_q = _fold(query)
        scored: list[tuple[float, int, _Item]] = []
        for item in self._items.values():
            best = max(
                (_score(folded_q, folded) for folded in item.folded_fields.values()),
                default=0.0,
            )
            if best > 0:
                # Negate insert_order so ascending sort breaks ties oldest-first.
                scored.append((best, -item.insert_order, item))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [
            Hit(id=it.id, score=score, fields=it.fields, payload=it.payload)
            for score, _, it in scored[:limit]
        ]

    def as_prompt_lines(
        self,
        limit: int = 50,
        header: str | None = None,
        line: Any = None,
        overflow: str | None = None,
    ) -> str:
        """Render the catalog as system-prompt context.

        Output format:

            <header>:
            - <line(item)>
            - <line(item)>
            (and N más, ...)

        - `header`: text before the colon. If `None`, no header line.
        - `line`: callable `(Hit) -> str` formatting one item. Default
          renders the first field's value.
        - `overflow`: text appended when the catalog has more than `limit`
          items. Default mentions the count in Spanish.

        Items rendered in insertion order (NOT search-score order — there's
        no query). Returns an empty string when the catalog is empty.
        """
        if not self._items:
            return ""

        # Render in insertion order so this is stable across runs and
        # consistent with how skills naturally upsert their data.
        ordered = sorted(self._items.values(), key=lambda it: it.insert_order)

        if line is None:

            def _default_line(hit: Hit) -> str:
                # First field value — works for the simple title-only case.
                first_field = next(iter(hit.fields.values()), hit.id)
                return f"- {first_field}"

            line_fn = _default_line
        else:
            line_fn = line

        lines: list[str] = []
        if header:
            lines.append(f"{header}:")
        for item in ordered[:limit]:
            hit = Hit(id=item.id, score=1.0, fields=item.fields, payload=item.payload)
            lines.append(line_fn(hit))
        if len(ordered) > limit:
            extra = len(ordered) - limit
            if overflow is None:
                overflow = f"(y {extra} más, búscalos por título o autor)"
            lines.append(overflow)
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._items)
