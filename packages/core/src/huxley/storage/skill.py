"""Per-skill storage adapter that namespaces keys against the framework store.

The SDK's `SkillStorage` protocol exposes a flat `get_setting`/`set_setting`
KV interface. The framework wraps its `Storage` with this adapter, prepending
`<skill_name>:` to every key so two skills can both store `last_id` without
colliding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huxley.storage.db import Storage


class NamespacedSkillStorage:
    """Adapter satisfying `huxley_sdk.SkillStorage` over framework `Storage`.

    Keys are stored as `<namespace>:<key>` in the underlying settings table.
    Skills see only their own namespace.
    """

    def __init__(self, base: Storage, namespace: str) -> None:
        self._base = base
        self._prefix = f"{namespace}:"

    async def get_setting(self, key: str) -> str | None:
        return await self._base.get_setting(self._prefix + key)

    async def set_setting(self, key: str, value: str) -> None:
        await self._base.set_setting(self._prefix + key, value)
