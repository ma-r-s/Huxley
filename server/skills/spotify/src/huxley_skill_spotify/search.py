"""Find something to play with the Spotify Web API.

Uses the client-credentials flow: the app's own token, no user login.
It lasts an hour and is renewed here on demand. Development-mode apps
are limited to 10 results per search, which is plenty for "play X".

Client-credentials tokens can't use `market=from_token`, so the market
comes from config; without it, results may include tracks this account
can't play.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import httpx

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"

KINDS = ("any", "track", "artist", "album", "playlist")


class SearchError(Exception):
    """Spotify's Web API couldn't answer (network, credentials, quota)."""


@dataclass(frozen=True)
class Match:
    """What to play: a track, album, playlist or artist URI. Soloist
    plays an artist URI natively (verified 2026-10-10, though its docs
    only list track/album/playlist/episode), the way the Spotify apps'
    play-artist button does."""

    uri: str
    kind: str
    title: str
    artist: str | None


def _fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _artists(entry: dict[str, Any]) -> str | None:
    names = [a.get("name") for a in entry.get("artists") or [] if a and a.get("name")]
    return ", ".join(names) or None


# A mixed search's sections, in the order a song beats an album beats a
# playlist beats an artist (an artist wins only by exact name).
_ANY_ORDER = (
    ("tracks", "track"),
    ("albums", "album"),
    ("playlists", "playlist"),
    ("artists", "artist"),
)


def _distinct(matches: list[Match], limit: int) -> list[Match]:
    """First `limit` matches, skipping repeats: the same URI, or the same
    title by the same artist (a remaster, a single next to its album)."""
    seen: set[tuple[str, str]] = set()
    uris: set[str] = set()
    kept: list[Match] = []
    for match in matches:
        key = (_fold(match.title), _fold(match.artist or ""))
        if match.uri in uris or key in seen:
            continue
        uris.add(match.uri)
        seen.add(key)
        kept.append(match)
        if len(kept) == limit:
            break
    return kept


# Words that join a title to its artist in a spoken request ("Weird
# Fishes de Radiohead"), not part of either.
_JOINERS = {"de", "del", "by", "of", "von", "di", "da"}
# Re-rank only when the top result's title is clearly not what was heard
# and another one clearly is (SequenceMatcher ratios, letters only).
_TITLE_POOR = 0.5
_TITLE_GOOD = 0.6
_TITLE_MARGIN = 0.2


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", _fold(text))


def heard_title(query: str, artist: str | None) -> str:
    """The query without the artist's words: what was heard of the title."""
    drop = set(_fold(artist or "").replace(",", " ").split()) | _JOINERS
    return " ".join(w for w in _fold(query).split() if w not in drop)


def title_score(heard: str, title: str) -> float:
    """How close what was heard is to a title, by letters, against the
    whole title and each part of it ("Weird Fishes / Arpeggi")."""
    h = _letters(heard)
    if not h:
        return 0.0
    parts = [title, *re.split(r"[/(\-]", title)]
    return max(
        (SequenceMatcher(None, h, _letters(p)).ratio() for p in parts if _letters(p)),
        default=0.0,
    )


def rerank_by_title(query: str, tracks: list[Match]) -> list[Match]:
    """Spoken English titles arrive garbled ("Weird Fishes de Radiohead"
    searched as "irfixes Radiohead", 2026-10-12): the artist still matches
    but Spotify ranks another of their songs first. When the top title is
    far from what was heard and another is close, that one goes first."""
    if len(tracks) < 2:
        return tracks
    scores = [title_score(heard_title(query, m.artist), m.title) for m in tracks]
    best = max(range(len(tracks)), key=scores.__getitem__)
    if (
        best != 0
        and scores[0] < _TITLE_POOR
        and scores[best] >= _TITLE_GOOD
        and scores[best] - scores[0] >= _TITLE_MARGIN
    ):
        return [tracks[best], *tracks[:best], *tracks[best + 1 :]]
    return tracks


class SpotifySearch:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        market: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._market = market
        # One search is up to two requests (token + search); keep the
        # worst case short, the user is waiting in silence.
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._token: str | None = None
        self._token_expires = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def find(self, query: str, kind: str = "any") -> Match | None:
        """Best match for `query`, or None if Spotify has nothing."""
        found = await self.candidates(query, kind, limit=1)
        return found[0] if found else None

    async def candidates(self, query: str, kind: str = "any", *, limit: int = 3) -> list[Match]:
        """Up to `limit` distinct matches for `query`, best first; [] if
        Spotify has nothing. The runners-up go back to the model, so after
        "no, the other one" it can pick one instead of repeating the guess."""
        query = query.strip()
        if not query:
            return []
        if kind not in KINDS:
            kind = "any"
        if kind == "track":
            return _distinct(await self._tracks_as_heard(query), limit)
        if kind != "any":
            data = await self._search(query, kind, limit=5)
            return _distinct(self._all(data, f"{kind}s", kind), limit)
        data = await self._search(query, "track,artist,album,playlist", limit=5)
        sections = {found: self._all(data, section, found) for section, found in _ANY_ORDER}
        # "Los Panchos" names an artist; "Sin ti" a song. Only treat
        # the query as an artist when it is the artist's name.
        artists = sections["artist"]
        if artists and _fold(artists[0].title) == _fold(query):
            best = "artist"
        else:
            best = next((k for _, k in _ANY_ORDER if sections[k]), "")
        if not best:
            return []
        # The best section first, then the others in their usual order.
        ordered = sections[best] + [m for _, k in _ANY_ORDER if k != best for m in sections[k]]
        return _distinct(ordered, limit)

    async def _tracks_as_heard(self, query: str) -> list[Match]:
        """A song request: Spotify's tracks, re-ranked by the heard title.
        If none of them is close but they share an artist, that artist's
        own tracks are searched too (a garbled title can miss the top
        five entirely)."""
        data = await self._search(query, "track", limit=5)
        tracks = self._all(data, "tracks", "track")
        if not tracks:
            return []
        top_artist = tracks[0].artist
        heard = heard_title(query, top_artist)
        if top_artist and heard and max(title_score(heard, t.title) for t in tracks) < _TITLE_GOOD:
            first = top_artist.split(",")[0].strip()
            more = await self._search(f'artist:"{first}"', "track", limit=10)
            tracks = tracks + self._all(more, "tracks", "track")
        return rerank_by_title(query, tracks)

    def _all(self, data: dict[str, Any], section: str, kind: str) -> list[Match]:
        matches: list[Match] = []
        # Playlist results can contain nulls (removed playlists).
        for entry in data.get(section, {}).get("items", []):
            if entry and entry.get("uri"):
                name = str(entry.get("name") or "")
                owner = (entry.get("owner") or {}).get("display_name")
                by = name if kind == "artist" else _artists(entry) or owner
                matches.append(
                    Match(
                        uri=str(entry["uri"]),
                        kind=kind,
                        title=name,
                        artist=str(by) if by else None,
                    )
                )
        return matches

    async def _search(self, query: str, types: str, *, limit: int) -> dict[str, Any]:
        params: dict[str, str | int] = {"q": query, "type": types, "limit": limit}
        if self._market:
            params["market"] = self._market
        for attempt in range(2):
            token = await self._access_token(force=attempt > 0)
            try:
                response = await self._http.get(
                    SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.HTTPError as exc:
                raise SearchError(f"search request failed: {type(exc).__name__}") from exc
            if response.status_code == 401 and attempt == 0:
                continue  # token revoked or expired early: renew once
            if response.status_code != 200:
                raise SearchError(f"search answered {response.status_code}")
            try:
                result: dict[str, Any] = response.json()
            except ValueError as exc:
                raise SearchError("search answered non-JSON") from exc
            return result
        raise SearchError("search unauthorized after token renewal")

    async def _access_token(self, *, force: bool = False) -> str:
        if not force and self._token and time.monotonic() < self._token_expires:
            return self._token
        try:
            response = await self._http.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"token request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise SearchError(f"token request answered {response.status_code}")
        try:
            body = response.json()
            self._token = str(body["access_token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SearchError("token response without access_token") from exc
        # Renew a minute early so a search never starts on a dying token.
        self._token_expires = time.monotonic() + float(body.get("expires_in", 3600)) - 60
        return self._token
