"""Test fixtures for huxley-skill-radio.

`FakeRadioPlayer` replaces the ffmpeg-based RadioPlayer with a fixed
chunk sequence per URL. Same pattern audiobooks uses for AudiobookPlayer
injection — keeps tests free of subprocess + network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class FakeRadioPlayer:
    """In-memory `RadioPlayer` that yields canned chunks per URL.

    `chunks_by_url`: dict of url-substring → list of bytes the stream
    should yield in order. URL match is fuzzy (substring) so tests
    don't have to reproduce every parameter.

    `urls_streamed`: log of every URL passed to `stream()`.
    """

    chunks_by_url: dict[str, list[bytes]] = field(default_factory=dict)
    urls_streamed: list[str] = field(default_factory=list)
    default_chunks: list[bytes] = field(
        default_factory=lambda: [b"radio_chunk_1", b"radio_chunk_2"]
    )

    async def stream(self, url: str) -> AsyncIterator[bytes]:
        self.urls_streamed.append(url)
        chunks = self.default_chunks
        for key, canned in self.chunks_by_url.items():
            if key in url:
                chunks = canned
                break
        for chunk in chunks:
            yield chunk
