"""Tests for the SQLite storage layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from huxley.storage.db import SCHEMA_VERSION, Storage

if TYPE_CHECKING:
    from pathlib import Path


class TestAudiobookProgress:
    async def test_default_position_is_zero(self, storage: Storage) -> None:
        pos = await storage.get_audiobook_position("nonexistent")
        assert pos == 0.0

    async def test_save_and_retrieve_position(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_1", 123.45)
        pos = await storage.get_audiobook_position("book_1")
        assert pos == 123.45

    async def test_update_existing_position(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_1", 100.0)
        await storage.save_audiobook_position("book_1", 200.0)
        pos = await storage.get_audiobook_position("book_1")
        assert pos == 200.0

    async def test_multiple_books_independent(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_a", 10.0)
        await storage.save_audiobook_position("book_b", 20.0)
        assert await storage.get_audiobook_position("book_a") == 10.0
        assert await storage.get_audiobook_position("book_b") == 20.0


class TestConversationSummaries:
    async def test_no_summary_returns_none(self, storage: Storage) -> None:
        result = await storage.get_latest_summary()
        assert result is None

    async def test_save_and_retrieve_summary(self, storage: Storage) -> None:
        await storage.save_summary("User was asking about books")
        result = await storage.get_latest_summary()
        assert result == "User was asking about books"

    async def test_latest_summary_is_most_recent(self, storage: Storage) -> None:
        await storage.save_summary("First conversation")
        await storage.save_summary("Second conversation")
        result = await storage.get_latest_summary()
        assert result == "Second conversation"


class TestSettings:
    async def test_default_when_missing(self, storage: Storage) -> None:
        result = await storage.get_setting("missing_key", default="fallback")
        assert result == "fallback"

    async def test_none_when_missing_no_default(self, storage: Storage) -> None:
        result = await storage.get_setting("missing_key")
        assert result is None

    async def test_save_and_retrieve(self, storage: Storage) -> None:
        await storage.set_setting("volume", "80")
        result = await storage.get_setting("volume")
        assert result == "80"

    async def test_update_existing(self, storage: Storage) -> None:
        await storage.set_setting("volume", "80")
        await storage.set_setting("volume", "50")
        result = await storage.get_setting("volume")
        assert result == "50"


class TestListAndDelete:
    async def test_list_empty_returns_empty(self, storage: Storage) -> None:
        assert await storage.list_settings("timer:") == []

    async def test_list_matches_prefix(self, storage: Storage) -> None:
        await storage.set_setting("timer:1", "a")
        await storage.set_setting("timer:2", "b")
        await storage.set_setting("other", "c")
        rows = await storage.list_settings("timer:")
        assert rows == [("timer:1", "a"), ("timer:2", "b")]

    async def test_list_empty_prefix_returns_all(self, storage: Storage) -> None:
        await storage.set_setting("x", "1")
        await storage.set_setting("y", "2")
        rows = await storage.list_settings()
        keys = [k for k, _ in rows]
        assert "x" in keys
        assert "y" in keys

    async def test_list_escapes_wildcards_in_prefix(self, storage: Storage) -> None:
        # Keys containing `%` or `_` must not glob when used as prefix.
        await storage.set_setting("a%b:1", "first")
        await storage.set_setting("axb:1", "should_not_match")
        rows = await storage.list_settings("a%b:")
        assert rows == [("a%b:1", "first")]

    async def test_delete_removes_key(self, storage: Storage) -> None:
        await storage.set_setting("tombstone", "v")
        await storage.delete_setting("tombstone")
        assert await storage.get_setting("tombstone") is None

    async def test_delete_missing_is_noop(self, storage: Storage) -> None:
        # Should not raise on a key that never existed.
        await storage.delete_setting("never_set")


class TestNamespacedSkillStorage:
    async def test_namespace_isolation(self, storage: Storage) -> None:
        from huxley.storage.skill import NamespacedSkillStorage

        ns_a = NamespacedSkillStorage(storage, "timers")
        ns_b = NamespacedSkillStorage(storage, "reminders")
        await ns_a.set_setting("1", "A")
        await ns_b.set_setting("1", "B")
        assert await ns_a.get_setting("1") == "A"
        assert await ns_b.get_setting("1") == "B"

    async def test_list_strips_namespace_prefix(self, storage: Storage) -> None:
        from huxley.storage.skill import NamespacedSkillStorage

        ns = NamespacedSkillStorage(storage, "timers")
        await ns.set_setting("timer:1", "x")
        await ns.set_setting("timer:2", "y")
        rows = await ns.list_settings("timer:")
        # Caller sees keys WITHOUT the namespace prefix.
        assert rows == [("timer:1", "x"), ("timer:2", "y")]

    async def test_list_scoped_to_namespace(self, storage: Storage) -> None:
        from huxley.storage.skill import NamespacedSkillStorage

        ns_a = NamespacedSkillStorage(storage, "timers")
        ns_b = NamespacedSkillStorage(storage, "reminders")
        await ns_a.set_setting("k", "A")
        await ns_b.set_setting("k", "B")
        # Listing ns_a must not see ns_b entries.
        assert await ns_a.list_settings() == [("k", "A")]

    async def test_delete_scoped_to_namespace(self, storage: Storage) -> None:
        from huxley.storage.skill import NamespacedSkillStorage

        ns_a = NamespacedSkillStorage(storage, "timers")
        ns_b = NamespacedSkillStorage(storage, "reminders")
        await ns_a.set_setting("shared", "A")
        await ns_b.set_setting("shared", "B")
        await ns_a.delete_setting("shared")
        assert await ns_a.get_setting("shared") is None
        # Same key under a different namespace must still exist.
        assert await ns_b.get_setting("shared") == "B"


class TestWalAndSchemaVersion:
    """T2.1 — WAL mode + schema versioning."""

    async def test_journal_mode_is_wal(self, storage: Storage) -> None:
        cursor = await storage._conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"

    async def test_schema_version_recorded_on_fresh_db(self, storage: Storage) -> None:
        cursor = await storage._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION

    async def test_schema_version_idempotent_on_reinit(self, tmp_db_path: Path) -> None:
        # First init writes the version; second init must not duplicate or
        # mutate the row, and must not raise.
        s1 = Storage(tmp_db_path)
        await s1.init()
        await s1.close()

        s2 = Storage(tmp_db_path)
        await s2.init()
        try:
            cursor = await s2._conn.execute(
                "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_version'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1
        finally:
            await s2.close()

    async def test_schema_version_mismatch_logged_not_crashed(self, tmp_db_path: Path) -> None:
        # Simulate a future-version DB on disk being opened by older code.
        s1 = Storage(tmp_db_path)
        await s1.init()
        await s1._conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 99),),
        )
        await s1._conn.commit()
        await s1.close()

        s2 = Storage(tmp_db_path)
        # Must not raise — older code proceeds, just logs the drift.
        await s2.init()
        await s2.close()
