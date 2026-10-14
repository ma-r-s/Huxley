"""Web-search skill, persona-agnostic.

One tool, `search_the_web(query, max_results)`. The LLM calls it for
anything that may have changed since its training (who holds an office,
results, prices, events, schedules, a person's status). The skill
returns ranked hits with source names, dates when known, snippets, a
trimmed excerpt of the best pages, and fresh headlines when the question
is news; the LLM answers in its persona's voice.

One call, in order (T1.48):
1. Web search through `ddgs`, one backend after another inside an 8 s
   deadline (provider.py), in parallel with a Google News lookup for
   fresh headlines (headlines.py).
2. Gentle re-rank: the persona's preferred domains up, social/video
   sites down.
3. The top pages fetched in parallel within 2.5 s and cut down to the
   paragraphs that share words with the question (pages.py).

Failure paths (empty results, rate-limited, timeout, error) all carry
a `say_to_user` field with a constructive recovery line: the
`never_say_no` constraint is enforced at the contract, not at the
prompt. The tool description tells the LLM to relay it.

Configuration (persona's `skills.search` block):
- `safesearch` (off | moderate | strict), default `"moderate"`.
- `region` (`<country>-<language>`, e.g. `"co-es"`), default `"wt-wt"`
  (no region, and no headlines lane).
- `timezone` (IANA name), default `"UTC"`: what `today` means.
- `preferred_domains` (list of hostnames): ranked up when present.
- `start_sound` (sound palette role, e.g. `"search_start"`), omit for
  no chime. Plays only on successful results, never on errors.
- `sounds_path` (relative to persona data_dir, default `"sounds"`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from huxley_sdk import (
    PlaySound,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.audio import load_pcm_palette
from huxley_skill_search.headlines import Headline, feed_url, parse_feed
from huxley_skill_search.pages import (
    HttpxPageFetcher,
    PageExcerpt,
    PageFetcher,
    PageFetchError,
    date_boost_terms,
    date_from_url,
    extract_page,
    iso_date,
    query_terms,
    today_iso,
    words,
)
from huxley_skill_search.provider import (
    DdgsProvider,
    SearchAttempt,
    SearchHit,
    SearchProvider,
    SearchProviderError,
    SearchRateLimitedError,
    SearchResponse,
    SearchTimeoutError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Skill internals, not configurable from persona.yaml. Cache TTL,
# snippet length, budgets and circuit-breaker thresholds are mechanical
# knobs the deployer should not need to tune.
_CACHE_TTL_SECONDS = 300
_SNIPPET_MAX_CHARS = 280
_MAX_RESULTS_CAP = 5
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_OPEN_DURATION_S = 60.0
_VALID_SAFESEARCH = ("off", "moderate", "strict")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]{2,3}$")

# Page excerpts: up to 3 candidates fetched at once, the best 2 kept.
# Measured on the Pi: most pages answer in 0.3-1.9 s, a few never do
# inside 2.5 s; a third candidate makes two excerpts likely.
_PAGE_BUDGET_S = 2.5
_PAGE_CANDIDATES = 3
_MAX_EXCERPTS = 2
_MIN_PAGE_BUDGET_S = 1.0
# The whole call stays under this: search (deadline 8 s) + pages.
_TOTAL_BUDGET_S = 10.0
_HEADLINES_TIMEOUT_S = 2.5
# News articles merged into the results: at most two, at most this old.
_MAX_NEWS_HITS = 2
_NEWS_MAX_AGE_DAYS = 3

# Never fetched for an excerpt (video, social, logins, Google's own
# redirect pages) and ranked last: their snippet is all a voice answer
# can use.
_LOW_VALUE_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "pinterest.com",
    "pinterest.es",
    "linkedin.com",
    "reddit.com",
    "news.google.com",
)
_PREFERRED_BOOST = 1.5  # ranks a preferred hit at most one or two places up
_LOW_VALUE_PENALTY = 10.0  # always after the others

_URL_RE = re.compile(r"https?://\S+")
_WHITESPACE_RE = re.compile(r"\s+")


# Recovery messages: skill-mechanical (not persona-flavored), so they
# ship with the skill instead of being persona-overridable.
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


def _domain_in(host: str, domains: Sequence[str]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _fetchable(hit: SearchHit) -> bool:
    if not hit.url.startswith(("http://", "https://")):
        return False
    if hit.url.lower().split("?", 1)[0].endswith(".pdf"):
        return False
    return not _domain_in(hit.source, _LOW_VALUE_DOMAINS)


def rank_hits(hits: list[SearchHit], preferred: Sequence[str]) -> list[SearchHit]:
    """Engine order, nudged: preferred domains up a little, low-value last.

    The engine's order is relevance; a preferred outlet moves up at most
    one place (a boost of 1.5 positions), so a reputable page that is
    much less relevant doesn't jump to the top.
    """

    def key(item: tuple[int, SearchHit]) -> float:
        idx, hit = item
        score = float(idx)
        if _domain_in(hit.source, _LOW_VALUE_DOMAINS):
            score += _LOW_VALUE_PENALTY
        elif preferred and _domain_in(hit.source, preferred):
            score -= _PREFERRED_BOOST
        return score

    return [hit for _, hit in sorted(enumerate(hits), key=key)]


def fresh_and_relevant(hit: SearchHit, query: str, today: date) -> bool:
    """A news hit worth showing: recent, and about the question.

    Fresh: the URL's date is within `_NEWS_MAX_AGE_DAYS`, or (no date in
    the URL) the engine's date is. The engine's date is an upper bound
    on age at best: ddgs reads Bing's "19 h" as 19 days, so an old-looking
    date may be fresh, but a recent one is never stale. Relevant: the
    title shares two of the query's words (one for a one-word query).
    """
    url_date = date_from_url(hit.url)
    stamp = url_date or iso_date(hit.published or "")
    if stamp is None:
        return False
    if (today - date.fromisoformat(stamp)).days > _NEWS_MAX_AGE_DAYS:
        return False
    terms = query_terms(query)
    need = min(2, len(terms))
    return need > 0 and len(terms & words(hit.title)) >= need


def merge_hits(web: list[SearchHit], news: list[SearchHit], limit: int) -> list[SearchHit]:
    """Fresh news first, then alternate: news, web, news, web, web...

    So the top three (the ones whose pages get excerpts) hold both the
    newest article and the best web page. Duplicates by URL are dropped.
    """
    web_urls = {h.url for h in web}
    news = [n for n in news if n.url not in web_urls][:_MAX_NEWS_HITS]
    merged = news[:1] + web[:1] + news[1:2] + web[1:]
    return merged[:limit]


def _normalize_domain(raw: object) -> str:
    text = str(raw).strip().lower()
    text = re.sub(r"^https?://", "", text).split("/", 1)[0]
    return text.removeprefix("www.")


class SearchSkill:
    """Open-web search tool. Single-tool skill, info-only.

    Stateful across calls only for the in-memory TTL cache and the
    consecutive-failure circuit breaker. Both are best-effort: the
    cache speeds up repeated queries, the breaker spares the user from
    sitting through a full deadline on every query during an outage.
    """

    # T1.14: JSON Schema for the user-tunable config fields so v2's PWA
    # Skills panel can render a form. `start_sound`, `sounds_path`,
    # `timezone` and `preferred_domains` are persona-author fields and
    # stay un-schemaed (v2 falls back to "edit YAML directly").
    config_schema: ClassVar[dict[str, Any] | None] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "safesearch": {
                "type": "string",
                "enum": ["off", "moderate", "strict"],
                "default": "moderate",
                "title": "Safe search",
                "x-huxley:help": (
                    "How aggressively to filter explicit results. "
                    "`moderate` is the DuckDuckGo default."
                ),
            },
            "region": {
                "type": "string",
                "pattern": "^[a-z]{2}-[a-z]{2,3}$",
                "default": "wt-wt",
                "title": "Region",
                "x-huxley:help": (
                    "Country and language of results, like `co-es` or `us-en`. "
                    "`wt-wt` means no region (and no news headlines)."
                ),
            },
        },
    }

    # No persisted state (the in-memory TTL cache vanishes at restart).
    data_schema_version: ClassVar[int] = 1

    def __init__(
        self,
        *,
        provider: SearchProvider | None = None,
        fetcher: PageFetcher | None = None,
    ) -> None:
        # `provider` and `fetcher` are keyword-only and reserved for tests
        # that inject fakes. Production setup() builds the real ones.
        self._provider: SearchProvider | None = provider
        self._fetcher: PageFetcher | None = fetcher
        self._owned_fetcher: HttpxPageFetcher | None = None
        self._logger: SkillLogger | None = None
        self._safesearch: str = "moderate"
        self._region: str = "wt-wt"
        self._timezone: str = "UTC"
        self._preferred: tuple[str, ...] = ()
        self._start_sound_role: str | None = None
        self._sounds: dict[str, bytes] = {}
        # UI language drives tool description + recovery message text.
        self._ui_language_code: str = "en"
        # Cache: (query_lower, max_results) -> (cached_at, payload)
        self._cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        # Circuit breaker: consecutive web-search failures. At the
        # threshold the circuit opens for `_CIRCUIT_OPEN_DURATION_S` and
        # queries short-circuit to the rate-limited recovery message.
        self._circuit_failures: int = 0
        self._circuit_open_until: float = 0.0

    @property
    def name(self) -> str:
        return "search"

    @property
    def tools(self) -> list[ToolDefinition]:
        code = self._ui_language_code.lower()
        if code.startswith("es"):
            return [self._tool(_DESCRIPTION_ES, _QUERY_ES, _MAX_RESULTS_ES)]
        if code.startswith("fr"):
            return [self._tool(_DESCRIPTION_FR, _QUERY_FR, _MAX_RESULTS_FR)]
        return [self._tool(_DESCRIPTION_EN, _QUERY_EN, _MAX_RESULTS_EN)]

    @staticmethod
    def _tool(description: str, query_help: str, max_results_help: str) -> ToolDefinition:
        return ToolDefinition(
            name="search_the_web",
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": query_help},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_RESULTS_CAP,
                        "description": max_results_help,
                    },
                },
                "required": ["query"],
            },
        )

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

        region = str(cfg.get("region", "wt-wt")).lower()
        if not _REGION_RE.match(region):
            raise ValueError(
                f"search skill: invalid region={region!r}. "
                "Use '<country>-<language>' like 'co-es', or 'wt-wt' for none."
            )
        self._region = region
        self._timezone = str(cfg.get("timezone", "UTC"))
        raw_preferred = cfg.get("preferred_domains") or []
        if not isinstance(raw_preferred, list):
            raise ValueError("search skill: preferred_domains must be a list of hostnames.")
        self._preferred = tuple(d for d in (_normalize_domain(x) for x in raw_preferred) if d)

        # Sound palette: only loaded if the persona configured a
        # start_sound role. Missing role / missing file = no chime.
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
            self._provider = DdgsProvider()
        if self._fetcher is None:
            self._owned_fetcher = HttpxPageFetcher()
            self._fetcher = self._owned_fetcher

        self._ui_language_code = ctx.language

        await ctx.logger.ainfo(
            "search.setup_complete",
            safesearch=self._safesearch,
            region=self._region,
            timezone=self._timezone,
            preferred_domains=len(self._preferred),
            ui_language=self._ui_language_code,
            chime=self._start_sound_role if self._sounds else None,
        )

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Pick up the session's UI language on each connect."""
        self._ui_language_code = ctx.language
        await ctx.logger.ainfo(
            "search.reconfigure",
            ui_language=self._ui_language_code,
        )

    async def teardown(self) -> None:
        # Close the shared HTTP client we built (never an injected one).
        # Must be safe when setup() never ran.
        if self._owned_fetcher is not None:
            await self._owned_fetcher.aclose()
            self._owned_fetcher = None

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

        if not query:
            await self._logger.awarning("search.empty_query")
            return self._failure_result("empty")

        # Clamp max_results to [1, _MAX_RESULTS_CAP]; default to the cap
        # (models economize too aggressively on this field).
        try:
            requested = int(max_results) if max_results is not None else _MAX_RESULTS_CAP
        except (TypeError, ValueError):
            requested = _MAX_RESULTS_CAP
        clamped = max(1, min(_MAX_RESULTS_CAP, requested))
        qh = _query_hash(query)

        await self._logger.ainfo(
            "search.dispatch",
            query_hash=qh,
            query_len=len(query),
            max_results=clamped,
        )
        await self._logger.adebug("search.dispatch_full", query=query)

        if len(query) < 3:
            await self._logger.ainfo("search.short_query", query_len=len(query))

        cache_key = (query.lower(), clamped)
        cached = self._cache_get(cache_key)
        if cached is not None:
            await self._logger.ainfo(
                "search.cache_hit",
                query_hash=qh,
                hits=len(cached.get("results", [])),
            )
            return self._success_result(cached)

        if _now_seconds() < self._circuit_open_until:
            await self._logger.ainfo(
                "search.circuit_blocked",
                query_hash=qh,
                seconds_until_close=round(self._circuit_open_until - _now_seconds(), 1),
            )
            return self._failure_result("rate_limited")

        started = _now_seconds()
        headlines_task = asyncio.create_task(self._headlines(query, qh))
        news_task = asyncio.create_task(self._news_hits(query, qh, clamped))
        response: SearchResponse | None = None
        search_error: SearchProviderError | None = None
        try:
            response = await self._provider.search(
                query,
                max_results=clamped,
                safesearch=self._safesearch,
                region=self._region,
            )
        except SearchProviderError as exc:
            search_error = exc
        except asyncio.CancelledError:
            # Don't swallow cancellation; the turn coordinator relies on it.
            headlines_task.cancel()
            news_task.cancel()
            raise
        if response is not None:
            attempts = response.attempts
        else:
            assert search_error is not None
            attempts = search_error.attempts
        for attempt in attempts:
            await self._log_attempt(qh, attempt)
        try:
            headlines = await headlines_task
            news_hits = await news_task
        except asyncio.CancelledError:
            headlines_task.cancel()
            news_task.cancel()
            raise

        web_hits: list[SearchHit] = []
        backend = ""
        if search_error is not None:
            await self._record_failure()
            kind = self._failure_kind(search_error)
            if not headlines and not news_hits:
                await self._log_failure(qh, kind, search_error, started)
                return self._failure_result(kind)
            await self._logger.ainfo(
                "search.web_failed_news_only",
                query_hash=qh,
                kind=kind,
                headlines=len(headlines),
                news=len(news_hits),
            )
        else:
            assert response is not None
            self._circuit_failures = 0
            web_hits = response.hits
            backend = response.backend
            if not web_hits and not headlines and not news_hits:
                await self._logger.ainfo(
                    "search.empty",
                    query_hash=qh,
                    latency_ms=int((_now_seconds() - started) * 1000),
                    attempts=_attempt_summary(attempts),
                )
                return self._failure_result("empty")

        ranked = merge_hits(rank_hits(web_hits, self._preferred), news_hits, clamped)
        remaining = _TOTAL_BUDGET_S - (_now_seconds() - started)
        today = date.fromisoformat(today_iso(self._timezone))
        excerpts = await self._excerpts(
            query,
            qh,
            ranked,
            min(_PAGE_BUDGET_S, remaining),
            boost=date_boost_terms(query, today),
        )

        results_payload: list[dict[str, Any]] = []
        for idx, hit in enumerate(ranked):
            item: dict[str, Any] = {"title": hit.title, "source": hit.source}
            excerpt = excerpts.get(idx)
            when = (excerpt.date if excerpt else None) or date_from_url(hit.url)
            if when:
                item["date"] = when
            item["snippet"] = _clean_snippet(hit.snippet)
            if excerpt is not None:
                item["excerpt"] = excerpt.text
            results_payload.append(item)

        payload: dict[str, Any] = {
            "today": today.isoformat(),
            "result_count": len(results_payload),
        }
        if headlines:
            payload["headlines"] = [h.to_dict() for h in headlines]
        payload["results"] = results_payload
        payload["say_to_user"] = None
        self._cache_set(cache_key, payload)

        await self._logger.ainfo(
            "search.results",
            query_hash=qh,
            count=len(results_payload),
            backend=backend,
            latency_ms=int((_now_seconds() - started) * 1000),
            excerpts=len(excerpts),
            headlines=len(headlines),
            news=len(news_hits),
            top_domains=[r["source"] for r in results_payload[:3] if r["source"]],
            output_chars=len(json.dumps(payload, ensure_ascii=False)),
        )
        return self._success_result(payload)

    # --- Lanes ---

    async def _headlines(self, query: str, qh: str) -> list[Headline]:
        """Fresh matching headlines, or [] on any failure. Never raises
        (except cancellation)."""
        assert self._logger is not None
        url = feed_url(query, self._region)
        if url is None or self._fetcher is None:
            return []
        t0 = _now_seconds()
        try:
            body = await asyncio.wait_for(
                self._fetcher.fetch(url, timeout_s=_HEADLINES_TIMEOUT_S),
                timeout=_HEADLINES_TIMEOUT_S,
            )
            found = await asyncio.to_thread(parse_feed, body, query)
        except TimeoutError:
            await self._logger.ainfo(
                "search.headlines",
                query_hash=qh,
                outcome="timeout",
                latency_ms=int((_now_seconds() - t0) * 1000),
            )
            return []
        except PageFetchError as exc:
            await self._logger.ainfo(
                "search.headlines",
                query_hash=qh,
                outcome="error",
                reason=exc.reason,
                latency_ms=int((_now_seconds() - t0) * 1000),
            )
            return []
        await self._logger.ainfo(
            "search.headlines",
            query_hash=qh,
            outcome="ok" if found else "none",
            count=len(found),
            latency_ms=int((_now_seconds() - t0) * 1000),
        )
        return found

    async def _news_hits(self, query: str, qh: str, limit: int) -> list[SearchHit]:
        """Fresh news articles that match the query, at most `_MAX_NEWS_HITS`.

        Web indexes lag by days on news; a news search finds today's
        article (with a URL, so it can be excerpted). Never raises
        (except cancellation): the provider's `news` doesn't.
        """
        assert self._logger is not None
        assert self._provider is not None
        response = await self._provider.news(
            query, max_results=limit, safesearch=self._safesearch, region=self._region
        )
        today = date.fromisoformat(today_iso(self._timezone))
        kept = [hit for hit in response.hits if fresh_and_relevant(hit, query, today)]
        attempt = response.attempts[0] if response.attempts else None
        await self._logger.ainfo(
            "search.news",
            query_hash=qh,
            outcome=attempt.outcome if attempt else "none",
            error_class=attempt.error_class if attempt else None,
            count=len(response.hits),
            kept=min(len(kept), _MAX_NEWS_HITS),
            latency_ms=attempt.latency_ms if attempt else 0,
        )
        return kept[:_MAX_NEWS_HITS]

    async def _excerpts(
        self,
        query: str,
        qh: str,
        ranked: list[SearchHit],
        budget_s: float,
        boost: frozenset[str] = frozenset(),
    ) -> dict[int, PageExcerpt]:
        """Excerpts for the best pages, keyed by position in `ranked`.

        Up to `_PAGE_CANDIDATES` pages are fetched at once; whatever is
        not back inside `budget_s` is cancelled. At most `_MAX_EXCERPTS`
        are kept, best-ranked first, and only those with text.
        """
        assert self._logger is not None
        fetcher = self._fetcher
        candidates = [(i, h) for i, h in enumerate(ranked) if _fetchable(h)][:_PAGE_CANDIDATES]
        if fetcher is None or not candidates:
            return {}
        if self._safesearch == "strict":
            # A child-safe persona: page text isn't filtered by anyone, so
            # it doesn't go to the model; snippets only.
            await self._logger.ainfo("search.pages_skipped", query_hash=qh, reason="strict")
            return {}
        if budget_s < _MIN_PAGE_BUDGET_S:
            await self._logger.ainfo(
                "search.pages_skipped",
                query_hash=qh,
                reason="budget",
                budget_s=round(budget_s, 2),
            )
            return {}

        async def one(rank: int, hit: SearchHit) -> PageExcerpt | None:
            t0 = _now_seconds()
            try:
                html = await fetcher.fetch(hit.url, timeout_s=budget_s)
                excerpt = await asyncio.to_thread(extract_page, html, query, boost=boost)
            except PageFetchError as exc:
                await self._log_page(qh, hit, rank, "error", t0, reason=exc.reason)
                return None
            outcome = "ok" if excerpt.text else "no_text"
            await self._log_page(qh, hit, rank, outcome, t0, chars=len(excerpt.text))
            return excerpt if excerpt.text else None

        t_start = _now_seconds()
        tasks = {asyncio.create_task(one(rank, hit)): (rank, hit) for rank, hit in candidates}
        try:
            done, pending = await asyncio.wait(tasks, timeout=budget_s)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        for task in pending:
            task.cancel()
            rank, hit = tasks[task]
            await self._log_page(qh, hit, rank, "timeout", t_start)
        found: dict[int, PageExcerpt] = {}
        for task in done:
            rank, _hit = tasks[task]
            excerpt = task.result()
            if excerpt is not None:
                found[rank] = excerpt
        best = sorted(found)[:_MAX_EXCERPTS]
        return {rank: found[rank] for rank in best}

    # --- Logging helpers ---

    async def _log_attempt(self, qh: str, attempt: SearchAttempt) -> None:
        assert self._logger is not None
        await self._logger.ainfo(
            "search.attempt",
            query_hash=qh,
            backend=attempt.backend,
            outcome=attempt.outcome,
            latency_ms=attempt.latency_ms,
            count=attempt.count,
            error_class=attempt.error_class,
        )

    async def _log_page(
        self,
        qh: str,
        hit: SearchHit,
        rank: int,
        outcome: str,
        t0: float,
        *,
        reason: str | None = None,
        chars: int = 0,
    ) -> None:
        assert self._logger is not None
        await self._logger.ainfo(
            "search.page",
            query_hash=qh,
            domain=hit.source,
            rank=rank,
            outcome=outcome,
            reason=reason,
            chars=chars,
            latency_ms=int((_now_seconds() - t0) * 1000),
        )

    async def _log_failure(
        self, qh: str, kind: str, exc: SearchProviderError, started: float
    ) -> None:
        assert self._logger is not None
        fields: dict[str, Any] = {
            "query_hash": qh,
            "reason": str(exc),
            "latency_ms": int((_now_seconds() - started) * 1000),
            "attempts": _attempt_summary(exc.attempts),
        }
        if kind == "rate_limited":
            await self._logger.awarning("search.rate_limited", **fields)
        elif kind == "timeout":
            await self._logger.awarning("search.timeout", **fields)
        else:
            await self._logger.awarning(
                "search.error", exception_type=type(exc).__name__, **fields
            )

    @staticmethod
    def _failure_kind(exc: SearchProviderError) -> str:
        if isinstance(exc, SearchRateLimitedError):
            return "rate_limited"
        if isinstance(exc, SearchTimeoutError):
            return "timeout"
        return "error"

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

        No chime: the chime signals "I found something."
        """
        payload = {
            "result_count": 0,
            "results": [],
            "say_to_user": self._recovery_message(kind),
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
        """Count a failed web search; open the circuit at the threshold."""
        self._circuit_failures += 1
        if self._circuit_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_open_until = _now_seconds() + _CIRCUIT_OPEN_DURATION_S
            self._circuit_failures = 0
            if self._logger is not None:
                await self._logger.ainfo(
                    "search.circuit_opened",
                    duration_s=_CIRCUIT_OPEN_DURATION_S,
                )


def _attempt_summary(attempts: Sequence[SearchAttempt]) -> str:
    return ",".join(f"{a.backend}:{a.outcome}:{a.latency_ms}ms" for a in attempts)


# --- LLM-facing text ---
#
# The description is the model's whole guide to when and how to search.
# The T1.48 change: search anything that may have changed since training
# (the model's memory is stale: in 2026 it still named the previous
# Colombian president), answer briefly from what came back, and say so
# when the results don't hold the answer.

_DESCRIPTION_ES = (
    "Busca en internet. Úsala para TODO lo que pudo cambiar desde que "
    "aprendiste, aunque creas saberlo: quién ocupa un cargo, resultados y "
    "partidos, precios y el dólar, el clima de otro lugar, lo que pasó, si "
    "alguien vive o murió, edades, horarios, programación, festivos y fechas "
    "de este año. No hace falta para lo que no cambia (una receta, una "
    "definición, historia) ni para las noticias del día de aquí (eso es "
    "`get_news`). ANTES de llamarla, di brevemente 'a ver, déjame buscar' o "
    "'un momento'. Devuelve `today` (la fecha de hoy), a veces `headlines` "
    "(titulares recientes, con su edad en horas) y `results` con fuente, "
    "fecha si se conoce, `snippet` y, en los mejores, `excerpt` (texto de la "
    "página). Responde en una o dos frases con el dato que se pidió y di la "
    "fuente por su nombre ('según El Tiempo'), sin leer direcciones web. "
    "Mira las fechas: si lo encontrado es viejo, dilo. Si los resultados no "
    "traen el dato o se contradicen, dilo con sencillez en vez de adivinar. "
    "Si la respuesta incluye `say_to_user`, díselo al usuario en tus propias "
    "palabras y termina con la pregunta del mensaje."
)
_QUERY_ES = (
    "Lo que se busca, como en un buscador: nombres concretos, el lugar y la "
    "fecha si importa (por ejemplo 'resultado Millonarios Once Caldas' o "
    "'precio del dólar hoy Colombia'). Un solo tema por búsqueda."
)
_MAX_RESULTS_ES = (
    "Número de resultados (1 a 5). Déjalo vacío: por defecto devuelve 5, y "
    "un solo resultado suele ser débil."
)

_DESCRIPTION_EN = (
    "Search the web. Use it for ANYTHING that may have changed since you "
    "were trained, even if you think you know it: who holds an office, "
    "results and matches, prices and exchange rates, the weather somewhere "
    "else, what happened, whether someone is alive or died, ages, opening "
    "hours, TV schedules, this year's holidays and dates. Not needed for "
    "what doesn't change (a recipe, a definition, history) nor for today's "
    "local news (that's `get_news`). BEFORE calling, briefly say 'let me "
    "check' or 'one moment'. Returns `today` (today's date), sometimes "
    "`headlines` (recent headlines with their age in hours) and `results` "
    "with source, date when known, `snippet` and, for the best ones, "
    "`excerpt` (text from the page). Answer in one or two sentences with "
    "the fact asked for and name the source ('according to the BBC'), "
    "without reading URLs. Check the dates: if what you found is old, say "
    "so. If the results don't hold the fact or disagree, say that plainly "
    "instead of guessing. If the response includes `say_to_user`, say it "
    "to the user in your own words and end with the message's question."
)
_QUERY_EN = (
    "What to look up, as you'd type it into a search engine: specific "
    "names, the place, and the date when it matters (e.g. 'Yankees score "
    "last night' or 'euro to dollar rate today'). One topic per search."
)
_MAX_RESULTS_EN = (
    "How many results (1 to 5). Leave it out: the default is 5, and a single result is often weak."
)

_DESCRIPTION_FR = (
    "Cherche sur internet. Utilise-la pour TOUT ce qui a pu changer depuis "
    "ton apprentissage, même si tu crois le savoir : qui occupe un poste, "
    "résultats et matchs, prix et taux de change, la météo d'un autre "
    "endroit, ce qui s'est passé, si quelqu'un est vivant ou décédé, âges, "
    "horaires, programmes télé, jours fériés et dates de cette année. Pas "
    "besoin pour ce qui ne change pas (une recette, une définition, "
    "l'histoire) ni pour les actualités locales du jour (c'est `get_news`). "
    "AVANT de l'appeler, dis brièvement 'voyons voir' ou 'un instant'. "
    "Renvoie `today` (la date du jour), parfois `headlines` (titres récents "
    "avec leur âge en heures) et `results` avec la source, la date si elle "
    "est connue, `snippet` et, pour les meilleurs, `excerpt` (texte de la "
    "page). Réponds en une ou deux phrases avec l'information demandée et "
    "cite la source par son nom ('selon Le Monde'), sans lire d'URL. "
    "Regarde les dates : si ce que tu as trouvé est ancien, dis-le. Si les "
    "résultats ne contiennent pas l'information ou se contredisent, dis-le "
    "simplement au lieu de deviner. Si la réponse contient `say_to_user`, "
    "dis-le à l'utilisateur dans tes propres mots et termine par la question "
    "du message."
)
_QUERY_FR = (
    "Ce qu'il faut chercher, comme dans un moteur de recherche : des noms "
    "précis, le lieu et la date si elle compte (par ex. 'résultat PSG "
    "Marseille' ou 'cours du dollar aujourd'hui'). Un seul sujet par "
    "recherche."
)
_MAX_RESULTS_FR = (
    "Nombre de résultats (1 à 5). Laisse vide : 5 par défaut, et un seul "
    "résultat est souvent faible."
)
