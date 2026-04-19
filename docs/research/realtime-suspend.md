# OpenAI Realtime API — suspend/resume characterization

**Date**: 2026-04-19 · **Purpose**: inform the `VoiceProvider.suspend()/resume()` contract for T1.4 Stage 2 (`InputClaim` for calls). · **Spike**: [`spikes/realtime_suspend.py`](../../spikes/realtime_suspend.py) · **Model tested**: `gpt-4o-mini-realtime-preview` · **API spend**: &lt;$1.

## TL;DR for the provider contract

- **Pausing ≠ stopping the server.** If we just stop reading the WebSocket, the model **continues generating** and buffers hundreds of KB of audio deltas. `suspend()` **must** send `response.cancel` + `input_audio_buffer.clear` at entry, or we leak compute (money) and resume into stale audio.
- **Session survives a multi-minute pause** without any client-side keepalive. No pings sent, no pings needed — the WebSocket stays open and the session ID is preserved across an idle gap. 3 minutes observed clean; no reason to expect shorter calls to break.
- **Resume is trivial after a clean cancel.** A fresh `input_audio_buffer.append` → `commit` → `response.create` on the same WebSocket works normally. No reconnect, no session re-init. Transcript history from before the pause is still in the conversation context.
- **Network race on cancel**: OpenAI may have already streamed audio deltas over the wire before the cancel lands. `suspend()` must **set a drop flag** so the provider's receive loop discards any `response.audio.delta` (and associated transcript/part events) arriving after the cancel until `resume()` is called.

## The four unknowns, answered

### 1. How long can we idle before the session dies?

**Answer**: **longer than we care about for a call.** 3 minutes of complete idle silence (no client→server traffic, no events received) did not close the connection. Session ID `sess_DWGLuEn2EsyTxD7xNhztu` stayed valid. No keepalive from client, no ping from server. For calls lasting minutes, idle-timeout is not a concern. If calls grow to 30+ minutes in some future use case, re-run experiment 1 with a longer duration.

**Implication**: `suspend()` doesn't need a keepalive task. Just cancel the in-flight response and stop sending; resume with a fresh `input_audio_buffer.append` when the claim ends.

### 2. What happens to a mid-response audio stream if we pause?

**Answer**: **it keeps going on the server.** In experiment 2, we triggered a response, received the first `response.audio.delta`, then stopped reading the socket for 30 s. When we drained: **247 KB of assistant audio** had been buffered (the server generated the entire 15-word response while we weren't listening; deltas piled up on the socket). The response completed server-side with `response.done` during our silence.

**Implication**: the natural "just stop reading" approach is wrong — it's both a leak (we pay for audio we discard) and a correctness bug (if resume arrives before we drain, that stale audio plays). `suspend()` must send `response.cancel` + `input_audio_buffer.clear` immediately on entry. Experiment 4 confirms the cancel path is clean: `response.audio_transcript.done` + `response.content_part.done` + `response.output_item.done` + `response.done` arrive promptly (within ~100 ms), and afterward a new turn works normally.

A residual concern: a few audio deltas may already be in-flight over the network when `response.cancel` is processed. The provider's receive loop must track a "suspended" flag and drop `response.audio.delta` events while it's set. Same pattern as `TurnCoordinator.interrupt()`'s existing cancel-drop flag — reusable shape.

### 3. Session ID preservation across pause

**Answer**: **preserved.** In experiment 3, turn 1 completed, we paused 60 s, then fired turn 2 successfully on the same WebSocket. No `session.created` event during resume = no session rotation. Conversation context (instructions + prior turns) remained intact; the model responded to turn 2 consistent with turn 1's state.

**Implication**: no transcript-cursor gymnastics needed on resume. The session continues where it left off; AbuelOS context is preserved. This matters for calls: after a 10-minute call ends, grandpa can say "¿qué decíamos?" and the model has the pre-call conversation to reference.

### 4. Billing during idle / paused state

**Answer**: **inconclusive but likely safe.** During experiment 1's 3-minute idle, zero server→client events arrived (not even the `rate_limits.updated` that showed up post-turn in experiment 3). No evidence of ongoing token/audio billing. The Realtime API's documented pricing is per input/output audio minute and per token; an open-but-silent session shouldn't accrue cost under that model. **Confirm via an actual invoice** after the first week of Stage 2 dev — add a check to the cost-tracker burn-down.

## Provider contract the spike implies

```python
class VoiceProvider(Protocol):
    # ... existing methods ...

    async def suspend(self) -> None:
        """Pause processing of user audio and assistant responses.

        Contract:
        - Idempotent. `suspend()` on an already-suspended provider is a no-op.
        - Cancels any in-flight response (sends `response.cancel` +
          `input_audio_buffer.clear`). Pending `response.audio.delta` events
          arriving after the cancel are dropped by the receive loop.
        - Does NOT close the WebSocket. The session stays open; session ID
          and conversation context are preserved.
        - Subsequent calls to `send_user_audio` are ignored silently until
          `resume()` is called.
        - No keepalive required — OpenAI does not timeout idle connections
          within call-duration timeframes.

        Called when a skill's `InputClaim` latches the mic to a handler
        outside the LLM loop (voice memo, incoming call).
        """

    async def resume(self) -> None:
        """Unpause. Audio path restored; next `send_user_audio` reaches OpenAI.

        Contract:
        - Idempotent. `resume()` on a non-suspended provider is a no-op.
        - Does NOT re-establish a session — the same session continues.
        - Does NOT emit any keepalive or warm-up signal; the next user
          turn (PTT commit → response) naturally validates the session.
        """
```

Two methods, both idempotent, both transport-layer only. No coordinator-visible state change beyond the flag. `InputClaim`-dispatch code in the coordinator calls `provider.suspend()` **first**, then `mic_router.claim(handler)` — matches the critic's suspend-first-then-swap ordering invariant.

## What the spike did NOT verify

- **Longer idles** (10+ min). If a call stalls with both sides silent, does the session survive? Extrapolating from 3min-clean this should be fine, but if it matters for your use case, repeat experiment 1 with `timeout_s=600`.
- **Billing ground truth.** Confirm via the actual invoice that idle-but-connected sessions don't accrue cost. Add a weekly check during Stage 2 development.
- **Tool-call mid-flight at suspend time.** Experiment 2 used a pure audio response. If the model is mid-tool-call when we suspend (function-call event received but output not yet sent), behavior is uncharacterized. Probably the existing `cancel_current_response` handling covers it, but flag for a focused test during commit 2.
- **Rate limiting on frequent suspend/resume.** A degenerate skill that flaps suspend/resume 100 times/minute might hit rate limits. Not a real concern for calls (one suspend per call, minutes apart) but note it.

## Re-running the spike

```bash
cd /Users/mario/Projects/Personal/Code/Huxley
uv run python spikes/realtime_suspend.py      # all 4 experiments (~3 minutes wall clock)
uv run python spikes/realtime_suspend.py 2    # just experiment 2 (the critical one)
```

The script loads `HUXLEY_OPENAI_API_KEY` from `packages/core/.env`. Each experiment opens a fresh WebSocket so state doesn't leak between them.
