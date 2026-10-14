"""huxley-spotify-shelf (T1.45): which editions pass, which are left out,
and what gets written."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from huxley_skill_spotify import shelf_build
from huxley_skill_spotify.search import Match, SearchError
from huxley_skill_spotify.shelf import ShelfFile, parse_shelf
from huxley_skill_spotify.shelf_build import Plan, SetupError, build, load_plan

if TYPE_CHECKING:
    from pathlib import Path


def _book(
    uri: str,
    title: str,
    author: str = "Albert Camus",
    *,
    languages: tuple[str, ...] = ("es",),
    narrators: tuple[str, ...] = ("Lector Uno",),
    about: str = "Una novela.",
    edition: str = "Unabridged",
) -> Match:
    return Match(
        uri=f"spotify:show:{uri}",
        kind="audiobook",
        title=title,
        artist=author,
        languages=languages,
        about=about,
        narrators=narrators,
        edition=edition,
    )


class FakeSearch:
    """`results[query]` is what a search answers; `pinned` what a lookup
    by URI answers; every book has one chapter unless `empty` lists it."""

    def __init__(
        self,
        results: dict[str, list[Match]],
        pinned: dict[str, Match] | None = None,
        *,
        fail: bool = False,
        empty: set[str] | None = None,
    ) -> None:
        self.results = results
        self.pinned = pinned or {}
        self.fail = fail
        self.empty = empty or set()
        self.queries: list[str] = []
        self.looked_up: list[str] = []
        self.fail_lookups = False

    async def audiobooks(self, query: str, *, limit: int = 10) -> list[Match]:
        self.queries.append(query)
        if self.fail:
            raise SearchError("request failed: ConnectError")
        return self.results.get(query, [])

    async def audiobook(self, uri: str) -> tuple[Match, int]:
        self.looked_up.append(uri)
        if self.fail_lookups:
            raise SearchError(f"{uri} answered 429", status=429)
        found = self.pinned.get(uri) or next(
            (m for ms in self.results.values() for m in ms if m.uri == uri), None
        )
        if found is None:
            raise SearchError(f"{uri} answered 404", status=404)
        return found, 0 if uri in self.empty else 12

    async def aclose(self) -> None:
        return None


def _entry(title: str, author: str = "Albert Camus", **extra: Any) -> dict[str, Any]:
    return {
        "title": title,
        "author": author,
        "category": "clásicos universales",
        "tags": ["filosofía"],
        "pitch": f"De qué trata {title}.",
        **extra,
    }


def _plan(tmp_path: Path, entries: list[dict[str, Any]]) -> Plan:
    books = tmp_path / "books"
    books.mkdir(exist_ok=True)
    source = books / "shelf.yaml"
    source.write_text(
        yaml.safe_dump(
            {"categories": ["clásicos universales", "cuentos"], "books": entries},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return Plan(
        shelf_json=books / "shelf.json",
        shelf_yaml=source,
        market="CO",
        language="es",
        client_id="id",
        client_secret="secret",
    )


async def _run(plan: Plan, search: FakeSearch, **kwargs: Any) -> tuple[int, str]:
    out = io.StringIO()
    code = await build(plan, search=search, out=out, write=kwargs.pop("write", True), **kwargs)  # type: ignore[arg-type]
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shelf_build, "PAUSE_S", 0.0)


class TestWhatIsLeftOut:
    @pytest.mark.parametrize(
        ("found", "reason"),
        [
            (_book("fiche", "L'Étranger (Fiche de lecture)"), "a guide or abridged"),
            (_book("lektur", "El extranjero (Lektürehilfe)"), "a guide or abridged"),
            (_book("abrev", "El extranjero (Edición abreviada)"), "a guide or abridged"),
            (_book("abridged", "El extranjero: abridged"), "a guide or abridged"),
            # Spotify's own edition note (14 such editions met in the real build).
            (_book("nota", "El extranjero", edition="Abridged edition"), "a guide or abridged"),
            (_book("otro", "El extranjero", author="Otra Persona"), "author doesn't match"),
            (_book("en", "El extranjero", languages=("en",)), "not in es"),
            (
                _book("guia", "El extranjero", about="Guía de lectura y resumen y análisis."),
                "synopsis reads as a guide",
            ),
            (_book("lejos", "La peste"), "title too far"),
        ],
    )
    async def test_an_edition_that_isnt_the_book(
        self, tmp_path: Path, found: Match, reason: str
    ) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero")])
        search = FakeSearch({"El extranjero Albert Camus": [found], "El extranjero": [found]})
        code, said = await _run(plan, search)
        assert code == 1
        assert said.startswith("REJECT  El extranjero / Albert Camus:") and reason in said
        assert search.queries == ["El extranjero Albert Camus", "El extranjero"]
        # The JSON keeps only what passed: here, nothing could be written.
        assert not plan.shelf_json.exists()

    async def test_no_chapters_is_left_out(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero")])
        found = _book("vacio", "El extranjero")
        search = FakeSearch({"El extranjero Albert Camus": [found]}, empty={found.uri})
        code, said = await _run(plan, search, write=False)
        assert code == 1 and "no chapters" in said


class TestChoosingTheEdition:
    async def test_an_edition_without_chapters_gives_way_to_the_next(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero")])
        first, second = _book("vacio", "El extranjero"), _book("lleno", "El extranjero")
        search = FakeSearch({"El extranjero Albert Camus": [first, second]}, empty={first.uri})
        code, said = await _run(plan, search)
        assert code == 0 and "-> spotify:show:lleno" in said.splitlines()[0]
        assert "spotify:show:vacio" not in said  # not offered as an alternative either

    async def test_a_pinned_edition_without_chapters_is_rejected(self, tmp_path: Path) -> None:
        pinned = _book("vacio", "El extranjero")
        plan = _plan(tmp_path, [_entry("El extranjero", uri=pinned.uri)])
        search = FakeSearch(
            {"El extranjero Albert Camus": [pinned, _book("lleno", "El extranjero")]},
            empty={pinned.uri},
        )
        code, said = await _run(plan, search, write=False)
        assert code == 1 and "no chapters listed for spotify:show:vacio" in said

    async def test_the_preferred_narrator_wins_and_the_others_are_listed(
        self, tmp_path: Path
    ) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero", narrator="Lectora Dos")])
        editions = [
            _book("uno", "El extranjero", narrators=("Lector Uno",)),
            _book("dos", "El extranjero (Latino)", narrators=("Lectora Dos",)),
            _book("guia", "El extranjero (Guía de lectura)"),
        ]
        code, said = await _run(plan, FakeSearch({"El extranjero Albert Camus": editions}))
        assert code == 0
        first, *rest = said.splitlines()
        assert first == (
            "CHECK   El extranjero / Albert Camus -> spotify:show:dos (Lectora Dos): "
            "no pinned uri: chose one, pin it after a listen"
        )
        assert rest[0].strip() == (
            "also: El extranjero / Lector Uno [Unabridged] spotify:show:uno"
        )
        built = json.loads(plan.shelf_json.read_text(encoding="utf-8"))
        assert built["books"][0]["uri"] == "spotify:show:dos"
        assert built["books"][0]["narrator"] == "Lectora Dos"

    async def test_a_pinned_uri_wins_even_when_search_ranks_another_first(
        self, tmp_path: Path
    ) -> None:
        pinned = "spotify:show:dos"
        plan = _plan(tmp_path, [_entry("El extranjero", uri=pinned, narrator="Lectora Dos")])
        editions = [
            _book("uno", "El extranjero", narrators=("Lector Uno",)),
            _book("dos", "El extranjero", narrators=("Lectora Dos",)),
        ]
        code, said = await _run(plan, FakeSearch({"El extranjero Albert Camus": editions}))
        assert code == 0 and said.startswith("OK      El extranjero / Albert Camus -> " + pinned)

    async def test_a_pinned_uri_search_didnt_return_is_looked_up(self, tmp_path: Path) -> None:
        pinned = _book("fijo", "El extranjero", narrators=("Lectora Dos",))
        plan = _plan(tmp_path, [_entry("El extranjero", uri=pinned.uri, narrator="Lectora Dos")])
        search = FakeSearch(
            {"El extranjero Albert Camus": [_book("uno", "El extranjero")]},
            pinned={pinned.uri: pinned},
        )
        code, said = await _run(plan, search)
        assert code == 0 and pinned.uri in said.splitlines()[0]

    async def test_a_pinned_uri_that_fails_is_rejected_not_replaced(self, tmp_path: Path) -> None:
        pinned = _book("ingles", "El extranjero", languages=("en",))
        plan = _plan(tmp_path, [_entry("El extranjero", uri=pinned.uri)])
        search = FakeSearch(
            {"El extranjero Albert Camus": [_book("uno", "El extranjero")]},
            pinned={pinned.uri: pinned},
        )
        code, said = await _run(plan, search)
        assert code == 1 and "pinned uri: not in es" in said

    async def test_a_pinned_uri_gone_from_spotify_is_rejected(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero", uri="spotify:show:gone")])
        code, said = await _run(plan, FakeSearch({}))
        assert code == 1 and "pinned uri not on Spotify" in said

    @pytest.mark.parametrize(
        ("wanted", "author", "spotify"),
        [
            ("1984", "George Orwell", "1984"),  # no letters at all
            (
                "Sapiens",
                "Yuval Noah Harari",
                "Sapiens. De animales a dioses (Latino): Una breve historia de la humanidad",
            ),
        ],
    )
    async def test_titles_seen_in_the_real_build(
        self, tmp_path: Path, wanted: str, author: str, spotify: str
    ) -> None:
        plan = _plan(tmp_path, [_entry(wanted, author=author)])
        found = _book("x", spotify, author=author)
        code, said = await _run(plan, FakeSearch({f"{wanted} {author}": [found]}), write=False)
        assert code == 0, said


class TestWriting:
    async def test_a_network_error_writes_nothing(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero")])
        plan.shelf_json.write_text('{"keep": "me"}', encoding="utf-8")
        code, said = await _run(plan, FakeSearch({}, fail=True))
        assert code == 2 and "network error after 0 books" in said and "nothing written" in said
        assert plan.shelf_json.read_text(encoding="utf-8") == '{"keep": "me"}'

    async def test_rejects_are_left_out_and_the_rest_is_written(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero"), _entry("La peste")])
        search = FakeSearch({"El extranjero Albert Camus": [_book("ext", "El extranjero")]})
        code, said = await _run(plan, search)
        assert code == 1
        assert "2 books: 0 OK, 1 CHECK, 1 REJECT" in said  # no pinned uri: CHECK
        built = json.loads(plan.shelf_json.read_text(encoding="utf-8"))
        assert [b["title"] for b in built["books"]] == ["El extranjero"]

    async def test_the_output_loads_in_the_skill(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero", uri="spotify:show:ext")])
        search = FakeSearch({"El extranjero Albert Camus": [_book("ext", "El extranjero")]})
        assert (await _run(plan, search))[0] == 0
        watcher = ShelfFile(plan.shelf_json)
        change = watcher.refresh()
        assert change is not None and change.event == "loaded"
        assert watcher.shelf is not None
        (book,) = watcher.shelf.books
        assert book.uri == "spotify:show:ext" and book.pitch == "De qué trata El extranjero."
        assert book.verified and book.language == "es"
        assert watcher.shelf.market == "CO"
        assert oct(plan.shelf_json.stat().st_mode & 0o777) == "0o644"

    async def test_only_checks_one_book_and_keeps_the_others(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero"), _entry("La peste")])
        both = FakeSearch(
            {
                "El extranjero Albert Camus": [_book("ext", "El extranjero")],
                "La peste Albert Camus": [_book("peste", "La peste")],
            }
        )
        assert (await _run(plan, both))[0] == 0
        newer = FakeSearch({"La peste Albert Camus": [_book("peste2", "La peste")]})
        code, _ = await _run(plan, newer, only="peste")
        assert code == 0 and newer.queries == ["La peste Albert Camus"]
        built = parse_shelf(json.loads(plan.shelf_json.read_text(encoding="utf-8")))
        assert [b.uri for b in built.books] == ["spotify:show:ext", "spotify:show:peste2"]

    async def test_one_lookup_per_edition_and_none_for_one_already_verified(
        self, tmp_path: Path
    ) -> None:
        """Spotify blocked `/audiobooks` for 19 h after repeated full runs
        (2026-10-13): a rerun only searches for what it already verified."""
        plan = _plan(tmp_path, [_entry("El extranjero"), _entry("La peste")])
        search = FakeSearch(
            {
                "El extranjero Albert Camus": [_book("ext", "El extranjero")],
                "La peste Albert Camus": [_book("peste", "La peste")],
            }
        )
        assert (await _run(plan, search))[0] == 0
        assert search.looked_up == ["spotify:show:ext", "spotify:show:peste"]
        again = FakeSearch(search.results)
        assert (await _run(plan, again))[0] == 0
        assert again.looked_up == []  # both were in the previous shelf.json

    async def test_a_rate_limit_mid_run_keeps_what_was_checked(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero"), _entry("La peste")])
        search = FakeSearch(
            {
                "El extranjero Albert Camus": [_book("ext", "El extranjero")],
                "La peste Albert Camus": [_book("peste", "La peste")],
            }
        )
        real = search.audiobook

        async def limited(uri: str) -> tuple[Match, int]:
            if uri.endswith("peste"):
                raise SearchError(f"{uri} answered 429", status=429)
            return await real(uri)

        search.audiobook = limited  # type: ignore[method-assign]
        code, said = await _run(plan, search)
        assert code == 2 and "network error after 1 books, 1 not checked" in said
        built = parse_shelf(json.loads(plan.shelf_json.read_text(encoding="utf-8")))
        assert [b.uri for b in built.books] == ["spotify:show:ext"]

    async def test_a_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, [_entry("El extranjero")])
        search = FakeSearch({"El extranjero Albert Camus": [_book("ext", "El extranjero")]})
        code, said = await _run(plan, search, write=False)
        assert code == 0 and "dry run" in said and not plan.shelf_json.exists()


class TestSetup:
    def _persona(self, tmp_path: Path, spotify: dict[str, Any], secrets: bool = True) -> Path:
        (tmp_path / "persona.yaml").write_text(
            yaml.safe_dump({"language_code": "es", "skills": {"spotify": spotify}}),
            encoding="utf-8",
        )
        if secrets:
            folder = tmp_path / "data" / "secrets" / "spotify"
            folder.mkdir(parents=True)
            (folder / "values.json").write_text(
                json.dumps({"client_id": "i", "client_secret": "s"}), encoding="utf-8"
            )
        return tmp_path

    def test_market_and_paths_come_from_the_persona(self, tmp_path: Path) -> None:
        plan = load_plan(self._persona(tmp_path, {"market": "CO"}))
        assert plan.market == "CO" and plan.language == "es"
        assert plan.shelf_json == tmp_path / "books" / "shelf.json"
        assert plan.shelf_yaml == tmp_path / "books" / "shelf.yaml"

    def test_no_market_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="market"):
            load_plan(self._persona(tmp_path, {}))

    def test_no_credentials_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="values.json"):
            load_plan(self._persona(tmp_path, {"market": "CO"}, secrets=False))
