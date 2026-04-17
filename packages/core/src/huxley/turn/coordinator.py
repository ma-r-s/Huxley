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
with `AsyncMock` and (in step 3) wire into the real `SessionManager` +
`AudioServer`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

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
    response_ids: list[str] = field(default_factory=list)
    pending_factories: list[Callable[[], AsyncIterator[bytes]]] = field(default_factory=list)
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

    def __init__(
        self,
        *,
        send_audio: Callable[[bytes], Awaitable[None]],
        send_audio_clear: Callable[[], Awaitable[None]],
        send_status: Callable[[str], Awaitable[None]],
        send_model_speaking: Callable[[bool], Awaitable[None]],
        send_user_audio_to_session: Callable[[bytes], Awaitable[None]],
        send_dev_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        oai_send_function_output: Callable[[str, str], Awaitable[None]],
        oai_commit: Callable[[], Awaitable[None]],
        oai_cancel: Callable[[], Awaitable[None]],
        oai_request_response: Callable[[], Awaitable[None]],
        oai_is_connected: Callable[[], bool],
        dispatch_tool: Callable[[str, dict[str, Any]], Awaitable[ToolResult]],
    ) -> None:
        self._send_audio = send_audio
        self._send_audio_clear = send_audio_clear
        self._send_status = send_status
        self._send_model_speaking = send_model_speaking
        self._send_user_audio_to_session = send_user_audio_to_session
        self._send_dev_event = send_dev_event
        self._oai_send_function_output = oai_send_function_output
        self._oai_commit = oai_commit
        self._oai_cancel = oai_cancel
        self._oai_request_response = oai_request_response
        self._oai_is_connected = oai_is_connected
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
        await self._send_status("Escuchando… (suelta para enviar)")
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
        # OpenAI requires >= 100 ms in the input buffer before commit.
        # 19 frames x 5.33 ms = 101 ms -- just above the floor.
        if frames < 19:
            await self._log.ainfo("coord.ptt_stop", frames=frames, committed=False)
            await self._send_status("Muy corto — mantén el botón mientras hablas")
            if self._oai_is_connected():
                await self._oai_cancel()
            self.current_turn = None
            self._bind_turn()
            return
        self.current_turn.state = TurnState.COMMITTING
        self.response_cancelled = False
        await self._log.ainfo("coord.ptt_stop", frames=frames, committed=True)
        await self._send_status("Enviado — esperando respuesta…")
        await self._oai_commit()

    async def on_user_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client. Forward to OpenAI iff we're LISTENING."""
        if (
            self.current_turn is not None
            and self.current_turn.state == TurnState.LISTENING
            and self._oai_is_connected()
        ):
            await self._send_user_audio_to_session(pcm)
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
        await self._send_status("Muy corto — mantén el botón mientras hablas")
        self.current_turn = None
        self._bind_turn()

    # --- OpenAI response events (from session/manager.py receive loop) ---

    async def on_audio_delta(self, pcm: bytes) -> None:
        """Model audio chunk — forward to client unless the response is cancelled."""
        if self.response_cancelled:
            await self._log.adebug("coord.audio_dropped")
            return
        self._enter_in_response_if_idle_between_rounds()
        if not self._model_speaking:
            self._model_speaking = True
            await self._send_model_speaking(True)
            await self._send_status("Respondiendo…")
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

    async def on_function_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Model called a tool — dispatch, send output back, latch the result."""
        if self.response_cancelled or self.current_turn is None:
            return
        self._enter_in_response_if_idle_between_rounds()

        result = await self._dispatch_tool(name, args)
        await self._oai_send_function_output(call_id, result.output)

        has_factory = result.audio_factory is not None
        self.current_turn.tool_calls += 1
        await self._log.ainfo(
            "coord.tool_dispatch",
            state=self.current_turn.state.value,
            name=name,
            has_factory=has_factory,
        )

        await self._send_dev_event(
            "tool_call",
            {
                "name": name,
                "args": args,
                "output": result.output,
                "has_audio_factory": has_factory,
            },
        )

        if result.audio_factory is not None:
            self.current_turn.pending_factories.append(result.audio_factory)
        else:
            self.current_turn.needs_follow_up = True

    async def on_response_done(self) -> None:
        """OpenAI finished a response. Decide if we need more rounds or barrier."""
        if self.response_cancelled or self.current_turn is None:
            return

        follow_up = self.current_turn.needs_follow_up
        factories = len(self.current_turn.pending_factories)
        self.current_turn.response_done_count += 1
        await self._log.ainfo(
            "coord.response_done",
            state=self.current_turn.state.value,
            follow_up=follow_up,
            factories=factories,
        )

        if follow_up:
            self.current_turn.needs_follow_up = False
            self.current_turn.state = TurnState.AWAITING_NEXT_RESPONSE
            if self._oai_is_connected():
                self.response_cancelled = False
                await self._oai_request_response()
        else:
            await self._apply_factories()

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
        self.current_turn.pending_factories.clear()
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
        will_cancel = has_response and self._oai_is_connected()
        pending = len(self.current_turn.pending_factories) if self.current_turn else 0

        await self._log.ainfo(
            "coord.interrupt",
            prev_state=prev_state,
            has_media=has_media,
            will_cancel=will_cancel,
            pending_factories=pending,
        )

        # 1. Drop flag FIRST
        self.response_cancelled = True
        # 2. Drop pending factories
        if self.current_turn is not None:
            self.current_turn.pending_factories.clear()
        # 3. Flush client-side audio queue + clear model-speaking state
        await self._send_audio_clear()
        if self._model_speaking:
            self._model_speaking = False
            await self._send_model_speaking(False)
        # 4. Cancel any long-running media task
        await self._stop_current_media_task()
        # 5. Cancel the in-flight response (only if one could exist)
        if will_cancel:
            await self._oai_cancel()
        # 6. Mark current turn as interrupted, emit summary, clear ref
        if self.current_turn is not None:
            self.current_turn.state = TurnState.INTERRUPTED
            await self._emit_turn_summary(reason="interrupted", spawned_factory=False)
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

    async def _apply_factories(self) -> None:
        """Invoke the pending factories at the terminal barrier."""
        if self.current_turn is None:
            return

        factories = self.current_turn.pending_factories
        turn_id = self._tid()
        self.current_turn.state = TurnState.APPLYING_FACTORIES

        if len(factories) > 1:
            await self._log.ainfo(
                "coord.factories_superseded",
                dropped=len(factories) - 1,
            )

        if factories:
            await self._stop_current_media_task()
            factory = factories[-1]
            self.current_media_task = asyncio.create_task(self._consume_factory(factory, turn_id))
            await self._log.ainfo("coord.factory_started")

        await self._emit_turn_summary(reason="ended", spawned_factory=bool(factories))
        await self._log.ainfo("coord.turn_ended")
        self.current_turn = None
        self._bind_turn()
        await self._send_status("Listo — mantén el botón para responder")

    async def _emit_turn_summary(self, *, reason: str, spawned_factory: bool) -> None:
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
            spawned_factory=spawned_factory,
            user_audio_frames=self.current_turn.user_audio_frames,
        )

    async def _consume_factory(
        self, factory: Callable[[], AsyncIterator[bytes]], turn_id: str | None
    ) -> None:
        """Pull chunks from a factory and forward them to send_audio."""
        try:
            async for chunk in factory():
                await self._send_audio(chunk)
            await logger.ainfo("coord.factory_ended", turn=turn_id, error=False)
        except asyncio.CancelledError:
            await logger.ainfo("coord.factory_ended", turn=turn_id, cancelled=True)
            raise
        except Exception:
            await logger.aexception("coord.factory_ended", turn=turn_id, error=True)

    async def _stop_current_media_task(self) -> None:
        """Cancel the running media task if any. Waits for cleanup."""
        task = self.current_media_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.current_media_task = None
