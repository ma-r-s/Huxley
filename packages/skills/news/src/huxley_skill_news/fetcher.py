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

import json
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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
    """Open-Meteo wrapper. Returns current + today's forecast.

    Open-Meteo is free, no API key, and returns JSON. We ask for: current
    temperature/wind/code + daily max/min/code for today. Units are metric;
    the skill converts to imperial if the persona configured `units: imperial`.
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
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "forecast_days": 1,
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
            "today": {
                "high": _first(daily.get("temperature_2m_max")),
                "low": _first(daily.get("temperature_2m_min")),
                "condition_key": _wmo_key(_first(daily.get("weather_code"))),
            },
            "units": "fahrenheit" if self.units == "imperial" else "celsius",
        }


def _first(seq: list[Any] | None) -> Any:
    return seq[0] if seq else None


# Google News RSS topic IDs (used as the SECTION param in the URL). See
# https://news.google.com/ for the canonical list. Categories the skill
# exposes map onto these (with "local" treated as country-scoped — Google
# News doesn't do hyperlocal; see docs/skills/news.md for the limitation).
_GOOGLE_NEWS_TOPICS: dict[str, str | None] = {
    "world": "WORLD",
    "national": "NATION",
    "local": "NATION",  # closest available; honest limitation
    "sports": "SPORTS",
    "tech": "TECHNOLOGY",
    "business": "BUSINESS",
    "science": "SCIENCE",
    "entertainment": "ENTERTAINMENT",
    "health": "HEALTH",
    # `weather` is fetched by WeatherFetcher, not Google News.
}


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    summary: str
    source: str
    published_at: datetime
    category: str

    def age_hours(self, now: datetime) -> float:
        return (now - self.published_at).total_seconds() / 3600

    def to_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "age_hours": round(self.age_hours(now), 1),
            "category": self.category,
        }


@dataclass(frozen=True, slots=True)
class NewsFetcher:
    """Google News RSS wrapper. Returns curated headlines.

    With no `category` and no `query`: pulls the country's "Top stories"
    feed — Google's algorithm picks what matters, we don't.
    With `category`: pulls the topic-specific feed (sports/tech/etc.).
    With `query`: pulls Google News search RSS for that text.
    """

    http: HttpClient
    country_code: str  # ISO 3166-1 alpha-2 ("ES", "CO", "US", ...)
    language_code: str  # ISO 639-1 ("es", "en", ...)

    @property
    def _ceid(self) -> str:
        return f"{self.country_code}:{self.language_code}"

    def _base_params(self) -> dict[str, str]:
        return {
            "hl": self.language_code,
            "gl": self.country_code,
            "ceid": self._ceid,
        }

    def _build_url(self, *, query: str | None, category: str | None) -> str:
        if query:
            params = {"q": query, **self._base_params()}
            return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
        if category and category in _GOOGLE_NEWS_TOPICS:
            topic = _GOOGLE_NEWS_TOPICS[category]
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
        now = datetime.now(UTC)
        fresh = [item for item in items if item.age_hours(now) <= max_age_hours]
        return fresh[:max_items]


def _parse_rss(body: str, *, category: str) -> list[NewsItem]:
    """Parse a Google News RSS feed into NewsItem records."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items: list[NewsItem] = []
    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        if not title:
            continue
        # Google News titles have format "Title - Source"; split off the source
        source = ""
        if " - " in title:
            title, _, source = title.rpartition(" - ")
            source = source.strip()
        # Description is HTML-ish; we just store the title for now since
        # description contains stripped HTML the LLM doesn't need.
        summary = (item_el.findtext("description") or "").strip()
        # Strip HTML tags crudely from the description (Google News
        # descriptions are minimal HTML — link list to the source).
        summary = _strip_tags(summary)
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
                title=title.strip(),
                summary=summary,
                source=source,
                published_at=pub_date,
                category=category,
            )
        )
    return items


def _strip_tags(html: str) -> str:
    """Remove HTML tags + decode common entities. Google News descriptions
    are very simple HTML (just <a href>...</a> wrapping linked headlines)."""
    import re

    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " ")
    return " ".join(text.split())
