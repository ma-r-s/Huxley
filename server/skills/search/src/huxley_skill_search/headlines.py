"""Fresh headlines for a search, from Google News RSS.

Web indexes lag: on 2026-10-13 a search for last night's match found
pages about matches from 2025, while Google News already had "Colombia
femenina gano 2-1 ante Venezuela". So the skill asks Google News (free,
no key, about half a second) in parallel with the web search, and keeps
up to three recent headlines that share the query's words. For a
question that isn't news (a recipe, an age) nothing matches and the
field is left out.

Pure parsing and filtering live here; the HTTP GET goes through the
same `PageFetcher` the page excerpts use, so tests stay offline.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from huxley_skill_search.pages import fold, query_terms, words

MAX_HEADLINES = 3
MAX_AGE_HOURS = 72
_TITLE_MAX_CHARS = 160
_DUPLICATE_OVERLAP = 0.6


@dataclass(frozen=True, slots=True)
class Headline:
    title: str
    source: str
    age_hours: float

    def to_dict(self) -> dict[str, object]:
        return {"title": self.title, "source": self.source, "age_hours": self.age_hours}


def feed_url(query: str, region: str) -> str | None:
    """Google News search RSS URL for `query`, or None without a country.

    `region` is the skill's ddgs-style `<country>-<language>` code;
    `wt-wt` (no region) has no Google News edition, so no headlines.
    Time words and stopwords are dropped from the query (Google News
    matches words, and "hoy" in a headline is rare), and `when:3d` keeps
    the feed to the last three days.
    """
    country, _, language = region.lower().partition("-")
    if len(country) != 2 or not language or country == "wt":
        return None
    terms = _news_query(query)
    if not terms:
        return None
    gl = country.upper()
    params = {
        "q": f"{terms} when:{MAX_AGE_HOURS // 24}d",
        "hl": language,
        "gl": gl,
        "ceid": f"{gl}:{language}",
    }
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def _news_query(query: str) -> str:
    kept = []
    meaningful = query_terms(query)
    for token in query.split():
        bare = re.sub(r"[^\w]", "", token)
        if bare and words(bare) & meaningful:
            kept.append(bare)
    return " ".join(kept)


class FeedError(ValueError):
    """The body isn't an RSS feed (a consent page, an error page)."""


@dataclass(frozen=True, slots=True)
class FeedResult:
    headlines: list[Headline]
    seen: int  # items in the feed, before any filter


def parse_feed(body: str, query: str, *, now: datetime | None = None) -> FeedResult:
    """Recent headlines from a Google News RSS body that match `query`.

    A headline is kept when it shares at least two of the query's words
    (one, for a one-word query), is at most `MAX_AGE_HOURS` old, and
    isn't a near copy of one already kept (the same story from another
    outlet). Feed order is Google's relevance order and is preserved.

    Raises:
        FeedError: the body doesn't parse as XML, so "no match" and "no
            feed" stay distinguishable in the log.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedError("bad_feed") from exc
    items = list(root.iter("item"))
    now = now or datetime.now(UTC)
    terms = query_terms(query)
    need = min(2, len(terms))
    if need == 0:
        return FeedResult(headlines=[], seen=len(items))
    kept: list[Headline] = []
    kept_words: list[set[str]] = []
    for item in items:
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        title, source = split_source(raw_title)
        age = _age_hours(item.findtext("pubDate"), now)
        if age is None or age > MAX_AGE_HOURS:
            continue
        if len(terms & words(title)) < need:
            continue
        title_words = query_terms(title)  # content words only, for the copy check
        if any(overlap(title_words, other) >= _DUPLICATE_OVERLAP for other in kept_words):
            continue
        kept.append(Headline(title=_cap(title), source=source, age_hours=round(age, 1)))
        kept_words.append(title_words)
        if len(kept) >= MAX_HEADLINES:
            break
    return FeedResult(headlines=kept, seen=len(items))


def split_source(raw_title: str) -> tuple[str, str]:
    """Google News titles end in " - Source"; some repeat it as " | Source"."""
    title, source = raw_title, ""
    if " - " in raw_title:
        title, _, source = raw_title.rpartition(" - ")
    title, source = title.strip(), source.strip()
    if source and " | " in title:
        head, _, tail = title.rpartition(" | ")
        if fold(tail) == fold(source):
            title = head.strip()
    return title, source


def overlap(a: set[str], b: set[str]) -> float:
    """Share of the smaller word set found in the other (0 to 1)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _age_hours(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        published = parsedate_to_datetime(stamp)
    except (TypeError, ValueError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return max(0.0, (now - published).total_seconds() / 3600)


def _cap(title: str) -> str:
    if len(title) <= _TITLE_MAX_CHARS:
        return title
    return title[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0] + "..."
