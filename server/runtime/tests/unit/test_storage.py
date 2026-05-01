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


class TestTimerPersistenceEndToEnd:
    """Cross-cutting test: real TimersSkill over real SQLite via the real
    NamespacedSkillStorage adapter.

    The skill's own test suite uses `_NoopSkillStorage` (dict-backed) so
    skill logic is tested in isolation; the storage suite above tests
    the adapter against real SQLite. Neither proves they compose. This
    test closes that gap by exercising the full write → persist →
    cross-process-equivalent-restart → restore path against the actual
    `Storage` implementation.

    Pattern mirrors `test_coordinator_skill_integration.py` — a core
    test may reach into an entry-point skill to prove framework
    plumbing end-to-end.
    """

    async def test_set_persists_through_real_storage_and_restores(self, storage: Storage) -> None:
        import json
        from datetime import UTC, datetime, timedelta

        from huxley.storage.skill import NamespacedSkillStorage
        from huxley_sdk.testing import make_test_context
        from huxley_skill_timers.skill import TimersSkill

        # --- Session 1: write a pending entry, then tear down (simulating
        # a server shutdown before the timer fires). Use a far-future
        # fire_at so the test doesn't need to deal with timing.
        ns = NamespacedSkillStorage(storage, "timers")
        skill_1 = TimersSkill()
        ctx_1 = make_test_context(storage=ns)
        await skill_1.setup(ctx_1)
        fire_at = datetime.now(UTC) + timedelta(hours=1)
        # Hand-write the entry in the same shape `set_timer` would, so
        # we don't have to wait on real `asyncio.sleep`. Tests that the
        # restore READ path works; the write path is covered separately.
        await ns.set_setting(
            "timer:1",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "medication",
                    "fired_at": None,
                }
            ),
        )
        # Skill teardown does NOT delete the entry — that's what persistence
        # relies on.
        await skill_1.teardown()

        # --- Verify the entry really lives in SQLite (not just in a stub).
        rows = await storage.list_settings("timers:")
        assert any(k == "timers:timer:1" for k, _ in rows), rows

        # --- Session 2: fresh skill instance over the SAME storage.
        # `setup()` must discover the entry via list_settings and reschedule.
        skill_2 = TimersSkill()
        ctx_2 = make_test_context(storage=ns)
        await skill_2.setup(ctx_2)
        assert 1 in skill_2._handles, "restored handle missing"
        # _next_id primed past the restored entry's id.
        assert skill_2._next_id == 2
        await skill_2.teardown()

    async def test_fired_at_dedup_through_real_storage(self, storage: Storage) -> None:
        """A crash-surviving entry with `fired_at` set must drop on
        restore via real SQLite — not just through the `_NoopSkillStorage`
        path the skill unit tests use."""
        import json
        from datetime import UTC, datetime

        from huxley.storage.skill import NamespacedSkillStorage
        from huxley_sdk.testing import make_test_context
        from huxley_skill_timers.skill import TimersSkill

        ns = NamespacedSkillStorage(storage, "timers")
        now = datetime.now(UTC)
        await ns.set_setting(
            "timer:7",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": now.isoformat(),
                    "message": "dose",
                    "fired_at": now.isoformat(),  # critical dedup marker
                }
            ),
        )
        skill = TimersSkill()
        ctx = make_test_context(storage=ns)
        await skill.setup(ctx)
        assert 7 not in skill._handles, "fired_at entry should not have been rescheduled"
        # Entry deleted from real SQLite, not just from a stub.
        assert await storage.list_settings("timers:") == []
        await skill.teardown()


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


class TestSkillSchemaVersion:
    """T1.14 — per-skill data_schema_version persistence in schema_meta.

    Behavior pinned in docs/skill-marketplace.md § Schema versioning:
    first boot writes silently; equal-version is a no-op; mismatch
    logs warning and writes the new declared version.
    """

    async def test_skill_version_absent_returns_none(self, storage: Storage) -> None:
        assert await storage.get_skill_schema_version("audiobooks") is None

    async def test_set_then_get_skill_version(self, storage: Storage) -> None:
        await storage.set_skill_schema_version("audiobooks", 3)
        assert await storage.get_skill_schema_version("audiobooks") == 3

    async def test_set_overwrites_skill_version(self, storage: Storage) -> None:
        await storage.set_skill_schema_version("audiobooks", 1)
        await storage.set_skill_schema_version("audiobooks", 2)
        assert await storage.get_skill_schema_version("audiobooks") == 2

    async def test_skill_versions_isolated_per_skill(self, storage: Storage) -> None:
        await storage.set_skill_schema_version("audiobooks", 5)
        await storage.set_skill_schema_version("news", 1)
        assert await storage.get_skill_schema_version("audiobooks") == 5
        assert await storage.get_skill_schema_version("news") == 1

    async def test_skill_versions_dont_collide_with_schema_version(self, storage: Storage) -> None:
        # The framework's own schema_version key must not be touched by
        # per-skill writes; both share the schema_meta table.
        cursor = await storage._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        before = row[0] if row else None
        await storage.set_skill_schema_version("audiobooks", 99)
        cursor = await storage._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        after = row[0] if row else None
        assert before == after


class TestSessions:
    """T1.12 — session history: persistence + retrieval.

    Boundary semantics: WS-connect/disconnect is the technical lifecycle;
    one user-visible "conversation" can span multiple connects so long as
    consecutive turns fall within `idle_window_min` of each other. See
    docs/triage.md T1.12 for the design + critic notes.
    """

    async def test_start_creates_new_session_when_no_history(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        assert sid >= 1
        sessions = await storage.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].id == sid

    async def test_resume_returns_same_id_within_window(self, storage: Storage) -> None:
        first = await storage.start_or_resume_session()
        await storage.record_turn(first, "user", "hola")
        # No DB-time manipulation: the turn was just recorded, so a follow-up
        # call within the default 30-min window must return the same row.
        second = await storage.start_or_resume_session()
        assert second == first

    async def test_new_session_after_idle_window_expires(self, storage: Storage) -> None:
        first = await storage.start_or_resume_session()
        await storage.record_turn(first, "user", "hola")
        # Backdate the last turn beyond the window directly in the DB.
        await storage._conn.execute(
            "UPDATE sessions SET last_turn_at = datetime('now', '-2 hours') WHERE id = ?",
            (first,),
        )
        await storage._conn.commit()
        second = await storage.start_or_resume_session(idle_window_min=30)
        assert second != first

    async def test_resume_clears_ended_at(self, storage: Storage) -> None:
        # End the session, then resume within the window: ended_at should
        # be cleared so the row reads as "live again" until the next end.
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "hola")
        await storage.end_session(sid, summary="first")
        resumed = await storage.start_or_resume_session()
        assert resumed == sid
        cursor = await storage._conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (sid,))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None

    async def test_record_turn_appends_in_order(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "primero")
        await storage.record_turn(sid, "assistant", "respuesta")
        await storage.record_turn(sid, "user", "tercero")
        turns = await storage.get_session_turns(sid)
        assert [t.idx for t in turns] == [0, 1, 2]
        assert [t.role for t in turns] == ["user", "assistant", "user"]
        assert [t.text for t in turns] == ["primero", "respuesta", "tercero"]

    async def test_turn_count_increments(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "a")
        await storage.record_turn(sid, "assistant", "b")
        sessions = await storage.list_sessions()
        match = next(s for s in sessions if s.id == sid)
        assert match.turn_count == 2

    async def test_preview_set_only_on_first_user_turn(self, storage: Storage) -> None:
        # Proactive turn lands first (assistant). preview must stay null
        # until a user turn arrives.
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "assistant", "Hora de tu medicina")
        sessions = await storage.list_sessions()
        assert next(s for s in sessions if s.id == sid).preview is None

        await storage.record_turn(sid, "user", "ya la tomé")
        sessions = await storage.list_sessions()
        assert next(s for s in sessions if s.id == sid).preview == "ya la tomé"

        # Subsequent user turns don't overwrite the preview.
        await storage.record_turn(sid, "user", "qué mas?")
        sessions = await storage.list_sessions()
        assert next(s for s in sessions if s.id == sid).preview == "ya la tomé"

    async def test_end_session_sets_ended_at_and_summary(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "hola")
        await storage.end_session(sid, summary="conversó sobre saludos")
        cursor = await storage._conn.execute(
            "SELECT ended_at, summary FROM sessions WHERE id = ?", (sid,)
        )
        row = await cursor.fetchone()
        assert row is not None
        ended_at, summary = row
        assert ended_at is not None
        assert summary == "conversó sobre saludos"

    async def test_end_session_with_null_summary_is_allowed(self, storage: Storage) -> None:
        # Provider may not generate a summary (e.g. session ended with no
        # transcript). end_session must accept summary=None gracefully.
        sid = await storage.start_or_resume_session()
        await storage.end_session(sid, summary=None)
        cursor = await storage._conn.execute("SELECT summary FROM sessions WHERE id = ?", (sid,))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None

    async def test_list_sessions_descending_by_id(self, storage: Storage) -> None:
        s1 = await storage.start_or_resume_session()
        await storage.record_turn(s1, "user", "a")
        # Force expiry so the next call creates a new row.
        await storage._conn.execute(
            "UPDATE sessions SET last_turn_at = datetime('now', '-2 hours') WHERE id = ?",
            (s1,),
        )
        await storage._conn.commit()
        s2 = await storage.start_or_resume_session()
        sessions = await storage.list_sessions()
        ids = [s.id for s in sessions]
        assert ids == [s2, s1]

    async def test_list_sessions_respects_limit(self, storage: Storage) -> None:
        for _ in range(5):
            sid = await storage.start_or_resume_session()
            await storage.record_turn(sid, "user", "x")
            await storage._conn.execute(
                "UPDATE sessions SET last_turn_at = datetime('now', '-2 hours') WHERE id = ?",
                (sid,),
            )
            await storage._conn.commit()
        sessions = await storage.list_sessions(limit=3)
        assert len(sessions) == 3

    async def test_get_session_turns_empty_for_nonexistent(self, storage: Storage) -> None:
        assert await storage.get_session_turns(99999) == []

    async def test_delete_session_cascades_to_turns(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "a")
        await storage.record_turn(sid, "assistant", "b")
        await storage.delete_session(sid)
        sessions = await storage.list_sessions()
        assert all(s.id != sid for s in sessions)
        assert await storage.get_session_turns(sid) == []

    async def test_clear_summaries_nullifies_preserves_rows(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "a")
        await storage.end_session(sid, summary="some summary")
        await storage.clear_summaries()
        # Row still present.
        sessions = await storage.list_sessions()
        assert any(s.id == sid for s in sessions)
        # Summary nulled.
        cursor = await storage._conn.execute("SELECT summary FROM sessions WHERE id = ?", (sid,))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None

    async def test_get_latest_summary_skips_null_summaries(self, storage: Storage) -> None:
        # Two sessions; only the older one has a summary.
        s1 = await storage.start_or_resume_session()
        await storage.record_turn(s1, "user", "a")
        await storage.end_session(s1, summary="older summary")
        await storage._conn.execute(
            "UPDATE sessions SET last_turn_at = datetime('now', '-2 hours') WHERE id = ?",
            (s1,),
        )
        await storage._conn.commit()
        s2 = await storage.start_or_resume_session()
        await storage.record_turn(s2, "user", "b")
        # s2 not finalized; summary IS NULL. get_latest_summary must
        # walk past the NULL row and return s1's summary.
        latest = await storage.get_latest_summary()
        assert latest == "older summary"

    async def test_get_latest_summary_returns_none_when_all_null(self, storage: Storage) -> None:
        sid = await storage.start_or_resume_session()
        await storage.record_turn(sid, "user", "x")
        await storage.end_session(sid, summary="only summary")
        await storage.clear_summaries()
        # After reset, no summary should be loadable.
        assert await storage.get_latest_summary() is None

    # ── The gold test (per critic, the "single regression catch-all") ──
    async def test_gold_resume_within_window_is_one_logical_session(
        self, storage: Storage
    ) -> None:
        """Connect → 2 user turns → disconnect → reconnect within window
        → 1 more user turn → disconnect.

        Asserts: list_sessions returns ONE row, transcript contains all 3
        user turns in order, summary is the one written on the SECOND
        disconnect.
        """
        # First "connect"
        sid1 = await storage.start_or_resume_session()
        await storage.record_turn(sid1, "user", "hola")
        await storage.record_turn(sid1, "user", "qué tal")
        await storage.end_session(sid1, summary="primera ronda")

        # Second "connect" — within idle window (no backdating).
        sid2 = await storage.start_or_resume_session()
        assert sid2 == sid1, "reconnect within idle window must resume the same session row"
        await storage.record_turn(sid2, "user", "y de comida?")
        await storage.end_session(sid2, summary="segunda ronda")

        sessions = await storage.list_sessions()
        assert len(sessions) == 1, f"expected one logical conversation, got {len(sessions)} rows"

        turns = await storage.get_session_turns(sid1)
        user_turns = [t.text for t in turns if t.role == "user"]
        assert user_turns == ["hola", "qué tal", "y de comida?"]

        # Final summary is the SECOND disconnect's summary.
        cursor = await storage._conn.execute("SELECT summary FROM sessions WHERE id = ?", (sid1,))
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "segunda ronda"


class TestSessionsMigrationFromV1:
    """T1.12 — schema v1 (conversation_summaries) → v2 (sessions) migration.

    Existing summaries become synthetic sessions rows so warm-reconnect
    context still loads after the upgrade.
    """

    async def test_v1_summary_migrates_into_synthetic_session(self, tmp_db_path: Path) -> None:
        # Manually construct a v1 DB with one summary row, then open it
        # with the new code.
        import aiosqlite

        async with aiosqlite.connect(tmp_db_path) as legacy:
            await legacy.executescript(
                """
                CREATE TABLE conversation_summaries (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE schema_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES('schema_version', '1');
                INSERT INTO conversation_summaries(summary)
                VALUES('legacy summary text');
                """
            )
            await legacy.commit()

        s = Storage(tmp_db_path)
        await s.init()
        try:
            # Migration ran on init; latest summary still loadable.
            assert await s.get_latest_summary() == "legacy summary text"
            # And it's now a row in the new sessions table.
            sessions = await s.list_sessions()
            assert len(sessions) == 1
            cursor = await s._conn.execute(
                "SELECT summary FROM sessions WHERE id = ?", (sessions[0].id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "legacy summary text"
        finally:
            await s.close()

    async def test_old_table_dropped_after_migration(self, tmp_db_path: Path) -> None:
        import aiosqlite

        async with aiosqlite.connect(tmp_db_path) as legacy:
            await legacy.executescript(
                """
                CREATE TABLE conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE schema_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES('schema_version', '1');
                """
            )
            await legacy.commit()

        s = Storage(tmp_db_path)
        await s.init()
        try:
            cursor = await s._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='conversation_summaries'"
            )
            row = await cursor.fetchone()
            assert row is None, "conversation_summaries should be dropped after migration"
        finally:
            await s.close()
