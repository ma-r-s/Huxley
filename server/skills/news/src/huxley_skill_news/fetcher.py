"""Data fetchers for the news skill.

Two upstreams, both free + no API key:
- Open-Meteo for weather (JSON over HTTPS, takes lat/lng)
- Google News RSS for news (country + language + topic filtered)

Both are accessed through the `HttpClient` Protocol so tests inject a
dict-backed fake — see `http.py`.

The skill calls `WeatherFetcher.fetch()` and `NewsFetcher.fetch()` and
composes their results into the JSON the LLM narrates. Categories map to
Google News topic feeds; the default (no category, no query) uses the
country's curated "Top stories" feed — Google's algorithm decides what's
the lead, not us.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from huxley_skill_news.http import HttpClient


# WMO weather codes → English condition keys. LLMs translate fluidly into
# the persona's language; saves us a per-language lookup table. Reference:
# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
_WMO_CODE_TO_KEY: dict[int, str] = {
    0: "clear",
    1: "mostly_clear",
    2: "partly_cloudy",
    3: "overcast",
    45: "fog",
    48: "rime_fog",
    51: "light_drizzle",
    53: "drizzle",
    55: "heavy_drizzle",
    56: "freezing_drizzle",
    57: "heavy_freezing_drizzle",
    61: "light_rain",
    63: "rain",
    65: "heavy_rain",
    66: "freezing_rain",
    67: "heavy_freezing_rain",
    71: "light_snow",
    73: "snow",
    75: "heavy_snow",
    77: "snow_grains",
    80: "light_showers",
    81: "showers",
    82: "violent_showers",
    85: "snow_showers",
    86: "heavy_snow_showers",
    95: "thunderstorm",
    96: "thunderstorm_with_hail",
    99: "thunderstorm_with_heavy_hail",
}


def _wmo_key(code: int | None) -> str:
    if code is None:
        return "unknown"
    return _WMO_CODE_TO_KEY.get(code, "unknown")


@dataclass(frozen=True, slots=True)
class WeatherFetcher:
    """Open-Meteo wrapper. Returns current + today's and tomorrow's forecast.

    Open-Meteo is free, no API key, and returns JSON. We ask for: current
    temperature/wind/code + daily max/min/code/rain chance for today and
    tomorrow. Units are metric unless the persona configured
    `units: imperial`, which Open-Meteo converts.
    """

    http: HttpClient
    latitude: float
    longitude: float
    units: str = "metric"  # "metric" or "imperial"

    async def fetch(self) -> dict[str, Any]:
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": (
                "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
            ),
            # Today and tomorrow: "will it rain tomorrow?" is the second most
            # common weather question, and without tomorrow the model
            # either guesses or searches the web (T1.48).
            "forecast_days": 2,
            "timezone": "auto",
        }
        if self.units == "imperial":
            params["temperature_unit"] = "fahrenheit"
            params["wind_speed_unit"] = "mph"
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        body = await self.http.get_text(url)
        data = json.loads(body)
        current = data.get("current", {})
        daily = data.get("daily", {})
        return {
            "current": {
                "temperature": current.get("temperature_2m"),
                "humidity_pct": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "condition_key": _wmo_key(current.get("weather_code")),
            },
            "today": _day(daily, 0),
            "tomorrow": _day(daily, 1),
            "units": "fahrenheit" if self.units == "imperial" else "celsius",
        }


def _day(daily: dict[str, Any], index: int) -> dict[str, Any]:
    code = _nth(daily.get("weather_code"), index)
    return {
        "high": _nth(daily.get("temperature_2m_max"), index),
        "low": _nth(daily.get("temperature_2m_min"), index),
        "condition_key": _wmo_key(code),
        "rain_chance_pct": _nth(daily.get("precipitation_probability_max"), index),
    }


def _nth(seq: list[Any] | None, index: int) -> Any:
    return seq[index] if seq and len(seq) > index else None


# Google News RSS topic IDs (used as the SECTION param in the URL). See
# https://news.google.com/ for the canonical list. `local` is not here:
# Google News has no city feed, so local news is a search for the
# persona's `local_query` (see `NewsFetcher.fetch`).
_GOOGLE_NEWS_TOPICS: dict[str, str] = {
    "world": "WORLD",
    "national": "NATION",
    "sports": "SPORTS",
    "tech": "TECHNOLOGY",
    "business": "BUSINESS",
    "science": "SCIENCE",
    "entertainment": "ENTERTAINMENT",
    "health": "HEALTH",
    # `weather` is fetched by WeatherFetcher, not Google News.
}

# Searches (a `query`, or local news) look back further than the top
# stories: Google ranks search results by relevance, not date, and a
# story from two days ago is still news to someone who asks about it.
SEARCH_MAX_AGE_HOURS = 72
_RELATED_MAX = 2
_RELATED_MAX_CHARS = 150
# Two headlines are the same story when this share of the shorter one's
# content words appears in the other (measured on the Villavicencio feed:
# one road accident came back as five headlines from five outlets).
_DUPLICATE_OVERLAP = 0.6

_STOPWORDS = frozenset(
    """
    el la los las un una unos unas de del al a en y o u que se su sus es son
    fue por para con sin sobre como este esta estos estas ese esa lo le les
    mas tras ante entre hasta desde hoy ayer the of and or to in on for with
    is are was at by from
    """.split()  # noqa: SIM905 - a word list reads better as text
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_RELATED_RE = re.compile(
    r"<a\b[^>]*>(?P<title>.*?)</a>(?:\s|&nbsp;|\xa0)*<font\b[^>]*>(?P<source>.*?)</font>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def content_words(text: str) -> set[str]:
    """Folded content words of a headline, crude plural strip, no stopwords."""
    out: set[str] = set()
    for word in _WORD_RE.findall(_fold(text)):
        if len(word) < 3 or word in _STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        out.add(word)
    return out


def same_story(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= _DUPLICATE_OVERLAP


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    source: str
    published_at: datetime
    category: str
    # Other outlets' headlines for the same story (Google clusters them in
    # the item's description). They often say what the main headline
    # leaves out ("what the sometimiento bill is"), so the model can tell
    # the story instead of reading a bare title.
    related: tuple[str, ...] = field(default_factory=tuple)

    def age_hours(self, now: datetime) -> float:
        return (now - self.published_at).total_seconds() / 3600

    def to_dict(self, now: datetime) -> dict[str, Any]:
        out: dict[str, Any] = {
            "title": self.title,
            "source": self.source,
            "age_hours": round(self.age_hours(now), 1),
        }
        if self.related:
            out["related"] = list(self.related)
        return out


@dataclass(frozen=True, slots=True)
class NewsFetcher:
    """Google News RSS wrapper. Returns curated headlines.

    With no `category` and no `query`: the country's "Top stories" feed;
    Google's algorithm picks what matters, we don't.
    With `category="local"`: a search for `local_query` (the persona's
    city and region), since Google News has no city feed.
    With another `category`: the topic feed (sports, tech, ...).
    With `query`: Google News search for that text.
    Searches carry `when:3d` so Google returns recent stories, and look
    back `SEARCH_MAX_AGE_HOURS`; feeds look back `max_age_hours`.
    """

    http: HttpClient
    country_code: str  # ISO 3166-1 alpha-2 ("ES", "CO", "US", ...)
    language_code: str  # ISO 639-1 ("es", "en", ...)
    local_query: str = ""

    @property
    def _ceid(self) -> str:
        return f"{self.country_code}:{self.language_code}"

    def _base_params(self) -> dict[str, str]:
        return {
            "hl": self.language_code,
            "gl": self.country_code,
            "ceid": self._ceid,
        }

    def _search_text(self, *, query: str | None, category: str | None) -> str | None:
        if query:
            return query
        if category == "local" and self.local_query:
            return self.local_query
        return None

    def _build_url(self, *, query: str | None, category: str | None) -> str:
        search = self._search_text(query=query, category=category)
        if search:
            days = SEARCH_MAX_AGE_HOURS // 24
            params = {"q": f"{search} when:{days}d", **self._base_params()}
            return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
        topic = _GOOGLE_NEWS_TOPICS.get(category or "")
        if category == "local":
            topic = "NATION"  # no local_query configured: closest feed
        if topic:
            params = self._base_params()
            return (
                f"https://news.google.com/rss/headlines/section/topic/{topic}?"
                + urllib.parse.urlencode(params)
            )
        # Default: country's top stories
        return "https://news.google.com/rss?" + urllib.parse.urlencode(self._base_params())

    async def fetch(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        max_items: int = 8,
        max_age_hours: int = 24,
    ) -> list[NewsItem]:
        url = self._build_url(query=query, category=category)
        body = await self.http.get_text(url)
        items = _parse_rss(body, category=category or ("search" if query else "top"))
        searched = self._search_text(query=query, category=category) is not None
        window = max(max_age_hours, SEARCH_MAX_AGE_HOURS) if searched else max_age_hours
        now = datetime.now(UTC)
        fresh = [item for item in items if item.age_hours(now) <= window]
        return dedupe(fresh)[:max_items]


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Drop items that repeat a story already kept (feed order wins)."""
    kept: list[NewsItem] = []
    kept_words: list[set[str]] = []
    for item in items:
        words = content_words(item.title)
        if any(same_story(words, other) for other in kept_words):
            continue
        kept.append(item)
        kept_words.append(words)
    return kept


def _parse_rss(body: str, *, category: str) -> list[NewsItem]:
    """Parse a Google News RSS feed into NewsItem records."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items: list[NewsItem] = []
    for item_el in root.iter("item"):
        raw_title = (item_el.findtext("title") or "").strip()
        if not raw_title:
            continue
        title, source = split_title(raw_title)
        pub_date_str = item_el.findtext("pubDate")
        try:
            pub_date = parsedate_to_datetime(pub_date_str) if pub_date_str else None
        except (TypeError, ValueError):
            pub_date = None
        if pub_date is None:
            continue
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=UTC)
        items.append(
            NewsItem(
                title=title,
                source=source,
                published_at=pub_date,
                category=category,
                related=_related(item_el.findtext("description") or "", title),
            )
        )
    return items


def split_title(raw_title: str) -> tuple[str, str]:
    """Google News titles are "Title - Source"; some repeat " | Source"."""
    title, source = raw_title, ""
    if " - " in raw_title:
        title, _, source = raw_title.rpartition(" - ")
    title, source = title.strip(), source.strip()
    if source and " | " in title:
        head, _, tail = title.rpartition(" | ")
        if _fold(tail.strip()) == _fold(source):
            title = head.strip()
    return title, source


def _related(description_html: str, title: str) -> tuple[str, ...]:
    """Up to two other outlets' headlines for the story, "Headline (Source)".

    The description of a clustered item is an <ol> of <a>headline</a>
    <font>source</font> pairs, the first being the item itself. Entries
    that just repeat the main headline are skipped.
    """
    if not description_html:
        return ()
    own = content_words(title)
    out: list[str] = []
    seen: list[set[str]] = [own]
    for match in _RELATED_RE.finditer(description_html):
        source = _clean_html(match.group("source"))
        headline, _ = split_title(f"{_clean_html(match.group('title'))} - {source}")
        words = content_words(headline)
        if not headline or any(same_story(words, other) for other in seen):
            continue
        seen.append(words)
        if len(headline) > _RELATED_MAX_CHARS:
            headline = headline[:_RELATED_MAX_CHARS].rsplit(" ", 1)[0] + "..."
        out.append(f"{headline} ({source})" if source else headline)
        if len(out) >= _RELATED_MAX:
            break
    return tuple(out)


def _clean_html(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).split())
