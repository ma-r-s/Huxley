"""TurnCoordinator — the single authority for audio sequencing around tool calls.

See `docs/turns.md` for the full spec. In short:

- A Turn is one user-assistant exchange. It may span multiple OpenAI
  response cycles when info tools need narration follow-ups.
- The model's speech plays first, across all chained responses. Only
  after the terminal `response.done` does tool-produced audio ("factories")
  fire, in declaration order. The last factory wins when a turn accumulates
  multiple (earlier ones are superseded).
- Interrupts are atomic: a new `ptt_start` during a live turn runs the
  6-step `interrupt()` method (drop flag -> clear pending -> audio_clear ->
  cancel content stream -> cancel OpenAI response -> mark INTERRUPTED).
- The `response_cancelled` drop flag discards stale audio deltas that
  OpenAI emits in the race window between `response.cancel` sent and
  actually processed.

Content-channel audio (audiobook playback etc.) flows through a
`ContentStreamObserver` attached to an `Activity` on the CONTENT
channel of the app-owned `FocusManager`. `_start_content_stream`
calls `fm.acquire(activity)` + `fm.wait_drained()` to spawn the pump
task through the actor; `_stop_content_stream` calls
`fm.release(CONTENT, interface_name)` + `fm.wait_drained()` to tear
it down. `wait_drained()` is the synchronization primitive that keeps
`interrupt()`'s strict step order intact — by the time
`_stop_content_stream` returns, the pump is fully dead and
`force_release` can safely clear SpeakingState without a re-acquire
race (per the 1a fix).

The coordinator is transport-agnostic: all I/O happens through callbacks
passed at construction time, which makes it straightforward to unit-test
with a `StubVoiceProvider` and wire into a concrete
`VoiceProvider` at construction time.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from huxley.focus.vocabulary import Activity, Channel
from huxley_sdk import (
    AudioStream,
    CancelMedia,
    ClaimBusyError,
    ClaimEndReason,
    ClaimHandle,
    ContentType,
    InjectPriority,
    InputClaim,
    PlaySound,
    SetVolume,
)

from .factory import TurnFactory
from .mic_router import MicRouter
from .observers import ClaimObserver, ContentStreamObserver, DialogObserver
from .speaking_state import SpeakingOwner, SpeakingState
from .state import Turn, TurnSource, TurnState

_SOURCE_TO_OWNER: dict[TurnSource, SpeakingOwner] = {
    TurnSource.USER: SpeakingOwner.USER,
    TurnSource.COMPLETION: SpeakingOwner.COMPLETION,
    TurnSource.INJECTED: SpeakingOwner.INJECTED,
}


@dataclass(frozen=True, slots=True)
class _InjectedRequest:
    """One queued `inject_turn` request. Stage 1d: FIFO queue, dedup,
    two-tier priority (NORMAL | PREEMPT). TTL and outcome handle arrive
    in a later stage.
    """

    prompt: str
    dedup_key: str | None
    priority: InjectPriority


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley.focus.manager import FocusManager
    from huxley.voice.provider import VoiceProvider
    from huxley_sdk import ToolResult

logger = structlog.get_logger()

__all__ = ["Turn", "TurnCoordinator", "TurnFactory", "TurnSource", "TurnState"]


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
        focus_manager: FocusManager,
        status_messages: dict[str, str] | None = None,
        send_set_volume: Callable[[int], Awaitable[None]] | None = None,
        send_input_mode: Callable[..., Awaitable[None]] | None = None,
        send_claim_started: Callable[[str, str, str | None], Awaitable[None]] | None = None,
        send_claim_ended: Callable[[str, str], Awaitable[None]] | None = None,
        send_stream_started: Callable[[str, str | None, int], Awaitable[None]] | None = None,
        send_stream_ended: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        # Client-facing outputs (to the WebSocket audio server).
        self._send_audio = send_audio
        self._send_audio_clear = send_audio_clear
        self._send_status = send_status
        self._send_model_speaking = send_model_speaking
        self._send_dev_event = send_dev_event

        async def _noop_volume(_level: int) -> None:
            pass

        async def _noop_input_mode(
            _mode: str,
            *,
            reason: str = "",
            claim_id: str | None = None,
        ) -> None:
            _ = (reason, claim_id)

        async def _noop_claim(_id: str, _arg: str) -> None:
            pass

        async def _noop_claim_started(_id: str, _skill: str, _title: str | None) -> None:
            pass

        self._send_set_volume: Callable[[int], Awaitable[None]] = (
            send_set_volume if send_set_volume is not None else _noop_volume
        )
        # Mic-policy + claim-lifecycle notifications to the client. Defaults
        # are no-ops so existing tests that construct `TurnCoordinator`
        # without a server wired in keep working — the claim still starts
        # correctly, the client just isn't told.
        self._send_input_mode: Callable[..., Awaitable[None]] = (
            send_input_mode if send_input_mode is not None else _noop_input_mode
        )
        self._send_claim_started: Callable[[str, str, str | None], Awaitable[None]] = (
            send_claim_started if send_claim_started is not None else _noop_claim_started
        )
        self._send_claim_ended: Callable[[str, str], Awaitable[None]] = (
            send_claim_ended if send_claim_ended is not None else _noop_claim
        )

        async def _noop_stream_started(_id: str, _label: str | None, _preroll_ms: int = 0) -> None:
            pass

        self._send_stream_started: Callable[[str, str | None, int], Awaitable[None]] = (
            send_stream_started if send_stream_started is not None else _noop_stream_started
        )
        self._send_stream_ended: Callable[[str, str], Awaitable[None]] = (
            send_stream_ended if send_stream_ended is not None else _noop_claim
        )
        self._speaking_state = SpeakingState(notify=send_model_speaking)
        self._status = {**self._DEFAULT_STATUS, **(status_messages or {})}
        # Baseline status messages retained so `set_ui_strings` can replay
        # the framework defaults underneath a new per-session override
        # without stacking stale persona strings across language changes.
        self._status_baseline: dict[str, str] = dict(self._DEFAULT_STATUS)
        # Provider — outgoing verbs (send_user_audio, send_tool_output,
        # commit_and_request_response, cancel_current_response,
        # request_response) are called directly. Incoming events arrive as
        # on_tool_call / on_audio_delta / on_response_done / on_audio_done
        # / on_commit_failed / on_session_end, wired via
        # VoiceProviderCallbacks at construction on the provider side.
        self._provider = provider
        self._dispatch_tool = dispatch_tool
        self._turn_factory = TurnFactory()
        self._mic_router = MicRouter(default_handler=provider.send_user_audio)
        # FocusManager (Application-owned, passed in). Required; drives
        # CONTENT-channel lifecycle as of 1c.2 (DIALOG arrives in 1c.3).
        self._focus_manager = focus_manager
        # Current content-stream observer (audiobook / radio / etc.). Exactly
        # one at a time — `_start_content_stream` always stops the previous.
        # A reference of None means idle. The observer owns the pump task;
        # access it via `current_media_task` for back-compat.
        self._content_obs: ContentStreamObserver | None = None
        # Current InputClaim observer (call / voice memo). Shares the
        # CONTENT channel with `_content_obs` — FocusManager enforces
        # single-occupant, and starting a claim first stops any running
        # content stream (and vice versa). A ref of None means no claim.
        # Single-slot policy (Stage 2b, 2026-04-23): a second claim gets
        # `ClaimBusyError`; the interface_name used on FM is the literal
        # `"claim:active"` so the single-slot semantics are reflected
        # in focus-manager state too.
        self._claim_obs: ClaimObserver | None = None
        # Wall clock when the current claim latched. Used by the PTT
        # debounce in `on_ptt_start` — a press within 300ms of a
        # claim-start is treated as a client-side bounce / race of the
        # same tap and ignored. None when no claim is active.
        self._claim_started_at: float | None = None
        # Current DIALOG-channel Activity's interface_name (if any). Set by
        # `inject_turn`; cleared by `_release_dialog` called from
        # `_apply_side_effects` (natural end) or `interrupt()` (preemption).
        # The interface_name is the framework-internal handle used to
        # release via `fm.release(Channel.DIALOG, ...)`.
        self._dialog_interface_name: str | None = None
        # FIFO queue of inject_turn requests that couldn't fire immediately
        # (a user or synthetic turn was already in progress). Drained in
        # `_apply_side_effects` when a turn ends without pending content.
        # Preserved across `interrupt()` and `on_session_disconnected` so
        # reminders aren't lost when the user PTTs mid-reminder.
        self._injected_queue: list[_InjectedRequest] = []
        # Dedup key of the currently-firing injected turn (if any). Used
        # to drop a re-enqueue with the same key while it's in-flight.
        # Cleared by `_release_dialog`.
        self._current_injected_dedup_key: str | None = None
        # Set by `_fire_injected_turn` just before `request_response()`;
        # signalled (set + cleared) when the injected turn completes or is
        # interrupted. Lets `inject_turn_and_wait` block until the LLM
        # finishes speaking without a hardcoded sleep.
        self._injected_turn_done: asyncio.Event | None = None
        # Monotonic time of the first audio delta the current turn sent
        # to the client, and the cumulative byte count at 24kHz mono
        # 16-bit (48 kB/s). Used by `inject_turn_and_wait` to estimate
        # when the client's playback buffer will drain — `response_done`
        # fires at server-side audio end, but the client still has
        # queued PCM to play. Without this wait, a subsequent
        # `start_input_claim` issues `audio_clear` and flushes the
        # still-playing announcement tail. Both fields reset on
        # turn-start; `audio_done` leaves them set so the drain-wait
        # computation sees the terminal values.
        self._turn_audio_first_sent_at: float | None = None
        self._turn_audio_bytes_sent: int = 0

        self.current_turn: Turn | None = None
        self.response_cancelled: bool = False
        # Bound logger — rebound with turn= in on_ptt_start, reset on turn end.
        self._log: structlog.stdlib.BoundLogger = logger

    @property
    def current_media_task(self) -> asyncio.Task[None] | None:
        """Back-compat accessor — points at the pump task owned by the
        current `ContentStreamObserver`, or `None` when idle.
        """
        return self._content_obs.task if self._content_obs is not None else None

    def _content_is_running(self) -> bool:
        """True iff a content stream is currently playing (pump task live)."""
        obs = self._content_obs
        return obs is not None and obs.task is not None and not obs.task.done()

    def set_ui_strings(self, ui_strings: dict[str, str] | None) -> None:
        """Swap the persona-provided status labels at session start.

        `set_ui_strings` overlays the given mapping on top of the framework's
        English defaults, so missing keys fall through safely. Passing
        `None` resets to defaults (used when a persona has no ui_strings).
        Called by the Application on every session connect after the
        persona is resolved for the client's selected language.
        """
        self._status = {**self._status_baseline, **(ui_strings or {})}

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
        """User pressed PTT. Start a new turn or interrupt + restart.

        An active `InputClaim` (a call / voice memo) also counts as
        "something to interrupt" — press during a call means "end the
        call, I want to talk to the assistant again." Without this
        branch, pressing PTT during a claim would start a new LISTENING
        turn while the mic stayed latched to the skill handler, with no
        way to actually send audio to the LLM.

        Claim-start debounce: a PTT press within 300 ms of a claim
        latching is treated as a client-side bounce / race of the same
        tap that dispatched the tool call (especially with the PWA's
        keyboard-repeat-defeating logic). Dropped silently so grandpa
        doesn't hang up on himself the moment the call connects.
        """
        active_turn = self.current_turn is not None and self.current_turn.state != TurnState.IDLE
        active_media = self._content_is_running()
        active_claim = self._claim_obs is not None
        prev_state = self.current_turn.state.value if self.current_turn else None

        if (
            active_claim
            and self._claim_started_at is not None
            and time.monotonic() - self._claim_started_at < 0.3
        ):
            await self._log.ainfo(
                "coord.ptt_start_debounced",
                since_claim_start_ms=int((time.monotonic() - self._claim_started_at) * 1000),
            )
            return

        if active_turn or active_media or active_claim:
            await self.interrupt()

        # PTT during an active call (claim) = hangup only. The user tapped
        # to end the call — they did NOT intend to immediately start talking
        # to the assistant. A second PTT opens a fresh conversation.
        # Contrast with PTT during media (audiobook/radio): that DOES start
        # a listening turn because interrupting content is the expected gesture
        # for "I want to say something now."
        if active_claim and not active_turn and not active_media:
            await self._log.ainfo(
                "coord.ptt_claim_hangup",
                had_turn=active_turn,
                had_media=active_media,
            )
            return

        self.current_turn = self._turn_factory.create(
            source=TurnSource.USER, initial_state=TurnState.LISTENING
        )
        self._bind_turn()
        await self._send_status(self._status["listening"])
        await self._log.ainfo(
            "coord.ptt_start",
            had_turn=active_turn,
            prev_state=prev_state,
            had_media=active_media,
            had_claim=active_claim,
            will_interrupt=active_turn or active_media or active_claim,
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
        """Mic frame from client. Forward via MicRouter on one of two paths:

        1. Normal PTT flow — forward iff we're in `LISTENING` and the
           provider is connected. This is the "talking to the assistant"
           path; MicRouter.dispatch routes to the voice provider.

        2. Active `InputClaim` — forward unconditionally. The client is
           in `skill_continuous` input mode and streams mic frames
           continuously; MicRouter.dispatch routes to the claim's
           `on_mic_frame` handler (the provider is suspended). Frames
           arriving before the claim has fully latched or after it's
           released fall through to the default handler (provider),
           which drops them silently while suspended. That's the
           stage-2 invariant the MicRouter was built for.
        """
        if self._mic_router.is_claimed:
            await self._mic_router.dispatch(pcm)
            return
        if (
            self.current_turn is not None
            and self.current_turn.state == TurnState.LISTENING
            and self._provider.is_connected
        ):
            await self._mic_router.dispatch(pcm)
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
        if not self._speaking_state.is_speaking:
            owner = (
                _SOURCE_TO_OWNER[self.current_turn.source]
                if self.current_turn is not None
                else SpeakingOwner.USER
            )
            await self._speaking_state.acquire(owner)
            await self._send_status(self._status["responding"])
            await self._log.ainfo(
                "coord.audio_start",
                state=self.current_turn.state.value if self.current_turn else None,
                owner=owner.value,
            )
            # Reset drain counters — this is a fresh audio stream, and the
            # subsequent inject_turn_and_wait drain computation should see
            # ONLY this stream's bytes (not leftovers from a prior turn).
            self._turn_audio_first_sent_at = time.monotonic()
            self._turn_audio_bytes_sent = 0
        self._turn_audio_bytes_sent += len(pcm)
        await self._send_audio(pcm)
        await self._log.adebug("coord.audio_fwd", bytes=len(pcm))

    async def on_audio_done(self) -> None:
        """OpenAI finished emitting audio for the current response."""
        if self.response_cancelled or not self._speaking_state.is_speaking:
            return
        await self._speaking_state.force_release()
        await self._log.ainfo("coord.audio_done")

    async def on_tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Model called a tool — dispatch, send output back, latch the result.

        Skill exceptions are caught here and turned into a structured error
        `tool_output` so the OpenAI session survives. Without this envelope a
        skill bug propagates to the receive loop, kills the session, and the
        user hears silence — see docs/triage.md T1.6.
        """
        if self.response_cancelled or self.current_turn is None:
            return
        self._enter_in_response_if_idle_between_rounds()

        try:
            result = await self._dispatch_tool(name, args)
        except Exception as exc:
            await self._handle_tool_error(call_id, name, args, exc)
            return
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
            await self._stop_content_stream()
            self.current_turn.needs_follow_up = True
        elif isinstance(result.side_effect, SetVolume):
            # Forward the volume command to the client immediately. The model
            # says the confirmation ("Listo, el volumen está al X%") via the
            # follow-up response.
            await self._send_set_volume(result.side_effect.level)
            self.current_turn.needs_follow_up = True
        elif isinstance(result.side_effect, PlaySound):
            # Latch the chime; sent to the audio channel right after
            # request_response() so it queues ahead of model audio (FIFO).
            # Latest tool wins — overwrites any earlier pending PlaySound.
            self.current_turn.pending_play_sound = result.side_effect
            self.current_turn.needs_follow_up = True
        elif isinstance(result.side_effect, InputClaim):
            # Latch the claim for the terminal barrier. The claim latches
            # the mic + suspends the LLM provider, so the model's speech
            # for THIS turn must finish first — that's why we dispatch at
            # the barrier, same timing as AudioStream. Latest-wins: a
            # chained tool returning a second claim replaces the first;
            # the dispatch path only ever starts one.
            self.current_turn.pending_input_claim = result.side_effect
        else:
            # Tool calls without a side-effect that is dispatched serially.
            # If a future persona needs parallel dispatch (multiple I/O-heavy
            # tools in one response), collect them and asyncio.gather before
            # sending outputs. See docs/triage.md C2.
            self.current_turn.needs_follow_up = True

    async def _handle_tool_error(
        self,
        call_id: str,
        name: str,
        args: dict[str, Any],
        exc: BaseException,
    ) -> None:
        """Skill raised in `handle()`. Don't kill the session.

        Sends a structured error tool_output back to OpenAI so the model can
        verbalize an apology naturally instead of going silent. Sets
        `needs_follow_up=True` to make the model produce that audible
        acknowledgement on the next response round. Logs the full traceback
        with tool/args context for diagnosis. The receive loop never sees the
        exception — see docs/triage.md T1.6.
        """
        await self._log.aexception(
            "coord.tool_error",
            tool=name,
            args=args,
            exception_class=type(exc).__name__,
            tid=self._tid(),
        )
        error_output = json.dumps(
            {
                "error": "tool_failed",
                "tool": name,
                "message": (
                    "esta acción falló inesperadamente; "
                    "discúlpate brevemente con el usuario y ofrece otra alternativa"
                ),
            }
        )
        await self._provider.send_tool_output(call_id, error_output)
        if self.current_turn is not None:
            self.current_turn.tool_calls += 1
            self.current_turn.needs_follow_up = True
        await self._send_dev_event(
            "tool_error",
            {
                "name": name,
                "args": args,
                "exception_class": type(exc).__name__,
                "message": str(exc),
            },
        )

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
                # Latched PlaySound (e.g. news intro chime): send right after
                # request_response so the chime hits the WebSocket ahead of the
                # model's audio deltas (FIFO). Skipped if PTT raced and set
                # response_cancelled while we were waiting.
                pending_sound = self.current_turn.pending_play_sound
                if pending_sound is not None and not self.response_cancelled:
                    self.current_turn.pending_play_sound = None
                    await self._send_audio(pending_sound.pcm)
                    await self._log.ainfo(
                        "coord.play_sound_dispatched", bytes=len(pending_sound.pcm)
                    )
        else:
            await self._apply_side_effects()

    async def on_session_disconnected(self) -> None:
        """OpenAI session dropped — abort any live turn without cancelling OpenAI.

        Ordering note: content stream MUST stop before `force_release`.
        Between the two awaits, the pump task can run `_factory_send_audio`,
        observe `is_speaking=False`, and re-acquire FACTORY — leaving
        SpeakingState stuck on a dead pump after cleanup completes.
        """
        had_media = self._content_is_running()
        was_speaking = self._speaking_state.is_speaking
        tid = self._tid()

        # Stop the pump FIRST so no subsequent acquire can race with force_release.
        # Release DIALOG too so FM's stacks don't carry orphan activities
        # across the disconnect. End any active claim with ERROR — a
        # session disconnect during a call isn't a natural close.
        await self._stop_content_stream()
        claim = self._claim_obs
        if claim is not None:
            claim.set_end_reason(ClaimEndReason.ERROR)
            await self._end_input_claim(claim)
        await self._release_dialog()
        await self._speaking_state.force_release()

        await logger.ainfo(
            "coord.session_disconnected",
            turn=tid,
            had_media=had_media,
            was_speaking=was_speaking,
        )

        if self.current_turn is None:
            return
        self.current_turn.pending_audio_streams.clear()
        self.current_turn.pending_input_claim = None
        self.current_turn.state = TurnState.INTERRUPTED
        self.current_turn = None
        # Defensive invariant — see matching pattern in `interrupt()`.
        self._current_injected_dedup_key = None
        self._signal_injected_turn_done()
        self._bind_turn()
        await self._send_audio_clear()

    # --- Interrupt: the atomic barrier ---

    async def interrupt(self) -> None:
        """Raise the interrupt barrier. Atomic 6-step sequence — see
        `docs/turns.md#3-interrupt`. Order matters.
        """
        prev_state = self.current_turn.state.value if self.current_turn else None
        has_media = self._content_is_running()
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
        # 2. Drop pending audio streams + any latched PlaySound chime +
        #    any latched InputClaim (tool returned a claim but interrupt
        #    fires before the terminal barrier — claim never starts).
        #    Skill's on_claim_end doesn't fire here because from the
        #    skill's perspective the interrupt-at-tool-dispatch is the
        #    same as the tool never having succeeded.
        if self.current_turn is not None:
            self.current_turn.pending_audio_streams.clear()
            self.current_turn.pending_play_sound = None
            self.current_turn.pending_input_claim = None
        # 3. Cancel any live content stream (pump task -> done) BEFORE
        #    force_release. If the pump is mid-`_factory_send_audio`
        #    during force_release's await, it can see `is_speaking=False`,
        #    re-acquire FACTORY, and leave SpeakingState stuck on a dead
        #    stream after cleanup. Stop the producer first. Also release
        #    any DIALOG Activity (from an in-flight inject_turn) so FM's
        #    stacks stay consistent with the coordinator's view.
        await self._stop_content_stream()
        # End any active InputClaim with USER_PTT reason (grandpa held
        # PTT during a call / voice memo — skill knows this wasn't a
        # natural close). The observer's cleanup calls `provider.resume()`
        # so the subsequent normal PTT flow works. Idempotent: no-op if
        # no claim is active.
        claim = self._claim_obs
        if claim is not None:
            claim.set_end_reason(ClaimEndReason.USER_PTT)
            await self._end_input_claim(claim)
        await self._release_dialog()
        # 4. Flush client-side audio queue + clear model-speaking state
        await self._send_audio_clear()
        await self._speaking_state.force_release()
        # 5. Cancel the in-flight response (only if one could exist)
        if will_cancel:
            await self._provider.cancel_current_response()
        # 6. Mark current turn as interrupted, emit summary, clear ref
        if self.current_turn is not None:
            self.current_turn.state = TurnState.INTERRUPTED
            await self._emit_turn_summary(reason="interrupted", spawned_audio_stream=False)
        self.current_turn = None
        # Invariant with `_release_dialog` (called above) but clear defensively
        # in case a future code path interrupts without going through it.
        self._current_injected_dedup_key = None
        # Unblock any inject_turn_and_wait caller so it doesn't hang forever
        # after an interrupt.
        self._signal_injected_turn_done()
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

        Clears `current_turn` BEFORE spawning the content stream so the
        stream's natural-completion path (which may fire a synthetic-turn
        announcement) doesn't race with our own turn-cleanup awaits.

        Split into two phases: (1) tear down the ending turn; (2) dispatch
        the post-turn sequence (preempt / content / queue drain). The
        dispatch branch count grows with future stages (1d.2 adds a TTL
        expiry branch; Stage 2 adds `InputClaim` cleanup), so keeping the
        decision in its own method keeps this one focused on cleanup.
        """
        if self.current_turn is None:
            return

        streams = self.current_turn.pending_audio_streams
        claim = self.current_turn.pending_input_claim
        turn_id = self._tid()
        self.current_turn.state = TurnState.APPLYING_FACTORIES

        if len(streams) > 1:
            await self._log.ainfo(
                "coord.audio_streams_superseded",
                dropped=len(streams) - 1,
            )

        # Tear down the parent turn fully before spawning the content stream.
        # Otherwise the stream's natural-completion callback can fire during
        # one of these awaits and see `current_turn != None`, falsely
        # concluding a new turn started.
        await self._emit_turn_summary(
            reason="ended",
            spawned_audio_stream=bool(streams),
        )
        await self._log.ainfo("coord.turn_ended")
        # Release any DIALOG Activity this turn held (injected turn finishing
        # normally). Content Activities are released by `_stop_content_stream`
        # which the caller path reaches separately.
        await self._release_dialog()
        self.current_turn = None
        # Clear the in-flight dedup key alongside `current_turn` (defensive —
        # `_release_dialog` also clears it, but coupling the clear to the
        # turn-clear makes the invariant robust against future code paths
        # that might end a turn without going through _release_dialog).
        self._current_injected_dedup_key = None
        # Signal any inject_turn_and_wait caller. Grab + clear the ref first
        # so _dispatch_post_turn (which may fire another injected turn) sees
        # a clean slot and a fresh event if needed.
        self._signal_injected_turn_done()
        self._bind_turn()
        await self._send_status(self._status["ready"])

        await self._dispatch_post_turn(streams, claim, turn_id)

    async def _dispatch_post_turn(
        self,
        streams: list[AudioStream],
        claim: InputClaim | None,
        turn_id: str | None,
    ) -> None:
        """Decide what happens after a turn ends.

        Mutually-exclusive branches in priority order:

        1. **PREEMPT over content/claim** — if the queue has a PREEMPT
           entry and this turn spawned content OR latched a claim, fire
           the PREEMPT and drop both. PREEMPT callers accept the tradeoff
           of dropping user-requested work (audiobook, voice memo,
           incoming call) to surface a time-critical event (fire alarm
           or equivalent). NORMAL / BLOCK_BEHIND_COMMS entries ahead of
           it keep their place. A dropped claim fires its own
           `on_claim_end(PREEMPTED)` so skills see the same lifecycle
           they would on mid-flight preemption.
        2. **BLOCK_BEHIND_COMMS over content (only)** — if the queue has
           a BLOCK_BEHIND_COMMS entry and this turn spawned a content
           stream but did NOT latch a claim, fire the alert and drop the
           stream. If a claim is pending, BLOCK_BEHIND_COMMS waits —
           that's the whole point of the tier, respect live calls.
        3. **Claim wins over streams** — a tool that latched the mic
           takes priority over a tool that also requested audio playback
           (rare — typically one tool returns one side-effect). Latest
           tool wins anyway, but we'd rather start the claim than play
           audio into a mic the skill wanted captured.
        4. **Content wins** — no claim, no PREEMPT/BLOCK_BEHIND_COMMS
           drain: start the pending stream.
        5. **Quiet moment** — nothing pending: drain the head of the
           queue (FIFO, any priority).
        """
        preempt_index = self._find_first_with_priority(InjectPriority.PREEMPT)
        has_streams = bool(streams)
        has_claim = claim is not None
        has_foreground_work = has_streams or has_claim
        if has_foreground_work and preempt_index is not None:
            request = self._injected_queue.pop(preempt_index)
            await self._log.ainfo(
                "coord.inject_turn_preempted_content",
                remaining=len(self._injected_queue),
                dedup_key=request.dedup_key,
                dropped_streams=len(streams),
                dropped_claim=claim is not None,
            )
            # Fire the dropped claim's `on_claim_end(PREEMPTED)` so skills
            # see the lifecycle callback even when the claim never started.
            # Preserves the invariant "every InputClaim returned gets one
            # on_claim_end call." Skill callback raising is isolated.
            if claim is not None and claim.on_claim_end is not None:
                try:
                    await claim.on_claim_end(ClaimEndReason.PREEMPTED)
                except Exception:
                    await logger.aexception(
                        "coord.dropped_claim_on_end_raised",
                    )
            await self._fire_injected_turn(request.prompt, request.dedup_key)
            return
        # BLOCK_BEHIND_COMMS: preempts streams but respects claims. If
        # there's a pending claim, the alert stays queued — drains at
        # the next quiet moment (typically the synthetic turn the
        # comms skill fires at claim-end, which has no foreground work
        # of its own and falls through to the FIFO branch below).
        if has_streams and not has_claim:
            block_idx = self._find_first_with_priority(InjectPriority.BLOCK_BEHIND_COMMS)
            if block_idx is not None:
                request = self._injected_queue.pop(block_idx)
                await self._log.ainfo(
                    "coord.inject_turn_preempted_content",
                    remaining=len(self._injected_queue),
                    dedup_key=request.dedup_key,
                    priority=request.priority.value,
                    dropped_streams=len(streams),
                    dropped_claim=False,
                )
                await self._fire_injected_turn(request.prompt, request.dedup_key)
                return
        if claim is not None:
            # Claim wins over streams: a mic latch is more authoritative
            # than audio playback in the same turn (rare — most skills
            # return one side-effect — but if a tool somehow returns
            # both, we start the claim and drop the stream silently).
            try:
                await self.start_input_claim(claim)
            except ClaimBusyError:
                # Defensive: a prior claim is still active when a new
                # tool tried to latch one. Skills should guard against
                # this themselves, but the coordinator cannot let this
                # propagate and crash the turn. Surface to the skill
                # via `on_claim_end(ERROR)` so its cleanup runs and the
                # user sees a coherent outcome.
                await logger.awarning(
                    "coord.claim_rejected_busy",
                    reason="existing_claim_active",
                )
                if claim.on_claim_end is not None:
                    try:
                        await claim.on_claim_end(ClaimEndReason.ERROR)
                    except Exception:
                        await logger.aexception(
                            "coord.claim_rejected_on_end_raised",
                        )
            return
        if streams:
            await self._start_content_stream(streams[-1], turn_id)
            return
        if self._injected_queue:
            request = self._injected_queue.pop(0)
            await self._log.ainfo(
                "coord.inject_turn_dequeued",
                remaining=len(self._injected_queue),
                dedup_key=request.dedup_key,
                priority=request.priority.value,
            )
            await self._fire_injected_turn(request.prompt, request.dedup_key)

    def _find_first_with_priority(self, priority: InjectPriority) -> int | None:
        """Return the index of the first queue entry with the given
        priority, or None if none exist. Used by `_dispatch_post_turn`
        to decide whether to drain a priority-specific branch
        (PREEMPT-over-all-foreground, BLOCK_BEHIND_COMMS-over-content)."""
        for i, req in enumerate(self._injected_queue):
            if req.priority is priority:
                return i
        return None

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

    async def _start_content_stream(self, stream: AudioStream, turn_id: str | None) -> None:
        """Spawn a pump for `stream` via a fresh `ContentStreamObserver`.

        Any previously-running content stream is stopped first. Routes
        through `FocusManager`: constructs an Activity on the CONTENT
        channel and acquires; the actor delivers FOREGROUND to the
        observer, which spawns the pump task. `wait_drained()` blocks
        until FOREGROUND has been fully processed, so
        `current_media_task` is non-None immediately on return
        (synchronous-looking semantics preserved for callers).

        SpeakingState acquires FACTORY on the first chunk and releases
        it when the stream ends naturally, unless
        `_maybe_fire_completion_prompt` transfers ownership to
        COMPLETION (synthetic audiobook-end turn).
        """
        await self._stop_content_stream()
        interface_name = f"turn.content.{turn_id or 'unknown'}"
        # Boxed so the async closures below can mutate it (Python has no
        # plain `nonlocal` for coroutines closed-over by multiple callables).
        owns_speaking = [False]

        async def _factory_send_audio(chunk: bytes) -> None:
            # Acquire FACTORY speaker lazily on the first chunk. If another
            # owner already holds the flag (unlikely but possible if the
            # stream starts while the model is still finishing its round),
            # defer to them — the chunk still forwards, we just don't take
            # the flag.
            if not owns_speaking[0] and not self._speaking_state.is_speaking:
                await self._speaking_state.acquire(SpeakingOwner.FACTORY)
                owns_speaking[0] = True
            await self._send_audio(chunk)

        async def _on_eof() -> None:
            await logger.ainfo(
                "coord.audio_stream_ended",
                turn=turn_id,
                interface=interface_name,
                cancelled=False,
            )
            await self._send_stream_ended(interface_name, "natural")
            transferred = await self._maybe_fire_completion_prompt(stream, turn_id)
            if transferred:
                # Ownership moved FACTORY -> COMPLETION; don't release here.
                # The synthetic turn's `on_audio_done` clears COMPLETION.
                owns_speaking[0] = False
            elif owns_speaking[0]:
                await self._speaking_state.release(SpeakingOwner.FACTORY)
                owns_speaking[0] = False

        obs = ContentStreamObserver(
            interface_name=interface_name,
            stream=stream,
            send_audio=_factory_send_audio,
            on_eof=_on_eof,
        )
        # Set the local cache BEFORE acquire — the actor may deliver
        # FOREGROUND before wait_drained returns if the mailbox is busy,
        # and downstream callbacks might query `current_media_task`.
        self._content_obs = obs

        # `AudioStream.content_type` defaults to NONMIXABLE (right for
        # spoken content — audiobooks, radio, news). A skill shipping
        # MIXABLE content (background music, ambient) sets it on the
        # stream; the FocusManager then delivers BACKGROUND/MAY_DUCK on
        # preemption (not NONE/MUST_STOP) and the observer's gain
        # envelope ramps down instead of hard-pausing.
        #
        # Patience: skill override wins; otherwise derive from
        # content_type. MIXABLE defaults to 5 min so the duck envelope
        # has room to work; NONMIXABLE defaults to 0 (evict on
        # preempt). Skills that want pause/resume for their
        # NONMIXABLE stream (audiobooks: a call parks the book for
        # ~30 min, then resumes on hangup) set `stream.patience`
        # explicitly.
        if stream.patience is not None:
            patience = stream.patience
        elif stream.content_type is ContentType.MIXABLE:
            patience = timedelta(minutes=5)
        else:
            patience = timedelta(0)
        activity = Activity(
            channel=Channel.CONTENT,
            interface_name=interface_name,
            content_type=stream.content_type,
            observer=obs,
            patience=patience,
        )
        await logger.ainfo(
            "coord.audio_stream_started",
            turn=turn_id,
            interface=interface_name,
        )
        await self._focus_manager.acquire(activity)
        # Wait for the actor to process the Acquire event → deliver
        # FOREGROUND to the observer → spawn the pump task. After this,
        # `obs.task is not None` and the stream is actually running.
        await self._focus_manager.wait_drained()
        await self._send_stream_started(interface_name, stream.label, stream.preroll_ms)

    async def _stop_content_stream(self) -> None:
        """Cancel any running content-stream observer. Idempotent.

        Routes through `FocusManager`: releases the CONTENT-channel
        Activity; the actor delivers NONE/MUST_STOP to the observer,
        which cancels the pump task and awaits its cleanup.
        `wait_drained()` blocks until that whole chain completes, so
        by the time we return the pump is fully dead — preserving
        `interrupt()`'s strict ordering guarantee (pump gone before
        `force_release` runs, per the 1a fix).
        """
        obs = self._content_obs
        if obs is None:
            return
        task = obs.task
        was_running = task is not None and not task.done()
        if was_running:
            await logger.ainfo(
                "coord.audio_stream_ended",
                interface=obs.interface_name,
                cancelled=True,
            )
            await self._send_stream_ended(obs.interface_name, "interrupted")
        self._content_obs = None
        await self._focus_manager.release(Channel.CONTENT, obs.interface_name)
        await self._focus_manager.wait_drained()

    # --- Input claim (T1.4 Stage 2 — mic-capture skills) ---

    async def start_input_claim(self, claim: InputClaim) -> ClaimHandle:
        """Latch the mic to `claim`'s handler via a COMMS-channel Activity.

        Direct-entry path — used by skills that start a claim from a
        `background_task` (incoming call, panic button). For claims
        triggered by a tool call, the side-effect path through
        `_apply_side_effects` (Stage 2 commit 3c) reaches this same
        internal machinery.

        Steps (order enforced by the observer's `_start`):

        1. `provider.suspend()` — stops OpenAI generating / processing.
        2. `mic_router.claim(handler)` — routes future mic frames to
           the skill.
        3. (If supplied) start speaker_source pump.

        Returns a `ClaimHandle` with `cancel()` (skill-initiated end
        with `NATURAL` reason) and `wait_end()` (resolves to the
        `ClaimEndReason` when the claim ends for any reason).

        Single-slot policy: raises `ClaimBusyError` if a claim is
        already active. Huxley does not stack claims today — the
        second call gets a clean rejection so the skill can notify
        its peer (e.g. Telegram sends DISCARDED_CALL). Interface_name
        is the literal `"claim:active"` so the one-slot semantics
        are reflected in focus-manager state too (same-interface
        replacement would be well-defined if ever reached, but the
        busy check above prevents that path).
        """
        if self._claim_obs is not None:
            raise ClaimBusyError(
                "a claim is already active on COMMS; reject or end the "
                "existing claim before starting a new one"
            )
        interface_name = "claim:active"
        end_event = asyncio.Event()
        final_reason: dict[str, ClaimEndReason] = {}

        async def _observer_on_end(reason: ClaimEndReason) -> None:
            # Coordinator-side cleanup after observer fires its skill
            # callback. Scrub local ref, send client notifications, then
            # resolve wait_end so callers see a consistent post-claim state.
            final_reason["reason"] = reason
            if self._claim_obs is observer:
                self._claim_obs = None
                self._claim_started_at = None
            # Tell the client the mic-policy is back to assistant_ptt
            # and fire the observability event so UIs can de-render
            # their "en llamada" indicator. Reason-mapped so a
            # preempted claim shows up distinctly in logs and UX.
            mode_reason = (
                "claim_preempted" if reason is ClaimEndReason.PREEMPTED else "claim_ended"
            )
            try:
                await self._send_claim_ended(interface_name, reason.value)
                await self._send_input_mode(
                    "assistant_ptt",
                    reason=mode_reason,
                    claim_id=None,
                )
            except Exception:
                # Never let a client-send error propagate out of the
                # observer's end callback — the claim must still
                # unwind cleanly server-side.
                await logger.aexception("coord.claim_end_notify_failed")
            # Signal after notifications are sent so wait_end() callers
            # observe a fully notified client state. (end_event.set is
            # synchronous — no yield — so moving it here is safe and
            # avoids a race where wait_end() returns before send_input_mode
            # fires.)
            end_event.set()

        # `release_self` bound to the observer's specific interface_name
        # so the observer can drive its own unwind on error (e.g., mic
        # router busy at FOREGROUND time) without holding a FocusManager
        # reference directly.
        async def _release_self() -> None:
            await self._focus_manager.release(Channel.COMMS, interface_name)

        observer = ClaimObserver(
            interface_name=interface_name,
            claim=claim,
            mic_router=self._mic_router,
            send_audio=self._send_audio,
            suspend_provider=self._provider.suspend,
            resume_provider=self._provider.resume,
            speaking_state=self._speaking_state,
            release_self=_release_self,
            on_end=_observer_on_end,
        )
        # Latch ref before acquire — the FM may deliver FOREGROUND
        # before `wait_drained` returns if the mailbox is busy.
        self._claim_obs = observer
        activity = Activity(
            channel=Channel.COMMS,
            interface_name=interface_name,
            content_type=ContentType.NONMIXABLE,
            observer=observer,
            patience=timedelta(0),
        )
        await self._log.ainfo("coord.claim_starting", interface=interface_name)
        # Flush any LLM audio still buffered at the client. The claim's speaker
        # source is about to become the only audio source; queued announcement
        # audio would play ahead of it, creating an audible delay (especially
        # on inbound calls where announce → claim start is back-to-back).
        await self._send_audio_clear()
        await self._focus_manager.acquire(activity)
        # Wait for FOREGROUND to be fully processed so suspend + mic
        # swap are done before the caller's next tick.
        await self._focus_manager.wait_drained()

        # Record the claim's start time for the debounce window — a PTT
        # press within 300ms of claim_started is ignored (same tap that
        # dispatched the tool-call would otherwise also end-claim).
        self._claim_started_at = time.monotonic()

        # Tell the client to switch to continuous mic streaming — only if
        # the claim is still active. A very fast speaker source (e.g. a
        # test fixture with a single-chunk iterator) can exhaust and
        # release the claim entirely within wait_drained() above.
        # _observer_on_end already sent "assistant_ptt" in that case;
        # sending "skill_continuous" after the fact would mis-sequence
        # the client's mode transitions.
        if self._claim_obs is observer:
            try:
                # Title is the human-readable label for UI clients
                # (e.g., contact name on a call). Falls back to the
                # interface name so observability consumers always
                # see something. Skill name isn't currently plumbed
                # into InputClaim — passing interface_name as `skill`
                # keeps wire-format stable while the UI uses `title`.
                await self._send_claim_started(
                    interface_name,
                    interface_name,
                    claim.title,
                )
                await self._send_input_mode(
                    "skill_continuous",
                    reason="claim_started",
                    claim_id=interface_name,
                )
            except Exception:
                await logger.aexception("coord.claim_start_notify_failed")

        coord = self
        # Holds a reference to the background cancel task so Python's GC
        # doesn't collect it mid-release. Cleared when the task completes.
        cancel_task_ref: dict[str, asyncio.Task[None]] = {}

        def _cancel_sync() -> None:
            """Synchronous cancel from ClaimHandle. Idempotent: if the
            observer has already ended we no-op (second cancel, or race
            with coordinator interrupt / inject PREEMPT that got there
            first). Fires the async release as a detached task — the
            handle's `wait_end()` is the caller-facing sync point."""
            if observer.is_ended:
                return
            observer.set_end_reason(ClaimEndReason.NATURAL)
            task = asyncio.create_task(
                coord._end_input_claim(observer),
                name=f"claim_cancel:{interface_name}",
            )
            cancel_task_ref["task"] = task
            task.add_done_callback(lambda _t: cancel_task_ref.pop("task", None))

        async def _wait() -> ClaimEndReason:
            await end_event.wait()
            return final_reason["reason"]

        return ClaimHandle(_cancel=_cancel_sync, _wait_end=_wait)

    async def _end_input_claim(self, observer: ClaimObserver) -> None:
        """Release the observer's Activity via FocusManager. Used by the
        skill-cancel path (async-triggered from sync `ClaimHandle.cancel`)
        and by the coordinator's user-PTT interrupt path. FM delivers
        NONE to the observer, which fires its cleanup chain."""
        if observer.is_ended:
            return
        await self._focus_manager.release(Channel.COMMS, observer.interface_name)
        await self._focus_manager.wait_drained()

    async def cancel_active_claim(
        self,
        *,
        reason: ClaimEndReason = ClaimEndReason.NATURAL,
    ) -> bool:
        """End any active InputClaim from outside the observer.

        Stage 2.1 — closes the gap where a side-effect-dispatched claim
        (the calls skill's path) has no `ClaimHandle` exposed to the
        skill. When the caller's WebSocket closes, the calls skill needs
        to end the claim so `on_claim_end` fires and "Mario colgó"
        narrates; without this method the skill would have to wait for
        grandpa to PTT or for a PREEMPT inject.

        Returns True if a claim was active and is being torn down,
        False if no claim was active (caller doesn't need to do anything).

        Idempotent: calling while a claim is already mid-end (observer
        marked `is_ended`) is a no-op that returns False.

        `reason` defaults to NATURAL (skill-initiated close). The calls
        skill could pass a custom reason in the future (e.g. a hypothetical
        `CALLER_HUNG_UP` if we ever differentiate from skill-cancel).
        For now NATURAL covers it.
        """
        observer = self._claim_obs
        if observer is None or observer.is_ended:
            return False
        observer.set_end_reason(reason)
        await self._end_input_claim(observer)
        return True

    # --- Proactive speech (T1.4 Stages 1c.3 + 1d — inject_turn) ---

    async def inject_turn(
        self,
        prompt: str,
        *,
        dedup_key: str | None = None,
        priority: InjectPriority = InjectPriority.NORMAL,
    ) -> None:
        """Speak `prompt` proactively via a synthetic DIALOG turn.

        Behavior:

        - If idle (no current turn), fire immediately. Any playing
          content stream gets preempted via FocusManager (CONTENT →
          BACKGROUND/MUST_PAUSE → pump cancels). Priority doesn't
          matter from idle — both tiers behave the same.
        - If a user or synthetic turn is already in progress, **queue**
          the request. The queue drains in `_apply_side_effects` when
          a turn ends — but WHAT drains depends on `priority`:
            - `NORMAL` drains only when the turn ends without a pending
              content stream (content wins; reminder waits for the next
              quiet moment).
            - `PREEMPT` drains unconditionally, even displacing a
              freshly-spawned content stream. Right for medication
              reminders and safety-critical events where "wait until
              the user PTTs again" could mean hours.

        Priority never barges into a live user turn — the queue always
        waits for turn-end, regardless of tier. Interrupting a user
        mid-speech is hostile; PREEMPT is about beating a content
        stream, not the user.

        `dedup_key` (Stage 1d.1): an opaque string identifying this
        logical request. If a queued entry already exists with the same
        key, the new request REPLACES it (last-writer-wins). If the
        currently-firing injected turn has the same key, the new
        request is silently DROPPED — duplicate narration-in-progress
        is worse than a missed dedup. `dedup_key=None` bypasses dedup.

        Not yet shipped (future stage): `expires_after` TTL, outcome
        handle with `wait_outcome()`, finer arbitration tiers.
        """
        if self.current_turn is not None:
            await self._enqueue_injected(prompt, dedup_key, priority)
            return
        # A **live** COMMS claim (live call, voice memo, etc.) counts as
        # "busy" for NORMAL and BLOCK_BEHIND_COMMS — respect the live
        # audio plane by queueing. The queue drains at turn-end, which
        # reliably follows claim-end (either via the comms skill's own
        # post-claim inject_turn or via the next user turn). Only
        # PREEMPT barges through a claim; that's its whole contract.
        #
        # `is_ended` guard is load-bearing: during claim teardown, the
        # skill's `on_claim_end` callback fires BEFORE the coordinator
        # gets a chance to scrub `_claim_obs`. A skill that fires an
        # `inject_turn` from `on_claim_end` (e.g., telegram's "la
        # llamada terminó" narration) would otherwise queue its own
        # announcement behind itself — the user hears nothing until
        # the next unrelated turn ends and drains the queue. Observer's
        # `_ended` flag is set at the top of `_end()` so by the time
        # any skill callback runs, `is_ended` is already True and we
        # correctly fall through to immediate fire.
        claim_obs = self._claim_obs
        if (
            claim_obs is not None
            and not claim_obs.is_ended
            and priority is not InjectPriority.PREEMPT
        ):
            await self._enqueue_injected(prompt, dedup_key, priority)
            return
        await self._fire_injected_turn(prompt, dedup_key)

    def _signal_injected_turn_done(self) -> None:
        """Set and clear `_injected_turn_done` if one is armed.

        Called from every path that clears `current_turn` (normal end,
        interrupt, session disconnect) so `inject_turn_and_wait` callers
        always unblock regardless of how the turn ends.
        """
        event = self._injected_turn_done
        if event is not None:
            self._injected_turn_done = None
            event.set()

    async def inject_turn_and_wait(
        self,
        prompt: str,
        *,
        dedup_key: str | None = None,
    ) -> None:
        """Like `inject_turn` but returns only after the client has
        finished PLAYING the narration (not just the server finishing
        sending it).

        If the coordinator is idle (`current_turn is None`), fires the
        injected turn immediately and awaits the `_injected_turn_done`
        event (signalled when `_apply_side_effects` / `interrupt()`
        clears the turn). At that moment the server has finished
        sending audio deltas, but the client is still playing them out
        of its buffer. We then sleep long enough for the buffer to
        drain. Without the drain wait, a caller chaining
        `inject_turn_and_wait` → `start_input_claim` would flush the
        client buffer (via `audio_clear`) and guillotine the tail of
        the announcement — the exact behavior the 2026-04-24 smoke
        test caught on inbound Telegram calls.

        If a turn is already in progress, falls back to a plain
        enqueue (same as `inject_turn`) and returns immediately — the
        caller should not depend on the wait semantics in that case.
        """
        if self.current_turn is not None:
            await self._enqueue_injected(prompt, dedup_key, InjectPriority.NORMAL)
            return
        await self._fire_injected_turn(prompt, dedup_key)
        # _fire_injected_turn sets _injected_turn_done before returning;
        # capture it now (before any other task can clear it) and wait.
        done = self._injected_turn_done
        if done is not None:
            await done.wait()
        await self._wait_for_client_playback_drain()

    # Audio rate for the one audio channel shared with the client:
    # 24kHz sample rate * 2 bytes/sample (PCM16 mono).
    _CLIENT_AUDIO_BYTES_PER_SECOND: ClassVar[int] = 24_000 * 2

    # Extra headroom past the computed drain time — covers the client's
    # Web Audio scheduling floor (`max(now + 0.01, nextTime)`) plus a
    # small WebSocket / scheduling jitter margin. 80ms is short enough
    # not to feel laggy and generous enough to absorb real-world
    # network variance on localhost and typical LANs.
    _PLAYBACK_DRAIN_SAFETY_MS: ClassVar[int] = 80

    async def _wait_for_client_playback_drain(self) -> None:
        """Sleep until the client is expected to have finished playing
        whatever the coordinator sent during the just-ended audio stream.

        Uses the byte counters updated in `on_audio_delta`:
        `_turn_audio_first_sent_at` (monotonic time of the first delta
        this response) + `_turn_audio_bytes_sent` / 48000 gives a wall-
        clock estimate of when the last sample will have been rendered
        on the client speaker. We add `_PLAYBACK_DRAIN_SAFETY_MS` of
        headroom to absorb the client's AudioContext scheduling floor
        and WebSocket jitter.

        No-op when no audio was sent this stream (e.g., the LLM
        responded with text-only or the request was cancelled before
        audio) — `_turn_audio_first_sent_at` stays None.
        """
        first_sent_at = self._turn_audio_first_sent_at
        bytes_sent = self._turn_audio_bytes_sent
        if first_sent_at is None or bytes_sent <= 0:
            return
        expected_duration_s = bytes_sent / self._CLIENT_AUDIO_BYTES_PER_SECOND
        drain_at = first_sent_at + expected_duration_s + self._PLAYBACK_DRAIN_SAFETY_MS / 1000
        remaining = drain_at - time.monotonic()
        if remaining <= 0:
            return
        await self._log.ainfo(
            "coord.inject_and_wait_drain_sleep",
            bytes_sent=bytes_sent,
            expected_duration_ms=int(expected_duration_s * 1000),
            remaining_ms=int(remaining * 1000),
        )
        await asyncio.sleep(remaining)

    async def _enqueue_injected(
        self,
        prompt: str,
        dedup_key: str | None,
        priority: InjectPriority,
    ) -> None:
        """Append or dedup-replace a pending inject_turn request."""
        if dedup_key is not None:
            if self._current_injected_dedup_key == dedup_key:
                await self._log.ainfo(
                    "coord.inject_turn_dropped",
                    reason="dedup_in_flight",
                    dedup_key=dedup_key,
                )
                return
            # Remove any same-key entries already queued (last-writer-wins).
            before = len(self._injected_queue)
            self._injected_queue = [r for r in self._injected_queue if r.dedup_key != dedup_key]
            if len(self._injected_queue) < before:
                await self._log.ainfo(
                    "coord.inject_turn_deduped",
                    dedup_key=dedup_key,
                    removed=before - len(self._injected_queue),
                )
        self._injected_queue.append(
            _InjectedRequest(prompt=prompt, dedup_key=dedup_key, priority=priority)
        )
        await self._log.ainfo(
            "coord.inject_turn_queued",
            queue_depth=len(self._injected_queue),
            dedup_key=dedup_key,
            priority=priority.value,
            prev_state=self.current_turn.state.value if self.current_turn else None,
        )

    async def _fire_injected_turn(self, prompt: str, dedup_key: str | None) -> None:
        """Actually create the INJECTED turn, acquire DIALOG, send prompt.
        Caller guarantees `self.current_turn is None`."""
        # Synthesize the Turn. Skills pass a plain string; the framework
        # wraps it in a TurnSource.INJECTED turn that the rest of the
        # coordinator treats like any other in-response turn — audio
        # deltas, audio_done, response_done all flow through the same
        # handlers and clean up at the terminal barrier.
        self.current_turn = self._turn_factory.create(
            source=TurnSource.INJECTED, initial_state=TurnState.IN_RESPONSE
        )
        self._bind_turn()
        self.response_cancelled = False

        interface_name = f"turn.dialog.{self._tid() or 'unknown'}"
        self._dialog_interface_name = interface_name
        self._current_injected_dedup_key = dedup_key

        async def _on_stop() -> None:
            # Fired when FM delivers NONE/MUST_STOP to the observer —
            # i.e. when the coordinator releases the DIALOG activity.
            # The turn-end bookkeeping (current_turn clear, summary,
            # status) happens in `_apply_side_effects` or `interrupt`;
            # this callback is a no-op placeholder that keeps the
            # DialogObserver contract satisfied.
            pass

        activity = Activity(
            channel=Channel.DIALOG,
            interface_name=interface_name,
            content_type=ContentType.NONMIXABLE,
            observer=DialogObserver(interface_name=interface_name, on_stop=_on_stop),
        )

        await self._log.ainfo(
            "coord.inject_turn",
            prompt_len=len(prompt),
            interface=interface_name,
            dedup_key=dedup_key,
        )
        await self._focus_manager.acquire(activity)
        await self._focus_manager.wait_drained()
        # Any content stream is now cancelled + pump dead. Flush any
        # chunks still in the client's audio queue so preempted book
        # audio doesn't trail into the injected narration.
        await self._send_audio_clear()
        # Clear any stale FACTORY owner on SpeakingState (Stage 1f fix).
        # The preempted pump raised CancelledError without releasing its
        # speaker-owner label, so without this the client sees one
        # unbroken `model_speaking=True` span from content → narration
        # with no transition cue, and the injected turn's audio_delta
        # skips the INJECTED acquire (because `is_speaking` is already
        # True). force_release is a no-op when owner is already None
        # (inject_turn from idle with no content playing).
        await self._speaking_state.force_release()

        # Arm the completion event BEFORE request_response() so it is
        # guaranteed to exist when _apply_side_effects tries to signal it.
        # Any previous event is replaced (shouldn't happen; defensive).
        self._injected_turn_done = asyncio.Event()

        # Send the prompt + ask the model to respond. The model narrates
        # in persona voice; subsequent `on_audio_delta` / `on_audio_done`
        # / `on_response_done` flow through the normal turn handlers.
        await self._provider.send_conversation_message(prompt)
        await self._provider.request_response()

    async def _release_dialog(self) -> None:
        """Release any held DIALOG Activity. Idempotent. Called from turn
        cleanup paths (`_apply_side_effects`, `interrupt`,
        `on_session_disconnected`).
        """
        iface = self._dialog_interface_name
        if iface is None:
            return
        self._dialog_interface_name = None
        # Clear the in-flight dedup key alongside the activity — a
        # re-enqueue with the same key is now allowed.
        self._current_injected_dedup_key = None
        await self._focus_manager.release(Channel.DIALOG, iface)
        await self._focus_manager.wait_drained()

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

        self.current_turn = self._turn_factory.create(
            source=TurnSource.COMPLETION, initial_state=TurnState.IN_RESPONSE
        )
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
        # Transfer speaker ownership FACTORY -> COMPLETION. No notify fires —
        # the client already sees model_speaking=true; the synthetic turn's
        # on_audio_done will clear it normally.
        self._speaking_state.transfer(SpeakingOwner.FACTORY, SpeakingOwner.COMPLETION)
        return True
