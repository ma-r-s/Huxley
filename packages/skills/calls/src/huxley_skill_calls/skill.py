"""`CallsSkill` — inbound call relay built on InputClaim.

User flow (auto-pickup with countdown, locked with Mario 2026-04-19):

1. Mario's web app does `GET /call/ring?from=Mario` with shared-secret
   header and connects WS to `/call?secret=<value>`.
2. AudioServer fires `on_ring` → this skill latches pending state and
   `inject_turn(PREEMPT)` with an instruction telling the LLM to
   announce the caller and call `answer_call` after counting down.
3. LLM speaks "Llamada de Mario, contestando en tres, dos, uno..." then
   calls `answer_call`.
4. `answer_call` returns a `ToolResult` whose `side_effect=InputClaim`
   wires the PCM relay: grandpa's mic frames → caller WS;
   `speaker_source` async iterator yields incoming caller PCM →
   coordinator forwards to grandpa's speaker.
5. Either side can end:
   - **Caller hangs up** (WS closes) → skill cancels the claim →
     `on_claim_end(NATURAL)` fires → "Mario colgó" announcement.
   - **Grandpa holds PTT** → coordinator's `interrupt()` ends the
     claim with `USER_PTT` → "Llamada finalizada" announcement.
   - **PREEMPT inject_turn** (medication reminder) → claim ends with
     `PREEMPTED`; we don't narrate (the inject is already speaking).

Security: shared secret in `persona.yaml`'s `skills.calls.secret`,
or `HUXLEY_CALLS_SECRET` env var (env wins). Transport: PCM16 mono
@ 24 kHz both directions, matching the device's main WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    ClaimEndReason,
    InjectPriority,
    InputClaim,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from websockets.asyncio.server import ServerConnection


# ---------------------------------------------------------------------------
# Prompts the LLM sees on ring + end. Spanish/AbuelOS-toned defaults; both
# overridable via persona config (`ring_prompt` / `end_natural_prompt` /
# `end_user_ptt_prompt`) for non-Spanish personas.
# ---------------------------------------------------------------------------

_DEFAULT_RING_PROMPT = (
    "Suena el teléfono. Tienes una llamada de {from_name}. Anuncia la "
    "llamada con calma — di algo como 'Llamada de {from_name}, "
    "contestando en tres, dos, uno' — y después, sin esperar respuesta del "
    "usuario, llama a la herramienta `answer_call` para abrir la "
    "comunicación. Si el usuario dice 'no' o 'ahora no' durante el "
    "conteo, llama a `reject_call` en lugar de answer_call."
)

_DEFAULT_END_NATURAL_PROMPT = (
    "El otro lado de la llamada colgó. Avísale al usuario brevemente — "
    "algo como '{from_name} colgó' o 'la llamada terminó'. Una sola frase "
    "corta."
)

_DEFAULT_END_USER_PTT_PROMPT = (
    "El usuario terminó la llamada. Confirma brevemente — algo como "
    "'llamada finalizada'. Una sola frase corta."
)

_DEFAULT_END_ERROR_PROMPT = (
    "La llamada se cortó por un problema técnico. Avísale al usuario — "
    "algo como 'la llamada se cortó, lo siento'. Una sola frase corta."
)


class CallsSkill:
    """Inbound calls via InputClaim PCM relay.

    Three tools:
    - `answer_call`: returns InputClaim that latches grandpa's mic to
      the caller and pumps caller audio to grandpa's speaker.
    - `reject_call`: cancel a pending call before answer.
    - `end_call`: explicitly end an active call (the LLM rarely needs
      this — usually the caller WS close or PTT does it, but provided
      for completeness when the LLM detects "adiós" intent in the
      pre-call announcement window).

    Two framework hooks (registered via Application wiring with
    AudioServer):
    - `on_ring(params)`: HTTP `/call/ring` arrived. Returns True if
      the ring was accepted.
    - `on_caller_connected(ws)`: caller WS upgraded. Skill takes
      ownership of the connection.
    """

    def __init__(self) -> None:
        self._ctx: SkillContext | None = None
        self._logger: SkillLogger | None = None
        self._secret: str | None = None
        # Prompts (persona-overridable).
        self._ring_prompt: str = _DEFAULT_RING_PROMPT
        self._end_natural_prompt: str = _DEFAULT_END_NATURAL_PROMPT
        self._end_user_ptt_prompt: str = _DEFAULT_END_USER_PTT_PROMPT
        self._end_error_prompt: str = _DEFAULT_END_ERROR_PROMPT
        # State for the active or pending call.
        self._caller_ws: ServerConnection | None = None
        self._caller_reader: asyncio.Task[None] | None = None
        self._pending_from: str | None = None
        self._caller_pcm_queue: asyncio.Queue[bytes] | None = None
        self._claim_active: bool = False

    @property
    def name(self) -> str:
        return "calls"

    @property
    def secret(self) -> str | None:
        """Public read-only accessor — Application wiring uses this to
        configure AudioServer's `ring_secret`. Returns None if no
        secret was configured."""
        return self._secret

    @property
    def is_busy(self) -> bool:
        """True if a call is pending or active. Used by `on_ring` to
        reject a second incoming call with 409 Busy."""
        return self._pending_from is not None or self._caller_ws is not None or self._claim_active

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="answer_call",
                description=(
                    "Contesta la llamada entrante y abre la comunicación de voz. "
                    "Llama esto INMEDIATAMENTE después de terminar el conteo "
                    "regresivo del anuncio de llamada (no esperes que el "
                    "usuario diga sí — el conteo ya implica la respuesta)."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="reject_call",
                description=(
                    "Rechaza una llamada entrante antes de contestarla. Solo "
                    "úsalo si el usuario dice 'no', 'ahora no', o 'no quiero "
                    "contestar' durante el conteo regresivo del anuncio."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="end_call",
                description=(
                    "Termina una llamada activa. Casi nunca lo necesitas — el "
                    "que llama o el botón del usuario terminan la llamada por "
                    "su lado. Úsalo solo si el usuario te pide explícitamente "
                    "'cuelga' o 'termina la llamada' durante una llamada activa."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        self._logger = ctx.logger
        # Secret precedence: env var beats persona config so an operator
        # can rotate without editing yaml. Either is fine.
        self._secret = os.environ.get("HUXLEY_CALLS_SECRET") or ctx.config.get("secret")
        if not self._secret:
            await ctx.logger.awarning(
                "calls.no_secret_configured",
                hint=(
                    "Set persona.skills.calls.secret OR HUXLEY_CALLS_SECRET env "
                    "var. Without a secret, the /call/ring + /call routes will "
                    "be disabled at the AudioServer level."
                ),
            )
        # Optional persona overrides for prompts (same pattern as timers).
        for key, attr, default in (
            ("ring_prompt", "_ring_prompt", _DEFAULT_RING_PROMPT),
            ("end_natural_prompt", "_end_natural_prompt", _DEFAULT_END_NATURAL_PROMPT),
            ("end_user_ptt_prompt", "_end_user_ptt_prompt", _DEFAULT_END_USER_PTT_PROMPT),
            ("end_error_prompt", "_end_error_prompt", _DEFAULT_END_ERROR_PROMPT),
        ):
            value = ctx.config.get(key)
            if isinstance(value, str) and value:
                setattr(self, attr, value)
            elif value is not None:
                await ctx.logger.awarning(
                    "calls.invalid_prompt_override",
                    key=key,
                    note="persona override must be a non-empty string",
                )
                setattr(self, attr, default)
        await ctx.logger.ainfo(
            "calls.setup_complete",
            has_secret=bool(self._secret),
        )

    async def teardown(self) -> None:
        # Cancel any in-flight caller reader; close the caller WS.
        await self._cleanup_caller_state()
        if self._logger is not None:
            await self._logger.ainfo("calls.teardown_complete")

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        _ = args  # all three tools take no args
        if self._logger is None or self._ctx is None:
            msg = "CallsSkill: handle() called before setup()"
            raise RuntimeError(msg)
        if tool_name == "answer_call":
            return await self._handle_answer()
        if tool_name == "reject_call":
            return await self._handle_reject()
        if tool_name == "end_call":
            return await self._handle_end_explicit()
        await self._logger.awarning("calls.unknown_tool", tool=tool_name)
        return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    # ------------------------------------------------------------------
    # AudioServer hooks
    # ------------------------------------------------------------------

    async def on_ring(self, params: dict[str, str]) -> bool:
        """Fired by AudioServer for HTTP `/call/ring`. Returns True if
        the ring was accepted (skill announces; LLM will dispatch
        `answer_call` shortly), False if grandpa is already on a call
        (server returns 409 Busy)."""
        if self._ctx is None or self._logger is None:
            return False
        if self.is_busy:
            await self._logger.awarning(
                "calls.ring_rejected_busy",
                pending=self._pending_from,
                claim_active=self._claim_active,
            )
            return False
        from_name = params.get("from", "alguien")
        self._pending_from = from_name
        await self._logger.ainfo("calls.ring_accepted", from_name=from_name)
        prompt = self._ring_prompt.format(from_name=from_name)
        # PREEMPT priority: the ring should drop a playing audiobook /
        # radio. Per the matrix, this ends any active CONTENT stream
        # and fires the inject after the model's first audio.
        await self._ctx.inject_turn(prompt, priority=InjectPriority.PREEMPT)
        return True

    async def on_caller_connected(self, ws: ServerConnection) -> None:
        """Fired by AudioServer when caller upgrades to WS `/call`. The
        skill owns this connection until the call ends — read PCM
        frames, drop them on the floor while the claim isn't active,
        forward into the speaker_source queue once the claim starts.

        This handler runs as long as the WS is open. The caller hanging
        up (close on their side) drops out of `async for` and we cancel
        any active claim from the finally."""
        if self._ctx is None or self._logger is None:
            await ws.close(1011, "Skill not initialized")
            return
        if self._caller_ws is not None:
            # Second concurrent caller — reject. AudioServer has already
            # accepted the WS upgrade, so we close it cleanly.
            await self._logger.awarning("calls.second_caller_rejected")
            await ws.close(1008, "Already in a call")
            return
        self._caller_ws = ws
        await self._logger.ainfo("calls.caller_connected")
        try:
            async for frame in ws:
                if not isinstance(frame, bytes):
                    # Caller side only sends raw PCM binary frames. Text
                    # would be a protocol mismatch; log and skip.
                    await self._logger.awarning("calls.caller_text_frame_ignored")
                    continue
                queue = self._caller_pcm_queue
                if queue is not None:
                    # Claim is active — push for speaker_source to yield.
                    queue.put_nowait(frame)
                # Else: claim not started yet (LLM still announcing) or
                # already ended (mid-cleanup). Drop on the floor.
        except Exception:
            # Includes websockets.ConnectionClosed and any unexpected
            # exception during read. Either way the caller is gone.
            await self._logger.aexception("calls.caller_read_loop_failed")
        finally:
            await self._on_caller_disconnected()

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_answer(self) -> ToolResult:
        assert self._logger is not None
        if self._caller_ws is None:
            await self._logger.awarning("calls.answer_no_caller")
            return ToolResult(output=json.dumps({"error": "no caller connected", "ok": False}))
        if self._claim_active:
            await self._logger.awarning("calls.answer_already_active")
            return ToolResult(output=json.dumps({"ok": False, "error": "already active"}))
        # Set up the PCM relay queue BEFORE returning the side-effect
        # so any caller frames arriving between this method's return
        # and the claim's FOREGROUND don't get dropped.
        self._caller_pcm_queue = asyncio.Queue()
        self._claim_active = True
        await self._logger.ainfo("calls.answer", from_name=self._pending_from)

        skill = self  # bind for closures

        async def on_mic_frame(pcm: bytes) -> None:
            ws = skill._caller_ws
            if ws is None:
                return
            try:
                await ws.send(pcm)
            except Exception:
                # Caller disappeared mid-send; the read-loop's finally
                # will trigger cleanup. Don't propagate — would crash
                # the MicRouter dispatch.
                if skill._logger is not None:
                    await skill._logger.adebug("calls.mic_send_failed")

        async def speaker_source() -> AsyncIterator[bytes]:
            assert skill._caller_pcm_queue is not None
            queue = skill._caller_pcm_queue
            try:
                while True:
                    frame = await queue.get()
                    yield frame
            except asyncio.CancelledError:
                raise

        async def on_claim_end(reason: ClaimEndReason) -> None:
            await skill._on_call_ended(reason)

        return ToolResult(
            output=json.dumps({"ok": True, "from": self._pending_from}),
            side_effect=InputClaim(
                on_mic_frame=on_mic_frame,
                speaker_source=speaker_source(),
                on_claim_end=on_claim_end,
            ),
        )

    async def _handle_reject(self) -> ToolResult:
        assert self._logger is not None
        if not self.is_busy:
            return ToolResult(output=json.dumps({"error": "no pending call", "ok": False}))
        await self._logger.ainfo("calls.rejected_by_user")
        await self._cleanup_caller_state()
        return ToolResult(output=json.dumps({"ok": True}))

    async def _handle_end_explicit(self) -> ToolResult:
        assert self._logger is not None
        if not self._claim_active:
            return ToolResult(output=json.dumps({"error": "no active call", "ok": False}))
        await self._logger.ainfo("calls.end_by_tool")
        # Just close the WS; the read-loop's finally + on_claim_end
        # cascade handles everything else.
        await self._cleanup_caller_state()
        return ToolResult(output=json.dumps({"ok": True}))

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _on_call_ended(self, reason: ClaimEndReason) -> None:
        """Fired by the InputClaim's on_claim_end. Cleans up caller
        state + narrates the end via inject_turn (priority depends on
        reason)."""
        assert self._logger is not None
        from_name = self._pending_from or "alguien"
        await self._logger.ainfo(
            "calls.ended",
            reason=reason.value,
            from_name=from_name,
        )
        # Close caller WS if still open. Caller-initiated close already
        # closed it from the other side; this is the grandpa-side close.
        await self._cleanup_caller_state()
        # Narrate the end. PREEMPTED skips narration (the inject that
        # took over is already speaking).
        if reason is ClaimEndReason.PREEMPTED:
            return
        if self._ctx is None:
            return
        if reason is ClaimEndReason.USER_PTT:
            prompt = self._end_user_ptt_prompt
        elif reason is ClaimEndReason.ERROR:
            prompt = self._end_error_prompt
        else:  # NATURAL — caller hung up
            prompt = self._end_natural_prompt.format(from_name=from_name)
        # NORMAL priority: end-of-call narration isn't safety-critical;
        # if grandpa happens to be talking already (rare), wait.
        await self._ctx.inject_turn(prompt, priority=InjectPriority.NORMAL)

    async def _on_caller_disconnected(self) -> None:
        """Caller WS read loop exited (caller closed their side). End
        the active claim via `ctx.cancel_active_claim` so the observer's
        `on_claim_end` fires with NATURAL → "Mario colgó" narration.

        `cancel_active_claim` (Stage 2.1) is the escape hatch for side-
        effect-dispatched claims that don't yield a `ClaimHandle` to
        the skill. No-op when the claim is already mid-end (race with
        a concurrent USER_PTT or PREEMPT)."""
        assert self._logger is not None
        assert self._ctx is not None
        await self._logger.ainfo("calls.caller_disconnected")
        # Drop the WS ref first so on_mic_frame doesn't try to send into
        # a closed socket while the cancel propagates.
        self._caller_ws = None
        # Drive the claim to NONE — observer's _end fires resume_provider
        # + on_claim_end(NATURAL) → narration via _on_call_ended.
        if self._claim_active:
            await self._ctx.cancel_active_claim(reason=ClaimEndReason.NATURAL)

    async def _cleanup_caller_state(self) -> None:
        """Reset everything to idle. Idempotent — safe to call from
        multiple lifecycle paths (rejected ring, caller disconnected,
        skill teardown)."""
        import contextlib

        ws = self._caller_ws
        self._caller_ws = None
        self._pending_from = None
        self._caller_pcm_queue = None
        self._claim_active = False
        if ws is not None:
            # Already-closed or transport error — fine, the caller is gone.
            with contextlib.suppress(Exception):
                await ws.close()
