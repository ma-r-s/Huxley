"""`OpenAIRealtimeProvider`: response bookkeeping, session refresh, logging.

Regression tests for failures in the 2026-10-12 server log:

- 06:44-06:45: after an interrupt, the cancelled response's late
  `response.done` ended the next press's turn, the next press then sent
  `response.create` while a response was live, and OpenAI rejected it
  (`conversation_already_has_active_response`): three presses unanswered.
- 07:31 and 08:31: `session_expired` (OpenAI's 60-minute limit) seconds
  after turns, because the planned reconnect timer was reset by every
  frame; and on idle sessions the timer's own disconnect cancelled
  itself (2026-10-11: `session_timeout`, then `session_expired` 5 min
  later, every hour).
- Tool outputs and the WebSocket close code were not in the log.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
import websockets
from websockets.frames import Close

from huxley.config import Settings
from huxley.voice.openai_protocol import ClientEventType
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import SkillRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from huxley.persona import PersonaSpec

CREATE = ClientEventType.RESPONSE_CREATE.value
COMMIT = ClientEventType.INPUT_AUDIO_BUFFER_COMMIT.value
CANCEL = ClientEventType.RESPONSE_CANCEL.value


class FakeWS:
    """Captures sent events; iterating blocks until `finish()` (or raises
    `error` if set), like a live socket with nothing to say."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.error: Exception | None = None
        self._done = asyncio.Event()

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))

    async def close(self) -> None:
        self.closed = True
        self._done.set()

    def finish(self, code: int, reason: str) -> None:
        self.close_code = code
        self.close_reason = reason
        self._done.set()

    def __aiter__(self) -> AsyncIterator[str]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[str]:
        await self._done.wait()
        if self.error is not None:
            raise self.error
        for _ in ():
            yield ""

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


def _callbacks() -> VoiceProviderCallbacks:
    return VoiceProviderCallbacks(
        on_audio_delta=AsyncMock(),
        on_tool_call=AsyncMock(),
        on_response_done=AsyncMock(),
        on_audio_done=AsyncMock(),
        on_commit_failed=AsyncMock(),
        on_session_end=AsyncMock(),
    )


def _provider(
    persona: PersonaSpec, *, refresh_minutes: float = 50
) -> tuple[OpenAIRealtimeProvider, FakeWS, VoiceProviderCallbacks]:
    callbacks = _callbacks()
    storage = AsyncMock()
    storage.get_latest_summary = AsyncMock(return_value=None)
    provider = OpenAIRealtimeProvider(
        config=Settings(openai_api_key="test-key", session_refresh_minutes=refresh_minutes),
        persona=persona,
        skill_registry=SkillRegistry(),
        storage=storage,
        callbacks=callbacks,
    )
    ws = FakeWS()
    provider._ws = ws  # type: ignore[assignment]
    return provider, ws, callbacks


def _created(response_id: str, generation: int | None) -> dict[str, Any]:
    response: dict[str, Any] = {"id": response_id, "status": "in_progress"}
    if generation is not None:
        response["metadata"] = {"huxley_gen": str(generation)}
    return {"type": "response.created", "response": response}


def _done(response_id: str, generation: int | None, status: str = "completed") -> dict[str, Any]:
    response: dict[str, Any] = {"id": response_id, "status": status}
    if generation is not None:
        response["metadata"] = {"huxley_gen": str(generation)}
    return {"type": "response.done", "response": response}


def _delta(response_id: str, pcm: bytes = b"\x01\x02" * 8) -> dict[str, Any]:
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "delta": base64.b64encode(pcm).decode("ascii"),
    }


def _mock(cb: Any) -> AsyncMock:
    assert isinstance(cb, AsyncMock)
    return cb


# --------------------------------------------------- one live response


class TestCreateAfterInterrupt:
    async def test_create_waits_for_the_cancelled_response(self, persona: PersonaSpec) -> None:
        """The 06:44 sequence: press 1 answered, press 2 interrupts and
        commits while press 1's response is still live."""
        provider, ws, cb = _provider(persona)
        await provider.commit_and_request_response()
        assert ws.sent[-1] == {"type": CREATE, "response": {"metadata": {"huxley_gen": "0"}}}
        await provider._handle_server_event(_created("r1", 0))
        await provider._handle_server_event(_delta("r1"))
        _mock(cb.on_audio_delta).assert_awaited_once()

        await provider.cancel_current_response()  # press 2 interrupts
        ws.sent.clear()
        await provider.commit_and_request_response()  # press 2 released
        assert ws.types() == [COMMIT]  # create held back: r1 still live

        # r1's late events: not this turn's, never reach the coordinator.
        await provider._handle_server_event(_delta("r1"))
        await provider._handle_server_event(
            {"type": "response.output_audio.done", "response_id": "r1"}
        )
        await provider._handle_server_event(_done("r1", 0, status="cancelled"))
        _mock(cb.on_audio_delta).assert_awaited_once()
        _mock(cb.on_audio_done).assert_not_awaited()
        _mock(cb.on_response_done).assert_not_awaited()
        # ...and its done releases press 2's create, in the new generation.
        assert ws.sent[-1] == {"type": CREATE, "response": {"metadata": {"huxley_gen": "1"}}}

        await provider._handle_server_event(_created("r2", 1))
        await provider._handle_server_event(_delta("r2"))
        await provider._handle_server_event(_done("r2", 1))
        assert _mock(cb.on_audio_delta).await_count == 2
        _mock(cb.on_response_done).assert_awaited_once()

    async def test_response_created_after_the_cancel_is_stale(self, persona: PersonaSpec) -> None:
        """Cancel sent before `response.created` arrived: the generation in
        the metadata still identifies the response as cancelled."""
        provider, _ws, cb = _provider(persona)
        await provider.commit_and_request_response()
        await provider.cancel_current_response()
        await provider._handle_server_event(_created("r1", 0))
        await provider._handle_server_event(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "r1",
                "call_id": "c1",
                "name": "play_audiobook",
                "arguments": "{}",
            }
        )
        await provider._handle_server_event(_done("r1", 0, status="cancelled"))
        _mock(cb.on_tool_call).assert_not_awaited()
        _mock(cb.on_response_done).assert_not_awaited()

    async def test_cancel_drops_a_held_back_create(self, persona: PersonaSpec) -> None:
        provider, ws, _cb = _provider(persona)
        await provider.commit_and_request_response()
        await provider._handle_server_event(_created("r1", 0))
        await provider.commit_and_request_response()  # held back
        await provider.cancel_current_response()  # that turn is interrupted too
        ws.sent.clear()
        await provider._handle_server_event(_done("r1", 0, status="cancelled"))
        assert CREATE not in ws.types()

    async def test_follow_up_after_done_is_sent_at_once(self, persona: PersonaSpec) -> None:
        provider, ws, cb = _provider(persona)
        await provider.commit_and_request_response()
        await provider._handle_server_event(_created("r1", 0))
        await provider._handle_server_event(_done("r1", 0))
        _mock(cb.on_response_done).assert_awaited_once()
        ws.sent.clear()
        await provider.request_response()
        assert ws.types() == [CREATE]

    async def test_without_metadata_events_still_flow(self, persona: PersonaSpec) -> None:
        """If the server didn't echo metadata, nothing is dropped as stale
        beyond the response that was live at the cancel."""
        provider, _ws, cb = _provider(persona)
        await provider.cancel_current_response()
        await provider._handle_server_event(_created("r9", None))
        await provider._handle_server_event(_delta("r9"))
        await provider._handle_server_event(_done("r9", None))
        _mock(cb.on_audio_delta).assert_awaited_once()
        _mock(cb.on_response_done).assert_awaited_once()

    async def test_suspend_keeps_lifecycle_events(self, persona: PersonaSpec) -> None:
        """suspend() cancels too, but its contract keeps response.done."""
        provider, _ws, cb = _provider(persona)
        await provider._handle_server_event(_created("r1", 0))
        await provider.suspend()
        await provider._handle_server_event(_done("r1", 0, status="cancelled"))
        _mock(cb.on_response_done).assert_awaited_once()


class TestAlreadyActiveError:
    _ERROR: dict[str, Any] = {  # noqa: RUF012
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "conversation_already_has_active_response",
            "message": "Conversation already has an active response in progress: resp_X.",
        },
    }

    async def test_retries_once_when_the_live_response_finishes(
        self, persona: PersonaSpec
    ) -> None:
        provider, ws, _cb = _provider(persona)
        await provider.commit_and_request_response()  # raced an unseen response
        await provider._handle_server_event(self._ERROR)
        ws.sent.clear()
        await provider._handle_server_event(_done("resp_X", None))
        assert ws.types() == [CREATE]

        # The retry fails too: no second retry.
        await provider._handle_server_event(self._ERROR)
        ws.sent.clear()
        await provider._handle_server_event(_done("resp_Y", None))
        assert CREATE not in ws.types()

    async def test_a_new_create_supersedes_a_pending_retry(self, persona: PersonaSpec) -> None:
        provider, ws, _cb = _provider(persona)
        await provider.commit_and_request_response()
        await provider._handle_server_event(self._ERROR)  # retry pending
        await provider.request_response()  # sent at once: nothing known live
        ws.sent.clear()
        await provider._handle_server_event(_done("resp_X", None))
        assert CREATE not in ws.types()  # not a second create for one request


# ------------------------------------------------------- session refresh


async def _wait_until(predicate: Any, deadline_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def _fast_refresh(provider: OpenAIRealtimeProvider) -> None:
    provider._REFRESH_POLL_S = 0.005  # type: ignore[misc]
    provider._REFRESH_QUIET_S = 0.05  # type: ignore[misc]
    provider._REFRESH_PLAYBACK_MARGIN_S = 0.0  # type: ignore[misc]


class TestSessionRefresh:
    async def test_refresh_waits_for_idle_then_really_disconnects(
        self, persona: PersonaSpec
    ) -> None:
        """Due while a response is live: waits for it, then the disconnect
        runs to the end (the old timer's disconnect cancelled itself at
        its first await, the summary call)."""
        provider, ws, cb = _provider(persona, refresh_minutes=0.0005)  # 30 ms
        _fast_refresh(provider)
        provider._transcript_lines.append("user: hola")
        provider._receive_task = asyncio.create_task(provider._receive_loop())
        await provider._handle_server_event(_created("r1", 0))

        async def summarize(*_args: Any, **_kwargs: Any) -> str:
            await asyncio.sleep(0.01)  # a real HTTP call yields to the loop
            return "resumen"

        with (
            patch("huxley.voice.openai_realtime.summarize_transcript", summarize),
            structlog.testing.capture_logs() as logs,
        ):
            provider._refresh_task = asyncio.create_task(provider._refresh_loop())
            await asyncio.sleep(0.15)
            assert not ws.closed  # response r1 still live
            due = [e for e in logs if e["event"] == "session.refresh_due"]
            assert due[0]["waiting_for"] == "response_active"

            await provider._handle_server_event(_done("r1", 0))
            await _wait_until(lambda: ws.closed)
            await _wait_until(lambda: _mock(cb.on_session_end).await_count == 1)
        _mock(cb.on_session_end).assert_awaited_once_with("resumen")
        assert provider._ws is None
        assert any(e["event"] == "session.refresh" for e in logs)

    async def test_due_time_counts_from_session_start(self, persona: PersonaSpec) -> None:
        """Traffic before the due time doesn't postpone it (the old timer
        restarted on every mic frame and commit)."""
        provider, ws, _cb = _provider(persona, refresh_minutes=0.001)  # 60 ms
        _fast_refresh(provider)
        provider._refresh_task = asyncio.create_task(provider._refresh_loop())
        for _ in range(4):
            await provider.send_user_audio(b"\x00\x00" * 240)
            await asyncio.sleep(0.01)
        await provider.commit_and_request_response()
        await provider._handle_server_event(_created("r1", 0))
        await provider._handle_server_event(_done("r1", 0))
        await _wait_until(lambda: ws.closed, deadline_s=1.0)

    def test_blocker_reasons(self, persona: PersonaSpec) -> None:
        provider, _ws, _cb = _provider(persona)
        now = provider._last_activity_at + 100
        assert provider._refresh_blocker(now) is None

        provider._active_response_id = "r1"
        assert provider._refresh_blocker(now) == "response_active"
        provider._active_response_id = None

        provider._create_deferred_at = now
        assert provider._refresh_blocker(now) == "response_pending"
        provider._create_deferred_at = None

        provider._input_pending = True
        provider._last_audio_append_at = now - 1
        assert provider._refresh_blocker(now) == "user_audio"
        provider._last_audio_append_at = now - 60  # press abandoned long ago
        assert provider._refresh_blocker(now) is None
        provider._input_pending = False

        provider._playback_until = now + 5
        assert provider._refresh_blocker(now) == "reply_playing"
        provider._playback_until = 0.0

        provider._last_activity_at = now - 1
        assert provider._refresh_blocker(now) == "recent_traffic"

    async def test_no_refresh_during_a_call(self, persona: PersonaSpec) -> None:
        """A claim (call) suspends the provider; a refresh would hang it up."""
        provider, _ws, _cb = _provider(persona)
        await provider.suspend()
        now = provider._last_activity_at + 100
        assert provider._refresh_blocker(now) == "input_claimed"
        await provider.resume()
        assert provider._refresh_blocker(now) is None

    async def test_server_close_stops_the_sessions_refresh_task(
        self, persona: PersonaSpec
    ) -> None:
        """After OpenAI closes the socket (e.g. `session_expired` while the
        refresh waited for idle), the old refresh task must not fire during
        the auto-reconnect and close the new session."""
        provider, ws, _cb = _provider(persona)
        refresh = asyncio.create_task(asyncio.sleep(3600))
        provider._refresh_task = refresh
        receive = asyncio.create_task(provider._receive_loop())
        await asyncio.sleep(0)
        ws.finish(1000, "session expired")
        await receive
        await asyncio.sleep(0)
        assert refresh.cancelled()
        assert provider._refresh_task is None

    async def test_reply_audio_counts_as_playing(self, persona: PersonaSpec) -> None:
        provider, _ws, _cb = _provider(persona)
        await provider._handle_server_event(_created("r1", 0))
        await provider._handle_server_event(_delta("r1", b"\x00\x00" * 24_000 * 3))  # 3 s
        await provider._handle_server_event(_done("r1", 0))
        loop_now = provider._last_activity_at
        assert provider._refresh_blocker(loop_now + 10) is None
        assert provider._refresh_blocker(loop_now + 2.5) == "reply_playing"

    async def test_connect_replaces_the_previous_sessions_refresh_task(
        self, persona: PersonaSpec
    ) -> None:
        """A session the server closed leaves its refresh task sleeping; the
        next connect must cancel it or it would end the new session."""
        provider, _old_ws, _cb = _provider(persona)
        old_task = asyncio.create_task(asyncio.sleep(3600))
        provider._refresh_task = old_task
        new_ws = FakeWS()
        with patch(
            "huxley.voice.openai_realtime.websockets.connect",
            AsyncMock(return_value=new_ws),
        ):
            await provider.connect()
        await asyncio.sleep(0)
        assert old_task.cancelled()
        assert provider._refresh_task is not None
        assert provider._refresh_task is not old_task
        await provider.disconnect(save_summary=False)


# --------------------------------------------------------------- logging


class TestLogging:
    async def test_tool_output_logged_truncated(self, persona: PersonaSpec) -> None:
        provider, _ws, _cb = _provider(persona)
        output = json.dumps({"results": ["x" * 50] * 20})
        with structlog.testing.capture_logs() as logs:
            await provider.send_tool_output("call_1", output)
        [entry] = [e for e in logs if e["event"] == "session.tx.tool_output"]
        assert entry["output"] == output[:300]
        assert entry["output_len"] == len(output)

    async def test_close_code_logged_when_openai_drops_the_socket(
        self, persona: PersonaSpec
    ) -> None:
        provider, ws, _cb = _provider(persona)
        ws.error = websockets.ConnectionClosedError(Close(1011, "session expired"), None)
        task = asyncio.create_task(provider._receive_loop())
        with structlog.testing.capture_logs() as logs:
            ws.finish(1011, "session expired")
            await task
        [entry] = [e for e in logs if e["event"] == "session_connection_closed"]
        assert entry["code"] == 1011
        assert entry["reason"] == "session expired"

    async def test_close_code_logged_on_a_clean_close(self, persona: PersonaSpec) -> None:
        provider, ws, cb = _provider(persona)
        task = asyncio.create_task(provider._receive_loop())
        with structlog.testing.capture_logs() as logs:
            ws.finish(1000, "bye")
            await task
        [entry] = [e for e in logs if e["event"] == "session_connection_closed"]
        assert entry["code"] == 1000
        assert entry["reason"] == "bye"
        _mock(cb.on_session_end).assert_awaited_once()


@pytest.fixture(autouse=True)
def _no_real_network() -> Any:
    """No test here may reach OpenAI."""
    with patch("huxley.voice.openai_realtime.summarize_transcript", AsyncMock(return_value=None)):
        yield
