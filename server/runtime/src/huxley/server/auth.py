"""The device token: `Authorization: Bearer <HUXLEY_DEVICE_TOKEN>`.

One check, used by the firmware routes (`huxley.server.firmware`) and the
WebSocket upgrade's production role (server.py). docs/protocol.md
"Authentication and roles".
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from pydantic import SecretStr

TokenState = Literal["valid", "missing", "bad_token"]


class HeaderLookup(Protocol):
    """What is read of a request's headers (websockets' Headers)."""

    def get_all(self, key: str) -> list[str]: ...


def forwarded_for(headers: HeaderLookup) -> str | None:
    """The original client behind a proxy (cloudflared adds both), if any."""
    for name in ("Cf-Connecting-Ip", "X-Forwarded-For"):
        values = headers.get_all(name)
        if values:
            return values[0].split(",")[0].strip()[:64]
    return None


def configured_token(token: SecretStr | None) -> SecretStr | None:
    """The token, or None when unset or empty (an empty token is no token)."""
    return token if token is not None and token.get_secret_value() else None


def bearer_token_state(headers: HeaderLookup, token: SecretStr | None) -> TokenState:
    """Whether `headers` carry exactly one `Authorization: Bearer <token>`.
    `missing`: no such header (or no token configured); `bad_token`: a
    bearer that isn't it (wrong, not ASCII, or several headers). Constant
    time over digests, so not even the length leaks. Never raises and never
    logs; the caller logs the state, never a token."""
    if token is None:
        return "missing"
    values = headers.get_all("Authorization")
    if not values:
        return "missing"
    if len(values) != 1:
        return "bad_token"
    scheme, _, presented = values[0].partition(" ")
    presented = presented.strip()
    if scheme.lower() != "bearer" or not presented:
        return "missing"
    if not presented.isascii():
        return "bad_token"
    expected = token.get_secret_value().encode("utf-8", "surrogateescape")
    same = hmac.compare_digest(
        hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected).digest()
    )
    return "valid" if same else "bad_token"
