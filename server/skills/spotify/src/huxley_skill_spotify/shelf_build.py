"""huxley-spotify-shelf: check the curated shelf against Spotify and build
the JSON the skill reads (T1.45).

    huxley-spotify-shelf <persona_dir> [--write] [--only TITLE]

Reads `<shelf>.yaml` (the curated books, edited by hand) next to the
persona's `book_shelf` path (default `books/shelf.json` in the persona
dir), the market from persona.yaml, and the Web API app from
`data/secrets/spotify/values.json`. For each book: search "title author"
as an audiobook, then the title alone if that finds nothing; keep an
edition only if its title is close (0.75, subtitle and edition note
dropped), an author's surname matches, it is in the persona's language,
it is not a reading guide or an abridgement, and Spotify lists its
chapters (one `/audiobooks` request per edition, none for an edition the
previous shelf.json already has for that book). A pinned `uri` wins,
then the preferred `narrator`, then the title score, then Spotify's
order; the other editions are printed.

One line per book: OK, CHECK (kept, look at the reason) or REJECT (left
out, with the reason). Exits 1 on any REJECT, 2 when Spotify can't be
reached or the persona isn't set up. `--write` replaces the JSON
atomically with the books that passed; `--only` checks the matching
titles and keeps the rest of the JSON. A network error mid-run (Spotify's
rate limit) writes what was checked and keeps the rest as the previous
JSON had it, so running again continues cheaply.

Spotify blocked this app's `/audiobooks` for about 19 hours after four
full runs in three hours (2026-10-13): fix one book with `--only`.

Playability can't be checked here: the Web API answers the same for a
book the plan includes and one that must be bought. The skill checks
each book once before offering it and remembers the answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from huxley_skill_spotify.books import fold, is_guide
from huxley_skill_spotify.search import Match, SearchError, SpotifySearch
from huxley_skill_spotify.shelf import SHELF_VERSION, closeness, parse_shelf

DEFAULT_SHELF = "../books/shelf.json"  # relative to the persona's data dir
TITLE_MIN = 0.75
SEARCH_LIMIT = 10  # a development-mode app's maximum
# Spotify's own pace for an app token is unpublished; a pause between
# books and a capped Retry-After keep a full run of ~130 books under it.
PAUSE_S = 0.2
RETRY_AFTER_MAX_S = 120.0
RETRIES = 3
# Languages Spotify may report for the persona's language.
_LANGUAGE_CODES = {"es": ("es", "spa"), "en": ("en", "eng"), "fr": ("fr", "fra", "fre")}


class SetupError(Exception):
    """The persona isn't set up for a build (no market, no credentials)."""


@dataclass(frozen=True)
class Entry:
    """One book of shelf.yaml."""

    title: str
    author: str
    category: str
    pitch: str
    tags: tuple[str, ...] = ()
    uri: str | None = None
    narrator: str | None = None


@dataclass
class Verdict:
    entry: Entry
    status: str  # OK, CHECK, REJECT
    reason: str = ""
    chosen: Match | None = None
    alternatives: list[Match] = field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    shelf_json: Path
    shelf_yaml: Path
    market: str
    language: str
    client_id: str
    client_secret: str


def load_plan(persona_dir: Path) -> Plan:
    persona = yaml.safe_load((persona_dir / "persona.yaml").read_text(encoding="utf-8"))
    spotify = ((persona or {}).get("skills") or {}).get("spotify") or {}
    market = spotify.get("market")
    if not market:
        raise SetupError("persona.yaml has no skills.spotify.market: results wouldn't be playable")
    raw = Path(str(spotify.get("book_shelf") or DEFAULT_SHELF))
    target = raw if raw.is_absolute() else Path(os.path.normpath(persona_dir / "data" / raw))
    secrets = persona_dir / "data" / "secrets" / "spotify" / "values.json"
    try:
        values = json.loads(secrets.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"can't read {secrets}: {type(exc).__name__}") from exc
    if not values.get("client_id") or not values.get("client_secret"):
        raise SetupError(f"{secrets} has no client_id / client_secret")
    return Plan(
        shelf_json=target,
        shelf_yaml=target.with_suffix(".yaml"),
        market=str(market),
        language=str(persona.get("language_code") or "es").lower()[:2],
        client_id=str(values["client_id"]),
        client_secret=str(values["client_secret"]),
    )


def load_entries(data: Any) -> tuple[list[str], list[Entry | str]]:
    """(categories, entries); an entry that can't be used is its title
    (or its position) as a string, reported as a REJECT."""
    if not isinstance(data, dict):
        raise SetupError("shelf.yaml is not a mapping")
    categories = [str(c).strip() for c in data.get("categories") or [] if str(c).strip()]
    if not categories:
        raise SetupError("shelf.yaml has no categories")
    entries: list[Entry | str] = []
    for i, raw in enumerate(data.get("books") or []):
        if not isinstance(raw, dict):
            entries.append(f"entry {i + 1}")
            continue
        text = {k: str(raw.get(k) or "").strip() for k in ("title", "author", "category", "pitch")}
        if not all(text.values()) or text["category"] not in categories:
            entries.append(text["title"] or f"entry {i + 1}")
            continue
        entries.append(
            Entry(
                title=text["title"],
                author=text["author"],
                category=text["category"],
                pitch=text["pitch"],
                tags=tuple(str(t).strip() for t in raw.get("tags") or [] if str(t).strip()),
                uri=str(raw.get("uri") or "").strip() or None,
                narrator=str(raw.get("narrator") or "").strip() or None,
            )
        )
    return categories, entries


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", fold(text)) if w}


def surnames(author: str) -> set[str]:
    """Each author's words after the first name ("García", "Márquez");
    a one-word name is its own surname."""
    found: set[str] = set()
    for name in fold(author).split(","):
        words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
        found.update(w for w in (words[1:] or words) if len(w) > 2)
    return found


def title_closeness(wanted: str, title: str) -> float:
    """The best of the whole title and what's before "(", ":" or ". "
    ("Sapiens. De animales a dioses (Latino): Una breve historia...")."""
    parts = {title, title.split("(")[0], title.split(":")[0], title.split(". ")[0]}
    return max(closeness(wanted, p) for p in parts if p.strip())


def problem(entry: Entry, match: Match, language: str) -> str | None:
    """Why an edition isn't this book, or None. Chapters are checked apart."""
    closeness = title_closeness(entry.title, match.title)
    if closeness < TITLE_MIN:
        return f"title too far ({closeness:.2f}: {match.title!r})"
    if not surnames(entry.author) & _words(match.artist or ""):
        return f"author doesn't match ({match.artist!r})"
    codes = _LANGUAGE_CODES.get(language, (language,))
    if not any(lang.lower().startswith(codes) for lang in match.languages):
        return f"not in {language} ({', '.join(match.languages) or 'no language'})"
    if is_guide(match.title, edition=match.edition):
        return f"a guide or abridged ({match.title!r}, {match.edition or 'no edition note'})"
    if is_guide("", match.about):
        return "the synopsis reads as a guide or abridged"
    return None


def _narrated_by(match: Match, narrator: str | None) -> bool:
    if not narrator:
        return False
    wanted = fold(narrator)
    return any(wanted in fold(n) or fold(n) in wanted for n in match.narrators)


async def verify(
    search: SpotifySearch, entry: Entry, language: str, verified: frozenset[str] = frozenset()
) -> Verdict:
    """Find and check this book's edition. Raises SearchError when
    Spotify can't be reached (a 4xx on a pinned lookup is a REJECT).
    `verified`: editions an earlier build already found with chapters;
    they aren't looked up again (searches are cheap, `/audiobooks` is
    where Spotify blocked the app for 19 hours, 2026-10-13)."""
    chapters: dict[str, int] = {uri: 1 for uri in verified}
    results: list[Match] = []
    kept: list[Match] = []
    first_problem = "not found"
    for query in (f"{entry.title} {entry.author}", entry.title):
        results = await search.audiobooks(query, limit=SEARCH_LIMIT)
        for match in results:
            why = problem(entry, match, language)
            if why is None:
                kept.append(match)
            elif first_problem == "not found":
                first_problem = why
        if kept:
            break
    note = ""
    pinned: Match | None = None
    if entry.uri:
        pinned = next((m for m in results if m.uri == entry.uri), None)
        if pinned is None:
            try:
                pinned, chapters[entry.uri] = await search.audiobook(entry.uri)
            except SearchError as exc:
                if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                    return Verdict(entry, "REJECT", f"pinned uri not on Spotify ({exc})")
                raise
        why = problem(entry, pinned, language)
        if why == "the synopsis reads as a guide or abridged":
            note = why + " (kept: the uri is pinned)"
        elif why is not None:
            return Verdict(entry, "REJECT", f"pinned uri: {why}")
        kept = [pinned, *(m for m in kept if m.uri != pinned.uri)]
    if not kept:
        return Verdict(entry, "REJECT", first_problem)
    order = {m.uri: i for i, m in enumerate(kept)}
    ranked = sorted(
        kept,
        key=lambda m: (
            not (pinned is not None and m.uri == pinned.uri),
            not _narrated_by(m, entry.narrator),
            -title_closeness(entry.title, m.title),
            order[m.uri],
        ),
    )
    # The first edition that lists chapters (one without can't be played
    # by chapter: El juego del angel's first edition, 2026-10-13). A pinned
    # one is the only choice: a human picked it.
    chosen: Match | None = None
    empty: list[str] = []
    for match in ranked[:1] if pinned is not None else ranked:
        if match.uri not in chapters:
            try:
                _, chapters[match.uri] = await search.audiobook(match.uri)
            except SearchError as exc:
                if exc.status == 429 or (exc.status or 0) >= 500 or _unreachable(exc):
                    raise
                chapters[match.uri] = 0  # this edition answers 4xx: try the next one
        if chapters[match.uri]:
            chosen = match
            break
        empty.append(match.uri)
    if chosen is None:
        return Verdict(entry, "REJECT", f"no chapters listed for {', '.join(empty)}")
    alternatives = [m for m in kept if m.uri != chosen.uri and m.uri not in empty]
    if not entry.uri:
        note = note or "no pinned uri: chose one, pin it after a listen"
    elif entry.narrator and not _narrated_by(chosen, entry.narrator):
        note = (
            note or f"narrator is {', '.join(chosen.narrators) or 'unknown'}, not {entry.narrator}"
        )
    return Verdict(entry, "CHECK" if note else "OK", note, chosen, alternatives)


def _unreachable(exc: SearchError) -> bool:
    """No answer from Spotify at all (as opposed to an answer about this
    edition): a request that failed, or a token that didn't come."""
    return exc.status is None and not str(exc).startswith("no audiobook")


def shelf_entry(verdict: Verdict, language: str, today: str) -> dict[str, Any]:
    entry, chosen = verdict.entry, verdict.chosen
    assert chosen is not None
    return {
        "title": entry.title,
        "author": entry.author,
        "narrator": ", ".join(chosen.narrators) or entry.narrator,
        "uri": chosen.uri,
        "language": language,
        "category": entry.category,
        "tags": list(entry.tags),
        "pitch": entry.pitch,
        "verified": today,
    }


def _line(verdict: Verdict) -> str:
    entry = verdict.entry
    name = f"{entry.title} / {entry.author}"
    if verdict.status == "REJECT":
        return f"REJECT  {name}: {verdict.reason}"
    chosen = verdict.chosen
    assert chosen is not None
    who = ", ".join(chosen.narrators) or "?"
    text = f"{verdict.status:<7} {name} -> {chosen.uri} ({who})"
    if verdict.reason:
        text += f": {verdict.reason}"
    for alt in verdict.alternatives:
        edition = f" [{alt.edition}]" if alt.edition else ""
        text += (
            f"\n        also: {alt.title} / {', '.join(alt.narrators) or '?'}{edition} {alt.uri}"
        )
    return text


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write("\n")
        Path(tmp).chmod(0o644)  # a repo file, not a secret (mkstemp makes it 0600)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def merged_books(
    entries: list[Entry | str],
    verdicts: dict[Entry, Verdict],
    previous: list[dict[str, Any]],
    language: str,
    today: str,
) -> list[dict[str, Any]]:
    """The JSON's books in shelf.yaml's order: what this run checked (the
    ones that passed), and for the rest (`--only`) what the JSON had."""
    before = {(fold(str(b.get("title"))), fold(str(b.get("author")))): b for b in previous}
    books: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, str):
            continue
        verdict = verdicts.get(entry)
        if verdict is None:
            kept = before.get((fold(entry.title), fold(entry.author)))
            if kept is not None:
                # Not checked this run: the edition as before, everything
                # else from shelf.yaml (a new pitch, tags or category is
                # never lost; T1.45 review).
                books.append(
                    {
                        "title": entry.title,
                        "author": entry.author,
                        "narrator": kept.get("narrator") or entry.narrator,
                        "uri": kept.get("uri"),
                        "language": kept.get("language") or language,
                        "category": entry.category,
                        "tags": list(entry.tags),
                        "pitch": entry.pitch,
                        "verified": kept.get("verified"),
                    }
                )
        elif verdict.status != "REJECT":
            books.append(shelf_entry(verdict, language, today))
    return books


def _write_shelf(plan: Plan, categories: list[str], books: list[dict[str, Any]]) -> None:
    shelf = {
        "version": SHELF_VERSION,
        "built": datetime.now(UTC).date().isoformat(),
        "market": plan.market,
        "categories": categories,
        "books": books,
    }
    parse_shelf(shelf)  # what the skill would refuse is never written
    write_atomic(plan.shelf_json, shelf)


async def build(
    plan: Plan,
    *,
    write: bool,
    only: str | None = None,
    search: SpotifySearch | None = None,
    out: Any = None,
) -> int:
    out = out or sys.stdout
    data = yaml.safe_load(plan.shelf_yaml.read_text(encoding="utf-8"))
    categories, entries = load_entries(data)
    picked = [
        e for e in entries if isinstance(e, Entry) and (not only or fold(only) in fold(e.title))
    ]
    broken = [e for e in entries if isinstance(e, str) and not only]
    own = search is None
    search = search or SpotifySearch(
        plan.client_id,
        plan.client_secret,
        market=plan.market,
        retry_after_max_s=RETRY_AFTER_MAX_S,
        retries=RETRIES,
    )
    previous: list[dict[str, Any]] = []
    if plan.shelf_json.exists():
        try:
            previous = list(json.loads(plan.shelf_json.read_text(encoding="utf-8"))["books"])
        except (OSError, ValueError, KeyError, TypeError):
            print("the previous shelf.json can't be read: checking every edition", file=out)
    known = {
        (fold(str(b.get("title"))), fold(str(b.get("author")))): b.get("uri") for b in previous
    }
    verdicts: dict[Entry, Verdict] = {}
    stopped: SearchError | None = None
    try:
        for entry in picked:
            uri = known.get((fold(entry.title), fold(entry.author)))
            verdict = await verify(
                search,
                entry,
                plan.language,
                frozenset({uri}) if isinstance(uri, str) else frozenset(),
            )
            verdicts[entry] = verdict
            print(_line(verdict), file=out, flush=True)
            await asyncio.sleep(PAUSE_S)
    except SearchError as exc:
        stopped = exc
    finally:
        if own:
            await search.aclose()
    if stopped is not None:
        # What was checked is kept (merged with the previous shelf for the
        # rest), so a run that hits Spotify's limit isn't thrown away.
        left = len(picked) - len(verdicts)
        print(
            f"network error after {len(verdicts)} books, {left} not checked: {stopped}", file=out
        )
        books = merged_books(
            entries, verdicts, previous, plan.language, datetime.now(UTC).date().isoformat()
        )
        if write and verdicts and books:
            _write_shelf(plan, categories, books)
            print(
                f"wrote {len(books)} books to {plan.shelf_json} (the unchecked ones as before)",
                file=out,
            )
        else:
            print("nothing written", file=out)
        return 2
    for name in broken:
        print(f"REJECT  {name}: incomplete entry or unknown category", file=out)
    counts = {s: sum(v.status == s for v in verdicts.values()) for s in ("OK", "CHECK", "REJECT")}
    counts["REJECT"] += len(broken)
    print(
        f"{len(picked) + len(broken)} books: {counts['OK']} OK, {counts['CHECK']} CHECK, "
        f"{counts['REJECT']} REJECT",
        file=out,
    )
    if write:
        today = datetime.now(UTC).date().isoformat()
        books = merged_books(entries, verdicts, previous if only else [], plan.language, today)
        if not books:
            print("no book passed: nothing written", file=out)
            return 1
        _write_shelf(plan, categories, books)
        print(f"wrote {len(books)} books to {plan.shelf_json}", file=out)
    else:
        print("dry run: nothing written (add --write)", file=out)
    return 1 if counts["REJECT"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="huxley-spotify-shelf",
        description="Check the persona's audiobook shelf against Spotify and build shelf.json.",
    )
    parser.add_argument("persona_dir", type=Path)
    parser.add_argument("--write", action="store_true", help="write shelf.json")
    parser.add_argument("--only", metavar="TITLE", help="check only titles containing TITLE")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.persona_dir)
        return asyncio.run(build(plan, write=args.write, only=args.only))
    except (SetupError, OSError, yaml.YAMLError) as exc:
        print(f"can't build: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
