"""Tests for `huxley.marketplace.fetch_marketplace` (Phase C).

Pin the contract:
1. Successful fetch returns the registry payload + augments each
   skill entry with `installed: bool`.
2. `installed` cross-references the active venv's entry-point group:
   a registry skill named `huxley-skill-foo` is `installed=True` iff
   the entry-point group contains a `foo` key.
3. Cache hit on second call within TTL — no second network round-trip.
4. Network failure with no prior cache returns `{skills: [], error}`.
5. Network failure with a prior cache returns `stale=true` payload.
6. Malformed feed (non-dict, missing skills array) returns empty
   skills + an error message.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from huxley import marketplace

_SAMPLE_FEED = {
    "registry_version": "1",
    "generated_at": "2026-05-02",
    "skills": [
        {
            "namespace": "io.github.ma-r-s.huxley-skill-audiobooks",
            "name": "huxley-skill-audiobooks",
            "display_name": "Audiobooks",
            "tagline": "Local-library audiobook playback.",
            "version": "0.1.0",
            "tier": "first-party",
        },
        {
            "namespace": "io.github.someone.huxley-skill-future",
            "name": "huxley-skill-future",
            "display_name": "Future Skill",
            "tagline": "A skill not yet installed.",
            "version": "0.1.0",
            "tier": "community",
        },
    ],
}


class _FakeEntryPoint:
    def __init__(self, name: str) -> None:
        self.name = name


def _stub_entry_points(names: list[str]):
    eps = [_FakeEntryPoint(n) for n in names]

    def _stub(group: str) -> list[_FakeEntryPoint]:
        assert group == "huxley.skills"
        return eps

    return _stub


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Drop the module-level cache before every test so they don't
    cross-contaminate."""
    marketplace.clear_cache()


class _FakeResponse:
    """Mimics enough of `httpx.Response` for the streaming code path
    used by `marketplace.fetch_marketplace`. `body` is bytes (or
    encoded from a Python object); `aiter_bytes` yields it as a single
    chunk OR as `chunks` if a sequence is supplied (lets tests
    exercise the size-cap by chunking past the threshold)."""

    def __init__(
        self,
        status: int,
        body: Any,
        *,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status
        if isinstance(body, bytes | bytearray):
            self._body_bytes = bytes(body)
        else:
            self._body_bytes = json.dumps(body).encode("utf-8")
        self._chunks = chunks

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    async def aiter_bytes(self):
        if self._chunks is not None:
            for chunk in self._chunks:
                yield chunk
            return
        yield self._body_bytes


class _FakeStreamCtx:
    """Mimics the async context returned by `httpx.AsyncClient.stream(...)`."""

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response
        self._call_count = 0

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakeStreamCtx:
        self._call_count += 1
        return _FakeStreamCtx(self._response)


async def test_successful_fetch_returns_decorated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `entry_points` is imported inside `_installed_skill_names`, not at
    # module scope of huxley.marketplace — patch the importlib.metadata
    # module so the lazy import sees the stub.
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points(["audiobooks"]))

    client = _FakeAsyncClient(_FakeResponse(200, _SAMPLE_FEED))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()

    assert out["registry_version"] == "1"
    assert out["error"] is None
    assert out["stale"] is False
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["huxley-skill-audiobooks"]["installed"] is True
    assert by_name["huxley-skill-future"]["installed"] is False
    # All upstream fields preserved (forward-compat).
    assert by_name["huxley-skill-audiobooks"]["display_name"] == "Audiobooks"
    assert by_name["huxley-skill-audiobooks"]["tier"] == "first-party"


async def test_cache_hit_within_ttl_avoids_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))

    client = _FakeAsyncClient(_FakeResponse(200, _SAMPLE_FEED))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    await marketplace.fetch_marketplace()
    await marketplace.fetch_marketplace()
    await marketplace.fetch_marketplace()
    # Network hit exactly once — three subsequent calls within TTL all
    # served from cache.
    assert client._call_count == 1


async def test_force_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    client = _FakeAsyncClient(_FakeResponse(200, _SAMPLE_FEED))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    await marketplace.fetch_marketplace()
    await marketplace.fetch_marketplace(force=True)
    assert client._call_count == 2


async def test_network_failure_no_prior_cache_returns_empty_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    import httpx

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    client = _FakeAsyncClient(httpx.ConnectError("offline"))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()
    assert out["skills"] == []
    assert out["error"] is not None
    assert "offline" in out["error"] or "registry fetch failed" in out["error"]
    assert out["stale"] is False


async def test_network_failure_with_prior_cache_returns_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    import httpx

    monkeypatch.setattr(md, "entry_points", _stub_entry_points(["audiobooks"]))

    # First fetch succeeds.
    success_client = _FakeAsyncClient(_FakeResponse(200, _SAMPLE_FEED))
    monkeypatch.setattr(
        "huxley.marketplace.httpx.AsyncClient",
        lambda **kw: success_client,
    )
    await marketplace.fetch_marketplace()

    # Second fetch fails. We force-refresh to bypass the cache.
    fail_client = _FakeAsyncClient(httpx.TimeoutException("timeout"))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: fail_client)

    out = await marketplace.fetch_marketplace(force=True)
    # Stale cache returned.
    assert out["stale"] is True
    assert out["error"] is not None
    assert len(out["skills"]) == 2  # cached payload preserved
    assert out["skills"][0]["installed"] is True


async def test_malformed_feed_missing_skills_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    client = _FakeAsyncClient(_FakeResponse(200, {"registry_version": "1"}))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()
    assert out["skills"] == []
    assert out["error"] is not None
    assert "malformed" in out["error"].lower()


async def test_malformed_feed_non_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    client = _FakeAsyncClient(_FakeResponse(200, ["not", "a", "dict"]))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()
    assert out["skills"] == []
    assert out["error"] is not None


async def test_skip_non_dict_entries_in_skills_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a registry entry that's accidentally a string (or
    null) doesn't blow up the whole panel — we just drop it."""
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    feed = {
        "registry_version": "1",
        "skills": [
            "garbage",
            {"name": "huxley-skill-real", "display_name": "Real"},
            None,
        ],
    }
    client = _FakeAsyncClient(_FakeResponse(200, feed))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()
    # Only the real entry survives.
    assert len(out["skills"]) == 1
    assert out["skills"][0]["name"] == "huxley-skill-real"


async def test_fetched_at_ms_is_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    client = _FakeAsyncClient(_FakeResponse(200, _SAMPLE_FEED))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    before = int(time.time() * 1000)
    out = await marketplace.fetch_marketplace()
    after = int(time.time() * 1000)
    assert before <= out["fetched_at_ms"] <= after


async def test_oversized_response_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase C critic round 1 finding 3: a registry feed larger than
    `_MAX_RESPONSE_BYTES` is rejected mid-stream rather than buffered
    into memory. The size-cap protects against a misconfigured
    federation endpoint streaming an unbounded body."""
    from importlib import metadata as md

    monkeypatch.setattr(md, "entry_points", _stub_entry_points([]))
    # Stream chunks adding up to > 2 MB. Each chunk is 256 KB; 10 of
    # them = 2.5 MB, blasting past the 2 MB cap on chunk 9.
    huge_chunks = [b"x" * (256 * 1024) for _ in range(10)]
    client = _FakeAsyncClient(_FakeResponse(200, b"unused", chunks=huge_chunks))
    monkeypatch.setattr("huxley.marketplace.httpx.AsyncClient", lambda **kw: client)

    out = await marketplace.fetch_marketplace()
    assert out["skills"] == []
    assert out["error"] is not None
    assert "exceeded" in out["error"].lower() or "too large" in out["error"].lower()
