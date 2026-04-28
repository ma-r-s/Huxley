"""HTTP boundary as a Protocol so tests can drop a dict-backed fake.

Production uses `HttpxClient`. Tests build a `FakeHttpClient({url: text})`
that satisfies the same Protocol — no httpx in the test path, no `respx`
dependency, no monkeypatching. Same pattern as `AudiobookPlayer` injection
in the audiobooks skill.
"""

from __future__ import annotations

from typing import Protocol


class HttpClient(Protocol):
    """Minimal async HTTP surface the news fetcher needs."""

    async def get_text(self, url: str, *, timeout_s: float = 10.0) -> str:
        """GET `url`, return body as text. Raises `HttpError` on failure."""
        ...


class HttpError(Exception):
    """Raised when an HTTP fetch fails (network, timeout, non-2xx)."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason}: {url}")
        self.url = url
        self.reason = reason


class HttpxClient:
    """Production `HttpClient` impl using httpx.AsyncClient."""

    def __init__(self) -> None:
        # Lazy import: don't pay the import cost (and don't force the dep
        # on test environments) unless someone actually constructs this.
        import httpx

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "huxley-skill-news/0.1"},
        )

    async def get_text(self, url: str, *, timeout_s: float = 10.0) -> str:
        import httpx

        try:
            response = await self._client.get(url, timeout=timeout_s)
            response.raise_for_status()
            return response.text
        except httpx.TimeoutException as exc:
            raise HttpError(url, "timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise HttpError(url, f"http_{exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise HttpError(url, f"network_error:{type(exc).__name__}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
