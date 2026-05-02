"""Marketplace registry feed (Marketplace v2 Phase C).

The PWA's Marketplace tab calls `get_marketplace`; the runtime fetches
the canonical `huxley-registry/index.json` feed (cached for ~1 hour
to keep GitHub ratelimits + offline tolerance reasonable), decorates
each entry with `installed: bool` cross-referenced against the local
`huxley.skills` entry-point group, and replies with the augmented
list. Phase C is browse-only — Phase D adds the `uv add` action.

Cache: in-memory, process-lifetime, 60-minute TTL with a one-time
"first-fetch racing OK" semantic. If the fetch fails (offline,
GitHub down, registry malformed), we surface an error code the PWA
can render as "couldn't reach registry" without crashing the panel.

The registry's schema is documented at
`https://github.com/ma-r-s/huxley-registry/blob/main/schema.json`.
We pass through every field the schema declares; the PWA decides
what to render. Forward-compat: new fields the PWA doesn't recognize
are ignored client-side.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from huxley.loader import ENTRY_POINT_GROUP

# Canonical feed — the registry repo serves this verbatim via raw.
# A federation deployment (org-private fork) would change this URL.
# v1 keeps it hardcoded; if/when forks become a real concern, the
# Settings layer can override.
DEFAULT_REGISTRY_URL = "https://raw.githubusercontent.com/ma-r-s/huxley-registry/main/index.json"

# In-memory cache for the parsed feed. Tuple of `(payload, fetched_at_ms)`.
# Process-lifetime; cleared only by a server restart. The PWA's "refresh"
# button could send a `force_refresh: true` flag in a future iteration —
# Phase C doesn't need that yet.
_CACHE: tuple[dict[str, Any], float] | None = None
_CACHE_TTL_S = 3600.0  # 60 minutes
_FETCH_TIMEOUT_S = 8.0
# Hard cap on registry response size. Today's canonical feed is < 100 KB;
# 2 MB is 20x headroom. Rejecting larger bodies prevents OOM if a
# federation operator's self-hosted endpoint mis-streams.
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_USER_AGENT = "Huxley-PWA/marketplace.v1"


async def fetch_marketplace(*, force: bool = False) -> dict[str, Any]:
    """Fetch (or return cached) registry feed, decorated with the
    `installed` flag per skill.

    Returns a dict shaped:
        {
            "skills": [<augmented index.json entry>, ...],
            "registry_version": "1",
            "generated_at": "<ISO date from upstream>",
            "fetched_at_ms": <epoch ms when we hit GitHub>,
            "stale": <bool — true if served from cache past TTL because
                     the latest fetch failed>,
            "error": <string | null>,
        }

    On error: returns the last-good cache (if any) with `stale=true`
    and an `error` description; otherwise returns `{"skills": [],
    "error": "<message>"}` so the PWA can render a graceful fallback.
    """
    global _CACHE
    now = time.time()

    if not force and _CACHE is not None:
        payload, fetched_at = _CACHE
        if now - fetched_at < _CACHE_TTL_S:
            return _decorate(payload, fetched_at_ms=int(fetched_at * 1000))

    try:
        # Stream-read with a hard size cap. The today-canonical
        # registry feed is < 100 KB, but a federation operator
        # could point us at a self-hosted endpoint that streams
        # an unbounded body. 2 MB is generous (20x headroom);
        # bigger than that is a misconfigured endpoint, not a
        # legitimate registry, and we reject rather than OOM.
        # Phase C critic round 1 finding 3.
        async with (
            httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT_S,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            ) as client,
            client.stream("GET", DEFAULT_REGISTRY_URL) as resp,
        ):
            resp.raise_for_status()
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _MAX_RESPONSE_BYTES:
                    msg = f"registry response exceeded {_MAX_RESPONSE_BYTES} bytes"
                    raise ValueError(msg)
            payload = json.loads(bytes(buf))
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        # Serve stale cache if we have one — better than blank panel.
        if _CACHE is not None:
            cached, fetched_at = _CACHE
            return {
                **_decorate(cached, fetched_at_ms=int(fetched_at * 1000)),
                "stale": True,
                "error": f"registry fetch failed: {exc!s}",
            }
        return {
            "skills": [],
            "registry_version": None,
            "generated_at": None,
            "fetched_at_ms": int(now * 1000),
            "stale": False,
            "error": f"registry fetch failed: {exc!s}",
        }

    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        return {
            "skills": [],
            "registry_version": None,
            "generated_at": None,
            "fetched_at_ms": int(now * 1000),
            "stale": False,
            "error": "registry feed malformed: missing or invalid `skills` array",
        }

    _CACHE = (payload, now)
    return _decorate(payload, fetched_at_ms=int(now * 1000))


def _decorate(payload: dict[str, Any], *, fetched_at_ms: int) -> dict[str, Any]:
    """Cross-reference every registry entry with the active venv's
    entry-point group + decorate with `installed: bool`. Other fields
    pass through unchanged so the PWA gets every field upstream
    publishes (forward-compat with new schema fields)."""
    installed_names = _installed_skill_names()
    raw_skills = payload.get("skills", [])
    out_skills: list[dict[str, Any]] = []
    for entry in raw_skills:
        if not isinstance(entry, dict):
            continue
        # Match against the package name. Drop entries with no string
        # `name` — the schema requires it, but a registry-PR slip
        # could still ship one through. Letting it pass would crash
        # the PWA's `entry.name.replace(...)` downstream. Phase C
        # critic round 1 finding 11.
        name_raw = entry.get("name")
        if not isinstance(name_raw, str) or not name_raw:
            continue
        # The registry's `name` is the PyPI dist (`huxley-skill-foo`);
        # entry points report just `foo`. Normalize via the convention
        # `huxley-skill-<key>` → `<key>`.
        ep_key = name_raw.removeprefix("huxley-skill-")
        installed = ep_key in installed_names
        out_skills.append({**entry, "installed": installed})
    return {
        "skills": out_skills,
        "registry_version": payload.get("registry_version"),
        "generated_at": payload.get("generated_at"),
        "fetched_at_ms": fetched_at_ms,
        "stale": False,
        "error": None,
    }


def _installed_skill_names() -> set[str]:
    """Set of installed skill names (entry-point keys) in the active
    venv. Read fresh on every fetch — cheap (importlib.metadata
    caches its discovery at the Python level).

    **Phase D caveat**: `uv add` mutates `site-packages` AFTER the
    Python process started. The cached entry-point index does NOT
    pick up the new package automatically — Phase D's install-
    completion path must call `importlib.invalidate_caches()` (or
    restart the process) before the next `_decorate` call to flip
    the freshly-installed skill's `installed: bool` from False to
    True. Phase C operates on a static venv so this is forward-only
    documentation.
    """
    from importlib.metadata import entry_points

    return {ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}


def clear_cache() -> None:
    """Test-only hook: drop the in-memory cache so a subsequent
    fetch always hits the network (or the patched fetcher)."""
    global _CACHE
    _CACHE = None


__all__ = [
    "DEFAULT_REGISTRY_URL",
    "clear_cache",
    "fetch_marketplace",
]
