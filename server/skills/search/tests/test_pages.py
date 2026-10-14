"""Page-excerpt extraction: fixtures in, excerpt + date out. No network."""

from __future__ import annotations

from huxley_skill_search.pages import (
    EXCERPT_MAX_CHARS,
    date_from_url,
    extract_page,
    query_terms,
    today_iso,
)

from .conftest import fixture_text


class TestNewsArticle:
    def test_keeps_the_answer_and_drops_boilerplate(self) -> None:
        excerpt = extract_page(
            fixture_text("news_article.html"), "resultado Millonarios Once Caldas"
        )
        text = excerpt.text
        # The fact the user asked for.
        assert "venció 2-0" in text or "2-0" in text
        assert "24 puntos" in text
        # Furniture gone: cookie banner, menu, share bar, footer, sidebar.
        assert "cookies" not in text.lower()
        assert "Inicio" not in text
        assert "Compartir" not in text
        assert "derechos reservados" not in text
        assert "Lo más leído" not in text
        # Related-article link list dropped (link density).
        assert "clásico de la Copa" not in text

    def test_reads_published_date_from_meta(self) -> None:
        excerpt = extract_page(fixture_text("news_article.html"), "Millonarios")
        assert excerpt.date == "2026-10-13"

    def test_excerpt_respects_cap(self) -> None:
        big = (
            "<html><body><article>"
            + "".join(
                f"<p>Párrafo número {i} sobre Millonarios y su campaña en la liga colombiana.</p>"
                for i in range(200)
            )
            + "</article></body></html>"
        )
        excerpt = extract_page(big, "Millonarios liga")
        assert 0 < len(excerpt.text) <= EXCERPT_MAX_CHARS

    def test_custom_cap(self) -> None:
        excerpt = extract_page(fixture_text("news_article.html"), "Millonarios", max_chars=200)
        assert len(excerpt.text) <= 200

    def test_relevant_paragraphs_win_over_order(self) -> None:
        # The answer sits after 30 filler paragraphs; it must still be in.
        filler = "".join(
            f"<p>Texto de relleno número {i} que habla del clima y de otras cosas.</p>"
            for i in range(30)
        )
        html = (
            "<html><body><article>"
            f"{filler}<p>La tasa de cambio cerró en 3.194 pesos por dólar este martes.</p>"
            "</article></body></html>"
        )
        excerpt = extract_page(html, "precio del dólar hoy", max_chars=400)
        assert "3.194" in excerpt.text


class TestTablesAndWrappers:
    def test_table_rows_survive_and_has_sidebar_wrapper_is_kept(self) -> None:
        excerpt = extract_page(
            fixture_text("table_page.html"), "pico y placa Villavicencio martes"
        )
        assert "Martes | 3 y 4" in excerpt.text
        assert "6:30" in excerpt.text
        # The real sidebar (an ad) still goes.
        assert "seguro de carro" not in excerpt.text

    def test_reference_page_uses_modified_date(self) -> None:
        # Created 2003, edited 2026: the edit date says how current it is.
        excerpt = extract_page(fixture_text("reference_page.html"), "edad cantante")
        assert excerpt.date == "2026-10-10"
        assert "49 años" in excerpt.text


class TestDegenerateInput:
    def test_app_shell_gives_empty_text(self) -> None:
        excerpt = extract_page(fixture_text("app_shell.html"), "cartelera cine")
        assert excerpt.text == ""

    def test_empty_and_garbage_never_raise(self) -> None:
        assert extract_page("", "x").text == ""
        assert extract_page("   ", "x").text == ""
        assert extract_page("<<<>>>not html at all", "x").date is None

    def test_xml_declaration_string_is_parsed(self) -> None:
        html = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<html><body><p>El dólar cerró hoy en 3.194 pesos según el Banco de la República.</p>"
            "</body></html>"
        )
        assert "3.194" in extract_page(html, "dólar").text

    def test_consent_text_is_not_the_lead(self) -> None:
        html = (
            "<html><body><article>"
            "<p>Acepto el tratamiento de mis datos personales conforme a la política.</p>"
            "<p>Los festivos que quedan en Colombia este año son el 2 y el 16 de noviembre.</p>"
            "</article></body></html>"
        )
        text = extract_page(html, "próximo festivo Colombia").text
        assert "Acepto" not in text
        assert "2 y el 16 de noviembre" in text


class TestHelpers:
    def test_date_from_url(self) -> None:
        assert date_from_url("https://x.com/deportes/2026/10/12/partido/") == "2026-10-12"
        assert date_from_url("https://x.com/2026-10-12-titular") == "2026-10-12"
        assert date_from_url("https://x.com/2026/13/40/bad") is None
        assert date_from_url("https://x.com/sin-fecha") is None

    def test_query_terms_fold_stem_and_drop_stopwords(self) -> None:
        terms = query_terms("¿Cuándo es el próximo festivo en Colombia hoy?")
        assert terms == {"proximo", "festivo", "colombia"}
        assert "festivo" in query_terms("festivos")

    def test_today_iso_unknown_zone_falls_back(self) -> None:
        assert len(today_iso("Not/AZone")) == 10
        assert len(today_iso("America/Bogota")) == 10


class TestDateBoost:
    def test_today_tomorrow_yesterday_map_to_weekday_names(self) -> None:
        from datetime import date

        from huxley_skill_search.pages import date_boost_terms, stem

        tuesday = date(2026, 10, 13)
        assert stem("martes") in date_boost_terms("¿hay pico y placa hoy?", tuesday)
        assert stem("miercoles") in date_boost_terms("¿va a llover mañana?", tuesday)
        assert stem("monday") in date_boost_terms("who won yesterday", tuesday)
        assert date_boost_terms("pico y placa Villavicencio", tuesday) == frozenset()

    def test_todays_row_beats_generic_blocks(self) -> None:
        # Every block says "pico y placa Villavicencio"; only one says
        # which plates rest on Tuesday. A tight cap must keep that one.
        generic = "".join(
            f"<p>El pico y placa en Villavicencio busca reducir el tráfico, punto {i}, "
            "según la Secretaría de Movilidad de Villavicencio.</p>"
            for i in range(12)
        )
        html = (
            "<html><body><article>"
            f"{generic}<ul>"
            + "".join(
                f"<li>{day}: {plates}, de 6:30 a 9:30 y de 17:00 a 20:00, para carros "
                "particulares en toda la ciudad.</li>"
                for day, plates in (
                    ("Lunes", "5 y 6"),
                    ("Martes", "7 y 8"),
                    ("Miércoles", "9 y 0"),
                )
            )
            + "</ul></article></body></html>"
        )
        from datetime import date

        from huxley_skill_search.pages import date_boost_terms

        boost = date_boost_terms("pico y placa Villavicencio hoy", date(2026, 10, 13))
        text = extract_page(
            html, "pico y placa Villavicencio hoy", max_chars=400, boost=boost
        ).text
        assert "Martes: 7 y 8" in text
        without = extract_page(html, "pico y placa Villavicencio hoy", max_chars=400).text
        assert "Martes: 7 y 8" not in without
