"""Test fixtures for huxley-skill-news.

`FakeHttpClient` is the test double for the HTTP boundary — a dict of
`url → response_body`. Tests construct it with the URLs they expect the
skill to fetch + the canned XML/JSON to return. No `httpx`, no `respx`,
no monkey-patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from huxley_skill_news.http import HttpError


@dataclass
class FakeHttpClient:
    """In-memory `HttpClient` impl that returns canned responses by URL.

    Fuzzy match: a key matches a request URL if it's a substring of the
    URL. This keeps tests readable — you don't have to reproduce every
    query parameter Google News appends to its RSS URLs.
    """

    responses: dict[str, str] = field(default_factory=dict)
    raises: dict[str, str] = field(default_factory=dict)
    requested_urls: list[str] = field(default_factory=list)

    async def get_text(self, url: str, *, timeout_s: float = 10.0) -> str:
        self.requested_urls.append(url)
        for key, reason in self.raises.items():
            if key in url:
                raise HttpError(url, reason)
        for key, body in self.responses.items():
            if key in url:
                return body
        raise HttpError(url, f"no_canned_response_for:{url}")
