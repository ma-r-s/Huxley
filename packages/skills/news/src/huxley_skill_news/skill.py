"""News + weather skill — persona-agnostic.

Returns structured JSON; the LLM narrates per its persona's tone. Optional
`PlaySound` chime fires before the LLM speaks if the persona configured
`start_sound` and the WAV exists in the persona's sound palette.

Tools:
- `get_news(query?, category?)` — single polymorphic news tool. No args
  returns the country's curated top stories + weather; `query` is search,
  `category` filters by topic.
- `get_weather(when?)` — weather only, separate because users ask for
  weather more often than for news.

Configuration (persona's `skills.news` block):
- Required: `location`, `latitude`, `longitude`, `country_code`, `language_code`
- Optional: `units` (metric|imperial, default metric), `max_items` (default 8),
  `max_age_hours` (default 24), `interests` (list of strings, hint for LLM),
  `start_sound` (key into sound palette, e.g. "news_start" — omit for no chime),
  `sounds_path` (relative to persona data_dir, default "sounds"),
  `cache_ttl_seconds` (default 300).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from huxley_sdk import (
    PlaySound,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.audio import load_pcm_palette
from huxley_skill_news.fetcher import NewsFetcher, WeatherFetcher
from huxley_skill_news.http import HttpClient, HttpError, HttpxClient

_VALID_CATEGORIES = (
    "weather",
    "local",
    "national",
    "world",
    "sports",
    "tech",
    "business",
    "science",
    "entertainment",
    "health",
)


def _now_seconds() -> float:
    return time.monotonic()


class NewsSkill:
    """Fetches news + weather, returns structured JSON for LLM narration."""

    def __init__(self, *, http: HttpClient | None = None) -> None:
        # `http` is keyword-only and reserved for tests that inject a fake.
        # Production setup() builds an HttpxClient.
        self._http: HttpClient | None = http
        self._weather: WeatherFetcher | None = None
        self._news: NewsFetcher | None = None
        self._logger: SkillLogger | None = None
        self._location_name: str = ""
        self._language_code: str = "en"
        self._units: str = "metric"
        self._max_items: int = 8
        self._max_age_hours: int = 24
        self._interests: list[str] = []
        self._start_sound_role: str | None = None
        self._sounds: dict[str, bytes] = {}
        # In-memory TTL cache: {cache_key: (epoch_seconds, json_payload)}.
        # Per-process; resets on restart. Saves a network round-trip when
        # the user asks for the same slice twice in quick succession.
        self._cache_ttl_seconds: int = 300
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @property
    def name(self) -> str:
        return "news"

    @property
    def tools(self) -> list[ToolDefinition]:
        # The descriptions guide LLM dispatch — keep them in the configured
        # language so the persona's voice picks the right tool naturally.
        if self._language_code.startswith("es"):
            return self._tools_es()
        return self._tools_en()

    def _tools_es(self) -> list[ToolDefinition]:
        loc = self._location_name
        interests = ", ".join(self._interests) if self._interests else "general"
        return [
            ToolDefinition(
                name="get_news",
                description=(
                    f"Obtiene las noticias actuales y el clima de {loc}. "
                    "ANTES de llamar esta herramienta, di brevemente algo como "
                    "'a ver' o 'un momento' para que el usuario sepa que "
                    "escuchaste mientras los datos cargan. "
                    "Sin argumentos: devuelve las noticias destacadas del país. "
                    "Con `query`: busca noticias sobre ese tema. "
                    "Con `category`: filtra por tema "
                    "(local, national, world, sports, tech, business, science, "
                    "entertainment, health). "
                    f"Intereses configurados de este usuario: {interests}."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Búsqueda de noticias por palabras clave (opcional). "
                                "Ejemplo: 'reforma tributaria', 'América de Cali'."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": list(_VALID_CATEGORIES),
                            "description": (
                                "Categoría temática (opcional). Si se omite, "
                                "devuelve las noticias destacadas mezcladas."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="get_weather",
                description=(
                    f"Obtiene el clima actual y el pronóstico de hoy para {loc}. "
                    "Úsala cuando el usuario pregunte específicamente por el clima."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    def _tools_en(self) -> list[ToolDefinition]:
        loc = self._location_name
        interests = ", ".join(self._interests) if self._interests else "general"
        return [
            ToolDefinition(
                name="get_news",
                description=(
                    f"Get current news headlines and weather for {loc}. "
                    "BEFORE calling this tool, briefly say something like "
                    "'one moment' or 'let me check' so the user knows you "
                    "heard them while the data loads. "
                    "No args: returns the country's top stories + weather. "
                    "With `query`: search news for that text. "
                    "With `category`: filter by topic "
                    "(local, national, world, sports, tech, business, science, "
                    "entertainment, health). "
                    f"User interests configured for this persona: {interests}."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Keyword search for news (optional). "
                                "Example: 'tax reform', 'Apple earnings'."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": list(_VALID_CATEGORIES),
                            "description": (
                                "Topic filter (optional). If omitted, returns mixed top stories."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="get_weather",
                description=(
                    f"Get current weather and today's forecast for {loc}. "
                    "Use when the user asks specifically about the weather."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        cfg = ctx.config
        self._logger = ctx.logger

        # Required config — fail fast (in setup, not at first tool call) so
        # bad persona.yaml is caught at startup with a clear stack frame.
        try:
            self._location_name = str(cfg["location"])
            latitude = float(cfg["latitude"])
            longitude = float(cfg["longitude"])
            country_code = str(cfg["country_code"]).upper()
            self._language_code = str(cfg["language_code"]).lower()
        except KeyError as exc:
            raise ValueError(
                f"news skill: missing required config key {exc.args[0]!r}. "
                "Required: location, latitude, longitude, country_code, language_code."
            ) from exc

        # Optional with defaults
        self._units = str(cfg.get("units", "metric"))
        self._max_items = int(cfg.get("max_items", 8))
        self._max_age_hours = int(cfg.get("max_age_hours", 24))
        self._interests = list(cfg.get("interests", []) or [])
        self._cache_ttl_seconds = int(cfg.get("cache_ttl_seconds", 300))

        # Sound palette — only loaded if persona configured a start_sound role.
        # Missing role / missing file = no chime, no error (persona is opting out).
        self._start_sound_role = cfg.get("start_sound")
        if self._start_sound_role:
            sounds_raw = cfg.get("sounds_path", "sounds")
            sounds_dir = (
                Path(sounds_raw)
                if Path(sounds_raw).is_absolute()
                else (ctx.persona_data_dir / sounds_raw)
            )
            self._sounds = load_pcm_palette(sounds_dir, [self._start_sound_role])
            if self._start_sound_role not in self._sounds:
                await ctx.logger.awarning(
                    "news.start_sound_missing",
                    role=self._start_sound_role,
                    path=str(sounds_dir),
                )

        if self._http is None:
            self._http = HttpxClient()

        self._weather = WeatherFetcher(
            http=self._http,
            latitude=latitude,
            longitude=longitude,
            units=self._units,
        )
        self._news = NewsFetcher(
            http=self._http,
            country_code=country_code,
            language_code=self._language_code,
        )

        await ctx.logger.ainfo(
            "news.setup_complete",
            location=self._location_name,
            country=country_code,
            language=self._language_code,
            interests=self._interests,
            chime=self._start_sound_role if self._sounds else None,
        )

    async def teardown(self) -> None:
        if isinstance(self._http, HttpxClient):
            await self._http.aclose()

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "get_news":
                return await self._handle_get_news(
                    query=args.get("query"),
                    category=args.get("category"),
                )
            case "get_weather":
                return await self._handle_get_weather()
            case _:
                return ToolResult(output=json.dumps({"error": f"unknown_tool:{tool_name}"}))

    async def _handle_get_news(self, *, query: str | None, category: str | None) -> ToolResult:
        assert self._news is not None
        assert self._weather is not None
        assert self._logger is not None

        # If the LLM asks for the "weather" category, route to the weather
        # tool's response shape — fewer surprises for the model than mixing
        # weather into the items list.
        if category == "weather":
            return await self._handle_get_weather()

        cache_key = f"news:{query or ''}:{category or ''}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            await self._logger.ainfo(
                "news.cache_hit",
                key=cache_key,
                items=len(cached.get("items", [])),
            )
            return self._success_result(cached)

        try:
            weather_data = await self._weather.fetch()
            items = await self._news.fetch(
                query=query,
                category=category,
                max_items=self._max_items,
                max_age_hours=self._max_age_hours,
            )
        except HttpError as exc:
            await self._logger.awarning("news.fetch_failed", url=exc.url, reason=exc.reason)
            return ToolResult(
                output=json.dumps(
                    {
                        "error": "fetch_failed",
                        "reason": exc.reason,
                        "retry_after_seconds": 60,
                    }
                )
            )

        from datetime import UTC, datetime

        now = datetime.now(UTC)
        payload = {
            "location": self._location_name,
            "fetched_at": now.isoformat(),
            "weather": weather_data,
            "interests": self._interests,
            "filter": {"query": query, "category": category},
            "items": [item.to_dict(now) for item in items],
            "item_count": len(items),
        }
        self._cache_set(cache_key, payload)
        await self._logger.ainfo(
            "news.fetched",
            query=query,
            category=category,
            items=len(items),
        )
        return self._success_result(payload)

    async def _handle_get_weather(self) -> ToolResult:
        assert self._weather is not None
        assert self._logger is not None

        cache_key = "weather"
        cached = self._cache_get(cache_key)
        if cached is not None:
            await self._logger.ainfo("news.cache_hit", key=cache_key)
            return self._success_result(cached)

        try:
            weather_data = await self._weather.fetch()
        except HttpError as exc:
            await self._logger.awarning("news.fetch_failed", url=exc.url, reason=exc.reason)
            return ToolResult(
                output=json.dumps(
                    {
                        "error": "fetch_failed",
                        "reason": exc.reason,
                        "retry_after_seconds": 60,
                    }
                )
            )
        from datetime import UTC, datetime

        payload = {
            "location": self._location_name,
            "fetched_at": datetime.now(UTC).isoformat(),
            "weather": weather_data,
        }
        self._cache_set(cache_key, payload)
        await self._logger.ainfo("news.weather_fetched")
        return self._success_result(payload)

    def _success_result(self, payload: dict[str, Any]) -> ToolResult:
        chime = self._sounds.get(self._start_sound_role) if self._start_sound_role else None
        side_effect = PlaySound(pcm=chime) if chime else None
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            side_effect=side_effect,
        )

    # --- Cache ---

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, payload = entry
        if _now_seconds() - cached_at > self._cache_ttl_seconds:
            del self._cache[key]
            return None
        return payload

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        self._cache[key] = (_now_seconds(), payload)
