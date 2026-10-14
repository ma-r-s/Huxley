"""Google News headlines lane: URL building and feed filtering. No network."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from xml.sax.saxutils import escape

from huxley_skill_search.headlines import MAX_HEADLINES, feed_url, parse_feed, split_source

NOW = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)


def rss(items: list[tuple[str, float]]) -> str:
    """A Google News-shaped feed: (title with " - Source", age in hours)."""
    body = "".join(
        f"<item><title>{escape(title)}</title>"
        f"<pubDate>{format_datetime(NOW - timedelta(hours=age), usegmt=True)}</pubDate></item>"
        for title, age in items
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><rss><channel>{body}</channel></rss>'


class TestFeedUrl:
    def test_country_region_builds_search_url(self) -> None:
        url = feed_url("¿cómo quedó Millonarios ayer?", "co-es")
        assert url is not None
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["gl"] == ["CO"]
        assert params["hl"] == ["es"]
        assert params["ceid"] == ["CO:es"]
        # Stopwords and time words dropped, recency operator added.
        assert params["q"] == ["quedó Millonarios when:3d"]

    def test_no_region_means_no_headlines(self) -> None:
        assert feed_url("Millonarios", "wt-wt") is None

    def test_only_stopwords_means_no_headlines(self) -> None:
        assert feed_url("¿qué hay hoy?", "co-es") is None


class TestParseFeed:
    def test_keeps_fresh_matching_headlines(self) -> None:
        body = rss(
            [
                ("Colombia femenina ganó 2-1 a Venezuela en Bogotá - El Tiempo", 1.0),
                ("Shakira anuncia gira por Europa - Infobae", 2.0),  # unrelated
            ]
        )
        found = parse_feed(body, "resultado Colombia femenina Venezuela", now=NOW).headlines
        assert [h.title for h in found] == ["Colombia femenina ganó 2-1 a Venezuela en Bogotá"]
        assert found[0].source == "El Tiempo"
        assert found[0].age_hours == 1.0

    def test_needs_two_query_words(self) -> None:
        body = rss([("Venezuela anuncia nuevas medidas económicas - BBC", 1.0)])
        assert parse_feed(body, "Colombia femenina Venezuela", now=NOW).headlines == []

    def test_one_word_query_needs_one(self) -> None:
        body = rss([("Millonarios recibe a Once Caldas - Win Sports", 1.0)])
        assert len(parse_feed(body, "Millonarios", now=NOW).headlines) == 1

    def test_drops_stale(self) -> None:
        body = rss([("Temblor en Colombia sacude Bucaramanga - Semana", 100.0)])
        assert parse_feed(body, "temblor Colombia", now=NOW).headlines == []

    def test_drops_near_copies_of_the_same_story(self) -> None:
        body = rss(
            [
                ("Temblor de magnitud 5,0 sacude Colombia este martes - El Tiempo", 1.0),
                ("Temblor de magnitud 5,0 sacude Colombia este martes en la tarde - Pulzo", 1.5),
                ("Evacúan edificios en Bucaramanga por el temblor en Colombia - Semana", 2.0),
            ]
        )
        found = parse_feed(body, "temblor Colombia", now=NOW).headlines
        assert [h.source for h in found] == ["El Tiempo", "Semana"]

    def test_caps_count(self) -> None:
        # Near-identical wording is deduped, so vary it enough.
        body = rss(
            [
                ("Millonarios gana el clásico - A", 1.0),
                ("Millonarios contrata nuevo técnico argentino - B", 1.0),
                ("Millonarios vende boletas para la final - C", 1.0),
                ("Millonarios estrena camiseta conmemorativa - D", 1.0),
            ]
        )
        assert len(parse_feed(body, "Millonarios", now=NOW).headlines) == MAX_HEADLINES

    def test_bad_xml_raises_feed_error(self) -> None:
        import pytest

        from huxley_skill_search.headlines import FeedError

        with pytest.raises(FeedError):
            parse_feed("<html>consent page", "x y", now=NOW)

    def test_seen_counts_items_before_filtering(self) -> None:
        body = rss([("Algo sin relación - A", 1.0), ("Otra cosa - B", 200.0)])
        result = parse_feed(body, "Millonarios", now=NOW)
        assert result.headlines == []
        assert result.seen == 2

    def test_title_shape(self) -> None:
        assert split_source("Algo pasó | El Colombiano - El Colombiano") == (
            "Algo pasó",
            "El Colombiano",
        )
        assert split_source("Sin fuente") == ("Sin fuente", "")
        assert split_source("Uno - dos - Fuente") == ("Uno - dos", "Fuente")
