"""Timers skill — one-shot user reminders via proactive speech.

The user sets a timer by voice ("recuérdame en 5 minutos que saque la
ropa"); the LLM dispatches `set_timer(seconds, message)`; the skill
schedules a supervised background task (`ctx.background_task`) that
fires `ctx.inject_turn(message)` when the timer expires. The
framework preempts any playing content stream and narrates the
reminder in persona voice.

Scope (T1.4 Stage 3b — persistence shipped):

- **Supervised tasks**: each timer runs under
  `ctx.background_task(name="timer:N", ...)` with
  `restart_on_crash=False` (one-shot — restarting a fired-too-early
  reminder would re-sleep for the original duration). Crashes log
  via the supervisor; teardown cancels via the returned handle.
- **Persistent across restart**: each timer writes a JSON entry to
  skill-namespaced `SkillStorage` keyed `timer:<id>` with the
  wall-clock fire time. `setup()` enumerates pending entries via
  `ctx.storage.list_settings("timer:")` and reschedules or drops
  them based on age (see `_restore_pending` for the policy). A
  `fired_at` field, written just before `inject_turn`, acts as a
  dedup guard so a process crash *between* narration and entry
  delete doesn't cause a second reminder on restore.
- `set_timer` only — no `list_timers` or `cancel_timer` yet. Add
  when a user flow actually needs them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    BackgroundTaskHandle,
    InjectPriority,
    InjectTurn,
    SkillContext,
    SkillLogger,
    SkillStorage,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_MIN_SECONDS = 1
_MAX_SECONDS = 3600  # 1 hour — anything longer suggests the user wants
# a different primitive (appointment / calendar), which this skill
# deliberately doesn't grow into.

# Storage key prefix for persisted pending timers. `setup()` enumerates
# this prefix on boot to rebuild `_handles` across a server restart.
_STORAGE_PREFIX = "timer:"

# Entry schema version — bumped if the persisted shape changes so a
# future migration can recognize v1 entries and upgrade them.
_ENTRY_VERSION = 1

# Default "how stale" threshold on restore: an entry older than this is
# dropped rather than fired late. 1 h matches `_MAX_SECONDS` — a timer
# that was supposed to fire 1 h ago, when the max duration is 1 h, is
# almost certainly a different intent the user has moved past. Personas
# that extend `_MAX_SECONDS` (not currently configurable) or that want
# a different latency tolerance override via `stale_restore_threshold_s`
# in their persona.yaml.
_DEFAULT_STALE_RESTORE_THRESHOLD = timedelta(hours=1)

# Default fire prompt — Spanish, AbuelOS-toned. A persona can override
# via the `fire_prompt` config key in `persona.yaml`'s `timers:` block.
# `{message}` is substituted at fire time via `str.format`; additional
# placeholders fail with a clear error.
#
# This default is opinionated (assumes Spanish, warm-friend register)
# because AbuelOS is the 99%-case consumer today. Non-Spanish or
# non-warm personas MUST override; they get a broken narration
# otherwise (LLM sees Spanish instruction in an English-system-prompt
# context and either translates weirdly or overfits the phrasing).
_DEFAULT_FIRE_PROMPT = (
    "Ha sonado un temporizador que el usuario programó. "
    "Avísale con tono amable y natural sobre: {message}. "
    "Empieza la frase como si se lo estuvieras recordando a un "
    "amigo (por ejemplo 'oye, recuerda que...' o 'ya es hora "
    "de...'). Usa una o dos frases cortas."
)


def _utcnow() -> datetime:
    """Indirection point so tests can monkeypatch the clock."""
    return datetime.now(UTC)


class TimersSkill:
    """Proactive one-shot reminders via `inject_turn`.

    One tool: `set_timer(seconds, message)`. The skill keeps each
    scheduled task's `BackgroundTaskHandle` in `self._handles` so
    `teardown()` can cancel pending timers (and a future
    `cancel_timer` tool can target by id) without leaking work into
    the event loop.
    """

    def __init__(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._logger: SkillLogger | None = None
        self._inject_turn: InjectTurn | None = None
        self._background_task: Callable[..., BackgroundTaskHandle] | None = None
        self._storage: SkillStorage | None = None
        # Per-timer handles — held so teardown can pre-shutdown cancel
        # specific timers (and a future `cancel_timer` tool can target
        # by id without going through the supervisor's name lookup).
        self._handles: dict[int, BackgroundTaskHandle] = {}
        self._next_id: int = 1
        # Fire-prompt template — persona-configurable so non-AbuelOS
        # personas don't inherit the Spanish/warm-friend default.
        # Populated in `setup()` from `ctx.config.get("fire_prompt")`.
        self._fire_prompt: str = _DEFAULT_FIRE_PROMPT
        # How stale a pending entry can be on restore before we drop it
        # instead of firing immediately. Default 1 h (matches the skill's
        # own `_MAX_SECONDS`); personas that extend the max duration
        # MUST extend this in lockstep, or restored entries longer than
        # the threshold will drop. Populated in `setup()` from
        # `ctx.config.get("stale_restore_threshold_s")`.
        self._stale_threshold: timedelta = _DEFAULT_STALE_RESTORE_THRESHOLD
        # Injection point for the `asyncio.sleep` used in `_fire_after` —
        # tests pass a near-instant stub to avoid burning wall-clock time
        # in the suite. Default is `asyncio.sleep` so production is
        # unchanged. Supervisor applies the same pattern (c2fa2b1).
        self._sleep: Callable[[float], Awaitable[None]] = sleep or asyncio.sleep

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
        if self._logger is None or self._inject_turn is None or self._background_task is None:
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
        fire_at = _utcnow() + timedelta(seconds=seconds)
        # Persist BEFORE scheduling the task so a crash between the
        # schedule and the write can't leave a live task with no
        # backing entry (would survive nowhere after restart).
        await self._write_entry(timer_id, fire_at=fire_at, message=message, fired_at=None)
        await self._schedule_fire(timer_id, seconds, message)

        await self._logger.ainfo(
            "timers.scheduled",
            timer_id=timer_id,
            seconds=seconds,
            message=message,
            fire_at=fire_at.isoformat(),
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

    async def _schedule_fire(self, timer_id: int, seconds: int, message: str) -> None:
        """Spawn the supervised sleep-then-fire task.

        `restart_on_crash=False` because a one-shot that crashed mid-
        sleep can't be meaningfully restarted (we'd either re-sleep
        the original duration — too late — or fire immediately with
        stale intent). Restore on next process boot via `setup()`
        handles the server-restart case instead.
        """
        assert self._background_task is not None
        handle = self._background_task(
            f"timer:{timer_id}",
            lambda: self._fire_after(timer_id, seconds, message),
            restart_on_crash=False,
        )
        self._handles[timer_id] = handle

    async def _fire_after(self, timer_id: int, seconds: int, message: str) -> None:
        """Sleep then fire `inject_turn`.

        Lifecycle transitions mark the storage entry so a restore on
        the next boot can tell "pending, reschedule me" from "fired,
        don't re-deliver":

        - During `asyncio.sleep(...)`: entry has `fired_at = None`.
          If the process dies, `setup()` reschedules on next boot.
        - After sleep completes, BEFORE `inject_turn`: we write
          `fired_at` and flip `_fired`. From this moment the entry is
          considered "committed to fire" — any crash between here and
          delete is interpreted as "the reminder probably played" by
          the restore path, which deletes + skips (better than
          double-firing a medication reminder).
        - Natural completion OR any exception after commit: the
          finally deletes the entry. The `_handles` pop runs in all
          paths so teardown's bookkeeping stays accurate.
        """
        assert self._logger is not None
        assert self._inject_turn is not None
        fired = False
        try:
            await self._sleep(seconds)
            # Past the sleep — commit to fire by stamping fired_at.
            # If the process dies between here and the delete in
            # `finally`, restore sees the entry as "fired" and skips.
            now = _utcnow()
            await self._write_entry(timer_id, fire_at=now, message=message, fired_at=now)
            fired = True
            await self._logger.ainfo("timers.fired", timer_id=timer_id, message=message)
            # Prompt shape matters: this gets sent to the LLM as a
            # conversation message. If it reads like a note ("Recordatorio:
            # X") the model minimally satisfies it; if it reads like an
            # instruction ("Avísale al usuario que...") the model narrates
            # naturally. Compare `AudioStream.on_complete_prompt` in the
            # audiobooks skill, which is imperative and works well.
            prompt = self._fire_prompt.format(message=message)
            try:
                # `BLOCK_BEHIND_COMMS` (Stage 5, 2026-04-23): preempts
                # content streams (book pauses, timer narrates, book
                # resumes on patience-covered return) but queues behind
                # active calls (doesn't interrupt grandpa's phone
                # conversation for a cooking timer). Right severity tier
                # for a user-set reminder whose value is immediate
                # narration against content but respectful of live calls.
                await self._inject_turn(prompt, priority=InjectPriority.BLOCK_BEHIND_COMMS)
            except Exception:
                # Don't let an inject_turn failure propagate out of an asyncio task
                # and kill the event loop's exception handler. Log and move on.
                await self._logger.aexception("timers.fire_failed", timer_id=timer_id)
        finally:
            # Scrub from in-memory tracking regardless of why we exited —
            # natural completion, cancellation, or unexpected exception.
            self._handles.pop(timer_id, None)
            # Only remove the persisted entry if we actually committed to
            # firing. Cancellation during the sleep (e.g., teardown at
            # server shutdown) must preserve the entry so it can be
            # restored on next boot.
            if fired:
                await self._delete_entry(timer_id)

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._inject_turn = ctx.inject_turn
        self._background_task = ctx.background_task
        self._storage = ctx.storage
        # Persona override for the fire prompt, if provided. Falls back
        # to the Spanish/AbuelOS-toned default defined above.
        configured = ctx.config.get("fire_prompt")
        if isinstance(configured, str) and configured:
            if "{message}" not in configured:
                await ctx.logger.awarning(
                    "timers.fire_prompt_missing_placeholder",
                    hint="persona 'fire_prompt' must contain '{message}'",
                )
            else:
                self._fire_prompt = configured
        # Persona override for the stale-restore threshold. An int or
        # float in seconds; non-numeric values get a warning + default.
        threshold_raw = ctx.config.get("stale_restore_threshold_s")
        if isinstance(threshold_raw, int | float) and threshold_raw > 0:
            self._stale_threshold = timedelta(seconds=float(threshold_raw))
        elif threshold_raw is not None:
            await ctx.logger.awarning(
                "timers.stale_threshold_invalid",
                hint="persona 'stale_restore_threshold_s' must be a positive number of seconds",
                value=threshold_raw,
            )
        restored, dropped = await self._restore_pending()
        await ctx.logger.ainfo(
            "timers.setup_complete",
            fire_prompt_source="persona_override" if configured else "default",
            stale_threshold_s=self._stale_threshold.total_seconds(),
            restored=restored,
            dropped=dropped,
        )

    async def _restore_pending(self) -> tuple[int, int]:
        """Rebuild `_handles` from persisted entries on boot.

        Policy (derived from post-Stage-3 critic):

        - `fired_at` set → the timer was mid-fire when the process
          died. Re-delivery risks a double dose for medication
          reminders (worse than a miss), so delete + skip regardless
          of how long ago `fired_at` is.
        - `now - fire_at > self._stale_threshold` → original intent
          is stale; delete + log.
        - `fire_at` in the past but within threshold → reschedule at
          `_MIN_SECONDS` and emit `timers.restored_overdue` so
          operators can tell "fired late because crash recovery" from
          "user just set a 1-second timer."
        - Future `fire_at` → reschedule with `fire_at - now` remaining.

        Malformed entries are skipped with a warning (no delete — a
        schema-migration opportunity, not a data loss event).

        Returns `(restored_count, dropped_count)` for the setup log.
        """
        assert self._logger is not None
        assert self._storage is not None
        entries = await self._storage.list_settings(_STORAGE_PREFIX)
        restored = 0
        dropped = 0
        ids_seen: list[int] = []
        now = _utcnow()
        for key, value in entries:
            timer_id = self._parse_timer_id(key)
            if timer_id is None:
                await self._logger.awarning("timers.restore_key_malformed", key=key)
                continue
            try:
                entry = json.loads(value)
                fire_at = datetime.fromisoformat(entry["fire_at"])
                message = entry["message"]
                fired_at_raw = entry.get("fired_at")
                fired_at = datetime.fromisoformat(fired_at_raw) if fired_at_raw else None
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                await self._logger.awarning(
                    "timers.restore_entry_malformed", key=key, value=value[:80]
                )
                continue

            ids_seen.append(timer_id)
            if fired_at is not None:
                await self._logger.ainfo(
                    "timers.restore_skipped_fired",
                    timer_id=timer_id,
                    fired_at=fired_at.isoformat(),
                )
                await self._delete_entry(timer_id)
                dropped += 1
                continue
            age = now - fire_at
            if age > self._stale_threshold:
                await self._logger.awarning(
                    "timers.restore_skipped_stale",
                    timer_id=timer_id,
                    fire_at=fire_at.isoformat(),
                    age_s=age.total_seconds(),
                )
                await self._delete_entry(timer_id)
                dropped += 1
                continue
            raw_remaining = int((fire_at - now).total_seconds())
            remaining = max(_MIN_SECONDS, raw_remaining)
            await self._schedule_fire(timer_id, remaining, message)
            if raw_remaining < 0:
                # Stale but recoverable — fired late because of a crash
                # or restart. Distinct event so logs can tell "1s timer
                # the user just set" from "1h overdue recovery."
                await self._logger.ainfo(
                    "timers.restored_overdue",
                    timer_id=timer_id,
                    overdue_s=-raw_remaining,
                    message=message,
                )
            else:
                await self._logger.ainfo(
                    "timers.restored",
                    timer_id=timer_id,
                    remaining_s=remaining,
                    message=message,
                )
            restored += 1

        # Prime `_next_id` so new `set_timer` calls after boot don't
        # overwrite a restored entry's key.
        if ids_seen:
            self._next_id = max(ids_seen) + 1
        return restored, dropped

    @staticmethod
    def _parse_timer_id(key: str) -> int | None:
        suffix = key.removeprefix(_STORAGE_PREFIX)
        if not suffix.isdigit():
            return None
        try:
            return int(suffix)
        except ValueError:
            return None

    async def _write_entry(
        self,
        timer_id: int,
        *,
        fire_at: datetime,
        message: str,
        fired_at: datetime | None,
    ) -> None:
        assert self._storage is not None
        payload = {
            "v": _ENTRY_VERSION,
            "fire_at": fire_at.isoformat(),
            "message": message,
            "fired_at": fired_at.isoformat() if fired_at else None,
        }
        await self._storage.set_setting(f"{_STORAGE_PREFIX}{timer_id}", json.dumps(payload))

    async def _delete_entry(self, timer_id: int) -> None:
        assert self._storage is not None
        await self._storage.delete_setting(f"{_STORAGE_PREFIX}{timer_id}")

    async def teardown(self) -> None:
        assert self._logger is not None
        # Cancel any pending timers before the framework's TaskSupervisor
        # stops everything globally. Per-handle cancel here gives the
        # `_fire_after` finally a chance to clear `_handles` cleanly;
        # otherwise the supervisor's stop() would do the same cancel
        # but without the per-handle bookkeeping side effect.
        #
        # Storage entries are deliberately NOT deleted — that's what
        # makes persistence work. `_fire_after`'s `if fired` guard
        # ensures a mid-sleep cancel (e.g., this teardown) leaves the
        # persisted entry alone for the next boot to restore.
        pending = list(self._handles.values())
        for handle in pending:
            handle.cancel()
        # Clear authoritatively. Tasks cancelled before their coroutine
        # body ever runs (e.g., created then cancelled in the same tick)
        # never execute their own `finally`, so the per-timer pop
        # wouldn't fire — see the matching pattern in
        # huxley.background.TaskSupervisor.stop.
        self._handles.clear()
        await self._logger.ainfo("timers.teardown_complete", cancelled=len(pending))

    def prompt_context(self) -> str:
        """Give the LLM awareness of any active timers.

        Empty when no timers are pending — avoids polluting the system
        prompt on fresh sessions.
        """
        if not self._handles:
            return ""
        count = len(self._handles)
        # Singular / plural Spanish — the AbuelOS persona is the only user
        # today; a future multilingual persona can override prompt_context
        # via a different skill class or accept the mismatch.
        noun = "temporizador" if count == 1 else "temporizadores"
        return f"Tienes {count} {noun} activo{'s' if count != 1 else ''} esperando a sonar."
