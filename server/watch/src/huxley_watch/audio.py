"""Serving the recorder's Ogg Opus files to the archive, read only.

`/audio/<path>` resolves only to an existing `.ogg` file under the
configured recordings dir: no `..`, no absolute path, no backslash, no
empty part, and no symlink that leads out of it (the path is resolved,
symlinks followed, and must still be under the resolved root). Browsers
fetch media in byte ranges (Safari insists), so one `bytes=a-b` range is
honoured with a 206.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import unquote

if TYPE_CHECKING:
    from pathlib import Path

CONTENT_TYPE = "audio/ogg"
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def resolve(root: Path | None, rel: str) -> Path | None:
    """The file `rel` names under `root`, or None if it isn't one we serve.
    `rel` is the URL's path after /audio/, still percent-encoded."""
    if root is None:
        return None
    name = unquote(rel)
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts) or not name.endswith(".ogg"):
        return None
    try:
        base = root.resolve(strict=True)
        path = (base / name).resolve(strict=True)
    except (OSError, RuntimeError):  # missing, or a symlink loop
        return None
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def byte_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """The inclusive (first, last) byte a `Range` header asks for; None to
    send the whole file (no header, or one to ignore: several ranges,
    another unit, bad syntax, an empty file, RFC 9110 14.2); False if it
    can't be satisfied (416)."""
    if not header or size <= 0:
        return None
    m = _RANGE.match(header.strip())
    if m is None:
        return None
    first, last = m.groups()
    if not first and not last:
        return None
    if not first:  # the last N bytes
        n = int(last)
        return (max(0, size - n), size - 1) if n > 0 else False
    a = int(first)
    if last and int(last) < a:
        return None  # not a valid range: ignored
    if a >= size:
        return False
    return a, min(int(last), size - 1) if last else size - 1
