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
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from abuel_os.types import ToolResult

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

        # Cross-turn state: current_media_task outlives turns so a book
        # started in turn N keeps playing until turn N+M interrupts it.
        self.current_turn: Turn | None = None
        self.current_media_task: asyncio.Task[None] | None = None
        self.response_cancelled: bool = False
        # Tracks whether we've fired `model_speaking: true` for the current
        # audio round. Flipped true on first delta, false on `on_audio_done`
        # or interrupt. Used by the client-side thinking-tone silence timer.
        self._model_speaking: bool = False

    # --- PTT lifecycle (from client) ---

    async def on_ptt_start(self) -> None:
        """User pressed PTT. Start a new turn or interrupt + restart."""
        # Interrupt if either a live turn OR an orphan media task (a book from
        # a prior turn still streaming) is in play. Without the media-task
        # check, pressing PTT mid-book wouldn't cancel the stream.
        active_turn = self.current_turn is not None and self.current_turn.state != TurnState.IDLE
        active_media = self.current_media_task is not None and not self.current_media_task.done()
        if active_turn or active_media:
            await self.interrupt()
        self.current_turn = Turn(state=TurnState.LISTENING)
        await self._send_status("Escuchando… (suelta para enviar)")
        await logger.ainfo("turn_started", turn_id=str(self.current_turn.id))

    async def on_ptt_stop(self) -> None:
        """User released PTT. Commit audio if enough frames were sent.

        Frame count comes from `user_audio_frames` on the active turn, which
        `on_user_audio_frame` increments as frames flow through.
        """
        if self.current_turn is None:
            return
        # AudioWorklet frames are 128 samples = 5.33 ms at 24 kHz.
        # OpenAI requires ≥ 100 ms in the input buffer before commit.
        # 19 frames x 5.33 ms = 101 ms -- just above the floor.
        if self.current_turn.user_audio_frames < 19:
            await self._send_status("Muy corto — mantén el botón mientras hablas")
            if self._oai_is_connected():
                await self._oai_cancel()
            self.current_turn = None
            return
        self.current_turn.state = TurnState.COMMITTING
        self.response_cancelled = False  # reset for the upcoming response
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

    async def on_commit_failed(self) -> None:
        """OpenAI rejected the input buffer commit (too little audio).

        Abort the turn so it doesn't sit in COMMITTING forever waiting for
        a response.done that will never come.
        """
        if self.current_turn is None:
            return
        await logger.ainfo("turn_commit_rejected", turn_id=str(self.current_turn.id))
        await self._send_status("Muy corto — mantén el botón mientras hablas")
        self.current_turn = None

    # --- OpenAI response events (from session/manager.py receive loop) ---

    async def on_audio_delta(self, pcm: bytes) -> None:
        """Model audio chunk — forward to client unless the response is cancelled.

        On the first delta of an audio round, fire `model_speaking: true` +
        the "Respondiendo…" status so the client can stop its thinking tone.
        """
        if self.response_cancelled:
            return
        self._enter_in_response_if_idle_between_rounds()
        if not self._model_speaking:
            self._model_speaking = True
            await self._send_model_speaking(True)
            await self._send_status("Respondiendo…")
        await self._send_audio(pcm)

    async def on_audio_done(self) -> None:
        """OpenAI finished emitting audio for the current response.

        Fires `model_speaking: false` so the client starts its thinking-tone
        silence timer — covers both terminal audio-done (before factories
        fire) and inter-round audio-done (chained responses).
        """
        if self.response_cancelled or not self._model_speaking:
            return
        self._model_speaking = False
        await self._send_model_speaking(False)

    async def on_function_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Model called a tool — dispatch, send output back, latch the result.

        - Tools with `audio_factory != None` (side-effect tools): the model
          pre-narrated already; factory latches onto `pending_factories` and
          will fire at the terminal barrier.
        - Tools with `audio_factory == None` (info tools): the model needs
          a follow-up response to narrate the output. Sets `needs_follow_up`.
        """
        if self.response_cancelled or self.current_turn is None:
            return
        self._enter_in_response_if_idle_between_rounds()

        result = await self._dispatch_tool(name, args)
        await self._oai_send_function_output(call_id, result.output)

        await self._send_dev_event(
            "tool_call",
            {
                "name": name,
                "args": args,
                "output": result.output,
                "has_audio_factory": result.audio_factory is not None,
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

        if self.current_turn.needs_follow_up:
            # Info tool was called this round — ask the model to narrate it.
            self.current_turn.needs_follow_up = False
            self.current_turn.state = TurnState.AWAITING_NEXT_RESPONSE
            if self._oai_is_connected():
                self.response_cancelled = False
                await self._oai_request_response()
        else:
            # Terminal: apply pending factories (if any) then end the turn.
            await self._apply_factories()

    async def on_session_disconnected(self) -> None:
        """OpenAI session dropped — abort any live turn without cancelling OpenAI
        (the session is already gone)."""
        if self._model_speaking:
            self._model_speaking = False
            await self._send_model_speaking(False)
        if self.current_turn is None:
            return
        self.current_turn.pending_factories.clear()
        self.current_turn.state = TurnState.INTERRUPTED
        self.current_turn = None
        await self._stop_current_media_task()
        await self._send_audio_clear()

    # --- Interrupt: the atomic barrier ---

    async def interrupt(self) -> None:
        """Raise the interrupt barrier. Atomic 6-step sequence — see
        `docs/turns.md#3-interrupt`. Order matters.
        """
        # 1. Drop flag FIRST — stale deltas from OpenAI get dropped at receive.
        self.response_cancelled = True
        # 2. Drop pending factories — interrupted turn never fires anything.
        if self.current_turn is not None:
            self.current_turn.pending_factories.clear()
        # 3. Flush client-side audio queue + clear any model-speaking state
        # (no more audio coming, so the client's thinking-tone timer can run).
        await self._send_audio_clear()
        if self._model_speaking:
            self._model_speaking = False
            await self._send_model_speaking(False)
        # 4. Cancel any long-running media task (audiobook from a prior turn).
        await self._stop_current_media_task()
        # 5. Cancel the in-flight response — but only if one could exist.
        # When interrupting a book (current_turn is None, only media task
        # was active) or a LISTENING turn (audio not committed yet), there's
        # no OpenAI response to cancel.
        has_response = self.current_turn is not None and self.current_turn.state in (
            TurnState.COMMITTING,
            TurnState.IN_RESPONSE,
            TurnState.AWAITING_NEXT_RESPONSE,
        )
        if has_response and self._oai_is_connected():
            await self._oai_cancel()
        # 6. Mark current turn as interrupted + clear the reference.
        if self.current_turn is not None:
            self.current_turn.state = TurnState.INTERRUPTED
        self.current_turn = None
        await logger.ainfo("turn_interrupted")

    # --- Internal ---

    def _enter_in_response_if_idle_between_rounds(self) -> None:
        """Transition to IN_RESPONSE from the waiting states.

        The coordinator is in COMMITTING when it just sent the audio-buffer
        commit and is awaiting the first event. It's in AWAITING_NEXT_RESPONSE
        when it requested a follow-up response mid-chain. Both transition to
        IN_RESPONSE on the first event of the new response (audio delta or
        function call).
        """
        if self.current_turn is None:
            return
        if self.current_turn.state in (
            TurnState.COMMITTING,
            TurnState.AWAITING_NEXT_RESPONSE,
        ):
            self.current_turn.state = TurnState.IN_RESPONSE

    async def _apply_factories(self) -> None:
        """Invoke the pending factories at the terminal barrier.

        v1 rule: only the LAST factory in the list runs. Earlier factories
        in the same turn are superseded — if the model called rewind + play
        in one turn, only the play factory takes effect. This keeps
        semantics predictable. Logged when superseding happens.
        """
        if self.current_turn is None:
            return

        factories = self.current_turn.pending_factories
        self.current_turn.state = TurnState.APPLYING_FACTORIES

        if len(factories) > 1:
            await logger.ainfo(
                "superseding_factories",
                dropped=len(factories) - 1,
                note="only the last factory in a turn runs; earlier ones are superseded",
            )

        if factories:
            # Kill any long-running prior stream before starting the new one.
            await self._stop_current_media_task()
            factory = factories[-1]
            self.current_media_task = asyncio.create_task(self._consume_factory(factory))

        # Turn ends when factories are *spawned*, not when they complete.
        self.current_turn = None
        await self._send_status("Listo — mantén el botón para responder")

    async def _consume_factory(self, factory: Callable[[], AsyncIterator[bytes]]) -> None:
        """Pull chunks from a factory and forward them to send_audio.

        Runs as a background task owned by `current_media_task`. Honors
        cancellation cleanly — the async iterator must release any
        subprocess / file / network resources on `task.cancel()`.
        """
        try:
            async for chunk in factory():
                await self._send_audio(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("factory_consume_error")

    async def _stop_current_media_task(self) -> None:
        """Cancel the running media task if any. Waits for cleanup."""
        task = self.current_media_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.current_media_task = None
