"""Unit tests for the per-sender debounce/coalesce buffer.

The buffer is pure logic with one async dependency (asyncio.call_later
from a running loop), so it can be exercised without mocking pyrogram,
the skill, or inject_turn.
"""

from __future__ import annotations

import asyncio

import pytest

from huxley_skill_telegram.inbox import (
    InboxBuffer,
    build_announcement,
    build_backfill_announcement,
)


class _Recorder:
    """Captures (user_id, display_name, messages) tuples passed to on_flush."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, list[str]]] = []

    async def __call__(self, user_id: int, display: str, messages: list[str]) -> None:
        self.calls.append((user_id, display, list(messages)))


# Short debounce keeps the test fast; the buffer has no minimum.
_FAST_DEBOUNCE = 0.05
_FLUSH_GRACE = 0.05  # extra time so the timer + spawned task complete


class TestDebounceAndCoalesce:
    @pytest.mark.asyncio
    async def test_single_message_flushes_after_debounce(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        buf.add(123, "hija", "hola")
        # Before debounce elapses: nothing yet.
        await asyncio.sleep(_FAST_DEBOUNCE / 2)
        assert rec.calls == []

        # After: one flush with one message.
        await asyncio.sleep(_FAST_DEBOUNCE / 2 + _FLUSH_GRACE)
        assert rec.calls == [(123, "hija", ["hola"])]

    @pytest.mark.asyncio
    async def test_burst_coalesces_into_one_flush(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        for text in ("hola", "papa", "estas?"):
            buf.add(123, "hija", text)
            await asyncio.sleep(_FAST_DEBOUNCE / 4)  # well within debounce window

        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert len(rec.calls) == 1
        user_id, display, messages = rec.calls[0]
        assert user_id == 123
        assert display == "hija"
        assert messages == ["hola", "papa", "estas?"]

    @pytest.mark.asyncio
    async def test_independent_senders_flush_independently(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        buf.add(1, "hija", "msg-A")
        buf.add(2, "hijo", "msg-B")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)

        # Both fired independently with their own messages.
        assert len(rec.calls) == 2
        by_user = {uid: (display, msgs) for uid, display, msgs in rec.calls}
        assert by_user[1] == ("hija", ["msg-A"])
        assert by_user[2] == ("hijo", ["msg-B"])

    @pytest.mark.asyncio
    async def test_late_message_extends_debounce_for_same_sender(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        buf.add(123, "hija", "first")
        # Wait most of the window, add another -- should reset the timer.
        await asyncio.sleep(_FAST_DEBOUNCE * 0.8)
        buf.add(123, "hija", "second")
        # If the first add's timer had fired, we'd have two separate flushes.
        # The reset means we get one combined flush after the second add's debounce.
        await asyncio.sleep(_FAST_DEBOUNCE / 2)
        assert rec.calls == []  # still within the extended window
        await asyncio.sleep(_FAST_DEBOUNCE / 2 + _FLUSH_GRACE)
        assert rec.calls == [(123, "hija", ["first", "second"])]

    @pytest.mark.asyncio
    async def test_display_name_uses_latest_value_for_burst(self) -> None:
        # A contact resolved from "unknown" to "hija" mid-burst gets the
        # proper name when the coalesced flush fires.
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        buf.add(123, "un numero desconocido", "first")
        buf.add(123, "hija", "second")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert rec.calls == [(123, "hija", ["first", "second"])]

    @pytest.mark.asyncio
    async def test_oldest_dropped_when_per_sender_cap_exceeded(self) -> None:
        # The cap (50) is internal; pump 60 messages and confirm the buffer
        # only holds the last 50.
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        for i in range(60):
            buf.add(123, "spammer", f"m{i}")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert len(rec.calls) == 1
        _, _, messages = rec.calls[0]
        assert len(messages) == 50
        # Newest preserved, oldest dropped.
        assert messages[-1] == "m59"
        assert messages[0] == "m10"


class TestStraddleRace:
    """Regression tests for the in-flight-flush race the first cut had.

    Before the fix, `_on_timer_fired` popped the sender state from
    `_senders` before spawning the flush task. Messages arriving during
    the flush would create a fresh sender state and fire a SECOND
    coalesced inject -- defeating the whole purpose of the buffer for
    bursts straddling the debounce boundary.

    These tests assert the corrected behavior: the sender stays resident
    in `_senders`; late-arriving messages append to its queue; the
    post-flush hook starts a new debounce timer that fires a follow-up
    burst once the in-flight flush completes.
    """

    @pytest.mark.asyncio
    async def test_message_during_flush_appends_into_followup_burst(self) -> None:
        # Slow flush: blocks for ~3x the debounce window. Any message
        # arriving while the flush awaits must NOT spawn a duplicate
        # parallel flush; instead it should land in a follow-up burst
        # that fires after the in-flight flush completes.
        flush_started = asyncio.Event()
        flush_release = asyncio.Event()
        recorded: list[tuple[int, str, list[str]]] = []

        async def slow_flush(uid: int, display: str, messages: list[str]) -> None:
            recorded.append((uid, display, list(messages)))
            flush_started.set()
            await flush_release.wait()

        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=slow_flush)

        buf.add(123, "hija", "first")
        # Wait for the timer to fire and the flush task to begin.
        await asyncio.wait_for(flush_started.wait(), timeout=1.0)
        # Add another message while the flush is parked.
        buf.add(123, "hija", "second")
        # Add a third one too, still during the flush.
        buf.add(123, "hija", "third")
        # Release the flush.
        flush_release.set()
        # Give the post-flush timer time to fire and the second burst to flush.
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE * 2)

        # Expectation: ONE in-flight flush with ['first'], then a SECOND
        # flush after the post-flush debounce with ['second', 'third'].
        # Each message announced exactly once.
        assert len(recorded) == 2
        assert recorded[0] == (123, "hija", ["first"])
        assert recorded[1] == (123, "hija", ["second", "third"])

    @pytest.mark.asyncio
    async def test_no_messages_during_flush_means_no_followup_burst(self) -> None:
        # Sanity check: if nothing arrives during the flush, no second
        # burst fires. (Catches a regression where the post-flush hook
        # would fire an empty inject.)
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)

        buf.add(123, "hija", "only")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        # Wait an additional debounce window to confirm no second flush.
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert rec.calls == [(123, "hija", ["only"])]

    @pytest.mark.asyncio
    async def test_state_cleaned_up_after_flush_with_no_followup(self) -> None:
        # After a successful flush with no late arrivals, the sender's
        # state is removed from _senders so the dict doesn't leak entries
        # for one-shot senders over the lifetime of the process.
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)
        buf.add(123, "hija", "x")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert 123 not in buf._senders  # type: ignore[attr-defined]


class TestClosedSemantics:
    @pytest.mark.asyncio
    async def test_add_after_flush_all_is_silently_dropped(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)
        await buf.flush_all()
        # New add post-close: no-op, no timer scheduled.
        buf.add(123, "hija", "too late")
        await asyncio.sleep(_FAST_DEBOUNCE + _FLUSH_GRACE)
        assert rec.calls == []


class TestFlushAll:
    @pytest.mark.asyncio
    async def test_flush_all_drains_pending_immediately(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=10.0, on_flush=rec)  # long timer; never elapses

        buf.add(1, "hija", "a")
        buf.add(2, "hijo", "b")
        await buf.flush_all()

        assert len(rec.calls) == 2
        by_user = {uid: msgs for uid, _, msgs in rec.calls}
        assert by_user[1] == ["a"]
        assert by_user[2] == ["b"]

    @pytest.mark.asyncio
    async def test_flush_all_is_safe_when_empty(self) -> None:
        rec = _Recorder()
        buf = InboxBuffer(debounce_seconds=_FAST_DEBOUNCE, on_flush=rec)
        # No adds -- should still complete cleanly.
        await buf.flush_all()
        assert rec.calls == []


class TestConstructorValidation:
    def test_zero_debounce_rejected(self) -> None:
        async def _noop(_uid: int, _d: str, _m: list[str]) -> None:
            return None

        with pytest.raises(ValueError, match="debounce_seconds must be positive"):
            InboxBuffer(debounce_seconds=0.0, on_flush=_noop)  # type: ignore[arg-type]

    def test_negative_debounce_rejected(self) -> None:
        async def _noop(_uid: int, _d: str, _m: list[str]) -> None:
            return None

        with pytest.raises(ValueError, match="debounce_seconds must be positive"):
            InboxBuffer(debounce_seconds=-1.0, on_flush=_noop)  # type: ignore[arg-type]


class TestBuildAnnouncement:
    """The prompt is an INSTRUCTION to the LLM, not literal speech.

    The first smoke test (2026-04-24) caught the LLM treating the
    inject as a silent notification when the prompt was a bare fact:
    it asked "do you want to reply?" without ever reading the content.
    Tests assert (a) the message body is present verbatim and (b) an
    explicit read-and-relay instruction is present.
    """

    def test_one_message_known_contact(self) -> None:
        text = build_announcement("hija", ["hola papá"])
        assert "de hija" in text
        assert "'hola papá'" in text
        # Explicit instruction to read aloud -- without this the LLM
        # treats the inject as a notification only.
        assert "Léeselo" in text or "léeselo" in text
        # No unknown-sender hint for a known contact.
        assert "número desconocido" not in text

    def test_one_message_unknown_sender_includes_origin_hint(self) -> None:
        text = build_announcement("un número desconocido", ["hola"])
        assert "de un número desconocido" in text
        assert "'hola'" in text
        # LLM is told to flag the unknown origin to the user.
        assert "número desconocido" in text
        assert "Léeselo" in text or "léeselo" in text

    def test_two_messages(self) -> None:
        text = build_announcement("hija", ["hola", "¿estás?"])
        assert "2 mensajes" in text
        assert "'hola'" in text and "'¿estás?'" in text
        assert "Léeselos" in text or "léeselos" in text
        assert "orden" in text  # "léeselos en orden"

    def test_three_messages(self) -> None:
        text = build_announcement("hija", ["a", "b", "c"])
        assert "3 mensajes" in text
        for body in ("'a'", "'b'", "'c'"):
            assert body in text

    def test_many_messages_summarized(self) -> None:
        # 4+ collapses to count + last 2 verbatim.
        text = build_announcement("hija", [f"m{i}" for i in range(7)])
        assert "7 mensajes" in text
        assert "'m5'" in text and "'m6'" in text
        # Earlier messages are NOT in the prompt verbatim.
        assert "'m0'" not in text and "'m3'" not in text
        # LLM is invited to offer to read the rest.
        assert "anteriores" in text

    def test_long_message_truncated(self) -> None:
        long_text = "x" * 500
        text = build_announcement("hija", [long_text], preview_chars=50)
        # 49 x's + the ellipsis character.
        assert f"'{'x' * 49}…'" in text

    def test_short_message_not_truncated(self) -> None:
        text = build_announcement("hija", ["hola"], preview_chars=200)
        assert "'hola'" in text
        # No ellipsis when within the limit.
        assert "…" not in text

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty messages"):
            build_announcement("hija", [])


class TestBuildBackfillAnnouncement:
    """Same instruction-prompt rules as live announcements: bodies present
    so the LLM has something to read when the user says yes. The first
    smoke test caught the "ask before reading" UX gap -- LLM had nothing
    to read on follow-up because the prompt only had counts, not bodies.
    """

    def test_one_sender_one_message_includes_body(self) -> None:
        text = build_backfill_announcement({"hija": ["¿papá, estás bien?"]})
        assert "de hija" in text
        # Body present verbatim so the LLM can read it.
        assert "'¿papá, estás bien?'" in text
        # Instruction to read.
        assert "léeselos" in text or "Léeselos" in text
        # Backfill framing.
        assert "desconectado" in text

    def test_one_sender_multiple_messages(self) -> None:
        text = build_backfill_announcement({"hija": ["hola", "papá", "¿estás?"]})
        assert "de hija (3 mensajes)" in text
        for body in ("'hola'", "'papá'", "'¿estás?'"):
            assert body in text

    def test_multiple_senders_each_with_bodies(self) -> None:
        text = build_backfill_announcement({"hija": ["¿papá?", "¿estás?"], "hijo": ["llámame"]})
        assert "de hija (2 mensajes)" in text
        assert "de hijo: 'llámame'" in text
        for body in ("'¿papá?'", "'¿estás?'", "'llámame'"):
            assert body in text

    def test_per_sender_body_cap_truncates_oldest(self) -> None:
        # 7 messages from one sender -> only the last 5 bodies appear,
        # plus a count of all 7 so the user knows the rest exist.
        msgs = [f"m{i}" for i in range(7)]
        text = build_backfill_announcement({"hija": msgs})
        assert "7 mensajes" in text
        assert "más recientes" in text
        # Last 5 present.
        for body in ("'m2'", "'m3'", "'m4'", "'m5'", "'m6'"):
            assert body in text
        # Earlier ones absent.
        for body in ("'m0'", "'m1'"):
            assert body not in text

    def test_long_body_truncated(self) -> None:
        text = build_backfill_announcement({"hija": ["x" * 500]}, preview_chars=50)
        assert f"'{'x' * 49}…'" in text

    def test_sender_with_empty_message_list_skipped(self) -> None:
        # Defensive: a sender with no messages shouldn't crash; it just gets
        # filtered out. If ALL senders are empty, that's an error.
        text = build_backfill_announcement({"hija": ["hola"], "hijo": []})
        assert "de hija" in text
        assert "hijo" not in text

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="empty per_sender"):
            build_backfill_announcement({})

    def test_all_senders_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty messages for all senders"):
            build_backfill_announcement({"hija": [], "hijo": []})
