"""Tests for the Catalog SDK primitive (T1.1).

Includes the five regression asserts the Gate-2 critic locked into the DoD:

1. Regression parity — Catalog matches the legacy `_resolve_book` behavior on
   real audiobook-style queries.
2. Misspelling tolerance — "naufrago" still finds "Relato de un náufrago".
3. Stopword noise — query "el" against a library of "El X" titles doesn't
   score them all near 1.0.
4. Determinism — repeated runs give identical top-10.
5. Prompt parity — `as_prompt_lines` byte-matches what the audiobooks skill
   was emitting before the refactor (not asserted here directly; the
   audiobooks test suite carries that assertion since it knows the persona's
   line format).
"""

from __future__ import annotations

import pytest

from huxley_sdk import Catalog
from huxley_sdk.catalog import _fold

# ---------------------------------------------------------------------------
# _fold


class TestFold:
    def test_lowercases(self) -> None:
        assert _fold("ABC") == "abc"

    def test_strips_spanish_accents(self) -> None:
        assert _fold("García Márquez") == "garcia marquez"

    def test_idempotent_on_already_folded(self) -> None:
        assert _fold(_fold("García Márquez")) == _fold("García Márquez")

    def test_handles_empty_string(self) -> None:
        assert _fold("") == ""

    def test_preserves_non_letter_characters(self) -> None:
        assert _fold("¿Qué?") == "¿que?"


# ---------------------------------------------------------------------------
# Insert


class TestCatalogInsert:
    async def test_empty_catalog_has_zero_length(self) -> None:
        catalog = Catalog()
        assert len(catalog) == 0

    async def test_upsert_increments_length(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello"})
        assert len(catalog) == 1

    async def test_upsert_dup_id_replaces_in_place(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "First"})
        await catalog.upsert(id="a", fields={"title": "Second"})
        assert len(catalog) == 1
        hits = await catalog.search("Second", limit=1)
        assert hits[0].fields["title"] == "Second"

    async def test_payload_preserved(self) -> None:
        catalog = Catalog()
        await catalog.upsert(
            id="a",
            fields={"title": "Hello"},
            payload={"path": "/foo.mp3", "duration": 42.0},
        )
        hits = await catalog.search("Hello", limit=1)
        assert hits[0].payload == {"path": "/foo.mp3", "duration": 42.0}

    async def test_payload_optional(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello"})
        hits = await catalog.search("Hello", limit=1)
        assert hits[0].payload == {}


# ---------------------------------------------------------------------------
# Search


class TestCatalogSearch:
    async def test_exact_match_top_score(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello world"})
        hits = await catalog.search("Hello world", limit=5)
        assert hits[0].id == "a"
        assert hits[0].score == 1.0

    async def test_accent_folded_match(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"author": "Gabriel García Márquez"})
        hits = await catalog.search("garcia marquez", limit=1)
        assert hits[0].id == "a"
        assert hits[0].score > 0.5

    async def test_multi_field_scoring_takes_max(self) -> None:
        catalog = Catalog()
        await catalog.upsert(
            id="a",
            fields={"title": "Cien años de soledad", "author": "Gabriel García Márquez"},
        )
        # Query matches author much more than title; max-across-fields wins.
        hits = await catalog.search("garcia marquez", limit=1)
        assert hits[0].id == "a"

    async def test_empty_query_returns_empty(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello"})
        assert await catalog.search("", limit=5) == []

    async def test_no_match_returns_empty(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "completely different"})
        # SequenceMatcher returns 0 for no character overlap at all.
        # "xxxxx" vs "completely different" → 0.
        hits = await catalog.search("xxxxx", limit=5)
        assert hits == []

    async def test_limit_truncates(self) -> None:
        catalog = Catalog()
        for i in range(10):
            await catalog.upsert(id=str(i), fields={"title": f"Book {i}"})
        hits = await catalog.search("Book", limit=3)
        assert len(hits) == 3

    async def test_excludes_zero_score_hits(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="match", fields={"title": "Hello world"})
        await catalog.upsert(id="nomatch", fields={"title": "xxxxxx"})
        hits = await catalog.search("Hello", limit=10)
        ids = [h.id for h in hits]
        assert "match" in ids
        assert "nomatch" not in ids


# ---------------------------------------------------------------------------
# Scoring (determinism + ordering)


class TestCatalogScoring:
    async def test_descending_score_order(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="exact", fields={"title": "Hello world"})
        await catalog.upsert(id="partial", fields={"title": "Hello"})
        await catalog.upsert(id="weak", fields={"title": "Helo"})
        hits = await catalog.search("Hello world", limit=10)
        # All three match; exact wins.
        assert hits[0].id == "exact"
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    async def test_ties_broken_by_insertion_order(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="first", fields={"title": "Hello"})
        await catalog.upsert(id="second", fields={"title": "Hello"})
        hits = await catalog.search("Hello", limit=10)
        # Both score 1.0; insertion order wins → "first" before "second".
        assert [h.id for h in hits] == ["first", "second"]

    async def test_determinism_across_runs(self) -> None:
        """Critic Finding #5 (locked DoD test): same fixture + query →
        byte-identical top-10 across many runs. Catches any dict-ordering
        leak in scoring."""
        first_run: list[tuple[str, float]] = []
        for run in range(20):
            catalog = Catalog()
            for i, title in enumerate(
                [
                    "Cien años de soledad",
                    "El amor en los tiempos del cólera",
                    "Crónica de una muerte anunciada",
                    "El otoño del patriarca",
                    "El coronel no tiene quien le escriba",
                    "Relato de un náufrago",
                    "El sabueso de los Baskerville",
                ]
            ):
                await catalog.upsert(id=str(i), fields={"title": title})
            hits = await catalog.search("amor", limit=10)
            run_result = [(h.id, round(h.score, 8)) for h in hits]
            if run == 0:
                first_run = run_result
            else:
                assert run_result == first_run, f"non-deterministic at run {run}"


# ---------------------------------------------------------------------------
# Critic-flagged regression asserts


class TestCriticRegressionAsserts:
    """Locked into DoD by the Gate-2 critic. Each catches a specific class
    of regression that the more-generic tests would miss.
    """

    async def test_regression_parity_audiobook_resolution(self) -> None:
        """Critic Finding #1+5: Catalog must score real audiobook queries
        the same way the legacy `_resolve_book` handled them.

        Two parts pin down the behavioral boundary:

        1. **High-confidence queries** the legacy resolved cleanly
           (`_fuzzy_score > 0.5` threshold). Catalog top-1 must be the
           expected book AND score above 0.5.
        2. **Low-confidence queries** the legacy left as None ("coronel"
           alone is too short to reliably match against long titles, even
           when the right title contains the word). Catalog returns
           SOMETHING but it must score below 0.5 — the audiobooks skill
           applies the same threshold as before and treats sub-threshold
           hits as "no match".

        The primitive returns scored data; the skill owns the confidence
        policy. Mirrors `personas/abuelos/data/audiobooks/`.
        """
        catalog = Catalog()
        library = [
            ("garcia-cien", "Cien años de soledad", "Gabriel García Márquez"),
            (
                "garcia-amor",
                "El amor en los tiempos del cólera",
                "Gabriel García Márquez",
            ),
            ("garcia-cronica", "Crónica de una muerte anunciada", "Gabriel García Márquez"),
            ("garcia-otono", "El otoño del patriarca", "Gabriel García Márquez"),
            (
                "garcia-coronel",
                "El coronel no tiene quien le escriba",
                "Gabriel García Márquez",
            ),
            ("garcia-naufrago", "Relato de un náufrago", "Gabriel García Márquez"),
            ("doyle-sabueso", "El sabueso de los Baskerville", "Arthur Conan Doyle"),
            ("isaacs-maria", "María", "Jorge Isaacs"),
        ]
        for book_id, title, author in library:
            await catalog.upsert(id=book_id, fields={"title": title, "author": author})

        # Queries the legacy resolved cleanly (score > 0.5).
        high_confidence_cases = [
            ("cien años", "garcia-cien", "exact title fragment"),
            ("García Márquez", "garcia-cien", "author w/ accents; first-inserted wins"),
            ("garcia marquez", "garcia-cien", "author folded; first-inserted wins"),
            ("naufrago", "garcia-naufrago", "missing accent on náufrago"),
            ("maria", "isaacs-maria", "short title, no accent"),
            ("María", "isaacs-maria", "short title, with accent"),
            ("baskerville", "doyle-sabueso", "proper-noun match"),
            ("conan doyle", "doyle-sabueso", "author by full name"),
        ]
        failures: list[str] = []
        for query, expected, label in high_confidence_cases:
            hits = await catalog.search(query, limit=1)
            if not hits:
                failures.append(f"  HIGH {query!r} ({label}): no hits at all")
                continue
            top = hits[0]
            if top.id != expected:
                failures.append(f"  HIGH {query!r} ({label}): expected {expected}, got {top.id}")
            elif top.score <= 0.5:
                failures.append(
                    f"  HIGH {query!r} ({label}): score {top.score:.3f} <= 0.5 threshold"
                )

        # Queries the legacy returned None on (sub-threshold). Catalog
        # returns hits but they MUST score under 0.5 so the skill's
        # threshold check correctly rejects them.
        low_confidence_cases = [
            ("coronel", "single short word vs much longer title"),
            ("sabueso", "single short word vs much longer title"),
        ]
        for query, label in low_confidence_cases:
            hits = await catalog.search(query, limit=1)
            if hits and hits[0].score >= 0.5:
                failures.append(
                    f"  LOW  {query!r} ({label}): score {hits[0].score:.3f} >= 0.5; "
                    f"skill's confidence gate would now resolve incorrectly to "
                    f"{hits[0].id}"
                )

        if failures:
            pytest.fail(
                "Catalog regression: legacy resolution behavior diverged.\n" + "\n".join(failures)
            )

    async def test_misspelling_tolerance_naufrago(self) -> None:
        """Critic Finding #2: 'naufrago' (no accent, no g) → 'Relato de un
        náufrago'. Currently works with SequenceMatcher; must keep working."""
        catalog = Catalog()
        await catalog.upsert(id="naufrago", fields={"title": "Relato de un náufrago"})
        await catalog.upsert(id="other", fields={"title": "Cien años de soledad"})
        hits = await catalog.search("naufrago", limit=1)
        assert hits[0].id == "naufrago"

    async def test_stopword_noise_does_not_dominate(self) -> None:
        """Critic Finding #3: query 'el' against a library of 'El X' titles
        must not give all of them near-perfect scores. SequenceMatcher
        handles this naturally because long candidates get low ratios for
        short queries."""
        catalog = Catalog()
        for i, title in enumerate(
            [
                "El sabueso de los Baskerville",
                "El amor en los tiempos del cólera",
                "El coronel no tiene quien le escriba",
                "El otoño del patriarca",
                "El extranjero",
            ]
        ):
            await catalog.upsert(id=str(i), fields={"title": title})

        hits = await catalog.search("el", limit=10)
        # All hits should score below 0.4 — "el" is 2 chars vs ~30 char
        # titles, so SequenceMatcher ratio is naturally low.
        for h in hits:
            assert h.score < 0.4, (
                f"stopword 'el' scored too high on {h.fields['title']!r}: {h.score}"
            )


# ---------------------------------------------------------------------------
# as_prompt_lines


class TestAsPromptLines:
    def test_empty_catalog_returns_empty_string(self) -> None:
        catalog = Catalog()
        assert catalog.as_prompt_lines() == ""

    async def test_renders_with_default_formatter(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello"})
        await catalog.upsert(id="b", fields={"title": "World"})
        out = catalog.as_prompt_lines()
        assert out == "- Hello\n- World"

    async def test_renders_with_header(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Hello"})
        out = catalog.as_prompt_lines(header="Library")
        assert out == "Library:\n- Hello"

    async def test_renders_with_custom_line_formatter(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="a", fields={"title": "Cien años", "author": "García"})
        out = catalog.as_prompt_lines(
            line=lambda h: f'- "{h.fields["title"]}" por {h.fields["author"]}'
        )
        assert out == '- "Cien años" por García'

    async def test_renders_in_insertion_order(self) -> None:
        catalog = Catalog()
        await catalog.upsert(id="b", fields={"title": "Second"})
        await catalog.upsert(id="a", fields={"title": "First"})
        out = catalog.as_prompt_lines()
        assert out == "- Second\n- First"

    async def test_limit_truncates_with_overflow_message(self) -> None:
        catalog = Catalog()
        for i in range(5):
            await catalog.upsert(id=str(i), fields={"title": f"Book {i}"})
        out = catalog.as_prompt_lines(limit=2)
        assert "- Book 0" in out
        assert "- Book 1" in out
        assert "- Book 2" not in out
        assert "3 más" in out  # default Spanish overflow message

    async def test_custom_overflow_message(self) -> None:
        catalog = Catalog()
        for i in range(5):
            await catalog.upsert(id=str(i), fields={"title": f"Book {i}"})
        out = catalog.as_prompt_lines(limit=2, overflow="(... and 3 more)")
        assert "(... and 3 more)" in out

    async def test_no_overflow_message_when_within_limit(self) -> None:
        catalog = Catalog()
        for i in range(2):
            await catalog.upsert(id=str(i), fields={"title": f"Book {i}"})
        out = catalog.as_prompt_lines(limit=10)
        assert "más" not in out
