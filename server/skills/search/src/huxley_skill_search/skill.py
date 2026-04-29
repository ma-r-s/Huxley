"""Web-search skill — persona-agnostic.

One tool, `search_the_web(query, max_results)`. The LLM calls it for
current/live information it doesn't already know (today's weather,
sports results, recent events, prices). The skill returns up to N
ranked hits with pre-extracted source hostnames; the LLM narrates the
answer in its persona's voice.

Failure paths (empty results, rate-limited, timeout, error) all carry
a `say_to_user` field with a constructive recovery line — the
`never_say_no` constraint is enforced at the contract, not at the
prompt. The tool description tells the LLM to speak `say_to_user`
verbatim-translated when present.

Configuration (persona's `skills.search` block):
- `safesearch` (off | moderate | strict) — defaults to `"moderate"`.
  A child-safe persona sets `"strict"`. Translates directly to ddgs's
  safesearch parameter.
- `start_sound` (sound palette role, e.g. `"search_start"`) — omit
  for no chime. Plays only on successful results, never on errors.
- `sounds_path` (relative to persona data_dir, default `"sounds"`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
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
from huxley_skill_search.provider import (
    DuckDuckGoProvider,
    SearchProvider,
    SearchProviderError,
    SearchRateLimitedError,
    SearchTimeoutError,
)

# Skill internals — not configurable from persona.yaml. Cache TTL,
# snippet length, circuit-breaker thresholds are mechanical knobs the
# deployer should not need to tune. If a credible reason to vary them
# emerges, expose them then.
_CACHE_TTL_SECONDS = 300
_SNIPPET_MAX_CHARS = 280
_MAX_RESULTS_CAP = 5
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_OPEN_DURATION_S = 60.0
_VALID_SAFESEARCH = ("off", "moderate", "strict")

_URL_RE = re.compile(r"https?://\S+")
_WHITESPACE_RE = re.compile(r"\s+")


# Recovery messages: skill-mechanical (not persona-flavored), so they
# ship with the skill instead of being persona-overridable. Spanish,
# English, French — same set the persona system_prompt supports.
_RECOVERY_MESSAGES: dict[str, dict[str, str]] = {
    "es": {
        "empty": ("No he encontrado nada sobre eso. ¿Quieres que pruebe con otras palabras?"),
        "rate_limited": ("Ahora mismo no puedo buscar. Dame un momento e intenta de nuevo."),
        "timeout": ("La búsqueda tardó demasiado. ¿Lo intentamos de nuevo?"),
        "error": ("Algo no fue bien con la búsqueda. Inténtalo otra vez en un momento."),
    },
    "en": {
        "empty": ("I didn't find anything on that. Want me to try other words?"),
        "rate_limited": ("I can't search right now. Give me a moment and try again."),
        "timeout": "The search took too long. Want me to try again?",
        "error": ("Something went wrong with the search. Try once more in a moment."),
    },
    "fr": {
        "empty": ("Je n'ai rien trouvé là-dessus. Veux-tu que j'essaie avec d'autres mots ?"),
        "rate_limited": (
            "Je ne peux pas chercher pour le moment. Laisse-moi un instant et réessaie."
        ),
        "timeout": "La recherche a pris trop de temps. On réessaie ?",
        "error": ("Quelque chose n'a pas marché avec la recherche. Réessaie dans un instant."),
    },
}


def _now_seconds() -> float:
    return time.monotonic()


def _query_hash(query: str) -> str:
    """Short SHA hash of a query for INFO-level logs.

    Search queries leak intent ("síntomas...", "abogados divorcio...")
    so the hash + length goes to info; the full query stays at debug.
    """
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]


def _clean_snippet(raw: str) -> str:
    """Strip URLs, collapse whitespace, truncate at word boundary."""
    text = _URL_RE.sub("", raw or "").strip()
    text = _WHITESPACE_RE.sub(" ", text)
    if len(text) <= _SNIPPET_MAX_CHARS:
        return text
    truncated = text[:_SNIPPET_MAX_CHARS].rsplit(" ", 1)[0]
    return truncated + "..."


class SearchSkill:
    """Open-web search tool. Single-tool skill, info-only.

    Stateful across calls only for the in-memory TTL cache and the
    consecutive-failure circuit breaker. Both are best-effort: the
    cache speeds up repeated queries, the breaker spares the user from
    sitting through a 4-second timeout on every query during a DDG
    outage.
    """

    def __init__(self, *, provider: SearchProvider | None = None) -> None:
        # `provider` is keyword-only and reserved for tests that inject
        # a fake. Production setup() builds a `DuckDuckGoProvider`.
        self._provider: SearchProvider | None = provider
        self._logger: SkillLogger | None = None
        self._safesearch: str = "moderate"
        self._start_sound_role: str | None = None
        self._sounds: dict[str, bytes] = {}
        # UI language drives tool description + recovery message text.
        # The framework calls reconfigure() on every session connect, so
        # this seeds the first description before a client's choice
        # flows through.
        self._ui_language_code: str = "en"
        # Cache: (query_lower, max_results) -> (cached_at, payload)
        self._cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        # Circuit breaker: counts consecutive failures (rate-limit /
        # timeout / error). When the count crosses the threshold, the
        # circuit opens for `_CIRCUIT_OPEN_DURATION_S` and any query
        # short-circuits to a rate-limited recovery message — sparing
        # the user a 4s hang per query during a DDG outage.
        self._circuit_failures: int = 0
        self._circuit_open_until: float = 0.0

    @property
    def name(self) -> str:
        return "search"

    @property
    def tools(self) -> list[ToolDefinition]:
        code = self._ui_language_code.lower()
        if code.startswith("es"):
            return self._tools_es()
        if code.startswith("fr"):
            return self._tools_fr()
        return self._tools_en()

    def _tools_es(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_the_web",
                description=(
                    "Búsqueda web abierta para informacion ACTUAL o RECIENTE: "
                    "tiempo de hoy, resultados deportivos, que ha pasado, "
                    "precios, eventos del dia, datos recientes que cambian. "
                    "NO la uses para hechos estables que ya sabes "
                    "(capitales, fechas historicas, definiciones), NI para "
                    "resumir las noticias del dia (eso es `get_news`). "
                    "ANTES de llamarla, di brevemente 'a ver, dejame buscar' "
                    "o 'un momento' para que el usuario sepa que estas "
                    "trabajando. Si la respuesta incluye `say_to_user`, "
                    "dilo al usuario en sus propias palabras y termina con "
                    "la pregunta del mensaje. Cita las fuentes por nombre "
                    "(por ejemplo 'segun El Pais') sin leer la URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Lo que el usuario quiere buscar, en sus propias palabras."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_RESULTS_CAP,
                            "description": (
                                "Numero de resultados (1 a 5). Por defecto "
                                "deja que devuelva varios (3-5) — un solo "
                                "resultado suele ser debil y te quedas "
                                "sin nada que contar. Solo pide 1 o 2 si "
                                "el usuario pide explicitamente una "
                                "respuesta rapida y corta."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    def _tools_fr(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_the_web",
                description=(
                    "Recherche web ouverte pour des informations ACTUELLES "
                    "ou RECENTES : meteo du jour, resultats sportifs, "
                    "ce qui s'est passe, prix, evenements du jour, donnees "
                    "recentes qui changent. N'utilise PAS pour des faits "
                    "stables que tu connais deja (capitales, dates "
                    "historiques, definitions), NI pour resumer les "
                    "actualites du jour (c'est `get_news`). AVANT de "
                    "l'appeler, dis brievement 'voyons voir' ou 'un instant' "
                    "pour que l'utilisateur sache que tu travailles. Si la "
                    "reponse contient `say_to_user`, dis-le a l'utilisateur "
                    "dans tes propres mots et termine par la question du "
                    "message. Cite les sources par nom (par ex. 'selon Le "
                    "Monde') sans lire l'URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Ce que l'utilisateur cherche, dans ses propres mots."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_RESULTS_CAP,
                            "description": (
                                "Nombre de resultats (1 a 5). Par defaut "
                                "laisse renvoyer plusieurs (3-5) — un seul "
                                "resultat est souvent faible et tu te "
                                "retrouves sans rien a raconter. Ne demande "
                                "1 ou 2 que si l'utilisateur demande "
                                "explicitement une reponse rapide et courte."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    def _tools_en(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_the_web",
                description=(
                    "Open web search for CURRENT or RECENT information: "
                    "today's weather, sports results, what just happened, "
                    "prices, events of the day, recent data that changes. "
                    "Do NOT use for stable facts you already know "
                    "(capitals, historical dates, definitions), NOR for "
                    "digesting today's news (that's `get_news`). BEFORE "
                    "calling, briefly say something like 'let me check' or "
                    "'one moment' so the user knows you heard them. If the "
                    "response includes `say_to_user`, say it to the user "
                    "in your own words and end with the message's question. "
                    "Cite sources by name (e.g. 'according to the BBC') "
                    "without reading the URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": ("What the user wants to look up, in their own words."),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_RESULTS_CAP,
                            "description": (
                                "How many results to return (1 to 5). By "
                                "default let it return several (3-5) — a "
                                "single result is often weak and you end up "
                                "with nothing to narrate. Only ask for 1 or "
                                "2 if the user explicitly asks for a quick "
                                "short answer."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        cfg = ctx.config
        self._logger = ctx.logger

        safesearch = str(cfg.get("safesearch", "moderate")).lower()
        if safesearch not in _VALID_SAFESEARCH:
            raise ValueError(
                f"search skill: invalid safesearch={safesearch!r}. "
                f"Must be one of {_VALID_SAFESEARCH}."
            )
        self._safesearch = safesearch

        # Sound palette — only loaded if persona configured a start_sound
        # role. Missing role / missing file = no chime, no error
        # (persona is opting out).
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
                    "search.start_sound_missing",
                    role=self._start_sound_role,
                    path=str(sounds_dir),
                )

        if self._provider is None:
            self._provider = DuckDuckGoProvider()

        self._ui_language_code = ctx.language

        await ctx.logger.ainfo(
            "search.setup_complete",
            safesearch=self._safesearch,
            ui_language=self._ui_language_code,
            chime=self._start_sound_role if self._sounds else None,
        )

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Pick up the session's UI language on each connect.

        Tool descriptions and recovery messages are computed per-call
        from `self._ui_language_code`, so the next dispatch sees the
        new language with no cache to bust.
        """
        self._ui_language_code = ctx.language
        await ctx.logger.ainfo(
            "search.reconfigure",
            ui_language=self._ui_language_code,
        )

    async def teardown(self) -> None:
        # Provider has no resources to release today — DuckDuckGoProvider
        # uses ddgs's `with DDGS() as client` per call, so there's no
        # long-lived HTTP session. Method exists to satisfy the Skill
        # protocol's lifecycle shape.
        return None

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name != "search_the_web":
            return ToolResult(output=json.dumps({"error": f"unknown_tool:{tool_name}"}))
        return await self._handle_search(
            query=str(args.get("query") or "").strip(),
            max_results=args.get("max_results"),
        )

    async def _handle_search(
        self,
        *,
        query: str,
        max_results: Any,
    ) -> ToolResult:
        assert self._logger is not None
        assert self._provider is not None

        # Empty query — caller bug, not a search outcome. Surface as a
        # recovery message so the LLM can ask the user to repeat.
        if not query:
            await self._logger.awarning("search.empty_query")
            return self._failure_result("empty")

        # Clamp max_results to [1, _MAX_RESULTS_CAP]. Default to the
        # cap when the LLM omits it — smoke testing showed models
        # economize too aggressively on this field (max_results=1 →
        # one weak hit → "no encontré información"). Better to ship
        # the full 5 and let the model narrate the best ones.
        try:
            requested = int(max_results) if max_results is not None else _MAX_RESULTS_CAP
        except (TypeError, ValueError):
            requested = _MAX_RESULTS_CAP
        clamped = max(1, min(_MAX_RESULTS_CAP, requested))

        await self._logger.ainfo(
            "search.dispatch",
            query_hash=_query_hash(query),
            query_len=len(query),
            max_results=clamped,
        )
        await self._logger.adebug("search.dispatch_full", query=query)

        # Short queries deserve a log signal but no filter — AbuelOS
        # users mumble; "Madrid?" is a real query and the LLM can ask
        # for clarification through `confirm_if_unclear` if needed.
        if len(query) < 3:
            await self._logger.ainfo("search.short_query", query_len=len(query))

        cache_key = (query.lower(), clamped)
        cached = self._cache_get(cache_key)
        if cached is not None:
            await self._logger.ainfo(
                "search.cache_hit",
                query_hash=_query_hash(query),
                hits=len(cached.get("results", [])),
            )
            return self._success_result(cached)

        # Circuit breaker: if open, short-circuit without hitting DDG.
        # Spares the user a 4-second hang per query during an outage.
        if _now_seconds() < self._circuit_open_until:
            await self._logger.ainfo(
                "search.circuit_blocked",
                query_hash=_query_hash(query),
                seconds_until_close=round(self._circuit_open_until - _now_seconds(), 1),
            )
            return self._failure_result("rate_limited")

        try:
            response = await self._provider.search(
                query,
                max_results=clamped,
                safesearch=self._safesearch,
            )
        except SearchRateLimitedError as exc:
            await self._logger.awarning(
                "search.rate_limited",
                query_hash=_query_hash(query),
                reason=str(exc),
            )
            await self._record_failure()
            return self._failure_result("rate_limited")
        except SearchTimeoutError as exc:
            await self._logger.awarning(
                "search.timeout",
                query_hash=_query_hash(query),
                reason=str(exc),
            )
            await self._record_failure()
            return self._failure_result("timeout")
        except SearchProviderError as exc:
            await self._logger.aexception(
                "search.error",
                query_hash=_query_hash(query),
                exception_type=type(exc).__name__,
            )
            await self._record_failure()
            return self._failure_result("error")
        except asyncio.CancelledError:
            # Don't swallow cancellation — let it propagate so the
            # turn coordinator's cancellation semantics work.
            raise

        # Provider returned cleanly. Reset failure count.
        if self._circuit_failures > 0:
            self._circuit_failures = 0

        if not response.hits:
            await self._logger.ainfo(
                "search.empty",
                query_hash=_query_hash(query),
            )
            return self._failure_result("empty")

        results_payload = [
            {
                "title": hit.title,
                "source": hit.source,
                "url": hit.url,
                "snippet": _clean_snippet(hit.snippet),
            }
            for hit in response.hits
        ]
        payload = {
            "result_count": len(results_payload),
            "results": results_payload,
            "say_to_user": None,
        }
        self._cache_set(cache_key, payload)

        top_domains = [r["source"] for r in results_payload[:3] if r["source"]]
        await self._logger.ainfo(
            "search.results",
            query_hash=_query_hash(query),
            count=len(results_payload),
            top_domains=top_domains,
        )
        return self._success_result(payload)

    # --- Result builders ---

    def _success_result(self, payload: dict[str, Any]) -> ToolResult:
        chime = self._sounds.get(self._start_sound_role) if self._start_sound_role else None
        side_effect = PlaySound(pcm=chime) if chime else None
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            side_effect=side_effect,
        )

    def _failure_result(self, kind: str) -> ToolResult:
        """Build a `say_to_user`-bearing result for the LLM to relay.

        No chime — the chime signals "I found something." Failure paths
        rely entirely on `say_to_user` for the recovery line.
        """
        message = self._recovery_message(kind)
        payload = {
            "result_count": 0,
            "results": [],
            "say_to_user": message,
        }
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))

    def _recovery_message(self, kind: str) -> str:
        code = self._ui_language_code.lower()
        if code.startswith("es"):
            lang = "es"
        elif code.startswith("fr"):
            lang = "fr"
        else:
            lang = "en"
        return _RECOVERY_MESSAGES[lang][kind]

    # --- Cache ---

    def _cache_get(self, key: tuple[str, int]) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, payload = entry
        if _now_seconds() - cached_at > _CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return payload

    def _cache_set(self, key: tuple[str, int], payload: dict[str, Any]) -> None:
        self._cache[key] = (_now_seconds(), payload)

    # --- Circuit breaker ---

    async def _record_failure(self) -> None:
        """Increment the failure count, open the circuit if threshold met.

        Called from every failure path (rate-limit, timeout, generic
        error) so any failure mode counts toward opening the breaker.
        """
        self._circuit_failures += 1
        if self._circuit_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_open_until = _now_seconds() + _CIRCUIT_OPEN_DURATION_S
            self._circuit_failures = 0
            if self._logger is not None:
                await self._logger.ainfo(
                    "search.circuit_opened",
                    duration_s=_CIRCUIT_OPEN_DURATION_S,
                )
