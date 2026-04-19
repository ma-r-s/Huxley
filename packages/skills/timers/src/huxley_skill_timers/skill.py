"""Timers skill — one-shot user reminders via proactive speech.

The user sets a timer by voice ("recuérdame en 5 minutos que saque la
ropa"); the LLM dispatches `set_timer(seconds, message)`; the skill
schedules a background task that fires `ctx.inject_turn(message)` when
the timer expires. The framework preempts any playing content stream
and narrates the reminder in persona voice.

MVP scope (T1.4 Stage 1c.3 follow-on):

- In-memory only — timers do NOT survive a server restart. Persistence
  (via `SkillStorage`) is an obvious follow-up once Stage 3's
  `background_task` supervisor lands, since the same setup-time restore
  path both needs.
- Per-timer `asyncio.create_task` (not supervised). If the task crashes
  before firing, the reminder is lost silently. Stage 3 adopts these
  under `background_task` for crash-resilience.
- `set_timer` only — no `list_timers` or `cancel_timer` yet. Add when
  a user flow actually needs them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from huxley_sdk import SkillContext, SkillLogger, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_MIN_SECONDS = 1
_MAX_SECONDS = 3600  # 1 hour — anything longer suggests the user wants
# a different primitive (appointment / calendar), which this skill
# deliberately doesn't grow into.


class TimersSkill:
    """Proactive one-shot reminders via `inject_turn`.

    One tool: `set_timer(seconds, message)`. The skill keeps each
    scheduled task in `self._tasks` so `teardown()` can cancel them on
    shutdown without leaking work into the event loop.
    """

    def __init__(self) -> None:
        self._logger: SkillLogger | None = None
        self._inject_turn: Callable[[str], Awaitable[None]] | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._next_id: int = 1

    @property
    def name(self) -> str:
        return "timers"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="set_timer",
                description=(
                    "Programa un recordatorio que se anuncia después de X segundos. "
                    "El mensaje se lee al usuario cuando el temporizador vence; "
                    "puede interrumpir un libro que esté sonando. "
                    "Ejemplos: 'recuérdame en 5 minutos que saque la ropa' → "
                    "set_timer(seconds=300, message='sacar la ropa de la lavadora')."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "integer",
                            "minimum": _MIN_SECONDS,
                            "maximum": _MAX_SECONDS,
                            "description": (
                                "Segundos hasta que suene el recordatorio. "
                                f"Rango valido: {_MIN_SECONDS}-{_MAX_SECONDS}."
                            ),
                        },
                        "message": {
                            "type": "string",
                            "description": (
                                "Qué decirle al usuario cuando suene — una instrucción "
                                "para el modelo, no las palabras literales. "
                                "Ej: 'sacar la ropa de la lavadora'."
                            ),
                        },
                    },
                    "required": ["seconds", "message"],
                },
            )
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if self._logger is None or self._inject_turn is None:
            raise RuntimeError("TimersSkill: handle() called before setup()")
        if tool_name != "set_timer":
            await self._logger.awarning("timers.unknown_tool", tool=tool_name)
            return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

        seconds_raw = args.get("seconds")
        message = args.get("message", "")
        if not isinstance(seconds_raw, int) or not isinstance(message, str) or not message:
            await self._logger.awarning("timers.invalid_args", args=args)
            return ToolResult(
                output=json.dumps(
                    {"error": "set_timer requires integer `seconds` and non-empty `message`"}
                )
            )
        seconds = max(_MIN_SECONDS, min(_MAX_SECONDS, seconds_raw))

        timer_id = self._next_id
        self._next_id += 1
        task = asyncio.create_task(
            self._fire_after(timer_id, seconds, message),
            name=f"timer:{timer_id}",
        )
        self._tasks[timer_id] = task
        await self._logger.ainfo(
            "timers.scheduled",
            timer_id=timer_id,
            seconds=seconds,
            message=message,
        )
        return ToolResult(
            output=json.dumps(
                {
                    "timer_id": timer_id,
                    "seconds": seconds,
                    "ok": True,
                }
            )
        )

    async def _fire_after(self, timer_id: int, seconds: int, message: str) -> None:
        """Sleep then fire `inject_turn`. Remove from tracking on completion
        (natural OR cancelled), so teardown doesn't see a stale entry.

        Cancellation propagates through cleanly — we don't log it here,
        because any `await logger.ainfo` inside a cancel handler runs
        against a partially-cancelled task and can behave oddly on some
        event loops. Teardown tracks the cancellation count separately.
        """
        assert self._logger is not None
        assert self._inject_turn is not None
        try:
            await asyncio.sleep(seconds)
            await self._logger.ainfo("timers.fired", timer_id=timer_id, message=message)
            # Prompt shape matters: this gets sent to the LLM as a
            # conversation message. If it reads like a note ("Recordatorio:
            # X") the model minimally satisfies it; if it reads like an
            # instruction ("Avísale al usuario que...") the model narrates
            # naturally. Compare `AudioStream.on_complete_prompt` in the
            # audiobooks skill, which is imperative and works well.
            prompt = (
                "Ha sonado un temporizador que el usuario programó. "
                f"Avísale con tono amable y natural sobre: {message}. "
                "Empieza la frase como si se lo estuvieras recordando a "
                "un amigo (por ejemplo 'oye, recuerda que...' o 'ya es "
                "hora de...'). Usa una o dos frases cortas."
            )
            try:
                # The framework wraps this in a DIALOG-channel Activity;
                # preempts content streams, asks the LLM to narrate.
                await self._inject_turn(prompt)
            except Exception:
                # Don't let an inject_turn failure propagate out of an asyncio task
                # and kill the event loop's exception handler. Log and move on.
                await self._logger.aexception("timers.fire_failed", timer_id=timer_id)
        finally:
            # Scrub self from tracking regardless of why we exited — natural
            # completion, cancellation, or unexpected exception — so teardown
            # sees an accurate picture.
            self._tasks.pop(timer_id, None)

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._inject_turn = ctx.inject_turn
        await ctx.logger.ainfo("timers.setup_complete")

    async def teardown(self) -> None:
        assert self._logger is not None
        pending = list(self._tasks.values())
        for task in pending:
            task.cancel()
        for task in pending:
            # Teardown is defensive — suppress everything so one misbehaving
            # timer can't prevent the others (or the rest of shutdown) from
            # tearing down cleanly. The inject_turn failure path inside
            # `_fire_after` already logs via `aexception`.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Clear authoritatively. Tasks cancelled before their coroutine body
        # ever runs (e.g., created then cancelled in the same tick) never
        # execute their own `finally`, so the per-timer pop wouldn't fire.
        # We know the bookkeeping must be empty after teardown; assert that
        # by clearing directly.
        self._tasks.clear()
        await self._logger.ainfo("timers.teardown_complete", cancelled=len(pending))

    def prompt_context(self) -> str:
        """Give the LLM awareness of any active timers.

        Empty when no timers are pending — avoids polluting the system
        prompt on fresh sessions.
        """
        if not self._tasks:
            return ""
        count = len(self._tasks)
        # Singular / plural Spanish — the AbuelOS persona is the only user
        # today; a future multilingual persona can override prompt_context
        # via a different skill class or accept the mismatch.
        noun = "temporizador" if count == 1 else "temporizadores"
        return f"Tienes {count} {noun} activo{'s' if count != 1 else ''} esperando a sonar."
