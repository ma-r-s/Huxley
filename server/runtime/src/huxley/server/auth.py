"""The device token: `Authorization: Bearer <HUXLEY_DEVICE_TOKEN>`.

One check, used by the firmware routes (`huxley.server.firmware`) and the
WebSocket upgrade's production role (server.py). docs/protocol.md
"Authentication and roles".
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pydantic import SecretStr

TokenState = Literal["valid", "missing", "bad_token"]

# A token shorter than this is reported at startup
# (`server.auth.device_token_short`): guessable through the tunnel.
MIN_TOKEN_CHARS = 32


class HeaderLookup(Protocol):
    """What is read of a request's headers (websockets' Headers)."""

    def get_all(self, key: str) -> list[str]: ...


def local_host_allowed(host: str | None) -> bool:
    """True for a host a person types to reach this server locally: an IP
    literal, `localhost`, a one-label name or a Tailscale `*.ts.net` name,
    with or without a port. A DNS-rebinding or cross-site page arrives with
    its own domain name and is refused. Used for `/admin`'s `Host` and the
    voice socket's `Origin`."""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        name = host[1:end] if end > 0 else ""
    else:
        name, _, port = host.rpartition(":")
        if not name or not port.isdigit():
            name = host
    if not name:
        return False
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(name)
        return True
    return name == "localhost" or "." not in name or name.endswith(".ts.net")


def local_origin(headers: HeaderLookup) -> bool:
    """True when the request has no `Origin` (not a browser: the firmware,
    curl, scripts) or one whose host is local. A web page on any other site
    can open a WebSocket to localhost or a Tailscale address, and the
    browser says where it came from in `Origin` (cross-site WebSocket
    hijacking)."""
    values = headers.get_all("Origin")
    if not values:
        return True
    if len(values) != 1:
        return False
    try:
        netloc = urlparse(values[0].strip()).netloc
    except ValueError:
        return False
    return local_host_allowed(netloc)


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


def header_secret_ok(headers: HeaderLookup, name: str, secret: SecretStr) -> bool:
    """Whether `headers` carry exactly one `name` header whose value is
    `secret`. Constant time over digests, like the bearer check. Never
    raises and never logs; the caller logs the refusal, never a value."""
    values = headers.get_all(name)
    if len(values) != 1:
        return False
    presented = values[0].strip()
    if not presented or not presented.isascii():
        return False
    expected = secret.get_secret_value().encode("utf-8", "surrogateescape")
    return hmac.compare_digest(
        hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected).digest()
    )


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
