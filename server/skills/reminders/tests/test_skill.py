"""Unit tests for `huxley-skill-reminders`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from huxley_sdk.testing import _NoopSkillStorage, make_test_context
from huxley_skill_reminders.skill import (
    _DEFAULT_LATE_WINDOWS,
    _MEDICATION_RETRY_INTERVALS,
    _STATE_ACKED,
    _STATE_CANCELLED,
    _STATE_FIRED,
    _STATE_MISSED,
    _STATE_PENDING,
    _STORAGE_PREFIX,
    RemindersSkill,
    _Entry,
)

if TYPE_CHECKING:
    from huxley_sdk import SkillStorage


async def _drain(ticks: int = 5) -> None:
    """Yield the event loop so background tasks can interleave with awaits."""
    for _ in range(ticks):
        await asyncio.sleep(0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _future_iso(seconds: int = 3600) -> str:
    return _iso(datetime.now(UTC) + timedelta(seconds=seconds))


async def _make_skill(
    *,
    storage: SkillStorage | None = None,
    inject_turn: AsyncMock | None = None,
    config: dict[str, object] | None = None,
    language: str = "es",
    start_scheduler: bool = True,
) -> tuple[RemindersSkill, AsyncMock, SkillStorage]:
    """Build a RemindersSkill wired to a recording inject_turn mock.

    Default config gives the persona timezone label "America/Bogota" so
    prompt_context tests have a concrete value to assert against.

    Tests that exercise the scheduler set `start_scheduler=True` (the
    default — matches production behavior) and either manage the
    scheduler explicitly via `_wakeup` + `_drain` or call teardown() in
    cleanup. Tests that don't need the scheduler can skip setup
    altogether and call individual handlers directly, but the default
    is to run setup so we keep coverage on the boot path.
    """
    skill = RemindersSkill()
    inject_mock = inject_turn or AsyncMock()
    cfg = dict(config) if config else {}
    cfg.setdefault("timezone", "America/Bogota")
    storage_ = storage if storage is not None else _NoopSkillStorage()
    ctx = make_test_context(config=cfg, storage=storage_, language=language)
    object.__setattr__(ctx, "inject_turn", inject_mock)
    if start_scheduler:
        await skill.setup(ctx)
    else:
        # Minimal wiring so handlers work without spawning the loop.
        # Mirrors what setup() does, less the background_task call.
        skill._logger = ctx.logger
        skill._inject_turn = ctx.inject_turn
        skill._background_task = ctx.background_task
        skill._storage = ctx.storage
        skill._language = ctx.language or "en"
        skill._timezone_label = "America/Bogota"
        skill._fire_prompt = "Recordatorio: avísale al usuario sobre {message} ({kind})."
        skill._late_windows = skill._resolve_late_windows(ctx)
    return skill, inject_mock, storage_


# ---------------------------------------------------------------- add_reminder


class TestAddReminder:
    async def test_happy_path(self) -> None:
        skill, _, storage = await _make_skill()
        result = await skill.handle(
            "add_reminder",
            {
                "message": "tomar la pastilla del corazón",
                "when_iso": _future_iso(3600),
                "kind": "medication",
                "recurrence": "daily",
            },
        )
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["id"] == 1
        assert payload["kind"] == "medication"
        assert payload["recurrence"] == "daily"
        # Persisted under reminder:1.
        rows = await storage.list_settings(_STORAGE_PREFIX)
        ids = [k for k, _ in rows if k == "reminder:1"]
        assert ids == ["reminder:1"]
        await skill.teardown()

    async def test_rejects_empty_message(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("add_reminder", {"message": "", "when_iso": _future_iso()})
        assert "error" in json.loads(result.output)

    async def test_rejects_missing_when(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("add_reminder", {"message": "x"})
        assert "error" in json.loads(result.output)

    async def test_rejects_naive_datetime(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        # No timezone offset.
        naive = datetime.now().isoformat()
        result = await skill.handle("add_reminder", {"message": "x", "when_iso": naive})
        assert "error" in json.loads(result.output)

    async def test_rejects_past_time(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        result = await skill.handle("add_reminder", {"message": "x", "when_iso": past})
        assert "error" in json.loads(result.output)

    async def test_rejects_invalid_kind(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle(
            "add_reminder",
            {"message": "x", "when_iso": _future_iso(), "kind": "weird"},
        )
        assert "error" in json.loads(result.output)

    async def test_rejects_invalid_recurrence(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle(
            "add_reminder",
            {"message": "x", "when_iso": _future_iso(), "recurrence": "biweekly"},
        )
        assert "error" in json.loads(result.output)

    async def test_default_kind_is_generic(self) -> None:
        skill, _, _ = await _make_skill()
        result = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        assert json.loads(result.output)["kind"] == "generic"
        await skill.teardown()

    async def test_unique_ids_across_calls(self) -> None:
        skill, _, _ = await _make_skill()
        r1 = await skill.handle("add_reminder", {"message": "a", "when_iso": _future_iso()})
        r2 = await skill.handle("add_reminder", {"message": "b", "when_iso": _future_iso()})
        assert json.loads(r1.output)["id"] == 1
        assert json.loads(r2.output)["id"] == 2
        await skill.teardown()


# -------------------------------------------------------------- list_reminders


class TestListReminders:
    async def test_empty_when_no_reminders(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("list_reminders", {})
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["reminders"] == []

    async def test_lists_pending_in_chronological_order(self) -> None:
        skill, _, _ = await _make_skill()
        await skill.handle("add_reminder", {"message": "later", "when_iso": _future_iso(7200)})
        await skill.handle("add_reminder", {"message": "sooner", "when_iso": _future_iso(60)})
        result = await skill.handle("list_reminders", {})
        rows = json.loads(result.output)["reminders"]
        assert [r["message"] for r in rows] == ["sooner", "later"]
        await skill.teardown()

    async def test_excludes_acked_and_cancelled(self) -> None:
        skill, _, _ = await _make_skill()
        await skill.handle("add_reminder", {"message": "keep", "when_iso": _future_iso()})
        r = await skill.handle("add_reminder", {"message": "drop", "when_iso": _future_iso()})
        drop_id = json.loads(r.output)["id"]
        await skill.handle("cancel_reminder", {"id": drop_id})
        result = await skill.handle("list_reminders", {})
        messages = [r["message"] for r in json.loads(result.output)["reminders"]]
        assert messages == ["keep"]
        await skill.teardown()


# -------------------------------------------------------------- cancel_reminder


class TestCancelReminder:
    async def test_cancels_pending_reminder(self) -> None:
        skill, _, storage = await _make_skill()
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        rid = json.loads(r.output)["id"]
        await skill.handle("cancel_reminder", {"id": rid})
        # State persisted as cancelled.
        raw = await storage.get_setting(f"reminder:{rid}")
        assert raw is not None
        entry = _Entry.from_json(raw)
        assert entry.state == _STATE_CANCELLED
        assert entry.cancelled_at is not None
        await skill.teardown()

    async def test_cancel_unknown_id_returns_error(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("cancel_reminder", {"id": 999})
        assert "error" in json.loads(result.output)

    async def test_cancel_already_terminal_is_noop(self) -> None:
        skill, _, _ = await _make_skill()
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        rid = json.loads(r.output)["id"]
        await skill.handle("cancel_reminder", {"id": rid})
        # Second cancel succeeds (already terminal) without raising.
        result = await skill.handle("cancel_reminder", {"id": rid})
        payload = json.loads(result.output)
        assert payload["ok"] is True
        await skill.teardown()


# ------------------------------------------------------------- snooze_reminder


class TestSnoozeReminder:
    async def test_snooze_reschedules(self) -> None:
        skill, _, storage = await _make_skill()
        # Originally fires in 60s.
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso(60)})
        rid = json.loads(r.output)["id"]
        before = await storage.get_setting(f"reminder:{rid}")
        assert before is not None
        original = _Entry.from_json(before)

        await skill.handle("snooze_reminder", {"id": rid, "minutes": 5})
        after = await storage.get_setting(f"reminder:{rid}")
        assert after is not None
        snoozed = _Entry.from_json(after)
        # Original fire was ~60s away; snoozed should be ~5min from now.
        assert snoozed.next_fire_at > original.next_fire_at
        # Within 1s of expected.
        expected = datetime.now(UTC) + timedelta(minutes=5)
        assert abs((snoozed.next_fire_at - expected).total_seconds()) < 2
        await skill.teardown()

    async def test_snooze_resets_fired_state_to_pending(self) -> None:
        # If a medication is in `fired` state (mid-retry) and the user
        # explicitly says "give me 5 more", we shouldn't keep
        # escalating during the snooze window.
        skill, _, storage = await _make_skill(start_scheduler=False)
        # Direct entry insertion to simulate mid-retry state.
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC) - timedelta(minutes=10),
            next_fire_at=datetime.now(UTC) + timedelta(minutes=5),
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=1,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill.handle("snooze_reminder", {"id": 1, "minutes": 10})
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        snoozed = _Entry.from_json(raw)
        assert snoozed.state == _STATE_PENDING

    async def test_snooze_rejects_out_of_range(self) -> None:
        skill, _, _ = await _make_skill()
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        rid = json.loads(r.output)["id"]
        result = await skill.handle("snooze_reminder", {"id": rid, "minutes": 999})
        assert "error" in json.loads(result.output)
        await skill.teardown()

    async def test_snooze_rejects_terminal_reminder(self) -> None:
        skill, _, _ = await _make_skill()
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        rid = json.loads(r.output)["id"]
        await skill.handle("acknowledge_reminder", {"id": rid})
        result = await skill.handle("snooze_reminder", {"id": rid, "minutes": 5})
        assert "error" in json.loads(result.output)
        await skill.teardown()


# --------------------------------------------------------- acknowledge_reminder


class TestAcknowledgeReminder:
    async def test_ack_marks_terminal(self) -> None:
        skill, _, storage = await _make_skill()
        r = await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        rid = json.loads(r.output)["id"]
        await skill.handle("acknowledge_reminder", {"id": rid})
        raw = await storage.get_setting(f"reminder:{rid}")
        assert raw is not None
        assert _Entry.from_json(raw).state == _STATE_ACKED
        await skill.teardown()

    async def test_ack_unknown_id_returns_error(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("acknowledge_reminder", {"id": 42})
        assert "error" in json.loads(result.output)

    async def test_ack_with_recurrence_schedules_next(self) -> None:
        skill, _, storage = await _make_skill()
        when = datetime.now(UTC) + timedelta(hours=1)
        r = await skill.handle(
            "add_reminder",
            {
                "message": "pastilla",
                "when_iso": when.isoformat(),
                "kind": "medication",
                "recurrence": "daily",
            },
        )
        rid = json.loads(r.output)["id"]
        await skill.handle("acknowledge_reminder", {"id": rid})
        # Original is acked; a fresh row exists for tomorrow.
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.endswith(":next_id") and not k.endswith(":seed_imported")
        ]
        states = {e.state for e in entries}
        assert _STATE_ACKED in states
        assert _STATE_PENDING in states
        # The new pending is one day later.
        next_pending = next(e for e in entries if e.state == _STATE_PENDING)
        expected = when + timedelta(days=1)
        assert abs((next_pending.scheduled_for - expected).total_seconds()) < 2
        await skill.teardown()


# ---------------------------------------------------------- prompt_context


class TestPromptContext:
    async def test_includes_time_and_timezone(self) -> None:
        skill, _, _ = await _make_skill(language="es", config={"timezone": "America/Bogota"})
        ctx = skill.prompt_context()
        assert "Hora actual" in ctx
        assert "America/Bogota" in ctx
        await skill.teardown()

    async def test_english_phrasing_for_en(self) -> None:
        skill, _, _ = await _make_skill(language="en", config={"timezone": "America/New_York"})
        ctx = skill.prompt_context()
        assert "Current time" in ctx
        assert "America/New_York" in ctx
        await skill.teardown()

    async def test_lists_missed_reminders(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        # Pre-populate a missed reminder.
        entry = _Entry(
            id=7,
            message="pastilla del corazón",
            kind="medication",
            scheduled_for=datetime.now(UTC) - timedelta(hours=4),
            next_fire_at=datetime.now(UTC) - timedelta(hours=4),
            recurrence=None,
            state=_STATE_MISSED,
            missed_at=datetime.now(UTC),
        )
        await storage.set_setting("reminder:7", entry.to_json())
        # Force the missed cache to refresh.
        await skill._refresh_missed_cache()
        ctx = skill.prompt_context()
        assert "pastilla del corazón" in ctx

    async def test_does_not_list_acked_or_cancelled(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        for state in (_STATE_ACKED, _STATE_CANCELLED):
            entry = _Entry(
                id=1,
                message=f"{state}-msg",
                kind="generic",
                scheduled_for=datetime.now(UTC),
                next_fire_at=datetime.now(UTC),
                recurrence=None,
                state=state,
            )
            await storage.set_setting(f"reminder:{state}", entry.to_json())
        await skill._refresh_missed_cache()
        ctx = skill.prompt_context()
        assert "msg" not in ctx  # none of the terminal-state messages surface


# -------------------------------------------------------- boot reconciliation


class TestBootReconciliation:
    async def test_future_pending_kept(self) -> None:
        storage = _NoopSkillStorage()
        future = datetime.now(UTC) + timedelta(hours=1)
        entry = _Entry(
            id=1,
            message="x",
            kind="generic",
            scheduled_for=future,
            next_fire_at=future,
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage)
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        assert _Entry.from_json(raw).state == _STATE_PENDING
        await skill.teardown()

    async def test_past_pending_within_window_kept_pending(self) -> None:
        storage = _NoopSkillStorage()
        # 10 minutes late on a medication (window=15min) → still pending,
        # scheduler will fire on next tick.
        past = datetime.now(UTC) - timedelta(minutes=10)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        # Disable scheduler so it doesn't actually fire during the test.
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        entry_after = _Entry.from_json(raw)
        assert entry_after.state == _STATE_PENDING

    async def test_past_pending_outside_window_marked_missed(self) -> None:
        storage = _NoopSkillStorage()
        # 4 hours late on medication → outside 15min window → missed.
        past = datetime.now(UTC) - timedelta(hours=4)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        entry_after = _Entry.from_json(raw)
        assert entry_after.state == _STATE_MISSED
        assert entry_after.missed_at is not None

    async def test_past_pending_outside_window_with_recurrence_schedules_next(
        self,
    ) -> None:
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(hours=4)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        # Original should be missed; a new pending row should exist for
        # tomorrow's instance.
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        states = {e.state for e in entries}
        assert _STATE_MISSED in states
        assert _STATE_PENDING in states

    async def test_fired_medication_with_retries_left_resumes(self) -> None:
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(minutes=2)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=1,
            last_fired_at=past,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        # Resumed back to pending so the scheduler picks it up.
        assert _Entry.from_json(raw).state == _STATE_PENDING

    async def test_fired_medication_retries_exhausted_marks_missed(self) -> None:
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(minutes=2)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=len(_MEDICATION_RETRY_INTERVALS),
            last_fired_at=past,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        assert _Entry.from_json(raw).state == _STATE_MISSED

    async def test_fired_non_medication_treated_as_acked(self) -> None:
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(minutes=2)
        entry = _Entry(
            id=1,
            message="appt",
            kind="appointment",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=1,
            last_fired_at=past,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        assert _Entry.from_json(raw).state == _STATE_ACKED

    async def test_malformed_entry_skipped(self) -> None:
        storage = _NoopSkillStorage()
        await storage.set_setting("reminder:1", "not json")
        await storage.set_setting("reminder:2", "{}")  # missing fields
        await storage.set_setting("reminder:bad", '{"v": 1}')  # non-numeric id
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        # No crash; nothing scheduled.
        # All malformed entries remain in storage (we don't delete them
        # so a future migration can read them).
        rows = await storage.list_settings(_STORAGE_PREFIX)
        # 3 malformed + meta:next_id (set by reconcile) = 4
        assert len(rows) >= 3


# ---------------------------------------------------------- medication retry


class TestMedicationRetry:
    async def test_first_fire_transitions_to_fired(self) -> None:
        skill, inject_mock, storage = await _make_skill(start_scheduler=False)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        inject_mock.assert_awaited_once()
        # State was advanced.
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        after = _Entry.from_json(raw)
        assert after.state == _STATE_FIRED
        assert after.fired_count == 1

    async def test_retry_budget_exhaustion_marks_missed(self) -> None:
        skill, inject_mock, storage = await _make_skill(start_scheduler=False)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=len(_MEDICATION_RETRY_INTERVALS) - 1,  # one retry left
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        # That fire used the last retry; expect missed.
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        after = _Entry.from_json(raw)
        assert after.state == _STATE_MISSED
        assert after.fired_count == len(_MEDICATION_RETRY_INTERVALS)

    async def test_one_shot_kinds_do_not_retry(self) -> None:
        for kind in ("appointment", "generic"):
            skill, _, storage = await _make_skill(start_scheduler=False)
            entry = _Entry(
                id=1,
                message="x",
                kind=kind,
                scheduled_for=datetime.now(UTC),
                next_fire_at=datetime.now(UTC),
                recurrence=None,
                state=_STATE_PENDING,
            )
            await storage.set_setting("reminder:1", entry.to_json())
            await skill._fire(entry)
            raw = await storage.get_setting("reminder:1")
            assert raw is not None
            after = _Entry.from_json(raw)
            assert after.state == _STATE_ACKED, kind


# ----------------------------------------------------------------- recurrence


class TestRecurrence:
    async def test_one_shot_fire_with_recurrence_schedules_next(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        when = datetime.now(UTC) + timedelta(hours=1)
        entry = _Entry(
            id=1,
            message="x",
            kind="generic",
            scheduled_for=when,
            next_fire_at=when,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        # Original acked, new pending exists for next day.
        states = {e.state for e in entries}
        assert _STATE_ACKED in states
        assert _STATE_PENDING in states
        next_pending = next(e for e in entries if e.state == _STATE_PENDING)
        expected = when + timedelta(days=1)
        assert abs((next_pending.scheduled_for - expected).total_seconds()) < 2

    async def test_missed_with_recurrence_schedules_next_on_boot(self) -> None:
        # Even if today's medication is missed, tomorrow's reminder
        # should still appear — recurrence outlasts a single missed dose.
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(hours=4)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        # Missed + new pending.
        assert {_STATE_MISSED, _STATE_PENDING} <= {e.state for e in entries}


# ------------------------------------------------------------------- seed


class TestSeedImport:
    async def test_seed_imported_on_first_boot(self) -> None:
        when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        seed = [
            {
                "message": "tomar la pastilla del corazón",
                "when_iso": when,
                "kind": "medication",
                "recurrence": "daily",
            }
        ]
        skill, _, storage = await _make_skill(config={"seed": seed})
        rows = await storage.list_settings(_STORAGE_PREFIX)
        # One reminder row + meta keys.
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        assert len(entries) == 1
        assert entries[0].message == "tomar la pastilla del corazón"
        assert entries[0].kind == "medication"
        assert entries[0].recurrence == "daily"
        await skill.teardown()

    async def test_seed_idempotent_across_reboots(self) -> None:
        when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        seed = [{"message": "x", "when_iso": when, "kind": "generic"}]
        storage = _NoopSkillStorage()
        skill, _, _ = await _make_skill(config={"seed": seed}, storage=storage)
        await skill.teardown()
        # Second boot — seed shouldn't be re-imported.
        skill2, _, _ = await _make_skill(config={"seed": seed}, storage=storage)
        await skill2.teardown()
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        assert len(entries) == 1

    async def test_seed_with_invalid_entry_skipped(self) -> None:
        when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        seed = [
            {"message": "good", "when_iso": when},
            {"message": "no when"},  # invalid
            {"message": "bad kind", "when_iso": when, "kind": "weird"},
        ]
        skill, _, storage = await _make_skill(config={"seed": seed})
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        # Only "good" survived.
        assert len(entries) == 1
        assert entries[0].message == "good"
        await skill.teardown()


# -------------------------------------------------------------- scheduler


class TestSchedulerFires:
    """One end-to-end test that lets the real scheduler fire a reminder."""

    async def test_scheduler_picks_up_overdue_pending_and_fires(self) -> None:
        storage = _NoopSkillStorage()
        # Pre-seed an overdue pending entry (within window so reconcile
        # leaves it pending; scheduler then picks it up immediately).
        past = datetime.now(UTC) - timedelta(seconds=5)
        entry = _Entry(
            id=1,
            message="agua",
            kind="generic",
            scheduled_for=past,
            next_fire_at=past,
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        inject_mock = AsyncMock()
        skill, _, _ = await _make_skill(
            storage=storage, inject_turn=inject_mock, start_scheduler=True
        )
        # Scheduler should fire on its first tick (sees past next_fire_at).
        # Yield enough times for the supervised task to pick up.
        for _ in range(20):
            await asyncio.sleep(0)
            if inject_mock.await_count > 0:
                break
        assert inject_mock.await_count >= 1
        # Generic kind → state should be acked.
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        assert _Entry.from_json(raw).state == _STATE_ACKED
        await skill.teardown()


# ----------------------------------------------------------- misc / coverage


class TestUnknownTool:
    async def test_returns_error_payload(self) -> None:
        skill, _, _ = await _make_skill(start_scheduler=False)
        result = await skill.handle("fake_tool", {})
        payload = json.loads(result.output)
        assert "error" in payload


class TestTeardown:
    async def test_teardown_cancels_scheduler(self) -> None:
        skill, _, _ = await _make_skill()
        assert skill._scheduler_handle is not None
        await skill.teardown()
        assert skill._scheduler_handle is None

    async def test_teardown_preserves_storage(self) -> None:
        skill, _, storage = await _make_skill()
        await skill.handle("add_reminder", {"message": "x", "when_iso": _future_iso()})
        await skill.teardown()
        # Reminder row preserved for the next boot to restore.
        raw = await storage.get_setting("reminder:1")
        assert raw is not None


class TestDefaultLateWindows:
    def test_medication_window_tighter_than_appointment(self) -> None:
        # Encodes the safety property: medication tolerates the smallest
        # delay (don't double-dose), generic tolerates more, appointment
        # tolerates the most.
        assert _DEFAULT_LATE_WINDOWS["medication"] < _DEFAULT_LATE_WINDOWS["generic"]
        assert _DEFAULT_LATE_WINDOWS["generic"] < _DEFAULT_LATE_WINDOWS["appointment"]


class TestPersonaConfig:
    async def test_custom_late_window_overrides_default(self) -> None:
        skill, _, _ = await _make_skill(
            config={"late_window_medication_s": 3600},
            start_scheduler=False,
        )
        assert skill._late_windows["medication"] == timedelta(hours=1)

    async def test_invalid_late_window_falls_back_to_default(self) -> None:
        skill, _, _ = await _make_skill(
            config={"late_window_medication_s": "nope"},
            start_scheduler=False,
        )
        assert skill._late_windows["medication"] == _DEFAULT_LATE_WINDOWS["medication"]


@pytest.mark.parametrize(
    "language, expected_substring",
    [("es", "Hora actual"), ("en", "Current time"), ("fr", "Heure actuelle")],
)
async def test_prompt_context_localized(language: str, expected_substring: str) -> None:
    skill, _, _ = await _make_skill(language=language, start_scheduler=False)
    ctx = skill.prompt_context()
    assert expected_substring in ctx


# ---------------------------------------------------------- regressions (post-review)


class TestRecurrenceIdempotency:
    """F1 / F2 / F14: `_schedule_next_recurrence` must not create
    duplicate successors when called multiple times for the same
    original row (boot loop on a missed recurring reminder)."""

    async def test_repeated_boot_does_not_fan_out_successors(self) -> None:
        # Set up a missed daily medication that's older than its
        # late-window. Each `_reconcile_on_boot` call would otherwise
        # create another successor — without the idempotency guard
        # we'd end up with N successors after N restarts.
        storage = _NoopSkillStorage()
        past = datetime.now(UTC) - timedelta(hours=4)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=past,
            next_fire_at=past,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())

        # Three consecutive boots.
        for _ in range(3):
            skill, _, _ = await _make_skill(storage=storage, start_scheduler=False)
            await skill._reconcile_on_boot()

        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        # Exactly two rows: the original (now missed) + one successor
        # for tomorrow. NOT three or four.
        assert len(entries) == 2, f"expected 2 entries, got {len(entries)}: {entries}"
        states = {e.state for e in entries}
        assert states == {_STATE_MISSED, _STATE_PENDING}

    async def test_idempotency_matches_on_kind_recurrence_message(self) -> None:
        # Two different recurring reminders with same kind+recurrence
        # but different messages must each get their own successor.
        skill, _, storage = await _make_skill(start_scheduler=False)
        when = datetime.now(UTC) + timedelta(hours=1)
        e1 = _Entry(
            id=1,
            message="pill A",
            kind="medication",
            scheduled_for=when,
            next_fire_at=when,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        e2 = _Entry(
            id=2,
            message="pill B",
            kind="medication",
            scheduled_for=when,
            next_fire_at=when,
            recurrence="daily",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", e1.to_json())
        await storage.set_setting("reminder:2", e2.to_json())
        await skill._schedule_next_recurrence(e1)
        await skill._schedule_next_recurrence(e2)
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        # Originals + two distinct successors = 4.
        assert len(entries) == 4
        next_pending_messages = sorted(e.message for e in entries if e.scheduled_for > when)
        assert next_pending_messages == ["pill A", "pill B"]


class TestCommitBeforeInject:
    """F31: state must be persisted BEFORE inject_turn runs so a
    process crash during narration doesn't cause re-narration on the
    next boot. Mirrors the timers skill's `fired_at` pattern."""

    async def test_state_saved_before_inject_for_medication(self) -> None:
        # Use a recording inject_turn that captures storage state at
        # the moment of the call, so we can assert the row is already
        # advanced when narration begins.
        skill, _, storage = await _make_skill(start_scheduler=False)
        captured_state: dict[str, str] = {}

        async def capturing_inject(prompt: str, **kwargs: object) -> None:
            raw = await storage.get_setting("reminder:1")
            assert raw is not None
            captured_state["state"] = _Entry.from_json(raw).state
            captured_state["fired_count"] = str(_Entry.from_json(raw).fired_count)

        skill._inject_turn = capturing_inject  # type: ignore[assignment]
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        # When `inject_turn` ran, the row was already at fired/1.
        assert captured_state == {"state": _STATE_FIRED, "fired_count": "1"}

    async def test_state_saved_before_inject_for_one_shot(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        captured_state: dict[str, str] = {}

        async def capturing_inject(prompt: str, **kwargs: object) -> None:
            raw = await storage.get_setting("reminder:1")
            assert raw is not None
            captured_state["state"] = _Entry.from_json(raw).state

        skill._inject_turn = capturing_inject  # type: ignore[assignment]
        entry = _Entry(
            id=1,
            message="appt",
            kind="appointment",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        assert captured_state == {"state": _STATE_ACKED}

    async def test_inject_failure_does_not_revert_state(self) -> None:
        # If inject_turn raises, state stays advanced — by design.
        # Silent miss > double dose for medication. The row will end
        # up missed after retry budget exhausts; operator sees the
        # `reminders.fire_failed` log line.
        skill, _, storage = await _make_skill(start_scheduler=False)

        async def failing_inject(prompt: str, **kwargs: object) -> None:
            raise RuntimeError("Realtime API down")

        skill._inject_turn = failing_inject  # type: ignore[assignment]
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        # Should NOT raise — the skill swallows inject failures.
        await skill._fire(entry)
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        after = _Entry.from_json(raw)
        assert after.state == _STATE_FIRED
        assert after.fired_count == 1


class TestMidFireCrashRecovery:
    """F37: simulate a process crash AFTER state save but BEFORE the
    next boot. The reconcile path must not re-narrate."""

    async def test_recovered_fired_medication_within_window_resumes_pending(
        self,
    ) -> None:
        # `_fire` ran, persisted state=FIRED with next_fire_at=+5min,
        # then the process died. On boot we see a fired row whose
        # last_fired_at is recent. Boot reconcile should resume.
        storage = _NoopSkillStorage()
        last_fired = datetime.now(UTC) - timedelta(minutes=2)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=last_fired - timedelta(minutes=2),
            next_fire_at=last_fired + _MEDICATION_RETRY_INTERVALS[0],
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=1,
            last_fired_at=last_fired,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        skill, inject_mock, _ = await _make_skill(storage=storage, start_scheduler=False)
        await skill._reconcile_on_boot()
        # Boot reconcile must NOT call inject_turn — that would be a
        # re-narration of the dose grandpa already heard.
        inject_mock.assert_not_awaited()
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        # Resumed back to pending so the scheduler picks up the retry.
        assert _Entry.from_json(raw).state == _STATE_PENDING


class TestAckOnFiredMedication:
    """F35: a medication that's mid-retry (state=fired) and the user
    acks via the LLM should transition to acked, schedule recurrence
    if any, and stop retrying."""

    async def test_ack_on_fired_medication_transitions_to_acked(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=datetime.now(UTC) - timedelta(minutes=10),
            next_fire_at=datetime.now(UTC) + timedelta(minutes=5),
            recurrence=None,
            state=_STATE_FIRED,
            fired_count=1,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill.handle("acknowledge_reminder", {"id": 1})
        raw = await storage.get_setting("reminder:1")
        assert raw is not None
        after = _Entry.from_json(raw)
        assert after.state == _STATE_ACKED
        assert after.acked_at is not None

    async def test_ack_on_fired_with_recurrence_schedules_next(self) -> None:
        # Same shape but recurrence="daily" — successor must exist
        # for tomorrow, exactly once (no duplicate from the in-flight
        # retry path).
        skill, _, storage = await _make_skill(start_scheduler=False)
        when = datetime.now(UTC) - timedelta(hours=1)
        entry = _Entry(
            id=1,
            message="pill",
            kind="medication",
            scheduled_for=when,
            next_fire_at=datetime.now(UTC) + timedelta(minutes=5),
            recurrence="daily",
            state=_STATE_FIRED,
            fired_count=1,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill.handle("acknowledge_reminder", {"id": 1})
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        states = sorted(e.state for e in entries)
        assert states == [_STATE_ACKED, _STATE_PENDING]
        next_pending = next(e for e in entries if e.state == _STATE_PENDING)
        expected = when + timedelta(days=1)
        assert abs((next_pending.scheduled_for - expected).total_seconds()) < 2


class TestWeeklyRecurrence:
    """F8: weekly math wasn't tested before this commit."""

    async def test_weekly_advances_by_seven_days(self) -> None:
        skill, _, storage = await _make_skill(start_scheduler=False)
        when = datetime.now(UTC) + timedelta(hours=1)
        entry = _Entry(
            id=1,
            message="weekly checkup",
            kind="generic",
            scheduled_for=when,
            next_fire_at=when,
            recurrence="weekly",
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        rows = await storage.list_settings(_STORAGE_PREFIX)
        entries = [
            _Entry.from_json(v)
            for k, v in rows
            if not k.removeprefix(_STORAGE_PREFIX).startswith("_meta:")
        ]
        next_pending = next(e for e in entries if e.state == _STATE_PENDING)
        expected = when + timedelta(weeks=1)
        assert abs((next_pending.scheduled_for - expected).total_seconds()) < 2


class TestInjectPriorityIsBlockBehindComms:
    """F40: assert the priority kwarg passed to inject_turn is the
    `BLOCK_BEHIND_COMMS` tier, not e.g. `NORMAL` or `PREEMPT`. Locks
    in the focus-plane semantic claim made by the skill doc."""

    async def test_fire_uses_block_behind_comms(self) -> None:
        from huxley_sdk import InjectPriority

        skill, inject_mock, storage = await _make_skill(start_scheduler=False)
        entry = _Entry(
            id=1,
            message="x",
            kind="generic",
            scheduled_for=datetime.now(UTC),
            next_fire_at=datetime.now(UTC),
            recurrence=None,
            state=_STATE_PENDING,
        )
        await storage.set_setting("reminder:1", entry.to_json())
        await skill._fire(entry)
        # AsyncMock records call kwargs; the second positional arg or
        # `priority=` kwarg must be BLOCK_BEHIND_COMMS.
        inject_mock.assert_awaited_once()
        kwargs = inject_mock.await_args.kwargs
        assert kwargs.get("priority") == InjectPriority.BLOCK_BEHIND_COMMS
