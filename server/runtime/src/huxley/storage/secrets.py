"""Per-skill secrets store backed by a JSON file under the persona data dir.

Concrete implementation of the SDK's :class:`huxley_sdk.SkillSecrets`
Protocol. One instance per (persona, skill) pair; constructed by the
runtime when assembling each skill's :class:`SkillContext`.

The on-disk shape is a flat ``dict[str, str]`` at
``<persona.data_dir>/secrets/<skill_name>/values.json`` with perms
``0700`` on the directory and ``0600`` on the file. Skills that need to
persist nested data (OAuth refresh state) JSON-encode the dict
themselves into a single key — see the OAuth-blob convention in
``docs/skill-marketplace.md`` § Secrets storage layout.

Reads recover from missing / unreadable / malformed files by returning
an empty view, never raising — the running server stays up if a values
file gets corrupted, and the skill's own soft-fail path takes over.

Writes are atomic (temp-file write + ``os.replace``) and serialized
via an asyncio Lock so two coroutines on the same skill don't tear
each other's writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class JsonFileSecrets:
    """File-backed per-skill secrets store satisfying ``SkillSecrets``."""

    def __init__(self, secrets_dir: Path) -> None:
        self._dir = secrets_dir
        self._path = secrets_dir / "values.json"
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        data = await asyncio.to_thread(self._read)
        return data.get(key)

    async def set(self, key: str, value: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write_one, key, value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_one, key)

    async def keys(self) -> list[str]:
        data = await asyncio.to_thread(self._read)
        return sorted(data.keys())

    # ------------------------------------------------------------------
    # Sync helpers (run via asyncio.to_thread).

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        # Nested dicts/lists get json.dumps-encoded (NOT str()-coerced —
        # str(dict) produces Python repr with single quotes which is not
        # valid JSON). Matches the OAuth-blob convention: a skill that
        # stored a JSON-encoded blob via set() reads back the same bytes
        # via get(); a hand-edited values.json with nested literals also
        # round-trips through json.loads cleanly.
        return {
            str(k): json.dumps(v) if isinstance(v, dict | list) else str(v)
            for k, v in data.items()
            if v is not None
        }

    def _write_one(self, key: str, value: str) -> None:
        existing = self._read_unfiltered()
        existing[key] = value
        self._atomic_write(existing)

    def _delete_one(self, key: str) -> None:
        existing = self._read_unfiltered()
        existing.pop(key, None)
        self._atomic_write(existing)

    def _read_unfiltered(self) -> dict[str, Any]:
        """Read the file as-is for read-modify-write paths.

        ``_read`` coerces nested values for callers; that coercion would
        be lossy on rewrite (re-encoding an already-encoded JSON string
        would double-quote it). For RMW we keep the raw shape and let
        callers decide what to write back via :meth:`set`.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _atomic_write(self, data: dict[str, Any]) -> None:
        # Ensure parent dirs exist; lock perms to 0700 even if parent
        # already existed with looser perms. Filesystems that don't
        # support chmod (Windows, some FUSE) silently fall back —
        # `<persona>/data/` is gitignored regardless.
        self._dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._dir.chmod(0o700)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(self._path)
