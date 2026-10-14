"""The audiobook shelf (T1.45): loading, matching what was said, ranking."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

import pytest

from huxley_skill_spotify import lines
from huxley_skill_spotify import shelf as shelves
from huxley_skill_spotify.books import Listener
from huxley_skill_spotify.shelf import ShelfError, ShelfFile, parse_shelf

from .conftest import SHELF_BOOKS, shelf_book, shelf_data, write_shelf

if TYPE_CHECKING:
    from pathlib import Path

SHELF = parse_shelf(shelf_data())


def _by_title(title: str) -> shelves.ShelfBook:
    return next(b for b in SHELF.books if b.title == title)


class TestValidation:
    def test_a_good_shelf_loads(self) -> None:
        assert len(SHELF.books) == len(SHELF_BOOKS)
        assert SHELF.categories[0] == "misterio y suspenso"
        assert SHELF.skipped == 0
        book = SHELF.by_uri("spotify:show:cien")
        assert book is not None and book.tags == ("familia", "colombia")

    def test_bad_entries_are_skipped_and_counted(self) -> None:
        good = SHELF_BOOKS[0]
        bad = [
            {**good, "uri": "spotify:album:x"},  # not a book
            {**good, "uri": "spotify:show:y", "category": "poesía"},  # unknown category
            {**good, "uri": "spotify:show:z", "title": ""},
            good,  # the same uri twice
            "not a book",
        ]
        shelf = parse_shelf(shelf_data([good, *bad]))
        assert [b.uri for b in shelf.books] == [good["uri"]]
        assert shelf.skipped == 5
        # A category with no book left is not offered.
        assert shelf.categories == ("novela latinoamericana",)

    @pytest.mark.parametrize(
        ("data", "error"),
        [
            ([], "not a JSON object"),
            ({**shelf_data(), "version": 2}, "version"),
            ({**shelf_data(), "categories": []}, "no categories"),
            (shelf_data([{"title": "x"}]), "no valid books"),
        ],
    )
    def test_a_shelf_that_cant_be_used_is_an_error(self, data: object, error: str) -> None:
        with pytest.raises(ShelfError, match=error):
            parse_shelf(data)


class TestReload:
    def test_missing_loaded_changed_invalid_and_gone(self, tmp_path: Path) -> None:
        path = tmp_path / "shelf.json"
        watcher = ShelfFile(path)

        missing = watcher.refresh()
        assert missing is not None and missing.event == "missing" and watcher.shelf is None
        assert watcher.refresh() is None  # said once

        write_shelf(path)
        loaded = watcher.refresh()
        assert loaded is not None and loaded.event == "loaded"
        assert watcher.shelf is not None and len(watcher.shelf.books) == len(SHELF_BOOKS)
        assert watcher.refresh() is None  # unchanged: not read again

        # A broken edit keeps the last good shelf.
        path.write_text("{ not json", encoding="utf-8")
        os.utime(path, (time.time() + 5, time.time() + 5))
        invalid = watcher.refresh()
        assert invalid is not None and invalid.event == "invalid"
        assert "invalid JSON" in (invalid.error or "")
        assert watcher.shelf is not None and len(watcher.shelf.books) == len(SHELF_BOOKS)

        # Fixed: the new shelf is read.
        write_shelf(path, SHELF_BOOKS[:2])
        os.utime(path, (time.time() + 10, time.time() + 10))
        again = watcher.refresh()
        assert again is not None and again.event == "loaded"
        assert len(watcher.shelf.books) == 2

        path.unlink()
        gone = watcher.refresh()
        assert gone is not None and gone.event == "missing" and watcher.shelf is None


class TestMatching:
    @pytest.mark.parametrize(
        ("said", "category"),
        [
            ("algo de misterio", "misterio y suspenso"),
            ("suspenso", "misterio y suspenso"),
            ("libros de cuentos", "cuentos"),
            ("historias", "historia y memorias"),
            ("aventura", "aventuras"),
            ("dragones", None),
        ],
    )
    def test_category(self, said: str, category: str | None) -> None:
        assert shelves.match_category(SHELF, said) == category

    def test_tag_and_author(self) -> None:
        assert shelves.match_tag(SHELF, "libros de detectives") == "detectives"
        assert shelves.match_tag(SHELF, "algo de viajes") == "viajes"
        assert shelves.match_author(SHELF, "garcía márquez") == "Gabriel García Márquez"
        assert shelves.match_author(SHELF, "algo de Borges") == "Jorge Luis Borges"
        assert shelves.match_author(SHELF, "conan doyle") == "Arthur Conan Doyle"
        assert shelves.match_author(SHELF, "misterio") is None

    @pytest.mark.parametrize(
        ("said", "title"),
        [
            ("cien años de soledad", "Cien años de soledad"),
            ("Cien años de soledad de García Márquez", "Cien años de soledad"),
            ("el coronel no tiene quien le escriba", "El coronel no tiene quien le escriba"),
            # Digits don't count, the rest of the title is close enough.
            ("la vuelta al mundo en 80 días", "La vuelta al mundo en ochenta días"),
            ("la vuelta al mundo en ochenta días", "La vuelta al mundo en ochenta días"),
            ("misterio", None),
            ("garcía márquez", None),
        ],
    )
    def test_title(self, said: str, title: str | None) -> None:
        found = shelves.match_title(SHELF, said)
        assert (found.title if found else None) == title

    def test_a_theme_is_neither_a_title_nor_an_author(self) -> None:
        assert shelves.is_theme(SHELF, "algo de misterio")
        assert shelves.is_theme(SHELF, "un libro de detectives")
        assert not shelves.is_theme(SHELF, "cuentos de la selva")  # a title
        assert not shelves.is_theme(SHELF, "garcía márquez")
        assert not shelves.is_theme(SHELF, "sidarta")  # a garbled title: search asks

    def test_editions_of_one_book(self) -> None:
        assert shelves.same_book(
            "Cien años de soledad",
            "Gabriel García Márquez",
            "Cien años de soledad (Edición conmemorativa)",
            "García Márquez",
        )
        assert not shelves.same_book(
            "Cien años de soledad", "Gabriel García Márquez", "Cien años de soledad", "Otro Autor"
        )


def _ids(books: list[shelves.ShelfBook]) -> list[str]:
    return [b.book_id for b in books]


MISTERIO = [b for b in SHELF.books if b.category == "misterio y suspenso"]


class TestRanking:
    def test_shelf_order_without_history(self) -> None:
        ranked, excluded = shelves.rank(MISTERIO, Listener(), SHELF)
        assert _ids(ranked) == ["sabueso", "orient", "diez", "escarlata"]
        assert excluded == 0

    def test_heard_finished_disliked_and_refused_are_never_offered(self) -> None:
        listener = Listener(
            started={"sabueso": time.time()},
            ended=frozenset({"orient"}),
            opinions={"diez": "disliked"},
            refused=frozenset({"escarlata"}),
        )
        ranked, excluded = shelves.rank(MISTERIO, listener, SHELF)
        assert ranked == [] and excluded == 4

    def test_another_edition_he_heard_counts_as_this_book(self) -> None:
        listener = Listener(
            started={"otra": time.time()},
            titles={"otra": "Asesinato en el Orient Express (Edición íntegra)"},
            authors={"otra": "Agatha Christie"},
        )
        ranked, _ = shelves.rank(MISTERIO, listener, SHELF)
        assert "orient" not in _ids(ranked)

    def test_a_liked_author_comes_first_and_a_disliked_one_last(self) -> None:
        liked = Listener(
            opinions={"x": "liked"}, authors={"x": "Agatha Christie"}, titles={"x": "Otro"}
        )
        assert _ids(shelves.rank(MISTERIO, liked, SHELF)[0])[:2] == ["orient", "diez"]
        disliked = Listener(
            opinions={"x": "disliked"}, authors={"x": "Arthur Conan Doyle"}, titles={"x": "Otro"}
        )
        assert _ids(shelves.rank(MISTERIO, disliked, SHELF)[0])[-2:] == ["sabueso", "escarlata"]

    def test_a_finished_author_and_a_liked_category_lift_a_book(self) -> None:
        finished = Listener(ended=frozenset({"x"}), authors={"x": "Juan Rulfo"})
        latin = [b for b in SHELF.books if b.category == "novela latinoamericana"]
        assert _ids(shelves.rank(latin, finished, SHELF)[0])[0] == "pedro"
        # Liking a mystery lifts mysteries in a mixed list.
        everything = list(SHELF.books)
        liked = Listener(opinions={"sabueso": "liked"}, started={"sabueso": time.time()})
        top = shelves.rank(everything, liked, SHELF)[0][0]
        assert top.category == "misterio y suspenso"

    def test_ties_go_to_the_book_offered_least_recently(self) -> None:
        now = time.time()
        listener = Listener(offered={"sabueso": now - 10, "orient": now - 5})
        assert _ids(shelves.rank(MISTERIO, listener, SHELF)[0]) == [
            "diez",
            "escarlata",
            "sabueso",
            "orient",
        ]

    def test_abandoned_is_started_unfinished_not_last_and_old(self) -> None:
        old = time.time() - 22 * 86_400
        listener = Listener(started={"a": old, "b": old, "c": time.time()}, last_book="b")
        assert listener.abandoned("a", time.time())
        assert not listener.abandoned("b", time.time())  # his last book
        assert not listener.abandoned("c", time.time())  # heard lately

    def test_like_a_book_shares_category_tags_and_author(self) -> None:
        sabueso = _by_title("El sabueso de los Baskerville")
        points = shelves.similar(
            SHELF,
            author=sabueso.author,
            category=sabueso.category,
            tags=sabueso.tags,
            besides=sabueso.uri,
        )
        assert sabueso.uri not in points
        # Same author, category and both tags beats one shared tag.
        assert points["spotify:show:escarlata"] > points["spotify:show:orient"]
        assert "spotify:show:pedro" not in points


class TestSpokenLists:
    @pytest.mark.parametrize(
        ("items", "lang", "said"),
        [
            (["a"], "es", "a"),
            (["a", "b", "c"], "es", "a, b y c"),
            (["cuentos", "historia y memorias"], "es", "cuentos e historia y memorias"),
            (["misterio", "ideas"], "es", "misterio e ideas"),
            (["agua", "hielo"], "es", "agua y hielo"),
            (["a", "b"], "en", "a and b"),
            (["a", "b"], "fr", "a et b"),
        ],
    )
    def test_lists(self, items: list[str], lang: str, said: str) -> None:
        assert shelves.spoken_list(items, lang) == said

    def test_an_offer_reads_three_numbered_with_pitches(self) -> None:
        said = lines.offer(
            "es",
            "head",
            "misterio y suspenso",
            [
                ("Diez negritos", "Agatha Christie", "Diez invitados en una isla"),
                ("Estudio en escarlata", "Arthur Conan Doyle", "La primera de Holmes."),
                ("El sabueso", "Arthur Conan Doyle", ""),
            ],
        )
        assert said == (
            "De misterio y suspenso tengo tres. Uno, Diez negritos, de Agatha Christie: Diez "
            "invitados en una isla. Dos, Estudio en escarlata, de Arthur Conan Doyle: La "
            "primera de Holmes. Tres, El sabueso, de Arthur Conan Doyle. ¿Cuál pongo, o busco "
            "otros?"
        )
        one = lines.offer("es", "", "", [("Ficciones", "Jorge Luis Borges", "Cuentos.")])
        assert one == "Ficciones, de Jorge Luis Borges: Cuentos. ¿Lo pongo, o busco otros?"


def test_the_real_shelf_loads_and_every_entry_is_complete() -> None:
    """The repo's shelf (built by huxley-spotify-shelf) is what the skill
    reads in production: it must load with nothing skipped."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "server" / "personas" / "abuelos" / "books"
    built = root / "shelf.json"
    if not built.exists():
        pytest.skip("shelf.json not built yet")
    shelf = parse_shelf(json.loads(built.read_text(encoding="utf-8")))
    assert shelf.skipped == 0
    assert len(shelf.books) >= 100
    assert all(b.pitch for b in shelf.books)


def test_shelf_book_helper_shape() -> None:
    entry = shelf_book("T", "A", "spotify:show:t", "cuentos", [])
    assert parse_shelf(shelf_data([entry])).books[0].title == "T"
