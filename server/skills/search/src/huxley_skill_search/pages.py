"""Result-page excerpts: fetch the top hits' pages and pull out the answer.

Search snippets are short and often miss the fact the user asked for (a
score, a price, the plate digits for today). For the best hits the skill
fetches the page itself within a short budget and returns a trimmed
excerpt of its main text beside the snippet.

Two parts, split so tests never touch the network:

- `PageFetcher` (Protocol) + `HttpxPageFetcher`: GET a URL, return the
  HTML (or a feed's XML) or raise `PageFetchError`. Size-capped.
- `extract_page(html, query)`: pure function. Drops boilerplate (scripts,
  navigation, menus, link lists), keeps the text blocks, and picks the
  ones that share words with the query, in page order, up to a length
  cap. Also reads the publication date when the page declares one.

The extraction is deliberately small (lxml, which `ddgs` already pulls
in): a readability-style heuristic, not a full article extractor. It
only has to surface the sentence with the answer for a voice model to
read, not reproduce the article.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lxml.html import HtmlElement

# Hard limits. Pages bigger than this are cut (the main text of an
# article is in the first part of the HTML on every site we measured).
MAX_PAGE_BYTES = 1_500_000
EXCERPT_MAX_CHARS = 1500
_BLOCK_MAX_CHARS = 400
_MIN_BLOCK_CHARS = 25  # shorter blocks only kept when they carry a number
_LEAD_MIN_CHARS = 60

# Elements whose text is never content.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
    "button",
    "select",
    "template",
    "dialog",
)
# class/id fragments that mark boilerplate containers (menus, share bars,
# related-article rails, cookie banners, ads, comments).
_BOILERPLATE_RE = re.compile(
    r"(^|[\s_-])("
    r"nav|navbar|menu|breadcrumbs?|footer|header|sidebar|share|sharing|social|"
    r"related|recommended|recomendad\w*|relacionad\w*|newsletter|subscribe|"
    r"suscri\w*|cookies?|consent|banner|advert\w*|ads?|publicidad|promo|"
    r"comments?|comentarios|widget|popular|trending|tags?|outbrain|taboola"
    r")($|[\s_-])",
    re.IGNORECASE,
)
# Block-level tags whose text becomes one candidate block each.
_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "tr", "dd", "dt", "blockquote", "pre")

_WS_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"[a-z0-9]+")
_URL_DATE_RE = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)")
_ISO_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")

# Words too common to say anything about relevance (es + en + fr),
# already folded (no accents). Time words ("hoy", "ayer", "actual") go
# too: pages rarely say "hoy" next to the fact.
_STOPWORDS = frozenset(
    """
    el la los las un una unos unas de del al a en y o u que se su sus es son
    fue por para con sin sobre como cual cuales quien quienes cuando donde
    hoy ayer manana anoche ahora actual actualmente ultimo ultima ultimos
    esta este esto estos estas ese esa eso lo le les mi me te tu ya muy mas
    menos hay ser estar tiene tienen cuanto cuanta cuantos cuantas dime sabes
    puedes favor the of and or to in on for with is are was what who when
    where how which today yesterday now current latest des du et est sont
    pour avec sur qui quoi quand comment aujourd hui
    """.split()  # noqa: SIM905 - a word list reads better as text
)

# Blocks that are site furniture even when they sit in the content area.
_JUNK_BLOCK_RE = re.compile(
    r"cookies?|acepto|aceptar|pol[ií]tica de (privacidad|tratamiento)|datos personales|"
    r"suscr[ií]bete|newsletter|derechos reservados|all rights reserved|"
    r"inicia sesi[oó]n|reg[ií]strate|"
    # Bot walls: a page that only says this has no content for us.
    r"verif\w* (your|tu) (browser|navegador)|not a (bot|robot)|captcha|"
    r"just a moment|checking your browser|enable javascript|activa javascript",
    re.IGNORECASE,
)


class PageFetchError(Exception):
    """A page could not be fetched (network, status, type, size)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PageFetcher(Protocol):
    """Minimal async surface for fetching one result page."""

    async def fetch(self, url: str, *, timeout_s: float) -> str:
        """GET `url` and return its body as text (HTML, or XML for feeds).

        Raises:
            PageFetchError: anything other than a 2xx HTML/XML response.
        """
        ...


class HttpxPageFetcher:
    """`PageFetcher` backed by one shared `httpx.AsyncClient`.

    Sends a browser-like User-Agent and Spanish-first Accept-Language:
    several Colombian news sites answer a bare client with 403. Reads
    the body as a stream and stops at `MAX_PAGE_BYTES`.
    """

    _HEADERS = {  # noqa: RUF012 - constant, never mutated
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.6",
    }

    def __init__(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            headers=self._HEADERS,
            max_redirects=5,
        )

    async def fetch(self, url: str, *, timeout_s: float) -> str:
        import httpx

        try:
            async with self._client.stream("GET", url, timeout=timeout_s) as response:
                if response.status_code >= 400:
                    raise PageFetchError(f"http_{response.status_code}")
                ctype = response.headers.get("content-type", "").lower()
                if ctype and "html" not in ctype and "xml" not in ctype:
                    raise PageFetchError("not_html")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_PAGE_BYTES:
                        break
                body = b"".join(chunks)[:MAX_PAGE_BYTES]
                encoding = response.charset_encoding or _sniff_charset(body) or "utf-8"
        except httpx.TimeoutException as exc:
            raise PageFetchError("timeout") from exc
        except httpx.HTTPError as exc:
            raise PageFetchError(f"network_error:{type(exc).__name__}") from exc
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    async def aclose(self) -> None:
        await self._client.aclose()


_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", re.IGNORECASE)


def _sniff_charset(body: bytes) -> str | None:
    match = _META_CHARSET_RE.search(body[:4096])
    return match.group(1).decode("ascii") if match else None


# --- Extraction ---


@dataclass(frozen=True, slots=True)
class PageExcerpt:
    """What the skill keeps from one fetched page."""

    text: str
    date: str | None  # ISO date (YYYY-MM-DD) when the page declares one


def fold(text: str) -> str:
    """Lowercase and strip accents, so an accented and a plain spelling compare equal."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def stem(word: str) -> str:
    """Crude plural strip so 'festivos' matches 'festivo', 'precios' 'precio'."""
    if len(word) > 5 and word.endswith("es"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def words(text: str) -> set[str]:
    """Folded, stemmed word set of `text`."""
    return {stem(w) for w in _WORD_RE.findall(fold(text))}


def query_terms(query: str) -> set[str]:
    """The words of a query that say something: folded, stemmed, no stopwords."""
    return {stem(w) for w in _WORD_RE.findall(fold(query)) if len(w) >= 3 and w not in _STOPWORDS}


def extract_page(
    html: str,
    query: str,
    *,
    max_chars: int = EXCERPT_MAX_CHARS,
    boost: frozenset[str] = frozenset(),
) -> PageExcerpt:
    """Pull a query-relevant excerpt and the publication date out of a page.

    `boost` holds words worth more than the query's own (see
    `date_boost_terms`). Returns an empty `text` when the page has no
    usable text (a script-only app shell, a consent wall). Never raises
    on malformed HTML.
    """
    root = _parse(html)
    if root is None:
        return PageExcerpt(text="", date=None)
    published = _published_date(root)
    _strip_boilerplate(root)
    blocks = _text_blocks(root)
    return PageExcerpt(text=_select(blocks, query, max_chars, boost), date=published)


_WEEKDAYS = (
    ("lunes", "monday", "lundi"),
    ("martes", "tuesday", "mardi"),
    ("miercoles", "wednesday", "mercredi"),
    ("jueves", "thursday", "jeudi"),
    ("viernes", "friday", "vendredi"),
    ("sabado", "saturday", "samedi"),
    ("domingo", "sunday", "dimanche"),
)
_DAY_WORDS = {
    "hoy": 0,
    "today": 0,
    "aujourd": 0,
    "manana": 1,
    "tomorrow": 1,
    "demain": 1,
    "ayer": -1,
    "yesterday": -1,
    "hier": -1,
}


def date_boost_terms(query: str, today: date) -> frozenset[str]:
    """Weekday names for "hoy" / "manana" / "ayer" (and en/fr) in the query.

    Pages answer "hay pico y placa hoy?" with "Martes: 7 y 8", not with
    the word "hoy"; and every block of such a page says "pico y placa".
    So the day the user means, by name, is the strongest signal of the
    block with the answer. Names in es/en/fr (stemmed like `words`); the
    page decides which one matches.
    """
    found: set[str] = set()
    for word in _WORD_RE.findall(fold(query)):
        offset = _DAY_WORDS.get(word)
        if offset is not None:
            day = date.fromordinal(today.toordinal() + offset)
            found.update(stem(name) for name in _WEEKDAYS[day.weekday()])
    return frozenset(found)


def date_from_url(url: str) -> str | None:
    """Many news URLs carry the date: /2026/10/12/ or /2026-10-12-."""
    match = _URL_DATE_RE.search(url)
    if not match:
        return None
    return _valid_iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _parse(html: str) -> HtmlElement | None:
    from lxml import etree
    from lxml import html as lxml_html

    if not html or not html.strip():
        return None
    try:
        return lxml_html.document_fromstring(html)
    except ValueError:
        # A str with an XML encoding declaration: lxml wants bytes.
        try:
            return lxml_html.document_fromstring(html.encode("utf-8"))
        except (ValueError, etree.ParserError):
            return None
    except etree.ParserError:
        return None


def _valid_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def iso_date(value: str) -> str | None:
    """The first YYYY-MM-DD in `value` that is a real date, or None."""
    match = _ISO_DATE_RE.search(value)
    if not match:
        return None
    return _valid_iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))


_DATE_META_KEYS = (
    "article:published_time",
    "og:article:published_time",
    "datepublished",
    "date",
    "pubdate",
    "publishdate",
    "publish-date",
    "dc.date.issued",
    "dc.date",
    "sailthru.date",
    "parsely-pub-date",
)


def _published_date(root: HtmlElement) -> str | None:
    """The page's own date, or None.

    A page first published long before its last edit is a reference page
    (Wikipedia: created 2003, edited this week); for those the edit date
    is what says how current it is.
    """
    metas: dict[str, str] = {}
    for meta in root.iter("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        content = meta.get("content")
        if key and content and key not in metas:
            metas[key] = content
    published: str | None = None
    for key in _DATE_META_KEYS:
        if key in metas and (found := iso_date(metas[key])):
            published = found
            break
    modified = iso_date(metas.get("article:modified_time", "")) or iso_date(
        metas.get("datemodified", "")
    )
    if published is None or modified is None:
        ld_published, ld_modified = _ld_json_dates(root)
        published = published or ld_published
        modified = modified or ld_modified
    if published is None:
        for time_el in root.iter("time"):
            stamp = time_el.get("datetime")
            if stamp and (found := iso_date(stamp)):
                published = found
                break
    if published and modified and _days_between(published, modified) > 365:
        return modified
    return published or modified


def _days_between(earlier: str, later: str) -> int:
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def _ld_json_dates(root: HtmlElement) -> tuple[str | None, str | None]:
    published: str | None = None
    modified: str | None = None
    for script in root.iter("script"):
        if (script.get("type") or "").lower() != "application/ld+json":
            continue
        try:
            data: Any = json.loads(script.text or "")
        except (ValueError, TypeError):
            continue
        stack: list[Any] = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if not isinstance(value, str):
                        stack.append(value)
                    elif key == "datePublished" and published is None:
                        published = iso_date(value)
                    elif key == "dateModified" and modified is None:
                        modified = iso_date(value)
            elif isinstance(node, list):
                stack.extend(node)
        if published:
            break
    return published, modified


def _content_chars(el: HtmlElement) -> int:
    """Text in paragraphs and table cells: a rough measure of content."""
    return sum(len(_clean(node.text_content())) for node in el.iter("p", "td"))


def _strip_boilerplate(root: HtmlElement) -> None:
    """Remove non-content elements in place.

    Tags in `_DROP_TAGS` always go. An element whose class/id looks like
    boilerplate goes too, unless it holds at least half of the page's
    content text: class names like "has-sidebar" or "with-share" sit on
    the main content wrapper of real sites.
    """
    doomed: list[HtmlElement] = []
    marked: list[HtmlElement] = []
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if not tag:  # comments, processing instructions
            doomed.append(el)
            continue
        if tag in _DROP_TAGS:
            doomed.append(el)
            continue
        if tag in ("body", "html", "main", "article"):
            continue
        marker = f"{el.get('class') or ''} {el.get('id') or ''} {el.get('role') or ''}"
        if marker.strip() and _BOILERPLATE_RE.search(marker):
            marked.append(el)
    for el in doomed:
        if el.getparent() is not None:
            # drop_tree keeps the tail text that follows the element.
            el.drop_tree()
    total = _content_chars(root)
    for el in marked:
        if el.getparent() is None:
            continue
        if total and _content_chars(el) * 2 >= total:
            continue
        el.drop_tree()


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _link_density(el: HtmlElement, text_len: int) -> float:
    if text_len == 0:
        return 1.0
    link_chars = sum(len(_clean(a.text_content())) for a in el.iter("a"))
    return link_chars / text_len


def _text_blocks(root: HtmlElement) -> list[str]:
    """Candidate blocks in page order, deduped, link lists dropped."""
    scope = _content_scope(root)
    blocks: list[str] = []
    seen: set[str] = set()
    for el in scope.iter(*_BLOCK_TAGS):
        # A block inside another block (a <p> in an <li>) is read once,
        # through its outermost block.
        if any(anc.tag in _BLOCK_TAGS for anc in el.iterancestors()):
            continue
        if el.tag == "tr":
            cells = [_clean(c.text_content()) for c in el.iter("td", "th")]
            text = " | ".join(c for c in cells if c)
        else:
            text = _clean(el.text_content())
        if not text:
            continue
        if len(text) < _MIN_BLOCK_CHARS and not _DIGIT_RE.search(text):
            continue
        if _link_density(el, len(text)) > 0.5:
            continue
        if len(text) < 300 and _JUNK_BLOCK_RE.search(text):
            continue
        key = fold(text)
        if key in seen:
            continue
        seen.add(key)
        blocks.append(_trim(text, _BLOCK_MAX_CHARS))
    return blocks


def _content_scope(root: HtmlElement) -> HtmlElement:
    """Narrow to <article> or <main> when the page marks its content.

    Picks the candidate with the most paragraph text, so a page with a
    small teaser <article> in a rail and the real one in the body reads
    the real one.
    """
    best: HtmlElement | None = None
    best_len = 0
    for el in root.iter("article", "main"):
        length = sum(len(_clean(p.text_content())) for p in el.iter("p"))
        if length > best_len:
            best, best_len = el, length
    if best is not None and best_len >= 200:
        return best
    body = root.find("body")
    return body if body is not None else root


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a sentence end, then a word boundary.
    for mark in (". ", "; ", ", "):
        pos = cut.rfind(mark)
        if pos >= limit * 0.6:
            return cut[: pos + 1].rstrip()
    return cut.rsplit(" ", 1)[0] + "..."


def _select(blocks: list[str], query: str, max_chars: int, boost: frozenset[str]) -> str:
    """Pick the lead, the blocks that share words with the query, then fill.

    Score = distinct query words in the block, plus (query words + 1) per
    `boost` word, so the day the user means outweighs a block that only
    repeats the question, plus 0.5 if it has a number (answers are often
    figures). Highest scores go in first; any room left is
    filled with the page's blocks from the top (an infobox row with the
    age rarely repeats the question's words). The result is put back in
    page order so it reads naturally.
    """
    if not blocks:
        return ""
    terms = query_terms(query)
    boost_weight = len(terms) + 1
    scored: list[tuple[float, int]] = []
    for idx, block in enumerate(blocks):
        block_words = words(block)
        hits = len(terms & block_words) + boost_weight * len(boost & block_words)
        if hits:
            bonus = 0.5 if _DIGIT_RE.search(block) else 0.0
            scored.append((hits + bonus, idx))

    chosen: set[int] = set()
    budget = max_chars

    def take(idx: int) -> None:
        nonlocal budget
        cost = len(blocks[idx]) + 1
        if idx not in chosen and cost <= budget:
            chosen.add(idx)
            budget -= cost

    lead = next((i for i, b in enumerate(blocks) if len(b) >= _LEAD_MIN_CHARS), None)
    if lead is not None:
        take(lead)
    for _score, idx in sorted(scored, key=lambda s: (-s[0], s[1])):
        take(idx)
    for idx in range(len(blocks)):
        if budget <= _MIN_BLOCK_CHARS:
            break
        take(idx)

    text = " ".join(blocks[i] for i in sorted(chosen))
    return _trim(text, max_chars)


def today_iso(tz_name: str) -> str:
    """Today's date in `tz_name` (falls back to UTC on an unknown zone)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date().isoformat()
