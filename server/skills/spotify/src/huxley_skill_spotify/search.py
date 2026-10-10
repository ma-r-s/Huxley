"""Find something to play with the Spotify Web API.

Uses the client-credentials flow: the app's own token, no user login.
It lasts an hour and is renewed here on demand. Development-mode apps
are limited to 10 results per search, which is plenty for "play X".

Client-credentials tokens can't use `market=from_token`, so the market
comes from config; without it, results may include tracks this account
can't play.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
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
        query = query.strip()
        if not query:
            return None
        if kind not in KINDS:
            kind = "any"
        if kind == "any":
            data = await self._search(query, "track,artist,album,playlist", limit=5)
            # "Los Panchos" names an artist; "Sin ti" a song. Only treat
            # the query as an artist when it is the artist's name.
            artist = self._first(data, "artists", "artist")
            if artist and _fold(artist.title) == _fold(query):
                return artist
            for section, found in (
                ("tracks", "track"),
                ("albums", "album"),
                ("playlists", "playlist"),
                ("artists", "artist"),
            ):
                match = self._first(data, section, found)
                if match:
                    return match
            return None
        data = await self._search(query, kind, limit=5)
        return self._first(data, f"{kind}s", kind)

    def _first(self, data: dict[str, Any], section: str, kind: str) -> Match | None:
        # Playlist results can contain nulls (removed playlists).
        for entry in data.get(section, {}).get("items", []):
            if entry and entry.get("uri"):
                name = str(entry.get("name") or "")
                owner = (entry.get("owner") or {}).get("display_name")
                by = name if kind == "artist" else _artists(entry) or owner
                return Match(
                    uri=str(entry["uri"]),
                    kind=kind,
                    title=name,
                    artist=str(by) if by else None,
                )
        return None

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
