"""TurnCoordinator — the single authority for audio sequencing around tool calls.

See `docs/turns.md` for the full spec. In short:

- A Turn is one user-assistant exchange. It may span multiple OpenAI
  response cycles when info tools need narration follow-ups.
- The model's speech plays first, across all chained responses. Only
  after the terminal `response.done` does tool-produced audio ("factories")
  fire, in declaration order. The last factory wins when a turn accumulates
  multiple (earlier ones are superseded).
- Interrupts are atomic: a new `ptt_start` during a live turn runs the
  6-step `interrupt()` method (drop flag → clear pending → audio_clear →
  cancel media task → cancel OpenAI response → mark INTERRUPTED).
- The `response_cancelled` drop flag discards stale audio deltas that
  OpenAI emits in the race window between `response.cancel` sent and
  actually processed.

The coordinator is transport-agnostic: all I/O happens through callbacks
passed at construction time, which makes it straightforward to unit-test
with a `StubVoiceProvider` and wire into a concrete
`VoiceProvider` at construction time.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID, uuid4

import structlog

from huxley_sdk import AudioStream, CancelMedia, SetVolume

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley.voice.provider import VoiceProvider
    from huxley_sdk import ToolResult

logger = structlog.get_logger()


class TurnState(Enum):
    """Finite states for a single user-assistant Turn.

    See `docs/turns.md#turn-lifecycle` for the full state diagram and
    transition rules. The `BARRIER` state from v2 was collapsed into
    the `IN_RESPONSE → APPLYING_FACTORIES` transition in v3.
    """

    IDLE = "idle"
    LISTENING = "listening"
    COMMITTING = "committing"
    IN_RESPONSE = "in_response"
    AWAITING_NEXT_RESPONSE = "awaiting_next_response"
    APPLYING_FACTORIES = "applying_factories"
    INTERRUPTED = "interrupted"


@dataclass
class Turn:
    """One user-assistant exchange. See `docs/turns.md#1-turn`."""

    id: UUID = field(default_factory=uuid4)
    state: TurnState = TurnState.LISTENING
    user_audio_frames: int = 0
    pending_audio_streams: list[AudioStream] = field(default_factory=list)
    needs_follow_up: bool = False
    # Summary tracking — emitted as coord.turn_summary at end-of-turn.
    started_at: float = field(default_factory=lambda: time.monotonic())
    tool_calls: int = 0
    response_done_count: int = 0


class TurnCoordinator:
    """Sequences model speech and tool audio within a single Turn.

    Transport-agnostic — I/O happens via callbacks passed at construction.
    See the module docstring and `docs/turns.md` for the full design.
    """

    # English fallbacks used when no persona ui_strings are provided (tests,
    # non-Spanish personas that omit the key).
    _DEFAULT_STATUS: ClassVar[dict[str, str]] = {
        "listening": "Listening... (release to send)",
        "too_short": "Too short — hold the button while speaking",
        "sent": "Sent — waiting for response...",
        "responding": "Responding...",
        "ready": "Ready — hold button to respond",
    }

    def __init__(
        self,
        *,
        send_audio: Callable[[bytes], Awaitable[None]],
        send_audio_clear: Callable[[], Awaitable[None]],
        send_status: Callable[[str], Awaitable[None]],
        send_model_speaking: Callable[[bool], Awaitable[None]],
        send_dev_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        provider: VoiceProvider,
        dispatch_tool: Callable[[str, dict[str, Any]], Awaitable[ToolResult]],
        status_messages: dict[str, str] | None = None,
        send_set_volume: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        # Client-facing outputs (to the WebSocket audio server).
        self._send_audio = send_audio
        self._send_audio_clear = send_audio_clear
        self._send_status = send_status
        self._send_model_speaking = send_model_speaking
        self._send_dev_event = send_dev_event

        async def _noop_volume(_level: int) -> None:
            pass

        self._send_set_volume: Callable[[int], Awaitable[None]] = (
            send_set_volume if send_set_volume is not None else _noop_volume
        )
        self._status = {**self._DEFAULT_STATUS, **(status_messages or {})}
        # Provider — outgoing verbs (send_user_audio, send_tool_output,
        # commit_and_request_response, cancel_current_response,
        # request_response) are called directly. Incoming events arrive as
        # on_tool_call / on_audio_delta / on_response_done / on_audio_done
        # / on_commit_failed / on_session_end, wired via
        # VoiceProviderCallbacks at construction on the provider side.
        self._provider = provider
        self._dispatch_tool = dispatch_tool

        self.current_turn: Turn | None = None
        self.current_media_task: asyncio.Task[None] | None = None
        self.response_cancelled: bool = False
        self._model_speaking: bool = False
        # Bound logger — rebound with turn= in on_ptt_start, reset on turn end.
        self._log: structlog.stdlib.BoundLogger = logger

    def _tid(self) -> str | None:
        """Short turn ID for explicit passing (factory tasks, etc.)."""
        return str(self.current_turn.id)[:8] if self.current_turn else None

    def _bind_turn(self) -> None:
        """Bind the current turn's short ID to the logger."""
        if self.current_turn is not None:
            self._log = logger.bind(turn=str(self.current_turn.id)[:8])
        else:
            self._log = logger

    # --- PTT lifecycle (from client) ---

    async def on_ptt_start(self) -> None:
        """User pressed PTT. Start a new turn or interrupt + restart."""
        active_turn = self.current_turn is not None and self.current_turn.state != TurnState.IDLE
        active_media = self.current_media_task is not None and not self.current_media_task.done()
        prev_state = self.current_turn.state.value if self.current_turn else None

        if active_turn or active_media:
            await self.interrupt()

        self.current_turn = Turn(state=TurnState.LISTENING)
        self._bind_turn()
        await self._send_status(self._status["listening"])
        await self._log.ainfo(
            "coord.ptt_start",
            had_turn=active_turn,
            prev_state=prev_state,
            had_media=active_media,
            will_interrupt=active_turn or active_media,
        )

    async def on_ptt_stop(self) -> None:
        """User released PTT. Commit audio if enough frames were sent."""
        if self.current_turn is None:
            return
        frames = self.current_turn.user_audio_frames
        # AudioWorklet frames are 128 samples = 5.33 ms at 24 kHz.
        # 25 frames x 5.33 ms = ~133 ms — just above the tap-noise floor
        # (accidental presses are <100 ms) while still allowing a quick "Sí".
        if frames < 25:
            await self._log.ainfo("coord.ptt_stop", frames=frames, committed=False)
            await self._send_status(self._status["too_short"])
            # audio_clear tells the client to cancel its silence timer — without
            # it the thinking tone fires 400ms after ptt_stop and loops forever
            # because no audio or model_speaking:true ever follows this path.
            await self._send_audio_clear()
            if self._provider.is_connected:
                await self._provider.cancel_current_response()
            self.current_turn = None
            self._bind_turn()
            return
        self.current_turn.state = TurnState.COMMITTING
        self.response_cancelled = False
        await self._log.ainfo("coord.ptt_stop", frames=frames, committed=True)
        await self._send_status(self._status["sent"])
        await self._provider.commit_and_request_response()

    async def on_user_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client. Forward to OpenAI iff we're LISTENING."""
        if (
            self.current_turn is not None
            and self.current_turn.state == TurnState.LISTENING
            and self._provider.is_connected
        ):
            await self._provider.send_user_audio(pcm)
            self.current_turn.user_audio_frames += 1
            await self._log.adebug("coord.mic_fwd", bytes=len(pcm))
        elif self.current_turn is not None:
            await self._log.adebug(
                "coord.mic_dropped",
                reason=self.current_turn.state.value,
            )

    async def on_commit_failed(self) -> None:
        """OpenAI rejected the input buffer commit (too little audio)."""
        if self.current_turn is None:
            return
        await self._log.ainfo("coord.commit_failed")
        await self._send_status(self._status["too_short"])
        await self._send_audio_clear()
        self.current_turn = None
        self._bind_turn()

    # --- Provider events (fired by the VoiceProvider's receive loop) ---

    async def on_audio_delta(self, pcm: bytes) -> None:
        """Model audio chunk — forward to client unless the response is cancelled."""
        if self.response_cancelled:
            await self._log.adebug("coord.audio_dropped")
            return
        self._enter_in_response_if_idle_between_rounds()
        if not self._model_speaking:
            self._model_speaking = True
            await self._send_model_speaking(True)
            await self._send_status(self._status["responding"])
            await self._log.ainfo(
                "coord.audio_start",
                state=self.current_turn.state.value if self.current_turn else None,
            )
        await self._send_audio(pcm)
        await self._log.adebug("coord.audio_fwd", bytes=len(pcm))

    async def on_audio_done(self) -> None:
        """OpenAI finished emitting audio for the current response."""
        if self.response_cancelled or not self._model_speaking:
            return
        self._model_speaking = False
        await self._send_model_speaking(False)
        await self._log.ainfo("coord.audio_done")

    async def on_tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Model called a tool — dispatch, send output back, latch the result."""
        if self.response_cancelled or self.current_turn is None:
            return
        self._enter_in_response_if_idle_between_rounds()

        result = await self._dispatch_tool(name, args)
        await self._provider.send_tool_output(call_id, result.output)

        audio_stream = result.side_effect if isinstance(result.side_effect, AudioStream) else None
        has_audio_stream = audio_stream is not None
        self.current_turn.tool_calls += 1
        await self._log.ainfo(
            "coord.tool_dispatch",
            state=self.current_turn.state.value,
            name=name,
            has_audio_stream=has_audio_stream,
        )

        await self._send_dev_event(
            "tool_call",
            {
                "name": name,
                "args": args,
                "output": result.output,
                "has_audio_stream": has_audio_stream,
            },
        )

        if audio_stream is not None:
            self.current_turn.pending_audio_streams.append(audio_stream)
        elif isinstance(result.side_effect, CancelMedia):
            # Cancel the running stream immediately so it stops before the
            # model's confirmation speech plays. needs_follow_up=True lets the
            # model narrate the result (e.g. "Listo, pausé el libro").
            await self._stop_current_media_task()
            self.current_turn.needs_follow_up = True
        elif isinstance(result.side_effect, SetVolume):
            # Forward the volume command to the client immediately. The model
            # says the confirmation ("Listo, el volumen está al X%") via the
            # follow-up response.
            await self._send_set_volume(result.side_effect.level)
            self.current_turn.needs_follow_up = True
        else:
            # Tool calls without a side-effect that is dispatched serially.
            # If a future persona needs parallel dispatch (multiple I/O-heavy
            # tools in one response), collect them and asyncio.gather before
            # sending outputs. See docs/triage.md C2.
            self.current_turn.needs_follow_up = True

    async def on_response_done(self) -> None:
        """OpenAI finished a response. Decide if we need more rounds or barrier."""
        if self.response_cancelled or self.current_turn is None:
            return

        follow_up = self.current_turn.needs_follow_up
        pending_streams = len(self.current_turn.pending_audio_streams)
        self.current_turn.response_done_count += 1
        await self._log.ainfo(
            "coord.response_done",
            state=self.current_turn.state.value,
            follow_up=follow_up,
            pending_audio_streams=pending_streams,
        )

        if follow_up:
            self.current_turn.needs_follow_up = False
            self.current_turn.state = TurnState.AWAITING_NEXT_RESPONSE
            if self._provider.is_connected:
                self.response_cancelled = False
                await self._provider.request_response()
        else:
            await self._apply_side_effects()

    async def on_session_disconnected(self) -> None:
        """OpenAI session dropped — abort any live turn without cancelling OpenAI."""
        had_media = self.current_media_task is not None and not self.current_media_task.done()
        was_speaking = self._model_speaking
        tid = self._tid()

        if self._model_speaking:
            self._model_speaking = False
            await self._send_model_speaking(False)

        await logger.ainfo(
            "coord.session_disconnected",
            turn=tid,
            had_media=had_media,
            was_speaking=was_speaking,
        )

        if self.current_turn is None:
            return
        self.current_turn.pending_audio_streams.clear()
        self.current_turn.state = TurnState.INTERRUPTED
        self.current_turn = None
        self._bind_turn()
        await self._stop_current_media_task()
        await self._send_audio_clear()

    # --- Interrupt: the atomic barrier ---

    async def interrupt(self) -> None:
        """Raise the interrupt barrier. Atomic 6-step sequence — see
        `docs/turns.md#3-interrupt`. Order matters.
        """
        prev_state = self.current_turn.state.value if self.current_turn else None
        has_media = self.current_media_task is not None and not self.current_media_task.done()
        has_response = self.current_turn is not None and self.current_turn.state in (
            TurnState.COMMITTING,
            TurnState.IN_RESPONSE,
            TurnState.AWAITING_NEXT_RESPONSE,
        )
        will_cancel = has_response and self._provider.is_connected
        pending = len(self.current_turn.pending_audio_streams) if self.current_turn else 0

        await self._log.ainfo(
            "coord.interrupt",
            prev_state=prev_state,
            has_media=has_media,
            will_cancel=will_cancel,
            pending_audio_streams=pending,
        )

        # 1. Drop flag FIRST
        self.response_cancelled = True
        # 2. Drop pending audio streams
        if self.current_turn is not None:
            self.current_turn.pending_audio_streams.clear()
        # 3. Flush client-side audio queue + clear model-speaking state
        await self._send_audio_clear()
        if self._model_speaking:
            self._model_speaking = False
            await self._send_model_speaking(False)
        # 4. Cancel any long-running media task
        await self._stop_current_media_task()
        # 5. Cancel the in-flight response (only if one could exist)
        if will_cancel:
            await self._provider.cancel_current_response()
        # 6. Mark current turn as interrupted, emit summary, clear ref
        if self.current_turn is not None:
            self.current_turn.state = TurnState.INTERRUPTED
            await self._emit_turn_summary(reason="interrupted", spawned_audio_stream=False)
        self.current_turn = None
        self._bind_turn()

    # --- Internal ---

    def _enter_in_response_if_idle_between_rounds(self) -> None:
        """Transition to IN_RESPONSE from the waiting states."""
        if self.current_turn is None:
            return
        if self.current_turn.state in (
            TurnState.COMMITTING,
            TurnState.AWAITING_NEXT_RESPONSE,
        ):
            self.current_turn.state = TurnState.IN_RESPONSE

    async def _apply_side_effects(self) -> None:
        """Invoke the pending side effects at the terminal barrier.

        Clears `current_turn` BEFORE spawning the media task so the task's
        natural-completion path (which may fire a synthetic-turn announcement)
        doesn't race with our own turn-cleanup awaits.
        """
        if self.current_turn is None:
            return

        streams = self.current_turn.pending_audio_streams
        turn_id = self._tid()
        self.current_turn.state = TurnState.APPLYING_FACTORIES

        if len(streams) > 1:
            await self._log.ainfo(
                "coord.audio_streams_superseded",
                dropped=len(streams) - 1,
            )

        # Tear down the parent turn fully before spawning the media task.
        # Otherwise the media task can complete during one of these awaits and
        # see `current_turn != None`, falsely concluding a new turn started.
        await self._emit_turn_summary(reason="ended", spawned_audio_stream=bool(streams))
        await self._log.ainfo("coord.turn_ended")
        self.current_turn = None
        self._bind_turn()
        await self._send_status(self._status["ready"])

        if streams:
            await self._stop_current_media_task()
            stream = streams[-1]
            self.current_media_task = asyncio.create_task(
                self._consume_audio_stream(stream, turn_id)
            )
            await logger.ainfo("coord.audio_stream_started", turn=turn_id)

    async def _emit_turn_summary(self, *, reason: str, spawned_audio_stream: bool) -> None:
        """One-line summary at end-of-turn. Useful for grep + per-turn timing."""
        if self.current_turn is None:
            return
        elapsed_ms = int((time.monotonic() - self.current_turn.started_at) * 1000)
        await self._log.ainfo(
            "coord.turn_summary",
            reason=reason,
            elapsed_ms=elapsed_ms,
            tool_calls=self.current_turn.tool_calls,
            response_done_count=self.current_turn.response_done_count,
            spawned_audio_stream=spawned_audio_stream,
            user_audio_frames=self.current_turn.user_audio_frames,
        )

    async def _consume_audio_stream(self, stream: AudioStream, turn_id: str | None) -> None:
        """Pull chunks from an AudioStream side effect and forward them to send_audio.

        Holds `model_speaking = true` for the duration of the factory so the
        client UI knows audio is flowing (otherwise factory chunks would arrive
        with `model_speaking = false`, potentially racing with the client-side
        thinking-tone trigger).
        """
        owns_speaking = False
        try:
            if not self._model_speaking:
                self._model_speaking = True
                owns_speaking = True
                await self._send_model_speaking(True)
            async for chunk in stream.factory():
                await self._send_audio(chunk)
            await logger.ainfo("coord.audio_stream_ended", turn=turn_id, cancelled=False)
            transferred = await self._maybe_fire_completion_prompt(stream, turn_id)
            if transferred:
                # Synthetic turn now owns model_speaking; on_audio_delta keeps it
                # true until on_audio_done clears it normally.
                owns_speaking = False
        except asyncio.CancelledError:
            await logger.ainfo("coord.audio_stream_ended", turn=turn_id, cancelled=True)
            raise
        except Exception:
            await logger.aexception("coord.audio_stream_ended", turn=turn_id, error=True)
        finally:
            # Stream finished without firing a completion prompt (no prompt set,
            # response cancelled, or new turn won the race). Clear the flag we set.
            if owns_speaking and self._model_speaking:
                self._model_speaking = False
                await self._send_model_speaking(False)

    async def _maybe_fire_completion_prompt(
        self, stream: AudioStream, parent_turn_id: str | None
    ) -> bool:
        """After natural stream end, send on_complete_prompt under a synthetic turn.

        Returns True iff the prompt was actually fired (a synthetic turn was
        created and request_response sent). Caller uses the return value to
        decide whether `model_speaking` ownership transferred to the synthetic
        turn (True) or stayed with the caller's stream (False).

        Order matters for low dead-air latency:
        1. Send the prompt + request_response immediately so the LLM starts
           generating during the silence below (overlapping latency with audio).
        2. Send `completion_silence_ms` of silence — covers model first-token
           latency. Once silence ends, the model's audio deltas (queued by the
           provider receive loop) play seamlessly afterward via on_audio_delta.

        Returns False when:
        - The stream has no prompt (most streams).
        - `response_cancelled` flipped while the stream was finishing (user
          pressed PTT — let their new turn proceed).
        - A new turn is already in progress (race won by the user's PTT).
        """
        if not stream.on_complete_prompt:
            return False
        if self.response_cancelled:
            await logger.ainfo(
                "coord.completion_prompt_skipped",
                turn=parent_turn_id,
                reason="response_cancelled",
            )
            return False
        if self.current_turn is not None:
            await logger.ainfo(
                "coord.completion_prompt_skipped",
                turn=parent_turn_id,
                reason="turn_in_progress",
            )
            return False

        self.current_turn = Turn(state=TurnState.IN_RESPONSE)
        self._bind_turn()
        self.response_cancelled = False
        await self._log.ainfo(
            "coord.completion_turn_started",
            parent_turn=parent_turn_id,
            prompt_len=len(stream.on_complete_prompt),
            silence_ms=stream.completion_silence_ms,
        )
        # Fire the request FIRST so the LLM begins generating concurrently
        # with the silence buffer below (covers first-token latency).
        await self._provider.send_conversation_message(stream.on_complete_prompt)
        await self._provider.request_response()
        if stream.completion_silence_ms > 0:
            sample_rate, channels, bytes_per_sample = 24000, 1, 2
            silence = b"\x00" * (
                sample_rate * channels * bytes_per_sample * stream.completion_silence_ms // 1000
            )
            await self._send_audio(silence)
        return True

    async def _stop_current_media_task(self) -> None:
        """Cancel the running media task if any. Waits for cleanup."""
        task = self.current_media_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.current_media_task = None
