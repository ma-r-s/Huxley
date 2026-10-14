# Skill: search

Persona-agnostic open-web search. The LLM calls `search_the_web(query, max_results)` for anything that may have changed since its training: who holds an office, results, prices and exchange rates, the weather somewhere else, what happened, whether someone is alive, ages, schedules, this year's holidays. The model's memory is stale (in October 2026 it still named the previous Colombian president), so "I think I know it" is not a reason to skip the search.

The skill does **zero narration**. It returns data; the LLM decides how to speak it based on the persona's `system_prompt` + constraints.

## What it does

| Tool             | Args                                                  | Returns                                                       |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------------------- |
| `search_the_web` | `query: string`, `max_results?: int` (1-5, default 5) | `{ today, result_count, headlines?, results[], say_to_user }` |

One call, in order (T1.48):

1. **Web search** through [`ddgs`](https://pypi.org/project/ddgs/), one backend after another inside an **8 s deadline** (`provider.py`). In parallel, two news lanes: **Google News headlines** (`headlines.py`) and a **news-article search** (`DdgsProvider.news`).
2. **Merge and re-rank**: up to two fresh news articles go first (alternating with the web results, so the top three hold both the newest article and the best web page); the persona's `preferred_domains` move up one place; video and social sites go last.
3. **Page excerpts**: the top three fetchable pages are fetched in parallel within **2.5 s**; the best two that came back with text are cut down to the paragraphs that share words with the question (`pages.py`), at most **1500 characters** each.

The output, compact because a voice model reads it:

```json
{
  "today": "2026-10-13",
  "result_count": 5,
  "headlines": [
    {
      "title": "Fuerte temblor en Colombia este 13 de octubre: ...",
      "source": "El Tiempo",
      "age_hours": 2.1
    }
  ],
  "results": [
    {
      "title": "Pico y Placa en Villavicencio: ...",
      "source": "infobae.com",
      "date": "2026-10-13",
      "snippet": "...",
      "excerpt": "... Martes: 7 y 8. El Pico y Placa aplica de lunes a viernes ..."
    }
  ],
  "say_to_user": null
}
```

- `today` is the date in the persona's `timezone`, so the model can tell "yesterday's match" from a 2025 page.
- `headlines` (only when some match): up to 3 Google News headlines from the last 72 hours sharing at least two of the query's words (one for a one-word query), near copies removed. No URLs: they are the freshest signal, not something to fetch.
- `results[]`: `title`, `source` (hostname, pre-extracted so the LLM doesn't spell URLs), `date` when known (the page's own date, else a date in the URL), `snippet` (max 280 chars, URLs stripped) and, for at most two, `excerpt`. No `url` field: the model must never read one aloud.
- `say_to_user` is non-null **only** on failure paths. When set, the tool description tells the LLM to relay it: this is how the skill honors `never_say_no` at the contract level.

A web search that fails while a news lane found something is still a success (results from the news lanes only, logged as `search.web_failed_news_only`).

## Backends (measured, not assumed)

`ddgs` scrapes public result pages, and which engines answer depends on the day and the IP. Measured from the production Pi on 2026-10-13 (8 queries each, fresh client per query):

| backend                         | answered | latency                                  |
| ------------------------------- | -------- | ---------------------------------------- |
| `yandex`                        | 8/8      | about 1 s                                |
| `yahoo`                         | 3/8      | 0.5-1.4 s                                |
| `auto`                          | 8/8      | 1.2-2.4 s (Wikipedia first, then Yandex) |
| `duckduckgo`, `brave`, `mojeek` | 0/6      | blocked ("No results found.")            |

So the order is `("yandex", "yahoo", "auto")` (`DEFAULT_BACKENDS`). Each attempt gets at most 3 s and never starts with less than 1 s left; an empty or failed attempt hands the rest of the 8 s to the next. A fresh ddgs client per attempt: a reused one kept a refused Yahoo session's cookies and kept getting nothing. Warming the client at setup was measured and not worth it: `import ddgs` costs 0.03-0.1 s and a reused connection saved about 0.35 s on Yandex, less than the risk of a sticky refused session.

The news-article lane is one `ddgs.news(backend="auto")` attempt with a 3 s budget (Bing News answered from the Pi when DuckDuckGo and Yahoo News didn't). Its dates are approximate (ddgs reads Bing's "19 h" as 19 days), so an article counts as fresh when the date in its URL is at most 3 days old, or (no URL date) the engine's date is; only the URL date is ever shown.

`region` is passed to every engine, but Yandex, Yahoo and Bing News ignore it today; the query's language and the Pi's IP carry the locality. It does pick the Google News edition (`co-es` gives `gl=CO`, `hl=es`), and `wt-wt` turns the headlines lane off.

## Page excerpts

`pages.py` is a small readability-style extractor on `lxml` (already a `ddgs` dependency; BSD), not a full article extractor:

- drop `script`, `style`, `nav`, `header`, `footer`, `aside`, `form` and friends, and elements whose class/id says menu, share, related, cookie, ad, comments..., unless that element holds at least half of the page's text (real sites put `has-sidebar` on the content wrapper);
- narrow to `<article>` / `<main>` when present; read `p`, `li`, headings, table rows (cells joined with `|`), `dd`/`dt`; drop link lists (link density over 0.5), cookie/subscribe/bot-wall text, and repeats;
- choose: the lead block, then blocks by score (query words present, folded and plural-stripped; plus a heavy bonus for the weekday the user means when the query says "hoy", "mañana" or "ayer", since pages answer "Martes: 7 y 8", not "hoy"; plus 0.5 for a number), then fill from the top; put back in page order and cap at 1500 chars;
- the date: `article:published_time` and similar metas, JSON-LD `datePublished`, `<time datetime>`; a page published more than a year before its last edit (Wikipedia) reports the edit date.

Fetching: one shared `httpx.AsyncClient`, browser-like User-Agent and `Accept-Language: es-CO` (some Colombian sites answer a bare client with 403), HTML/XML only, body capped at 1.5 MB, parsing in a worker thread so the event loop (and the audio) never waits on it. Never fetched: YouTube, Facebook, Instagram, TikTok, X, Pinterest, LinkedIn, Reddit, `news.google.com` redirect pages, PDFs.

A child-safe persona (`safesearch: strict`) gets **no page excerpts**: page text isn't filtered by anyone (see Honest limitations).

## Configuration

Persona's `skills.search` block:

| Key                 | Required | Default      | Notes                                                                                                                                                              |
| ------------------- | -------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `safesearch`        | no       | `"moderate"` | `off` / `moderate` / `strict`. Mapped to ddgs's `off` / `moderate` / `on`. `strict` also turns off page excerpts.                                                  |
| `region`            | no       | `"wt-wt"`    | `<country>-<language>`, e.g. `co-es`. Picks the Google News edition; `wt-wt` means no region and no headlines.                                                     |
| `timezone`          | no       | `"UTC"`      | IANA name; what `today` means and which weekday "hoy" is.                                                                                                          |
| `preferred_domains` | no       | `[]`         | Hostnames ranked up one place when they appear (subdomains count). Never added, never forced.                                                                      |
| `start_sound`       | no       | _none_       | Sound palette role (e.g. `search_start`). Omit for no chime.                                                                                                       |
| `sounds_path`       | no       | `sounds`     | Sound palette directory (relative to persona `data_dir` or absolute). Personas typically point this at `../../_shared/sounds`; see [`../sounds.md`](../sounds.md). |

Skill internals (budgets, caps, backend order, cache TTL, breaker thresholds, the low-value domain list, recovery message text) are **not** persona-overridable. They're mechanical; if a deployer needs to vary one, expose it then.

## How Abuelo uses it

`region: co-es`, `timezone: America/Bogota`, and national Colombian outlets plus BBC/CNN en Español/Wikipedia as `preferred_domains`. The persona's BÚSQUEDA WEB rule: search anything that could have changed, even if it seems known; answer in one or two short sentences naming the source ("según El Tiempo"); check the dates and say when what was found is old; if the results don't hold the answer or disagree, say so instead of guessing; for health and medicines, general careful information and "confírmalo con el médico o el farmacéutico". Pre-narration ("a ver, déjame buscar") stays.

User says _"¿hay pico y placa hoy?"_ → LLM says _"a ver, déjame buscar"_ → chime → _"Hoy martes no circulan las placas terminadas en 7 y 8, según Infobae."_

## Measured quality (T1.48 eval)

`scripts/search_eval.py` runs 15 search questions plus 2 that belong to other tools, live, on the Pi, and checks each tool output for the expected fact. 2026-10-13, old code vs new: **6/15 → 13/15** search answers present, median latency 2.0 s → 2.1 s (max 3.9 → 3.8 s), median output 2.1 → 4.3 KB. Still missing: today's cinema listings (sites render them with JavaScript) and stomach-ache advice (the engines returned Pinterest/TikTok). Full table in [`docs/triage.md`](../triage.md) T1.48.

## Failure modes

Every failure carries a `say_to_user` line for `never_say_no` compliance; the tool description makes the LLM speak it.

| Failure                              | Detection                                             | Behavior                                                                                  |
| ------------------------------------ | ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Empty results everywhere             | every backend empty, no headline, no fresh article    | `say_to_user`: "No he encontrado nada sobre eso. ¿Quieres que pruebe con otras palabras?" |
| Rate-limited                         | any attempt rate-limited and nothing else answered    | `say_to_user`: "Ahora mismo no puedo buscar. Dame un momento e intenta de nuevo."         |
| Timeout                              | the 8 s deadline ran out (or every attempt timed out) | `say_to_user`: "La búsqueda tardó demasiado. ¿Lo intentamos de nuevo?"                    |
| Other error                          | any other provider failure                            | `say_to_user`: "Algo no fue bien con la búsqueda. Inténtalo otra vez en un momento."      |
| Web failed, news lanes found some    | provider raised, headlines or articles non-empty      | Success with what the news lanes found; counts toward the breaker.                        |
| A page fetch fails or is slow        | error, no text, or not back inside 2.5 s              | That result has no `excerpt`; logged per page.                                            |
| Empty / blank query                  | skill defends                                         | Same as "empty results".                                                                  |
| `start_sound` configured but missing | file not in palette                                   | Logs `search.start_sound_missing`; runs silently.                                         |

Worst failure wins when several attempts fail differently (rate limited, then timeout, then error), so a throttled backend isn't reported as "nothing found". ddgs says "No results found." both for a blocked engine and a truly empty one; it counts as empty, and the next backend gets a turn either way.

Recovery messages ship in **es / en / fr** built into the skill.

### Circuit breaker

After **3 consecutive** failed web searches, the breaker opens for **60 seconds**: queries short-circuit to the rate-limited message without touching the network. A successful web search resets the counter. Internal-only.

## The chime mechanism (PlaySound)

Same pattern as the news skill. When `start_sound` is set and the WAV exists, a successful call returns `ToolResult(side_effect=PlaySound(pcm=...))`, queued ahead of the model's audio. **No chime on failure paths.**

## Observability

Events (per [`observability.md`](../observability.md)); every event of one call carries the same `query_hash`:

| Event                                                     | When                                             | Fields                                                                                                                      |
| --------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `search.setup_complete`                                   | At skill init                                    | `safesearch`, `region`, `timezone`, `preferred_domains` (count), `ui_language`, `chime`                                     |
| `search.reconfigure`                                      | On every session connect                         | `ui_language`                                                                                                               |
| `search.dispatch`                                         | Tool entry: **`query_hash` only**, no full query | `query_hash` (sha256[:8]), `query_len`, `max_results`                                                                       |
| `search.dispatch_full`                                    | Full query (debug level only)                    | `query`                                                                                                                     |
| `search.short_query`                                      | Query length < 3                                 | `query_len`                                                                                                                 |
| `search.cache_hit`                                        | TTL cache hit                                    | `query_hash`, `hits`                                                                                                        |
| `search.attempt`                                          | One per web-search attempt                       | `query_hash`, `backend`, `outcome` (ok, empty, timeout, rate_limited, error), `latency_ms`, `count`, `error_class`          |
| `search.news`                                             | The news-article lane finished                   | `query_hash`, `outcome`, `error_class`, `count` (returned), `kept` (fresh and relevant), `latency_ms`                       |
| `search.headlines`                                        | The Google News lane finished                    | `query_hash`, `outcome` (ok, none, error, timeout), `count`, `reason`, `latency_ms`                                         |
| `search.page`                                             | One per page fetched for an excerpt              | `query_hash`, `domain`, `rank`, `outcome` (ok, no_text, error, timeout), `reason`, `chars`, `latency_ms`                    |
| `search.pages_skipped`                                    | No page fetched                                  | `query_hash`, `reason` (strict, budget), `budget_s`                                                                         |
| `search.results`                                          | Successful response                              | `query_hash`, `count`, `backend`, `latency_ms` (whole call), `excerpts`, `headlines`, `news`, `top_domains`, `output_chars` |
| `search.web_failed_news_only`                             | Web search failed, answered from the news lanes  | `query_hash`, `kind`, `headlines`, `news`                                                                                   |
| `search.empty`                                            | Nothing anywhere                                 | `query_hash`, `latency_ms`, `attempts`                                                                                      |
| `search.rate_limited` / `search.timeout` / `search.error` | Failure (warning)                                | `query_hash`, `reason`, `latency_ms`, `attempts` (`backend:outcome:ms,...`); `search.error` adds `exception_type`           |
| `search.circuit_opened`                                   | 3 consecutive failures                           | `duration_s`                                                                                                                |
| `search.circuit_blocked`                                  | A query short-circuited by the open breaker      | `query_hash`, `seconds_until_close`                                                                                         |

"Why was the answer wrong?" reads from one `query_hash`: which backend answered (`search.attempt`), whether fresh news existed (`search.news`, `search.headlines`), which pages gave text (`search.page`), and how big the output was (`search.results output_chars`).

**Privacy**: queries leak intent (medical lookups, legal questions). The skill logs only an 8-char SHA-256 prefix at info level; the full query stays at debug. Domains of fetched pages are logged; their text is not.

## File layout

```
server/skills/search/
├── pyproject.toml                        # huxley-skill-search; huxley-sdk, ddgs, httpx, lxml
├── src/huxley_skill_search/
│   ├── __init__.py                       # exports SearchSkill
│   ├── skill.py                          # dispatch, lanes, merge/rank, payload, cache, breaker
│   ├── provider.py                       # SearchProvider Protocol + DdgsProvider (fallback, deadline, news)
│   ├── pages.py                          # PageFetcher + HttpxPageFetcher, extract_page (excerpt + date)
│   ├── headlines.py                      # Google News RSS URL + feed filtering
│   └── py.typed
└── tests/
    ├── conftest.py                       # FakeSearchProvider, FakePageFetcher
    ├── fixtures/*.html                   # pages for the extractor
    ├── test_skill.py
    ├── test_provider.py                  # fallback, deadline, error mapping (injected sync fn)
    ├── test_pages.py                     # extraction on fixtures
    └── test_headlines.py
```

Both network boundaries are Protocols (`SearchProvider`, `PageFetcher`) so tests inject fakes: no `ddgs`, no network, no monkey-patching. A deployer can drop in a Brave/SearXNG/Tavily-backed provider without touching the skill.

## Honest limitations

- **`ddgs` reliability**: it scrapes; engines get blocked per IP and change their HTML. Three backends and a deadline soften it; when all three are blocked the skill is unavailable until ddgs or the order changes (the per-attempt log line says which).
- **Safe search is mostly not enforced.** In ddgs 9.14 only Brave and Mojeek read `safesearch` (both blocked from the Pi); Yandex, Yahoo, DuckDuckGo, Bing News and Wikipedia ignore it. A child-safe persona's `strict` therefore filters little; the skill withholds page text for it but snippets and titles are unfiltered.
- **Headlines have no article text.** Google News links are encoded redirects; the headline is all the model gets from that lane (the news-article lane exists to bring fetchable articles).
- **JavaScript-rendered pages give nothing** (cinema listings, live scores): the excerpt is empty and the model has only snippets.
- **The extractor is heuristic.** It reads the main text of ordinary article and table pages well; it can pick the wrong blocks on unusual layouts, and it doesn't follow pagination.
- **Output grew** from about 2 KB to about 4.3 KB (median) per call, more input tokens for the voice model; the caps (2 excerpts x 1500 chars, 3 headlines, 5 results) bound it under 7.5 KB.
