"""Tests for the per-skill data_schema_version check + persist flow.

The runtime's :meth:`Application._check_skill_schema_versions` and
:meth:`Application._persist_skill_schema_versions` together implement
the contract pinned in ``docs/skill-marketplace.md`` § Schema versioning.

Critical invariants this file pins:

1. Equal-version is silent (no log events) — protects T1.13 hot-swap
   from per-swap log noise.
2. Persist runs AFTER setup_all succeeds — a torn setup leaves
   ``schema_meta`` at the OLD version so the next boot re-warns.
3. First-boot is silent: no event on ``stored=None``; the persist
   pass writes the declared version.
4. Mismatch (upgrade or downgrade) emits exactly one structured event
   per skill.
5. A non-numeric ``data_schema_version`` declared on a skill emits a
   single ``invalid_declared_version`` error and otherwise no-ops.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import structlog

from huxley.app import Application

if TYPE_CHECKING:
    from pathlib import Path

    from huxley.storage.db import Storage


class _FakeSkill:
    """A minimal skill stub — just ``name`` and an optional
    ``data_schema_version``. The runtime methods read both via
    ``getattr`` so the absence of other Skill members is fine."""

    def __init__(self, name: str, data_schema_version: object = 1) -> None:
        self.name = name
        # Use object so tests can inject non-int values.
        self.data_schema_version = data_schema_version


def _make_app(skills: list[_FakeSkill], storage: Storage) -> Application:
    """Build an Application-shaped object exposing only the slice the
    schema-version methods touch (`self.skill_registry.skills` and
    `self.storage`). We bypass `Application.__init__` because the real
    constructor wires audio servers, focus managers, OpenAI clients, and
    a dozen other things this test doesn't care about — and any of
    those breaking would cause false test failures."""
    app = Application.__new__(Application)
    app.skill_registry = SimpleNamespace(skills=skills)  # type: ignore[assignment]
    app.storage = storage  # type: ignore[assignment]
    return app


@pytest.fixture
async def storage(tmp_db_path: Path) -> Storage:
    from huxley.storage.db import Storage

    s = Storage(tmp_db_path)
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_first_boot_is_silent_then_persists(storage: Storage) -> None:
    skills = [_FakeSkill("audiobooks", 3)]
    app = _make_app(skills, storage)

    with structlog.testing.capture_logs() as logs:
        await app._check_skill_schema_versions()
        await app._persist_skill_schema_versions()

    # No events from check (first boot is silent); persist writes the
    # declared version. After this, stored == declared.
    assert [e for e in logs if e["event"].startswith("skill.schema.")] == []
    assert await storage.get_skill_schema_version("audiobooks") == 3


@pytest.mark.asyncio
async def test_equal_version_is_silent_no_op(storage: Storage) -> None:
    # Pre-populate stored == declared.
    await storage.set_skill_schema_version("audiobooks", 3)
    skills = [_FakeSkill("audiobooks", 3)]
    app = _make_app(skills, storage)

    with structlog.testing.capture_logs() as logs:
        await app._check_skill_schema_versions()
        await app._persist_skill_schema_versions()

    assert [e for e in logs if e["event"].startswith("skill.schema.")] == []
    assert await storage.get_skill_schema_version("audiobooks") == 3


@pytest.mark.asyncio
async def test_three_consecutive_swaps_silent_after_first(storage: Storage) -> None:
    """The DoD test from docs/skill-marketplace.md § Schema versioning:
    swap personas 3x, no skill.schema.* events fire after the first
    boot of each (skill, persona) pair. Pre-populate stored=2 to
    simulate "this persona has seen this skill at v2 before"; declared
    is 2 throughout, so all three calls must be silent."""
    await storage.set_skill_schema_version("audiobooks", 2)
    app = _make_app([_FakeSkill("audiobooks", 2)], storage)

    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            await app._check_skill_schema_versions()
            await app._persist_skill_schema_versions()

    schema_events = [e for e in logs if e["event"].startswith("skill.schema.")]
    assert schema_events == [], f"expected no events; got {schema_events}"


@pytest.mark.asyncio
async def test_upgrade_emits_warning_then_persist_writes(storage: Storage) -> None:
    await storage.set_skill_schema_version("audiobooks", 1)
    app = _make_app([_FakeSkill("audiobooks", 2)], storage)

    with structlog.testing.capture_logs() as logs:
        await app._check_skill_schema_versions()

    upgrade_events = [e for e in logs if e["event"] == "skill.schema.upgrade_needed"]
    assert len(upgrade_events) == 1
    assert upgrade_events[0]["declared"] == 2
    assert upgrade_events[0]["stored"] == 1

    # Check is read-only — stored is still 1 until persist runs.
    assert await storage.get_skill_schema_version("audiobooks") == 1

    await app._persist_skill_schema_versions()
    assert await storage.get_skill_schema_version("audiobooks") == 2


@pytest.mark.asyncio
async def test_torn_setup_leaves_stored_at_old_version(storage: Storage) -> None:
    """The check-vs-persist split's reason for existing: if setup_all
    throws between check and persist, the stored version stays at the
    old value, so the next boot re-warns the same way. Without this
    split, a torn upgrade silently advances stored and the warning is
    lost."""
    await storage.set_skill_schema_version("audiobooks", 1)
    app = _make_app([_FakeSkill("audiobooks", 2)], storage)

    # Simulate the production sequence: check, then setup throws,
    # persist NEVER runs.
    with structlog.testing.capture_logs():
        await app._check_skill_schema_versions()
        # ... skill.setup() raises here ...
        # persist deliberately not called.

    # Stored still at the old version. Next boot will re-warn.
    assert await storage.get_skill_schema_version("audiobooks") == 1


@pytest.mark.asyncio
async def test_downgrade_emits_warning(storage: Storage) -> None:
    await storage.set_skill_schema_version("audiobooks", 5)
    app = _make_app([_FakeSkill("audiobooks", 3)], storage)

    with structlog.testing.capture_logs() as logs:
        await app._check_skill_schema_versions()

    downgrade_events = [e for e in logs if e["event"] == "skill.schema.downgrade_detected"]
    assert len(downgrade_events) == 1


@pytest.mark.asyncio
async def test_invalid_declared_version_logs_error_and_skips(storage: Storage) -> None:
    """A skill that declares a non-numeric ``data_schema_version`` is
    a developer bug. The runtime logs once and then no-ops — never
    crashes startup."""
    skills = [_FakeSkill("buggy", "not a number")]
    app = _make_app(skills, storage)

    with structlog.testing.capture_logs() as logs:
        await app._check_skill_schema_versions()
        await app._persist_skill_schema_versions()

    invalid_events = [e for e in logs if e["event"] == "skill.schema.invalid_declared_version"]
    assert len(invalid_events) == 1
    # No version was persisted because we couldn't parse declared.
    assert await storage.get_skill_schema_version("buggy") is None


@pytest.mark.asyncio
async def test_skill_without_declared_version_uses_default_one(storage: Storage) -> None:
    # _FakeSkill's default constructor sets data_schema_version=1.
    skills = [_FakeSkill("vanilla")]
    app = _make_app(skills, storage)

    await app._check_skill_schema_versions()
    await app._persist_skill_schema_versions()

    assert await storage.get_skill_schema_version("vanilla") == 1


@pytest.mark.asyncio
async def test_multiple_skills_isolated(storage: Storage) -> None:
    skills = [
        _FakeSkill("audiobooks", 3),
        _FakeSkill("news", 1),
        _FakeSkill("radio", 7),
    ]
    app = _make_app(skills, storage)

    await app._check_skill_schema_versions()
    await app._persist_skill_schema_versions()

    assert await storage.get_skill_schema_version("audiobooks") == 3
    assert await storage.get_skill_schema_version("news") == 1
    assert await storage.get_skill_schema_version("radio") == 7
