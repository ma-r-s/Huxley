"""Unit tests for `OpenAIRealtimeProvider._handle_server_event`.

The receive loop's per-event dispatch logic — extracted in T2.3 so it
can be exercised without a real WebSocket. These tests are the safety
net for the upcoming T1.3 coordinator refactor: every observable
behavior of the receive loop's branching is asserted here, so a
behavior-preserving refactor either keeps these green or surfaces a
regression immediately.

Each test constructs a real `OpenAIRealtimeProvider`, hands it a
crafted server event dict (mirroring what OpenAI Realtime would send),
and asserts the right callback fired with the right arguments.

See docs/triage.md T2.3.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog

from huxley.cost import CostThresholds, CostTracker
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import SkillRegistry

if TYPE_CHECKING:
    from huxley.config import Settings
    from huxley.persona import PersonaSpec
    from huxley.storage.db import Storage


# ---------------------------------------------------------------------------
# Helpers


def _callbacks() -> tuple[VoiceProviderCallbacks, dict[str, AsyncMock]]:
    """Build a callback set with every method as an AsyncMock for assertions."""
    mocks = {
        "on_audio_delta": AsyncMock(),
        "on_tool_call": AsyncMock(),
        "on_response_done": AsyncMock(),
        "on_audio_done": AsyncMock(),
        "on_commit_failed": AsyncMock(),
        "on_session_end": AsyncMock(),
        "on_transcript": AsyncMock(),
        "on_response_refused": AsyncMock(),
    }
    cb = VoiceProviderCallbacks(
        on_audio_delta=mocks["on_audio_delta"],
        on_tool_call=mocks["on_tool_call"],
        on_response_done=mocks["on_response_done"],
        on_audio_done=mocks["on_audio_done"],
        on_commit_failed=mocks["on_commit_failed"],
        on_session_end=mocks["on_session_end"],
        on_transcript=mocks["on_transcript"],
        on_response_refused=mocks["on_response_refused"],
    )
    return cb, mocks


def _provider(
    settings: Settings,
    persona: PersonaSpec,
    storage: Storage,
    callbacks: VoiceProviderCallbacks,
    cost_tracker: CostTracker | None = None,
) -> OpenAIRealtimeProvider:
    return OpenAIRealtimeProvider(
        config=settings,
        persona=persona,
        skill_registry=SkillRegistry(),
        storage=storage,
        callbacks=callbacks,
        cost_tracker=cost_tracker,
    )


# ---------------------------------------------------------------------------
# Audio


class TestHandleAudioDelta:
    async def test_decodes_base64_and_calls_on_audio_delta(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        raw_pcm = b"\x01\x02\x03\x04"
        await prov._handle_server_event(
            {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(raw_pcm).decode("ascii"),
            }
        )

        mocks["on_audio_delta"].assert_awaited_once_with(raw_pcm)
        mocks["on_response_done"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Function calls


class TestHandleFunctionCall:
    async def test_parses_args_json_and_dispatches(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_42",
                "name": "play_audiobook",
                "arguments": json.dumps({"book_id": "garcia-marquez"}),
            }
        )

        mocks["on_tool_call"].assert_awaited_once_with(
            "call_42", "play_audiobook", {"book_id": "garcia-marquez"}
        )

    async def test_malformed_args_json_falls_back_to_empty_dict(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_x",
                "name": "broken",
                "arguments": "not-json",
            }
        )

        mocks["on_tool_call"].assert_awaited_once_with("call_x", "broken", {})


# ---------------------------------------------------------------------------
# Transcripts


class TestHandleTranscript:
    async def test_assistant_transcript_appended_and_callback_fired(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "response.output_audio_transcript.done",
                "transcript": "Buenos días",
            }
        )

        mocks["on_transcript"].assert_awaited_once_with("assistant", "Buenos días")
        assert prov._transcript_lines == ["Buenos días"]

    async def test_user_transcript_role(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "ponme un libro",
            }
        )

        mocks["on_transcript"].assert_awaited_once_with("user", "ponme un libro")
        assert prov._transcript_lines == ["ponme un libro"]


# ---------------------------------------------------------------------------
# Errors


class TestHandleError:
    async def test_response_cancel_not_active_is_silent(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "error",
                "error": {
                    "code": "response_cancel_not_active",
                    "message": "no active response",
                },
            }
        )

        # No callback fires for this benign error.
        for name, m in mocks.items():
            if name not in ("on_session_end",):  # session_end never fires here
                m.assert_not_awaited()

    async def test_input_audio_buffer_commit_empty_fires_on_commit_failed(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "error",
                "error": {
                    "code": "input_audio_buffer_commit_empty",
                    "message": "buffer empty",
                },
            }
        )

        mocks["on_commit_failed"].assert_awaited_once()

    async def test_other_error_codes_are_logged_only(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {
                "type": "error",
                "error": {
                    "code": "model_not_found",
                    "message": "no such model",
                },
            }
        )

        # No callbacks fire — purely informational; receive loop continues.
        for m in mocks.values():
            m.assert_not_awaited()


# ---------------------------------------------------------------------------
# Response/audio done


class TestHandleResponseDone:
    async def test_audio_done_fires_on_audio_done(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event({"type": "response.output_audio.done"})

        mocks["on_audio_done"].assert_awaited_once()
        mocks["on_response_done"].assert_not_awaited()

    async def test_response_done_without_usage_still_fires_callback(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event({"type": "response.done", "response": {"id": "resp_1"}})

        mocks["on_response_done"].assert_awaited_once()

    async def test_response_done_with_usage_records_to_cost_tracker(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        tracker = CostTracker(
            storage=storage,
            model="gpt-realtime-2.1-mini",
            thresholds=CostThresholds(),
        )
        prov = _provider(settings, persona, storage, cb, cost_tracker=tracker)

        await prov._handle_server_event(
            {
                "type": "response.done",
                "response": {
                    "id": "resp_42",
                    "usage": {
                        "input_token_details": {"text_tokens": 10_000},
                        "output_token_details": {"audio_tokens": 1_000},
                    },
                },
            }
        )

        # Cost tracker writes today's micro-dollar total.
        # Cost = 10k*0.60/1M + 1k*20/1M = 0.006 + 0.02 = 0.026 USD = 26000 micro-USD
        keys = []
        cursor = await storage._conn.execute(
            "SELECT key FROM settings WHERE key LIKE 'cost:%:micro_usd'"
        )
        async for row in cursor:
            keys.append(row[0])
        assert len(keys) == 1
        stored = await storage.get_setting(keys[0])
        assert stored is not None
        assert int(stored) == 26_000

        mocks["on_response_done"].assert_awaited_once()

    async def test_cost_tracker_failure_does_not_block_response_done(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()

        # Tracker that always raises.
        class BoomTracker:
            async def record(self, _usage: dict[str, Any]) -> None:
                raise RuntimeError("tracker exploded")

        prov = _provider(settings, persona, storage, cb, cost_tracker=BoomTracker())  # type: ignore[arg-type]

        # Must not raise — exception swallowed in handler.
        await prov._handle_server_event(
            {
                "type": "response.done",
                "response": {
                    "usage": {"input_token_details": {"text_tokens": 1}},
                },
            }
        )

        mocks["on_response_done"].assert_awaited_once()


# ---------------------------------------------------------------------------
# Refusals (T1.43): no credit or a spend limit. Before, an `error` with
# these codes was only logged and the turn hung in silence.


class TestHandleRefusal:
    @pytest.mark.parametrize(
        "error",
        [
            {"code": "insufficient_quota", "type": "insufficient_quota"},
            {"code": "organization_spend_limit_exceeded"},
            {"code": None, "type": "billing_hard_limit_reached"},
        ],
        ids=["credit", "limit", "type_only"],
    )
    async def test_refusal_error_fires_on_response_refused(
        self,
        settings: Settings,
        persona: PersonaSpec,
        storage: Storage,
        error: dict[str, Any],
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event(
            {"type": "error", "error": {**error, "message": "You exceeded your quota."}}
        )

        expected = error["code"] or error["type"]
        mocks["on_response_refused"].assert_awaited_once_with(expected)
        for name, m in mocks.items():
            if name != "on_response_refused":
                m.assert_not_awaited()

    async def test_refusal_logs_the_error_and_the_refusal(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, _ = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        with structlog.testing.capture_logs() as logs:
            await prov._handle_server_event(
                {"type": "error", "error": {"code": "credit_balance_exhausted", "message": "m"}}
            )

        names = [(e["event"], e.get("code"), e.get("via")) for e in logs]
        assert ("session.rx.error", "credit_balance_exhausted", None) in names
        assert ("session.response_refused", "credit_balance_exhausted", "error") in names

    async def test_failed_response_done_with_a_refusal_code_is_a_refusal(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        """Realtime can report a refused answer as a failed `response.done`.
        It is not forwarded as a done: one arriving after a new press would
        end that press's turn."""
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)
        prov._create_deferred_at = 1.0  # a create held back for this turn

        await prov._handle_server_event(
            {
                "type": "response.done",
                "response": {
                    "id": "resp_9",
                    "status": "failed",
                    "status_details": {
                        "type": "failed",
                        "error": {"type": "insufficient_quota", "code": "insufficient_quota"},
                    },
                },
            }
        )

        mocks["on_response_refused"].assert_awaited_once_with("insufficient_quota")
        mocks["on_response_done"].assert_not_awaited()
        assert prov._create_deferred_at is None

    async def test_other_failed_response_done_is_forwarded(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        with structlog.testing.capture_logs() as logs:
            await prov._handle_server_event(
                {
                    "type": "response.done",
                    "response": {
                        "id": "resp_9",
                        "status": "failed",
                        "status_details": {"error": {"type": "server_error", "code": None}},
                    },
                }
            )

        mocks["on_response_done"].assert_awaited_once()
        mocks["on_response_refused"].assert_not_awaited()
        (done,) = [e for e in logs if e["event"] == "session.rx.response_done"]
        assert done["error_type"] == "server_error"

    async def test_stale_refused_response_done_is_not_a_refusal(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        """A cancelled turn's response: the turn is gone, and the current
        one must not be ended for it."""
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)
        prov._stale_response_ids.add("resp_old")

        await prov._handle_server_event(
            {
                "type": "response.done",
                "response": {
                    "id": "resp_old",
                    "status": "failed",
                    "status_details": {"error": {"code": "insufficient_quota"}},
                },
            }
        )

        mocks["on_response_refused"].assert_not_awaited()

    async def test_refusal_without_a_callback_only_logs(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, _ = _callbacks()
        bare = VoiceProviderCallbacks(
            on_audio_delta=cb.on_audio_delta,
            on_tool_call=cb.on_tool_call,
            on_response_done=cb.on_response_done,
            on_audio_done=cb.on_audio_done,
            on_commit_failed=cb.on_commit_failed,
            on_session_end=cb.on_session_end,
        )
        prov = _provider(settings, persona, storage, bare)

        with structlog.testing.capture_logs() as logs:
            await prov._handle_server_event(
                {"type": "error", "error": {"code": "insufficient_quota", "message": "m"}}
            )

        assert [e["event"] for e in logs] == ["session.rx.error", "session.response_refused"]


# ---------------------------------------------------------------------------
# Unknown events


class TestHandleUnknownEvents:
    async def test_unknown_event_type_is_silent_noop(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _callbacks()
        prov = _provider(settings, persona, storage, cb)

        await prov._handle_server_event({"type": "session.created", "session": {"id": "sess_1"}})
        await prov._handle_server_event({"type": "totally.made.up"})

        for m in mocks.values():
            m.assert_not_awaited()


# ---------------------------------------------------------------------------
# Suppress the "_ws is None" assert that other paths in the provider check.
# These tests target only `_handle_server_event` which doesn't touch _ws.


@pytest.fixture(autouse=True)
def _silence_aiohttp_warnings() -> None:
    """No-op — placeholder for any future warning filters this module needs."""
