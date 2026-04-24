"""Per-sender debounce + coalesce buffer for inbound Telegram messages.

The framework's `inject_turn(dedup_key=...)` collapses pending duplicates
but silently drops same-key calls that arrive while one is already firing
(coordinator: `_current_injected_dedup_key`). For burst-y conversations
("hola", "papá", "¿estás?") that means the user hears the first message
and silently loses the rest -- a real safety gap for a blind user.

This buffer fixes that at the skill layer: each inbound message is
appended to a per-sender queue and a debounce timer is (re)started.
When the timer fires, the queue is drained and a single coalesced
inject is built ("X te envió 3 mensajes: ..."). Burst-y senders get
one announcement covering everything they typed; idle senders trigger
immediately at the next debounce window.

Straddle-race correctness: messages that arrive WHILE a flush task is
running for the same sender accumulate into a fresh burst. When the
in-flight flush completes, a new debounce timer starts; the next flush
covers exactly those late-arriving messages. The sender state stays
resident in `_senders` for the entire flush duration so `add()` can
append rather than spawning a duplicate burst.

The buffer is pure: no Pyrogram, no skill, no inject_turn coupling. It
takes a `flush` callback and uses asyncio's loop directly for timing.
That keeps it unit-testable with short debounce values and verifies
behavior independent of the full skill stack.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

# A sender that fires more than this many messages inside one debounce
# window has the oldest dropped. Prevents a stuck/runaway sender from
# blowing up memory between flushes.
_MAX_QUEUED_PER_SENDER = 50


@dataclass(slots=True)
class _SenderState:
    display_name: str
    messages: list[str] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None
    # True between "timer fired, flush task spawned" and "flush task's
    # finally block ran". While True, `add()` appends to `messages` but
    # does NOT start a new debounce timer -- the post-flush hook handles
    # scheduling the follow-up burst.
    flushing: bool = False


class InboxBuffer:
    """Per-sender debounced coalesce buffer.

    Usage:
        buf = InboxBuffer(debounce_seconds=2.5, on_flush=skill._flush_inbox)
        buf.add(user_id=123, display_name="hija", text="hola")
        buf.add(user_id=123, display_name="hija", text="¿estás?")
        # ...2.5s later, on_flush(123, "hija", ["hola", "¿estás?"]) fires once

    Independence: senders debounce independently -- a message from sender A
    does not delay sender B's flush.

    Display name: the latest `display_name` passed in for a sender wins
    (a contact resolved from "unknown" to "hija" mid-burst gets the
    proper name when the burst flushes).

    Thread-affinity: all `add()` and `flush_all()` calls must originate
    on the same event loop the buffer was first used on. Mixing loops
    would split timers across runtimes; tests that hit this would
    deadlock awaiting tasks from a different loop. Pyrogram message
    handlers run on the client's loop, which is the loop the skill
    constructs the buffer on -- the invariant holds in practice.
    """

    def __init__(
        self,
        *,
        debounce_seconds: float,
        on_flush: Callable[[int, str, list[str]], Coroutine[object, object, None]],
    ) -> None:
        if debounce_seconds <= 0:
            msg = f"debounce_seconds must be positive, got {debounce_seconds}"
            raise ValueError(msg)
        self._debounce_seconds = debounce_seconds
        self._on_flush = on_flush
        self._senders: dict[int, _SenderState] = {}
        # In-flight asyncio tasks spawned when a debounce timer fires.
        # Tracked so teardown can await them and so GC doesn't reap them.
        self._flush_tasks: set[asyncio.Task[None]] = set()
        # Set by `flush_all()` so subsequent `add()` calls during teardown
        # are silently dropped rather than scheduling fresh timers that
        # would fire on a possibly-disconnected transport.
        self._closed = False

    def add(self, user_id: int, display_name: str, text: str) -> None:
        """Append a message and (re)start the sender's debounce timer.

        Called from the inbound message handler (sync; pyrogram message
        callback wrappers can be sync or async -- we don't care). The flush
        runs as a background task spawned by the timer callback.

        Behavior:
        - First message from this sender: create state, start timer.
        - Subsequent message before timer fires: cancel timer, restart
          (debounce window resets).
        - Subsequent message while a flush is in flight: append to the
          state's queue; the post-flush hook will start a new timer.
        - After `flush_all()` was called: silently drop.
        """
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        state = self._senders.get(user_id)
        if state is None:
            state = _SenderState(display_name=display_name)
            self._senders[user_id] = state
        else:
            state.display_name = display_name
            if state.timer is not None:
                state.timer.cancel()
                state.timer = None

        state.messages.append(text)
        if len(state.messages) > _MAX_QUEUED_PER_SENDER:
            # Drop oldest -- newer messages are more relevant for the announcement.
            state.messages = state.messages[-_MAX_QUEUED_PER_SENDER:]

        if not state.flushing:
            state.timer = loop.call_later(self._debounce_seconds, self._on_timer_fired, user_id)

    def _on_timer_fired(self, user_id: int) -> None:
        """Timer callback (sync, runs in event loop). Snapshots the burst,
        clears the queue, marks the sender as flushing, and spawns the
        async flush. Sender state remains resident in `_senders` so
        late-arriving messages append into the same state.
        """
        state = self._senders.get(user_id)
        if state is None or state.flushing or not state.messages:
            return
        state.timer = None
        state.flushing = True
        burst = state.messages
        state.messages = []
        task = asyncio.create_task(
            self._do_flush(user_id, state.display_name, burst),
            name=f"inbox-flush-{user_id}",
        )
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)

    async def _do_flush(self, user_id: int, display: str, burst: list[str]) -> None:
        """Run on_flush, then schedule the next burst if more messages
        accumulated during the flush. Always clears the `flushing` flag
        in the finally block so a raising on_flush doesn't strand the
        sender state in flushing=True forever.
        """
        try:
            await self._on_flush(user_id, display, burst)
        finally:
            state = self._senders.get(user_id)
            if state is not None:
                state.flushing = False
                if state.messages:
                    # Late arrivals during the flush -- start a fresh
                    # debounce window. The finally branch runs after at
                    # least one await point, so a running loop exists.
                    loop = asyncio.get_running_loop()
                    state.timer = loop.call_later(
                        self._debounce_seconds, self._on_timer_fired, user_id
                    )
                else:
                    # Empty queue, no in-flight follow-up -- collect the
                    # state so the dict doesn't leak per sender.
                    del self._senders[user_id]

    async def flush_all(self) -> None:
        """Drain every pending sender immediately and await all flush tasks.

        Marks the buffer closed so concurrent `add()` calls from the
        Pyrogram handler during teardown become no-ops. Loops until
        `_senders` is empty AND `_flush_tasks` is empty -- a flush
        completing may schedule a follow-up burst (post-flush hook), so
        a single drain pass isn't sufficient.
        """
        self._closed = True
        while self._senders or self._flush_tasks:
            for user_id, state in list(self._senders.items()):
                if state.timer is not None:
                    state.timer.cancel()
                    state.timer = None
                if state.flushing or not state.messages:
                    continue
                state.flushing = True
                burst = state.messages
                state.messages = []
                task = asyncio.create_task(
                    self._do_flush(user_id, state.display_name, burst),
                    name=f"inbox-flush-{user_id}",
                )
                self._flush_tasks.add(task)
                task.add_done_callback(self._flush_tasks.discard)
            if self._flush_tasks:
                await asyncio.gather(*self._flush_tasks, return_exceptions=True)


def build_announcement(display_name: str, messages: list[str], *, preview_chars: int = 200) -> str:
    """Build the LLM-facing inject prompt for a coalesced burst.

    The prompt is an INSTRUCTION to the LLM, not literal speech. Without
    an explicit "léeselo al usuario" the model treats the inject as a
    notification ("acknowledged") and forgets to read the content aloud --
    confirmed bug, 2026-04-24 first smoke test. Pattern matches the
    call-ended inject in skill.py: state the fact, then instruct.

    Known contact: "{name}" appears as the sender ("de hija").
    Unknown contact: display starts with "un " ("un número desconocido")
    so the framing shifts to "de un número desconocido" + an extra
    instruction to flag the unknown origin to the user.

    Each message body is truncated to `preview_chars` with an ellipsis
    if cut.
    """
    if not messages:
        msg = "build_announcement called with empty messages"
        raise ValueError(msg)

    def _trim(text: str) -> str:
        text = text.strip()
        if len(text) <= preview_chars:
            return text
        return text[: preview_chars - 1].rstrip() + "…"

    is_named = not display_name.startswith("un ")
    sender_phrase = f"de {display_name}"
    unknown_hint = "" if is_named else " Recuerda mencionar que es de un número desconocido."

    if len(messages) == 1:
        body = _trim(messages[0])
        return (
            f"Llegó un mensaje de Telegram {sender_phrase}. El mensaje dice: '{body}'. "
            f"Léeselo al usuario tal cual y pregúntale si quiere responder.{unknown_hint}"
        )
    if len(messages) <= 3:
        quoted = [f"'{_trim(m)}'" for m in messages]
        joined = ", ".join(quoted[:-1]) + " y " + quoted[-1]
        return (
            f"Llegaron {len(messages)} mensajes nuevos de Telegram {sender_phrase}: "
            f"{joined}. Léeselos al usuario en orden, sin cambiar el contenido, "
            f"y pregúntale si quiere responder.{unknown_hint}"
        )
    last_two = messages[-2:]
    quoted = [f"'{_trim(m)}'" for m in last_two]
    return (
        f"Llegaron {len(messages)} mensajes nuevos de Telegram {sender_phrase}; "
        f"los más recientes dicen {quoted[0]} y {quoted[1]}. Cuéntale al usuario "
        f"que llegaron varios mensajes y léele los dos más recientes; ofrécele "
        f"leerle los anteriores si quiere.{unknown_hint}"
    )


_BACKFILL_MAX_BODIES_PER_SENDER = 5


def build_backfill_announcement(
    per_sender: dict[str, list[str]], *, preview_chars: int = 200
) -> str:
    """Build the single inject for the proactive-inbox backfill on connect.

    Same instruction-prompt pattern as `build_announcement`: state the
    fact, then tell the LLM what to communicate -- including the actual
    message bodies, so the LLM can read them when the user says yes.
    Without bodies, the user hears "tienes mensajes, ¿quieres que te
    los lea?" but the LLM has nothing to read on follow-up. Confirmed
    failure mode in 2026-04-24 first smoke test.

    Per-sender bodies are capped at `_BACKFILL_MAX_BODIES_PER_SENDER`
    (5) -- the persona-config `backfill_max` (50 by default) limits
    total messages, but a single overflowing sender shouldn't blow up
    the prompt with 50 "hola"s.
    """
    if not per_sender:
        msg = "build_backfill_announcement called with empty per_sender"
        raise ValueError(msg)

    def _trim(text: str) -> str:
        text = text.strip()
        if len(text) <= preview_chars:
            return text
        return text[: preview_chars - 1].rstrip() + "…"

    def _quote_bodies(messages: list[str]) -> tuple[str, bool]:
        cap = _BACKFILL_MAX_BODIES_PER_SENDER
        truncated = len(messages) > cap
        sample = messages[-cap:] if truncated else messages
        quoted = [f"'{_trim(m)}'" for m in sample]
        if len(quoted) == 1:
            return quoted[0], truncated
        return ", ".join(quoted[:-1]) + " y " + quoted[-1], truncated

    sender_chunks: list[str] = []
    for name, messages in per_sender.items():
        if not messages:
            continue
        bodies, truncated = _quote_bodies(messages)
        word = "mensaje" if len(messages) == 1 else "mensajes"
        if truncated:
            sender_chunks.append(
                f"de {name} llegaron {len(messages)} {word} (los más recientes dicen {bodies})"
            )
        elif len(messages) == 1:
            sender_chunks.append(f"de {name}: {bodies}")
        else:
            sender_chunks.append(f"de {name} ({len(messages)} mensajes): {bodies}")

    if not sender_chunks:
        msg = "build_backfill_announcement called with empty messages for all senders"
        raise ValueError(msg)

    joined = "; ".join(sender_chunks)
    return (
        f"Mientras estabas desconectado, llegaron mensajes nuevos de Telegram: "
        f"{joined}. Cuéntale al usuario que llegaron mensajes mientras estaba "
        f"desconectado, dile de quién, y léeselos en orden -- sin cambiar el "
        f"contenido. Pregúntale si quiere responder a alguno."
    )
