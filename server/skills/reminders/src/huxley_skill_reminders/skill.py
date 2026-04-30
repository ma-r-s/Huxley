"""Reminders skill — scheduled, persistent, with retry escalation.

User says "recuérdame mañana a las 8 que tome la pastilla del corazón"; the LLM
calls `add_reminder(message=..., when_iso=..., kind="medication", recurrence_rule="FREQ=DAILY")`;
the skill stores it in `SkillStorage`, runs a single supervised scheduler
background task, and at fire time calls `ctx.inject_turn(prompt,
priority=BLOCK_BEHIND_COMMS)` so the framework preempts content (audiobook
pauses) but yields to live calls. The LLM narrates in persona voice.

Distinct from the timers skill:

- **Persistent across restart**, with kind-aware boot reconciliation: an
  entry whose `scheduled_for` is far enough in the past is marked `missed`
  rather than fired (medication safety: don't double-dose at 11am if the
  8am dose was missed and the next dose is at 8pm).
- **Acknowledgment loop** for medications: re-fires up to 3 times at
  5 / 10 / 30 minute intervals until the LLM calls `acknowledge_reminder`.
- **Recurrence**: `daily` / `weekly` re-schedules the next instance on
  ack OR on missed (recurrence outlasts a single missed dose).
- **Catch-up via prompt_context**: any reminder that was marked `missed`
  surfaces to the LLM in the next session so it can mention "te perdiste
  la pastilla de las 8" without grandpa asking.

Architecture invariants (mirrors timers' Stage-3b persistence pattern):

- A reminder lives in `SkillStorage` keyed `reminder:<id>` as a JSON blob.
  Each row encodes the full state machine — no in-memory-only fields that
  would be lost on restart.
- The single `scheduler` background task is `restart_on_crash=True` —
  one crash mid-sleep doesn't lose pending reminders, the next loop re-
  reads from storage. Different from timers (which uses one task per
  timer with `restart_on_crash=False`) because reminders' state machine
  carries the truth — restarting the loop re-derives next-due safely.
- Retry timer state is encoded in `next_fire_at`; the scheduler treats
  retries as just another row in the next-due query. No separate task
  per retry.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

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

# State machine values. `surfaced` is a terminal-after-narration state —
# once the LLM has been told about a missed reminder via prompt_context,
# it transitions out of `missed` so the same miss isn't surfaced twice.
_STATE_PENDING = "pending"
_STATE_FIRED = "fired"  # narrated at least once, awaiting ack (medication only)
_STATE_ACKED = "acked"
_STATE_MISSED = "missed"  # too-late on boot, or retry budget exhausted
_STATE_CANCELLED = "cancelled"

# A `missed` row keeps surfacing in `prompt_context` until the LLM calls
# `acknowledge_reminder` or `cancel_reminder` to clear it. The persona
# prompt instructs the LLM to dismiss missed reminders explicitly after
# mentioning them. If the LLM forgets, the row surfaces again next turn —
# annoying but bounded, and self-limiting because the LLM tends to
# follow through on the second mention. We previously had a `surfaced`
# state intended to auto-dismiss after one prompt-context tick; that
# couldn't be implemented cleanly because `prompt_context` is sync (no
# storage write) and the resulting dead state caused a fan-out bug
# during boot reconcile (review F4, 2026-04-29). Removed.

_VALID_KINDS = ("medication", "appointment", "generic")

# Legacy recurrence enum values from v1 storage entries. Translated to
# RRULE strings on read in `_Entry.from_json`. Not accepted as input
# from new tool calls — the tool description teaches the LLM to emit
# RRULE strings directly.
_LEGACY_RECURRENCE_TO_RRULE = {
    "daily": "FREQ=DAILY",
    "weekly": "FREQ=WEEKLY",
}

# How late we'll deliver a reminder whose fire time was during a server-down
# window. The medication value is deliberately tight — narrating "es hora de
# la pastilla" 2 hours late risks a double-dose if the next dose is in 3
# hours. Appointments tolerate hours of slop. Generic reminders fall in
# between. Personas can override per-kind via `late_window_<kind>_s`.
_DEFAULT_LATE_WINDOWS: dict[str, timedelta] = {
    "medication": timedelta(minutes=15),
    "appointment": timedelta(hours=2),
    "generic": timedelta(hours=1),
}

# Medication-only retry escalation. Gaps BETWEEN successive fires:
# 5 minutes after the first fire, 10 minutes after the second, 30
# minutes after the third. After 3 unacked fires, mark `missed`.
# Generic and appointment reminders never re-fire.
_MEDICATION_RETRY_INTERVALS = (
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=30),
)
_MEDICATION_MAX_RETRIES = len(_MEDICATION_RETRY_INTERVALS)

_STORAGE_PREFIX = "reminder:"
_STORAGE_META_NEXT_ID = "_meta:next_id"
# Tracks whether the persona's seed list has already been imported into
# storage on a previous boot. Prevents re-importing the same seeds after
# a user manually deletes them.
_STORAGE_META_SEED_DONE = "_meta:seed_imported"

# Schema versions for `_Entry`:
#   v1 — `recurrence: 'daily' | 'weekly' | None` enum
#   v2 — `recurrence_rule: <RRULE string> | None` (RFC 5545)
# `_Entry.from_json` reads any version and upgrades v1 → v2 on the fly;
# the next `_save_entry` writes v2.
_ENTRY_VERSION = 2

# When the scheduler has nothing to do (no pending reminders), it polls
# this often so a freshly-added reminder gets picked up promptly without
# the loop spinning. Tighter than the timers skill's per-task model
# because reminders share one loop.
_SCHEDULER_IDLE_POLL_SECONDS = 30.0

# Per-language fire-prompt template. The LLM narrates from this; persona
# can override globally (`fire_prompt`) or per-language
# (`i18n.<lang>.fire_prompt`). `{message}` and `{kind}` are substituted
# at fire time.
_DEFAULT_FIRE_PROMPTS: dict[str, str] = {
    "es": (
        "Suena un recordatorio que el usuario programó. "
        "Avísale con tono cálido y natural sobre: {message}. "
        "Empieza la frase como si se lo recordaras a un amigo "
        "(por ejemplo 'oye, ya es hora de...' o 'recuerda que...'). "
        "Una o dos frases cortas. Si dice que ya lo hizo, llama a "
        "`acknowledge_reminder` con el id."
    ),
    "en": (
        "A reminder the user scheduled has come due. Let them know "
        "warmly and naturally about: {message}. Start the phrase as if "
        "reminding a friend (for example 'hey, it's time to...' or "
        "'remember that...'). One or two short sentences. If they say "
        "they already did it, call `acknowledge_reminder` with the id."
    ),
    "fr": (
        "Un rappel programmé par l'utilisateur arrive à échéance. "
        "Préviens-le chaleureusement et naturellement au sujet de : "
        "{message}. Commence comme si tu rappelais à un ami "
        "(par exemple 'tiens, c'est l'heure de...' ou 'rappelle-toi "
        "que...'). Une ou deux phrases courtes. S'il dit qu'il l'a "
        "déjà fait, appelle `acknowledge_reminder` avec l'id."
    ),
}

# Per-language tool descriptions. Keys map onto tool names + parameter
# slots; the LLM dispatches based on this copy so phrasing must match
# the active session language.
_TOOL_DESC: dict[str, dict[str, str]] = {
    "es": {
        "add_reminder": (
            "Programa un recordatorio que se anuncia a una hora futura. "
            "Para horas relativas ('en 30 minutos', 'en 5 minutos') usa el "
            "skill de temporizadores en su lugar — este es para horas "
            "específicas (mañana a las 8, hoy a las 6 de la tarde). "
            "El sistema te dirá la zona horaria y la hora actual; calcula "
            "`when_iso` en formato ISO con offset (p.ej. '2026-04-30T08:00:00-05:00'). "
            'Si es un medicamento usa `kind="medication"` para que insista '
            "hasta que el usuario confirme. Si se repite, pasa "
            "`recurrence_rule` con una regla RFC 5545 (RRULE)."
        ),
        "message_param": (
            "Qué recordarle al usuario — instrucción para el modelo, "
            "no las palabras literales. Ej: 'tomar la pastilla del corazón'."
        ),
        "when_iso_param": (
            "Hora exacta del recordatorio en formato ISO 8601 con offset, "
            "p.ej. '2026-04-30T08:00:00-05:00'. La calculas a partir de la "
            "hora actual y la zona horaria del usuario que están en el contexto."
        ),
        "kind_param": (
            "Tipo de recordatorio. 'medication' = medicamento (insiste hasta "
            "ack); 'appointment' = cita; 'generic' = otro. Por defecto 'generic'."
        ),
        "recurrence_rule_param": (
            "Regla RFC 5545 (RRULE) que describe cómo se repite. "
            "Ejemplos comunes: "
            "'FREQ=DAILY' (todos los días a la misma hora) · "
            "'FREQ=WEEKLY' (cada semana el mismo día) · "
            "'FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR' (lunes a viernes) · "
            "'FREQ=WEEKLY;BYDAY=MO,WE,FR' (lunes, miércoles y viernes) · "
            "'FREQ=WEEKLY;INTERVAL=2' (cada dos semanas) · "
            "'FREQ=MONTHLY;BYMONTHDAY=15' (el 15 de cada mes) · "
            "'FREQ=DAILY;COUNT=7' (durante 7 días). "
            "Omite si es solo una vez."
        ),
        "list_reminders": ("Lista los recordatorios pendientes y los recientemente perdidos."),
        "cancel_reminder": "Cancela un recordatorio por su id (ya no sonará).",
        "snooze_reminder": (
            "Aplaza un recordatorio activo X minutos. Útil cuando suena "
            "y el usuario dice 'dame cinco minutos'."
        ),
        "snooze_minutes_param": ("Cuántos minutos posponerlo. Rango 1-120."),
        "id_param": "Id del recordatorio (lo devuelve `add_reminder` y `list_reminders`).",
        "acknowledge_reminder": (
            "Marca un recordatorio como atendido cuando el usuario confirma "
            "que lo hizo (p.ej. 'ya me la tomé', 'listo'). Detiene los "
            "reintentos para los medicamentos."
        ),
    },
    "en": {
        "add_reminder": (
            "Schedule a reminder announced at a future time. For relative "
            "times ('in 30 minutes', 'in 5 minutes') use the timers skill "
            "instead — this is for specific times (tomorrow at 8, today at "
            "6pm). The system tells you the timezone and current time in "
            "context; compute `when_iso` as ISO 8601 with offset "
            "(e.g. '2026-04-30T08:00:00-05:00'). If it's a medication use "
            '`kind="medication"` so it insists until the user confirms. '
            "If it repeats, pass `recurrence_rule` with an RFC 5545 RRULE."
        ),
        "message_param": (
            "What to remind the user about — instruction for the model, "
            "not the literal words. Example: 'take the heart pill'."
        ),
        "when_iso_param": (
            "Exact reminder time in ISO 8601 with offset, e.g. "
            "'2026-04-30T08:00:00-05:00'. Compute from the user's "
            "current time and timezone shown in context."
        ),
        "kind_param": (
            "Reminder kind. 'medication' = medication (insists until ack); "
            "'appointment' = appointment; 'generic' = other. Default 'generic'."
        ),
        "recurrence_rule_param": (
            "RFC 5545 RRULE string describing how the reminder repeats. "
            "Common examples: "
            "'FREQ=DAILY' (every day at the same time) · "
            "'FREQ=WEEKLY' (every week on the same weekday) · "
            "'FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR' (weekdays only) · "
            "'FREQ=WEEKLY;BYDAY=MO,WE,FR' (Monday, Wednesday, Friday) · "
            "'FREQ=WEEKLY;INTERVAL=2' (every two weeks) · "
            "'FREQ=MONTHLY;BYMONTHDAY=15' (15th of every month) · "
            "'FREQ=DAILY;COUNT=7' (for 7 days). "
            "Omit for a one-shot reminder."
        ),
        "list_reminders": "List pending and recently missed reminders.",
        "cancel_reminder": "Cancel a reminder by id (it will not fire).",
        "snooze_reminder": (
            "Postpone an active reminder by X minutes. Useful when it "
            "fires and the user says 'give me five more minutes'."
        ),
        "snooze_minutes_param": "How many minutes to delay. Range 1-120.",
        "id_param": "Reminder id (returned by `add_reminder` and `list_reminders`).",
        "acknowledge_reminder": (
            "Mark a reminder as handled when the user confirms they did "
            "it (e.g. 'I took it', 'done'). Stops retries for medications."
        ),
    },
    "fr": {
        "add_reminder": (
            "Programme un rappel annoncé à une heure future. Pour des "
            "heures relatives ('dans 30 minutes', 'dans 5 minutes') "
            "utilise plutôt la compétence minuteries — celle-ci est pour "
            "des heures précises (demain à 8h, aujourd'hui à 18h). "
            "Le système te donne le fuseau horaire et l'heure courante "
            "dans le contexte ; calcule `when_iso` en ISO 8601 avec "
            "offset (par ex. '2026-04-30T08:00:00-05:00'). Si c'est un "
            'médicament utilise `kind="medication"` pour qu\'il insiste '
            "jusqu'à confirmation. S'il se répète, passe une règle "
            "`recurrence_rule` au format RFC 5545 (RRULE)."
        ),
        "message_param": (
            "De quoi rappeler — instruction pour le modèle, pas les mots "
            "littéraux. Par ex. : 'prendre la pilule pour le cœur'."
        ),
        "when_iso_param": (
            "Heure exacte du rappel en ISO 8601 avec offset, par ex. "
            "'2026-04-30T08:00:00-05:00'. Calcule depuis l'heure actuelle "
            "et le fuseau horaire de l'utilisateur dans le contexte."
        ),
        "kind_param": (
            "Type de rappel. 'medication' = médicament (insiste jusqu'à "
            "ack) ; 'appointment' = rendez-vous ; 'generic' = autre. "
            "Par défaut 'generic'."
        ),
        "recurrence_rule_param": (
            "Règle RFC 5545 (RRULE) décrivant la récurrence. "
            "Exemples courants : "
            "'FREQ=DAILY' (chaque jour à la même heure) · "
            "'FREQ=WEEKLY' (chaque semaine le même jour) · "
            "'FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR' (jours ouvrés) · "
            "'FREQ=WEEKLY;BYDAY=MO,WE,FR' (lundi, mercredi, vendredi) · "
            "'FREQ=WEEKLY;INTERVAL=2' (toutes les deux semaines) · "
            "'FREQ=MONTHLY;BYMONTHDAY=15' (le 15 du mois) · "
            "'FREQ=DAILY;COUNT=7' (pendant 7 jours). "
            "Omet pour un rappel ponctuel."
        ),
        "list_reminders": "Liste les rappels en attente et récemment manqués.",
        "cancel_reminder": "Annule un rappel par son id (il ne sonnera pas).",
        "snooze_reminder": (
            "Reporte un rappel actif de X minutes. Utile quand il sonne "
            "et que l'utilisateur dit 'donne-moi cinq minutes'."
        ),
        "snooze_minutes_param": "Nombre de minutes à reporter. Plage 1-120.",
        "id_param": "Id du rappel (retourné par `add_reminder` et `list_reminders`).",
        "acknowledge_reminder": (
            "Marque un rappel comme pris en charge quand l'utilisateur "
            "confirme qu'il l'a fait (par ex. 'c'est fait', 'ok'). "
            "Arrête les relances pour les médicaments."
        ),
    },
}


def _lang_bucket(language: str) -> str:
    code = (language or "en").lower()
    for key in ("es", "en", "fr"):
        if code.startswith(key):
            return key
    return "en"


def _utcnow() -> datetime:
    """Indirection point so tests can monkeypatch the clock."""
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string and normalize to UTC.

    Naive datetimes are rejected — a reminder whose timezone we don't
    know is a reminder we can't fire safely. The LLM is prompted with
    the persona's timezone in `prompt_context`, so it should always
    produce an offset-bearing string.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"datetime missing timezone: {value!r}")
    return parsed.astimezone(UTC)


def _next_recurrence(
    series_start: datetime,
    after: datetime,
    recurrence_rule: str,
    tz: ZoneInfo,
) -> datetime | None:
    """Compute the next instance after `after` in a recurring series.

    Uses RFC 5545 RRULE semantics via `dateutil.rrule.rrulestr`. The
    rule is evaluated with a tz-aware `dtstart=series_start` in the
    persona's timezone, then queried for "first occurrence after
    `after`". Two timestamps because:

    - `series_start` anchors the recurrence — it's the FIRST
      occurrence of the series, never changes across the chain.
      Required so `COUNT` / `UNTIL` semantics work: a
      `FREQ=DAILY;COUNT=3` series with series_start=May 1 produces
      exactly May 1, May 2, May 3 — independent of which row is
      currently asking. Re-anchoring on each successor would reset
      the count and the series would never terminate.
    - `after` is the exact instant whose successor we want
      (typically the current row's `scheduled_for`).

    DST: handled natively by `dateutil.rrule` when `series_start` is
    tz-aware. A daily 8 AM rule in `America/New_York` produces 8 AM
    EST in winter and 8 AM EDT in summer — the wall-clock hour is
    preserved, the UTC instant shifts. Naive `+timedelta(days=1)` UTC
    arithmetic would produce 9 AM EDT after spring-forward. Wrong.

    Returns `None` when the rule has no further occurrences after
    `after` (COUNT exhausted, UNTIL past). Callers treat None as
    "the recurring series is complete; close out the row, don't
    schedule a successor."
    """
    dtstart = series_start.astimezone(tz)
    rrule = rrulestr(recurrence_rule, dtstart=dtstart)
    nxt = rrule.after(after.astimezone(tz), inc=False)
    return nxt.astimezone(UTC) if nxt is not None else None


def _validate_rrule(recurrence_rule: str, tz: ZoneInfo) -> str | None:
    """Return None if the rule is parseable, else a short error reason.

    The LLM occasionally emits invalid RRULE strings ("FREQ=DAILY;EVERY=1",
    raw "daily", etc.). We catch parsing errors at `add_reminder` time
    so the user gets an immediate "I couldn't parse that" rather than
    a scheduler crash hours later. Validation requires a tz-aware
    dtstart — we use `now()` in the persona tz which is throwaway.
    """
    try:
        rrulestr(recurrence_rule, dtstart=datetime.now(tz))
    except (ValueError, TypeError) as exc:
        return str(exc)
    return None


@dataclass
class _Entry:
    """In-memory shape of a persisted reminder.

    Stored as JSON under `reminder:<id>`. `_to_json` / `_from_json`
    handle (de)serialization; bumping `_ENTRY_VERSION` forces the
    restore path to recognize old shapes and either upgrade or skip.
    """

    id: int
    message: str
    kind: Literal["medication", "appointment", "generic"]
    # UTC; THIS row's target time (anchor for retry math, never changes
    # within a row).
    scheduled_for: datetime
    # UTC; what the scheduler actually waits on. Equals `scheduled_for`
    # for the first fire; advances to `now + retry_interval` for
    # medication retries; advances on snooze.
    next_fire_at: datetime
    # RFC 5545 RRULE string ("FREQ=DAILY", "FREQ=WEEKLY;BYDAY=MO,WE,FR",
    # etc.) or None for one-shot. v1 entries persisted with `recurrence`
    # ('daily' / 'weekly' enum) are upgraded on read by `from_json`.
    recurrence_rule: str | None
    state: str  # _STATE_*
    # UTC; the FIRST occurrence of the recurring series (= `scheduled_for`
    # for the very first row, inherited unchanged by every successor in
    # the chain). Used as `dtstart` when evaluating the RRULE so COUNT
    # / UNTIL semantics work across the chain. None for one-shot rows
    # or v1 rows that didn't track this; readers fall back to
    # `scheduled_for` in that case.
    series_start: datetime | None = None
    fired_count: int = 0
    last_fired_at: datetime | None = None
    acked_at: datetime | None = None
    cancelled_at: datetime | None = None
    missed_at: datetime | None = None

    @property
    def effective_series_start(self) -> datetime:
        """`series_start` if present, else fall back to `scheduled_for`.

        Old (v1) rows didn't track series_start; treating
        `scheduled_for` as the dtstart on those rows is the correct
        upgrade — for an indefinite recurrence (no COUNT/UNTIL) the
        result is identical, and v1 didn't support COUNT/UNTIL so no
        information is lost.
        """
        return self.series_start or self.scheduled_for

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": _ENTRY_VERSION,
                "id": self.id,
                "message": self.message,
                "kind": self.kind,
                "scheduled_for": self.scheduled_for.isoformat(),
                "next_fire_at": self.next_fire_at.isoformat(),
                "recurrence_rule": self.recurrence_rule,
                "series_start": self.series_start.isoformat() if self.series_start else None,
                "state": self.state,
                "fired_count": self.fired_count,
                "last_fired_at": self.last_fired_at.isoformat() if self.last_fired_at else None,
                "acked_at": self.acked_at.isoformat() if self.acked_at else None,
                "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
                "missed_at": self.missed_at.isoformat() if self.missed_at else None,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> _Entry:
        payload = json.loads(raw)
        kind = payload["kind"]
        if kind not in _VALID_KINDS:
            raise ValueError(f"invalid kind: {kind!r}")
        # v1 → v2 migration: legacy `recurrence` enum → RRULE string.
        # `version` field is informational; we read both shapes and
        # take whichever exists. The migrated value is written back
        # in v2 shape on the next `_save_entry`.
        recurrence_rule = payload.get("recurrence_rule")
        if recurrence_rule is None:
            legacy = payload.get("recurrence")
            if legacy is not None:
                if legacy in _LEGACY_RECURRENCE_TO_RRULE:
                    recurrence_rule = _LEGACY_RECURRENCE_TO_RRULE[legacy]
                else:
                    raise ValueError(f"unknown legacy recurrence: {legacy!r}")
        return cls(
            id=int(payload["id"]),
            message=str(payload["message"]),
            kind=kind,
            scheduled_for=_parse_iso(payload["scheduled_for"]),
            next_fire_at=_parse_iso(payload["next_fire_at"]),
            recurrence_rule=recurrence_rule,
            series_start=(
                _parse_iso(payload["series_start"]) if payload.get("series_start") else None
            ),
            state=str(payload["state"]),
            fired_count=int(payload.get("fired_count", 0)),
            last_fired_at=(
                _parse_iso(payload["last_fired_at"]) if payload.get("last_fired_at") else None
            ),
            acked_at=_parse_iso(payload["acked_at"]) if payload.get("acked_at") else None,
            cancelled_at=(
                _parse_iso(payload["cancelled_at"]) if payload.get("cancelled_at") else None
            ),
            missed_at=_parse_iso(payload["missed_at"]) if payload.get("missed_at") else None,
        )


class RemindersSkill:
    """Persistent reminders with kind-aware retry, recurrence, and catch-up."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._logger: SkillLogger | None = None
        self._inject_turn: InjectTurn | None = None
        self._background_task: Callable[..., BackgroundTaskHandle] | None = None
        self._storage: SkillStorage | None = None
        self._scheduler_handle: BackgroundTaskHandle | None = None
        # Wakes the scheduler when an entry is added / cancelled / snoozed
        # so it doesn't have to wait out its current sleep before noticing.
        self._wakeup: asyncio.Event = asyncio.Event()
        self._language: str = "en"
        self._timezone_label: str = "UTC"  # surfaced to LLM via prompt_context
        # Resolved tz used for RRULE evaluation. `_next_recurrence`
        # needs a tz-aware dtstart so DST transitions don't drift the
        # local fire time. Defaults to UTC; `setup()` overrides from
        # `ctx.config["timezone"]` (a TZ Database name like
        # "America/Bogota").
        self._tz: ZoneInfo = ZoneInfo("UTC")
        self._fire_prompt: str = _DEFAULT_FIRE_PROMPTS["en"]
        # Persona-overrideable; resolved in setup() and reconfigure().
        self._late_windows: dict[str, timedelta] = dict(_DEFAULT_LATE_WINDOWS)
        # Sleep injection so tests can run instantly. Production uses
        # `asyncio.sleep`. The scheduler uses a wait-on-event-or-sleep
        # pattern via `_sleep_or_wake`, so this is only ever called when
        # the loop wants to wait for a future fire time.
        self._sleep: Callable[[float], Awaitable[None]] = sleep or asyncio.sleep
        # Bounds the scheduler's single sleep so a clock change or a
        # forgotten wakeup doesn't strand the loop.
        self._max_sleep_seconds: float = 60.0
        # Snapshot of `state='missed'` rows. Refreshed after every save
        # and on storage reads so `prompt_context` (sync, can't await)
        # has fresh data without a round-trip.
        self._missed_cache_value: list[_Entry] = []
        # Serializes `_allocate_id`'s read-then-write against itself
        # across concurrent callers. Without this, a tool-handler
        # `add_reminder` and a scheduler-driven
        # `_schedule_next_recurrence` interleaving on a real (I/O-
        # awaiting) `SkillStorage` can both read the same `next_id`
        # and produce colliding rows. Tests' in-memory storage doesn't
        # yield on get/set so the race never appeared in CI; production
        # SQLite definitely does.
        self._id_lock: asyncio.Lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "reminders"

    @property
    def tools(self) -> list[ToolDefinition]:
        bucket = _lang_bucket(self._language)
        td = _TOOL_DESC.get(bucket, _TOOL_DESC["en"])
        return [
            ToolDefinition(
                name="add_reminder",
                description=td["add_reminder"],
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": td["message_param"]},
                        "when_iso": {"type": "string", "description": td["when_iso_param"]},
                        "kind": {
                            "type": "string",
                            "enum": list(_VALID_KINDS),
                            "description": td["kind_param"],
                        },
                        "recurrence_rule": {
                            "type": "string",
                            "description": td["recurrence_rule_param"],
                        },
                    },
                    "required": ["message", "when_iso"],
                },
            ),
            ToolDefinition(
                name="list_reminders",
                description=td["list_reminders"],
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="cancel_reminder",
                description=td["cancel_reminder"],
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": td["id_param"]},
                    },
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="snooze_reminder",
                description=td["snooze_reminder"],
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": td["id_param"]},
                        "minutes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                            "description": td["snooze_minutes_param"],
                        },
                    },
                    "required": ["id", "minutes"],
                },
            ),
            ToolDefinition(
                name="acknowledge_reminder",
                description=td["acknowledge_reminder"],
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": td["id_param"]},
                    },
                    "required": ["id"],
                },
            ),
        ]

    # ------------------------------------------------------------------ setup

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._inject_turn = ctx.inject_turn
        self._background_task = ctx.background_task
        self._storage = ctx.storage
        self._language = ctx.language or "en"
        self._timezone_label = self._resolve_timezone_label(ctx)
        self._tz = await self._resolve_tz(ctx)
        self._fire_prompt = await self._resolve_fire_prompt(ctx)
        self._late_windows = self._resolve_late_windows(ctx)
        await self._reconcile_on_boot()
        await self._maybe_seed(ctx)
        self._scheduler_handle = self._background_task(
            "scheduler",
            self._scheduler_loop,
            restart_on_crash=True,
        )
        await ctx.logger.ainfo(
            "reminders.setup_complete",
            language=self._language,
            timezone=self._timezone_label,
            tz=str(self._tz),
            late_windows={k: v.total_seconds() for k, v in self._late_windows.items()},
        )

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Refresh language-dependent state on a session re-handshake.

        Picks up the new ctx.language and re-resolves the fire prompt /
        timezone label / tool descriptions so a language flip mid-
        deployment doesn't require restarting the skill.
        """
        self._language = ctx.language or self._language
        self._timezone_label = self._resolve_timezone_label(ctx)
        self._tz = await self._resolve_tz(ctx)
        self._fire_prompt = await self._resolve_fire_prompt(ctx)
        self._late_windows = self._resolve_late_windows(ctx)
        await ctx.logger.ainfo("reminders.reconfigure", language=self._language)

    async def _resolve_tz(self, ctx: SkillContext) -> ZoneInfo:
        """Parse the persona's timezone string into a ZoneInfo.

        Falls back to UTC with a warning on an unknown TZDB name. Used
        to evaluate RRULEs and compute prompt_context's wall-clock
        time. The display label (`_timezone_label`) and the actual tz
        are always the same string in production but resolved
        independently so a typo or platform-missing zone doesn't
        block startup.
        """
        label = self._timezone_label
        try:
            return ZoneInfo(label)
        except ZoneInfoNotFoundError:
            await ctx.logger.awarning(
                "reminders.tz_invalid",
                label=label,
                hint="persona 'timezone' must be a TZ Database name (e.g. 'America/Bogota')",
            )
            return ZoneInfo("UTC")

    @staticmethod
    def _resolve_timezone_label(ctx: SkillContext) -> str:
        # The persona's timezone label is shown to the LLM in
        # prompt_context so it can compute correct offsets. Pulled from
        # skill config first, then framework-level if exposed; falls
        # back to UTC. Persona authors set this explicitly when they
        # want the LLM to see "America/Bogota" instead of "UTC".
        configured = ctx.config.get("timezone")
        if isinstance(configured, str) and configured:
            return configured
        return "UTC"

    async def _resolve_fire_prompt(self, ctx: SkillContext) -> str:
        configured = ctx.config.get("fire_prompt")
        if isinstance(configured, str) and configured:
            if "{message}" not in configured:
                await ctx.logger.awarning(
                    "reminders.fire_prompt_missing_placeholder",
                    hint="persona 'fire_prompt' must contain '{message}'",
                )
            else:
                return configured
        bucket = _lang_bucket(self._language)
        return _DEFAULT_FIRE_PROMPTS.get(bucket, _DEFAULT_FIRE_PROMPTS["en"])

    def _resolve_late_windows(self, ctx: SkillContext) -> dict[str, timedelta]:
        """Pick up persona overrides like `late_window_medication_s: 600`.

        Default per-kind values come from `_DEFAULT_LATE_WINDOWS`. An
        invalid override (non-positive, non-numeric) silently keeps the
        default — surfaced via a log warning so the persona author
        notices but the skill still boots.
        """
        result = dict(_DEFAULT_LATE_WINDOWS)
        for kind in _VALID_KINDS:
            key = f"late_window_{kind}_s"
            raw = ctx.config.get(key)
            if raw is None:
                continue
            if isinstance(raw, int | float) and raw > 0:
                result[kind] = timedelta(seconds=float(raw))
            # Async-context-free path: log later in setup_complete; we
            # don't want this helper to be awaitable for one warning.
        return result

    # --------------------------------------------------------- boot reconcile

    async def _reconcile_on_boot(self) -> None:
        """Process every persisted reminder once on startup.

        Cases (in order checked, per row):

        - Already terminal (`acked` / `cancelled` / `surfaced`): leave alone.
          They're useful for `list_reminders` until the user prunes them.
        - `missed` (from a previous boot, never surfaced): if recurrence
          is set and the next-instance is in the future, spawn it.
          Either way, leave the original `missed` row for prompt_context
          to surface in the next session.
        - `pending` with `next_fire_at` in the future: leave alone.
          Scheduler picks it up on its next tick.
        - `pending` with `next_fire_at` past, within `late_window[kind]`:
          leave as `pending`; scheduler will fire it on its next tick.
          Don't fire here — that would race with the scheduler.
        - `pending` with `next_fire_at` past, beyond `late_window[kind]`:
          mark `missed`. If recurrence is set, schedule the next
          instance as a fresh row.
        - `fired` (mid-retry when the process died): re-derive the
          retry timer from `fired_count`. If the next retry is past
          `late_window`, treat as missed (medication safety: silently
          dropping a 4h-late retry is better than firing it).
        """
        assert self._logger is not None
        assert self._storage is not None
        now = _utcnow()
        entries = await self._load_all_entries()
        # Persist next-id BEFORE processing entries so that any
        # `_schedule_next_recurrence` call inside the loop allocates an
        # id past every existing row instead of colliding with an
        # original that hasn't been saved yet.
        max_id = max((e.id for e in entries), default=0)
        await self._storage.set_setting(
            f"{_STORAGE_PREFIX}{_STORAGE_META_NEXT_ID}", str(max_id + 1)
        )
        for entry in entries:
            if entry.state in (_STATE_ACKED, _STATE_CANCELLED):
                continue
            if entry.state == _STATE_MISSED:
                # Already missed; surfacing is on the next prompt_context.
                # Recurrence rolls forward anyway so today's miss doesn't
                # cancel tomorrow's reminder.
                if entry.recurrence_rule:
                    await self._schedule_next_recurrence(entry)
                continue
            if entry.state == _STATE_FIRED:
                await self._reconcile_fired(entry, now)
                continue
            if entry.state == _STATE_PENDING:
                await self._reconcile_pending(entry, now)
                continue
            await self._logger.awarning(
                "reminders.unknown_state_on_boot", id=entry.id, state=entry.state
            )

    async def _reconcile_pending(self, entry: _Entry, now: datetime) -> None:
        """Pending row: leave alone if future or recently due, mark missed if old."""
        assert self._logger is not None
        if entry.next_fire_at >= now:
            return  # future — scheduler will handle it
        late_by = now - entry.next_fire_at
        window = self._late_windows.get(entry.kind, _DEFAULT_LATE_WINDOWS[entry.kind])
        if late_by <= window:
            # Within tolerance — leave pending; the scheduler picks it up
            # immediately (it'll see next_fire_at < now and fire on the
            # next tick).
            await self._logger.ainfo(
                "reminders.boot_within_window",
                id=entry.id,
                kind=entry.kind,
                late_s=late_by.total_seconds(),
            )
            return
        # Beyond the safe-late window. For medication this prevents a
        # double-dose by firing a 4h-late reminder shortly before the
        # next dose; for appointments / generic it just avoids announcing
        # something the user has already moved past.
        await self._mark_missed(entry, reason="boot_outside_window", now=now)

    async def _reconcile_fired(self, entry: _Entry, now: datetime) -> None:
        """Fired but not acked when we crashed. Resume retry or mark missed."""
        assert self._logger is not None
        if entry.kind != "medication":
            # Non-medication kinds don't retry; if a fire was in flight
            # we've already narrated it once, treat as terminal.
            await self._mark_acked_or_missed_for_recurrence(entry, now=now)
            return
        if entry.fired_count >= _MEDICATION_MAX_RETRIES:
            await self._mark_missed(entry, reason="boot_retries_exhausted", now=now)
            return
        # Still have retries left. Resume by setting next_fire_at to
        # now + the appropriate interval (clamped so we don't fire
        # instantly on boot). If that target is itself outside the
        # late_window, give up — better than narrating a 5h-old reminder.
        interval = _MEDICATION_RETRY_INTERVALS[entry.fired_count]
        next_at = (entry.last_fired_at or now) + interval
        if next_at < now:
            # Retry was due during the outage. If we're past the late
            # window, mark missed; otherwise reset to fire shortly.
            late_by = now - next_at
            window = self._late_windows.get(entry.kind, _DEFAULT_LATE_WINDOWS[entry.kind])
            if late_by > window:
                await self._mark_missed(entry, reason="boot_retry_outside_window", now=now)
                return
            next_at = now
        entry.next_fire_at = next_at
        entry.state = _STATE_PENDING  # back to the scheduler's queue
        await self._save_entry(entry)
        await self._logger.ainfo(
            "reminders.boot_resumed_retry",
            id=entry.id,
            fired_count=entry.fired_count,
            next_fire_at=next_at.isoformat(),
        )

    async def _mark_acked_or_missed_for_recurrence(self, entry: _Entry, *, now: datetime) -> None:
        """For non-medication fires we crashed mid-narration: record terminal,
        then handle recurrence. We treat "we already fired once" as
        delivered (better than re-narrating an appointment notice 4h late).
        """
        entry.state = _STATE_ACKED  # close out the row
        entry.acked_at = now
        await self._save_entry(entry)
        if entry.recurrence_rule:
            await self._schedule_next_recurrence(entry)

    async def _mark_missed(self, entry: _Entry, *, reason: str, now: datetime) -> None:
        """Transition a row to `missed` and (if recurring) schedule the next."""
        assert self._logger is not None
        entry.state = _STATE_MISSED
        entry.missed_at = now
        await self._save_entry(entry)
        await self._logger.ainfo(
            "reminders.missed",
            id=entry.id,
            kind=entry.kind,
            reason=reason,
            scheduled_for=entry.scheduled_for.isoformat(),
            fired_count=entry.fired_count,
        )
        if entry.recurrence_rule:
            await self._schedule_next_recurrence(entry)

    async def _schedule_next_recurrence(self, entry: _Entry) -> None:
        """Create a fresh `pending` row at the next recurrence boundary.

        **Idempotent**: if a successor already exists for the computed
        `next_when`, returns without creating a duplicate. This guard
        is load-bearing — boot reconciliation calls
        `_schedule_next_recurrence` for every `_STATE_MISSED` recurring
        row on every restart. Without the guard, N restarts on a daily
        medication produces N duplicate rows for tomorrow's dose. The
        check matches on `(kind, recurrence, message,
        scheduled_for ≈ next_when)` rather than just id so a
        successor created via a different path (ack handler vs.
        boot reconcile) is also detected.
        """
        assert self._logger is not None
        assert self._storage is not None
        if not entry.recurrence_rule:
            return
        next_when = _next_recurrence(
            entry.effective_series_start,
            entry.scheduled_for,
            entry.recurrence_rule,
            self._tz,
        )
        if next_when is None:
            # RRULE has no further occurrences (e.g. COUNT exhausted,
            # UNTIL past). The original row is the last instance —
            # don't schedule a successor.
            await self._logger.ainfo(
                "reminders.recurrence_complete",
                id=entry.id,
                rule=entry.recurrence_rule,
            )
            return
        # Idempotency check — see docstring. Costs one storage scan per
        # call; called O(reminders * restarts), so still cheap.
        existing = await self._load_all_entries()
        for other in existing:
            if (
                other.id != entry.id
                and other.recurrence_rule == entry.recurrence_rule
                and other.message == entry.message
                and other.kind == entry.kind
                and other.state in (_STATE_PENDING, _STATE_FIRED)
                and abs((other.scheduled_for - next_when).total_seconds()) < 1
            ):
                await self._logger.adebug(
                    "reminders.recurrence_successor_exists",
                    original_id=entry.id,
                    successor_id=other.id,
                    next_when=next_when.isoformat(),
                )
                return
        new_id = await self._allocate_id()
        new_entry = _Entry(
            id=new_id,
            message=entry.message,
            kind=entry.kind,
            scheduled_for=next_when,
            next_fire_at=next_when,
            recurrence_rule=entry.recurrence_rule,
            # Successors carry the original series anchor unchanged so
            # COUNT/UNTIL semantics work across the chain.
            series_start=entry.effective_series_start,
            state=_STATE_PENDING,
        )
        await self._save_entry(new_entry)
        self._wakeup.set()

    # ------------------------------------------------------------------ seed

    async def _maybe_seed(self, ctx: SkillContext) -> None:
        """Import persona's `skills.reminders.seed` list on first ever boot.

        Idempotent: a meta key records that seeding ran, so manually
        deleting all reminders later doesn't cause them to come back.
        """
        assert self._storage is not None
        assert self._logger is not None
        seeded_marker = await self._storage.get_setting(
            f"{_STORAGE_PREFIX}{_STORAGE_META_SEED_DONE}"
        )
        if seeded_marker == "1":
            return
        seed_raw = ctx.config.get("seed")
        if not isinstance(seed_raw, list) or not seed_raw:
            await self._storage.set_setting(f"{_STORAGE_PREFIX}{_STORAGE_META_SEED_DONE}", "1")
            return
        seeded = 0
        for raw in seed_raw:
            if not isinstance(raw, dict):
                await self._logger.awarning("reminders.seed_skipped_non_dict", raw=raw)
                continue
            try:
                message = str(raw["message"])
                when_iso = str(raw["when_iso"])
                when = _parse_iso(when_iso)
                kind = raw.get("kind", "generic")
                if kind not in _VALID_KINDS:
                    raise ValueError(f"invalid kind: {kind!r}")
                # Seed accepts EITHER `recurrence_rule` (preferred,
                # RRULE) OR a legacy `recurrence` enum ('daily',
                # 'weekly') — translated on import. Mixed seeds across
                # personas are tolerated.
                recurrence_rule = raw.get("recurrence_rule")
                legacy_recurrence = raw.get("recurrence")
                if recurrence_rule is None and legacy_recurrence is not None:
                    if legacy_recurrence not in _LEGACY_RECURRENCE_TO_RRULE:
                        raise ValueError(
                            f"invalid legacy recurrence in seed: {legacy_recurrence!r}"
                        )
                    recurrence_rule = _LEGACY_RECURRENCE_TO_RRULE[legacy_recurrence]
                if recurrence_rule is not None:
                    err = _validate_rrule(recurrence_rule, self._tz)
                    if err is not None:
                        raise ValueError(f"invalid recurrence_rule {recurrence_rule!r}: {err}")
            except (KeyError, ValueError, TypeError) as exc:
                await self._logger.awarning(
                    "reminders.seed_skipped_invalid", raw=raw, error=str(exc)
                )
                continue
            new_id = await self._allocate_id()
            entry = _Entry(
                id=new_id,
                message=message,
                kind=kind,
                scheduled_for=when,
                next_fire_at=when,
                recurrence_rule=recurrence_rule,
                # First row in a recurring series; series_start =
                # scheduled_for. None for one-shot rows preserves
                # storage size on the common case.
                series_start=when if recurrence_rule else None,
                state=_STATE_PENDING,
            )
            await self._save_entry(entry)
            seeded += 1
        await self._storage.set_setting(f"{_STORAGE_PREFIX}{_STORAGE_META_SEED_DONE}", "1")
        await self._logger.ainfo("reminders.seeded", count=seeded)

    # ------------------------------------------------------------- scheduler

    async def _scheduler_loop(self) -> None:
        """Single supervised loop that fires due reminders.

        Works against `SkillStorage` rather than an in-memory queue so
        a crash + restart doesn't lose state. On each iteration:

        1. Pick the soonest non-terminal `pending` row.
        2. Sleep until its `next_fire_at` (or the wakeup event, whichever
           comes first — a new add_reminder / snooze fires the event so
           we don't have to wait out a long sleep).
        3. Re-read the row (it may have been cancelled / acked / snoozed
           during the sleep; we MUST NOT fire on stale state).
        4. Fire `inject_turn`. For medication, transition to `fired` +
           schedule the next retry. For others, mark `acked` (they only
           fire once) and roll recurrence if any.
        """
        assert self._logger is not None
        while True:
            entry = await self._next_due_entry()
            if entry is None:
                # Nothing pending. Wait for an external wakeup or poll.
                await self._sleep_or_wake(_SCHEDULER_IDLE_POLL_SECONDS)
                continue
            now = _utcnow()
            wait_s = max(0.0, (entry.next_fire_at - now).total_seconds())
            if wait_s > 0:
                # Bound the sleep so a clock change or missed wakeup
                # doesn't strand the loop. `_sleep_or_wake` returns
                # early if the wakeup event fires.
                await self._sleep_or_wake(min(wait_s, self._max_sleep_seconds))
                continue
            # Re-read in case state changed during sleep (cancel / ack /
            # snooze). Stale fires are the most insidious bug class for
            # this loop — be paranoid here.
            fresh = await self._load_entry(entry.id)
            if fresh is None or fresh.state != _STATE_PENDING:
                continue
            if fresh.next_fire_at > _utcnow():
                # Snoozed forward during our sleep window. Re-loop.
                continue
            await self._fire(fresh)

    async def _sleep_or_wake(self, seconds: float) -> None:
        """Sleep up to `seconds`, but return early if the wakeup event fires.

        The wakeup event is set by `add_reminder`, `cancel_reminder`,
        and `snooze_reminder` so the scheduler picks up changes without
        waiting out its current sleep. After waking we clear the event
        so the next sleep doesn't return immediately.
        """
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            self._wakeup.clear()

    async def _fire(self, entry: _Entry) -> None:
        """Commit state, then narrate.

        **Order matters for medication safety.** If we narrated first
        and the process died before saving the post-fire state, the
        next boot would see `state=pending` with `next_fire_at` past
        and within the late window — and re-narrate. Double-dose.
        Mirrors the timers skill's commit-then-narrate pattern
        (`fired_at` written before `inject_turn`).

        **Trade-off**: a transient `inject_turn` failure (Realtime API
        blip) burns a retry budget slot for medication without
        narrating to the user. A sustained outage spanning all three
        retries marks the row `missed` without grandpa hearing
        anything. We accept that — silent miss is safer than double
        dose. Operators diagnose via the `reminders.fire_failed` log
        line plus the `reminders.fired` line that records each
        attempt's `fired_count`.

        Recurrence is also scheduled BEFORE narration: a crash between
        terminal-save and recurrence-schedule would otherwise lose
        tomorrow's reminder. `_schedule_next_recurrence` is itself
        idempotent so re-running it on boot reconcile is safe.
        """
        assert self._logger is not None
        assert self._inject_turn is not None
        now = _utcnow()
        entry.fired_count += 1
        entry.last_fired_at = now

        advance_recurrence = False
        if entry.kind == "medication":
            if entry.fired_count >= _MEDICATION_MAX_RETRIES:
                entry.state = _STATE_MISSED
                entry.missed_at = now
                advance_recurrence = bool(entry.recurrence_rule)
            else:
                # 1-based: index 0 is the wait BEFORE retry #2 (after
                # the first fire). Pairs with first-fire-then-wait-5min.
                interval = _MEDICATION_RETRY_INTERVALS[entry.fired_count - 1]
                entry.next_fire_at = now + interval
                entry.state = _STATE_FIRED
        else:
            entry.state = _STATE_ACKED
            entry.acked_at = now
            advance_recurrence = bool(entry.recurrence_rule)

        await self._save_entry(entry)
        if advance_recurrence:
            await self._schedule_next_recurrence(entry)

        if entry.state == _STATE_MISSED:
            # Budget exhausted — log the miss with the same shape as
            # other miss paths so operators can grep `reminders.missed`
            # uniformly. We only inline the log here; transitioning was
            # already done above as part of commit-before-inject.
            await self._logger.ainfo(
                "reminders.missed",
                id=entry.id,
                kind=entry.kind,
                reason="retry_budget_exhausted",
                scheduled_for=entry.scheduled_for.isoformat(),
                fired_count=entry.fired_count,
            )

        prompt = self._fire_prompt.format(message=entry.message, kind=entry.kind)
        try:
            # BLOCK_BEHIND_COMMS: preempts content (audiobook pauses,
            # narrates, audiobook resumes via patience), queues behind
            # active calls (doesn't interrupt grandpa's phone call for
            # a medication reminder — call ends, narration drains).
            await self._inject_turn(prompt, priority=InjectPriority.BLOCK_BEHIND_COMMS)
        except Exception:
            # State already advanced. Don't roll back: rolling back
            # creates the double-fire risk this whole pattern exists
            # to prevent.
            await self._logger.aexception("reminders.fire_failed", id=entry.id)

        await self._logger.ainfo(
            "reminders.fired",
            id=entry.id,
            kind=entry.kind,
            fired_count=entry.fired_count,
            state=entry.state,
            message=entry.message,
        )

    # -------------------------------------------------------------- handlers

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if (
            self._logger is None
            or self._inject_turn is None
            or self._background_task is None
            or self._storage is None
        ):
            raise RuntimeError("RemindersSkill: handle() called before setup()")
        try:
            if tool_name == "add_reminder":
                return await self._handle_add(args)
            if tool_name == "list_reminders":
                return await self._handle_list()
            if tool_name == "cancel_reminder":
                return await self._handle_cancel(args)
            if tool_name == "snooze_reminder":
                return await self._handle_snooze(args)
            if tool_name == "acknowledge_reminder":
                return await self._handle_ack(args)
        except _SkillBadInputError as exc:
            await self._logger.awarning("reminders.bad_input", tool=tool_name, error=str(exc))
            return ToolResult(output=json.dumps({"error": str(exc)}))
        await self._logger.awarning("reminders.unknown_tool", tool=tool_name)
        return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def _handle_add(self, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        message = args.get("message")
        when_iso = args.get("when_iso")
        kind = args.get("kind", "generic")
        # Tool param is `recurrence_rule` (RFC 5545 string). We also
        # accept the legacy `recurrence` enum so an already-trained
        # session that emits `recurrence="daily"` keeps working — it
        # gets translated to the equivalent RRULE on the way in.
        recurrence_rule = args.get("recurrence_rule")
        legacy_recurrence = args.get("recurrence")
        if not isinstance(message, str) or not message:
            raise _SkillBadInputError("add_reminder requires a non-empty `message`")
        if not isinstance(when_iso, str) or not when_iso:
            raise _SkillBadInputError("add_reminder requires `when_iso` (ISO 8601 with offset)")
        if kind not in _VALID_KINDS:
            raise _SkillBadInputError(f"invalid kind: {kind!r}; must be one of {_VALID_KINDS}")
        if recurrence_rule is None and legacy_recurrence is not None:
            if legacy_recurrence not in _LEGACY_RECURRENCE_TO_RRULE:
                raise _SkillBadInputError(
                    f"invalid legacy recurrence: {legacy_recurrence!r}; "
                    "use `recurrence_rule` with an RFC 5545 RRULE string"
                )
            recurrence_rule = _LEGACY_RECURRENCE_TO_RRULE[legacy_recurrence]
        if recurrence_rule is not None:
            if not isinstance(recurrence_rule, str):
                raise _SkillBadInputError("recurrence_rule must be a string")
            err = _validate_rrule(recurrence_rule, self._tz)
            if err is not None:
                raise _SkillBadInputError(f"invalid recurrence_rule {recurrence_rule!r}: {err}")
        try:
            when = _parse_iso(when_iso)
        except ValueError as exc:
            raise _SkillBadInputError(f"could not parse when_iso: {exc}") from exc
        now = _utcnow()
        if when <= now:
            # Refusing to schedule reminders in the past keeps the LLM
            # honest about timezone math. The error message names the
            # received value so the LLM can self-correct.
            raise _SkillBadInputError(
                f"when_iso must be in the future; got {when.isoformat()} "
                f"(now is {now.isoformat()})"
            )
        new_id = await self._allocate_id()
        entry = _Entry(
            id=new_id,
            message=message,
            kind=kind,
            scheduled_for=when,
            next_fire_at=when,
            recurrence_rule=recurrence_rule,
            # First row in a (potentially) recurring series — series_start
            # is the user-requested time. None for one-shot keeps storage
            # tidy on the common case.
            series_start=when if recurrence_rule else None,
            state=_STATE_PENDING,
        )
        await self._save_entry(entry)
        # Wake the scheduler so a reminder added during a long sleep
        # gets picked up immediately.
        self._wakeup.set()
        await self._logger.ainfo(
            "reminders.added",
            id=new_id,
            kind=kind,
            recurrence_rule=recurrence_rule,
            scheduled_for=when.isoformat(),
            message=message,
        )
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "id": new_id,
                    "scheduled_for": when.isoformat(),
                    "kind": kind,
                    "recurrence_rule": recurrence_rule,
                }
            )
        )

    async def _handle_list(self) -> ToolResult:
        entries = await self._load_all_entries()
        # Show pending + recently-fired-medication (so the LLM can answer
        # "did you remind me about X?") + still-unsurfaced missed.
        active_states = (_STATE_PENDING, _STATE_FIRED, _STATE_MISSED)
        rows = [
            {
                "id": e.id,
                "message": e.message,
                "kind": e.kind,
                "scheduled_for": e.scheduled_for.isoformat(),
                "next_fire_at": e.next_fire_at.isoformat(),
                "recurrence_rule": e.recurrence_rule,
                "state": e.state,
                "fired_count": e.fired_count,
            }
            for e in sorted(entries, key=lambda x: x.next_fire_at)
            if e.state in active_states
        ]
        return ToolResult(output=json.dumps({"ok": True, "reminders": rows}))

    async def _handle_cancel(self, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        rid = self._coerce_id(args)
        entry = await self._load_entry(rid)
        if entry is None:
            raise _SkillBadInputError(f"no reminder with id {rid}")
        if entry.state in (_STATE_ACKED, _STATE_CANCELLED):
            return ToolResult(
                output=json.dumps(
                    {"ok": True, "id": rid, "state": entry.state, "note": "already terminal"}
                )
            )
        entry.state = _STATE_CANCELLED
        entry.cancelled_at = _utcnow()
        await self._save_entry(entry)
        self._wakeup.set()
        await self._logger.ainfo("reminders.cancelled", id=rid)
        return ToolResult(output=json.dumps({"ok": True, "id": rid}))

    async def _handle_snooze(self, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        rid = self._coerce_id(args)
        minutes = args.get("minutes")
        if not isinstance(minutes, int) or not (1 <= minutes <= 120):
            raise _SkillBadInputError("snooze_reminder requires `minutes` in [1, 120]")
        entry = await self._load_entry(rid)
        if entry is None:
            raise _SkillBadInputError(f"no reminder with id {rid}")
        if entry.state in (_STATE_ACKED, _STATE_CANCELLED):
            raise _SkillBadInputError(f"reminder {rid} is already terminal ({entry.state})")
        entry.next_fire_at = _utcnow() + timedelta(minutes=minutes)
        # A snooze on a `fired` medication resets retry: we explicitly
        # accepted "give me five more minutes" so don't keep escalating
        # until the snooze expires. If the snooze itself elapses without
        # ack, the medication ladder resumes at fired_count = 1.
        if entry.state == _STATE_FIRED:
            entry.state = _STATE_PENDING
        await self._save_entry(entry)
        self._wakeup.set()
        await self._logger.ainfo(
            "reminders.snoozed",
            id=rid,
            minutes=minutes,
            next_fire_at=entry.next_fire_at.isoformat(),
        )
        return ToolResult(
            output=json.dumps(
                {"ok": True, "id": rid, "next_fire_at": entry.next_fire_at.isoformat()}
            )
        )

    async def _handle_ack(self, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        rid = self._coerce_id(args)
        entry = await self._load_entry(rid)
        if entry is None:
            raise _SkillBadInputError(f"no reminder with id {rid}")
        already = entry.state in (_STATE_ACKED, _STATE_CANCELLED)
        if already:
            return ToolResult(
                output=json.dumps(
                    {"ok": True, "id": rid, "state": entry.state, "note": "already terminal"}
                )
            )
        entry.state = _STATE_ACKED
        entry.acked_at = _utcnow()
        await self._save_entry(entry)
        # Acks may close out a row that's mid-retry; wake the scheduler
        # so it doesn't sleep on a retry timer that's no longer needed.
        self._wakeup.set()
        await self._logger.ainfo("reminders.acked", id=rid, kind=entry.kind)
        if entry.recurrence_rule:
            await self._schedule_next_recurrence(entry)
        return ToolResult(output=json.dumps({"ok": True, "id": rid}))

    @staticmethod
    def _coerce_id(args: dict[str, Any]) -> int:
        raw = args.get("id")
        if not isinstance(raw, int):
            raise _SkillBadInputError("missing or invalid integer `id`")
        return raw

    # -------------------------------------------------------------- teardown

    async def teardown(self) -> None:
        assert self._logger is not None
        if self._scheduler_handle is not None:
            self._scheduler_handle.cancel()
            self._scheduler_handle = None
        # Storage rows are deliberately preserved — that's how persistence
        # across restart works. Pending rows resume on next setup().
        await self._logger.ainfo("reminders.teardown_complete")

    # --------------------------------------------------------- prompt_context

    def prompt_context(self) -> str:
        """Tell the LLM the current time + timezone, plus any missed reminders.

        Two purposes:

        1. **Time + tz** — the LLM needs both to convert "mañana a las 8"
           into a correct ISO offset for `add_reminder.when_iso`. Without
           this the LLM either guesses UTC (wrong) or refuses.
        2. **Missed surfacing** — any reminder still in `missed` state is
           listed; the LLM can mention it in the next exchange. We do
           NOT transition state here (sync method) — the ack handler
           transitions on actual ack, the scheduler handles aging.

        Empty-ish output (just time + tz) is fine — keeps the prompt
        from accumulating per-skill banner text on idle sessions.
        """
        # NOTE: `prompt_context` is sync by Skill protocol; we can't
        # await storage. We rely on `_missed_cache` populated by the
        # scheduler / handlers as they transition rows. This trades
        # exactness for protocol-compatibility — a missed reminder
        # added between handler returns and the next prompt_context
        # tick is missed by exactly that one tick, then surfaces.
        lines: list[str] = []
        bucket = _lang_bucket(self._language)
        now_local = _utcnow()  # framework's clock; LLM does the offset math
        if bucket == "es":
            lines.append(
                f"Hora actual (UTC): {now_local.isoformat(timespec='minutes')}. "
                f"Zona horaria del usuario: {self._timezone_label}."
            )
        elif bucket == "fr":
            lines.append(
                f"Heure actuelle (UTC) : {now_local.isoformat(timespec='minutes')}. "
                f"Fuseau horaire de l'utilisateur : {self._timezone_label}."
            )
        else:
            lines.append(
                f"Current time (UTC): {now_local.isoformat(timespec='minutes')}. "
                f"User timezone: {self._timezone_label}."
            )
        if self._missed_cache:
            if bucket == "es":
                lines.append(
                    "Recordatorios que se perdieron y aún no le has mencionado al usuario "
                    "(menciónaselos cuando sea natural, no de inmediato si está en otra cosa):"
                )
            elif bucket == "fr":
                lines.append(
                    "Rappels manqués que tu n'as pas encore mentionnés à l'utilisateur "
                    "(mentionne-les quand c'est naturel, pas tout de suite s'il fait "
                    "autre chose) :"
                )
            else:
                lines.append(
                    "Missed reminders not yet surfaced to the user "
                    "(mention them when it's natural — not immediately if they're "
                    "in the middle of something else):"
                )
            for entry in self._missed_cache:
                lines.append(
                    f"  - id={entry.id} kind={entry.kind} "
                    f"scheduled_for={entry.scheduled_for.isoformat(timespec='minutes')} "
                    f"message={entry.message!r}"
                )
        return "\n".join(lines)

    @property
    def _missed_cache(self) -> list[_Entry]:
        # Recomputing each call is fine; storage list is small (dozens
        # at most) and prompt_context is called once per turn build.
        return self._missed_cache_value

    # --------------------------------------------------------------- storage

    async def _allocate_id(self) -> int:
        """Atomically allocate a fresh id, defending against missing meta.

        Reads the `_meta:next_id` cursor; if absent or behind any
        existing row's id, scans every persisted row and seeds the
        cursor past the actual max. Belt-and-suspenders: setup() also
        primes the cursor early in `_reconcile_on_boot`, but a code
        path that calls `_schedule_next_recurrence` before that prime
        (or in a test that skips setup) would otherwise collide on id 1
        and overwrite the original row.

        Wrapped in `_id_lock` so concurrent callers
        (scheduler-side `_schedule_next_recurrence` racing a tool-
        handler `add_reminder`) serialize the read-then-write. The
        scheduler and tool handlers do run on the same event loop
        but each `await` inside this method is a yield point that
        could let the other path see stale `next_id` without the lock.
        """
        assert self._storage is not None
        async with self._id_lock:
            meta_key = f"{_STORAGE_PREFIX}{_STORAGE_META_NEXT_ID}"
            raw = await self._storage.get_setting(meta_key)
            try:
                current = int(raw) if raw is not None else 0
            except ValueError:
                current = 0
            if current <= 0:
                # Cursor missing or invalid — derive from existing rows.
                rows = await self._storage.list_settings(_STORAGE_PREFIX)
                max_existing = 0
                for key, _ in rows:
                    suffix = key.removeprefix(_STORAGE_PREFIX)
                    if suffix.isdigit():
                        max_existing = max(max_existing, int(suffix))
                current = max_existing + 1
            await self._storage.set_setting(meta_key, str(current + 1))
            return current

    async def _save_entry(self, entry: _Entry) -> None:
        assert self._storage is not None
        await self._storage.set_setting(f"{_STORAGE_PREFIX}{entry.id}", entry.to_json())
        # Update the missed cache snapshot synchronously so prompt_context
        # picks up state changes without an extra round trip.
        await self._refresh_missed_cache()

    async def _load_entry(self, rid: int) -> _Entry | None:
        assert self._storage is not None
        raw = await self._storage.get_setting(f"{_STORAGE_PREFIX}{rid}")
        if raw is None:
            return None
        try:
            return _Entry.from_json(raw)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    async def _load_all_entries(self) -> list[_Entry]:
        """Read every reminder row, skipping malformed and meta keys."""
        assert self._storage is not None
        assert self._logger is not None
        result: list[_Entry] = []
        rows = await self._storage.list_settings(_STORAGE_PREFIX)
        for key, value in rows:
            suffix = key.removeprefix(_STORAGE_PREFIX)
            if suffix.startswith("_meta:"):
                continue
            if not suffix.isdigit():
                await self._logger.awarning("reminders.bad_storage_key", key=key)
                continue
            try:
                result.append(_Entry.from_json(value))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                await self._logger.awarning("reminders.bad_storage_entry", key=key, error=str(exc))
                continue
        await self._refresh_missed_cache(_loaded=result)
        return result

    async def _next_due_entry(self) -> _Entry | None:
        """Soonest pending entry, or None if there are none."""
        candidates = [e for e in await self._load_all_entries() if e.state == _STATE_PENDING]
        if not candidates:
            return None
        return min(candidates, key=lambda e: e.next_fire_at)

    async def _refresh_missed_cache(self, *, _loaded: list[_Entry] | None = None) -> None:
        """Rebuild the missed-cache snapshot used by prompt_context.

        Pulled out so handlers and the scheduler can keep it fresh
        without prompt_context having to await storage on every call.
        """
        if _loaded is None:
            assert self._storage is not None
            rows = await self._storage.list_settings(_STORAGE_PREFIX)
            entries: list[_Entry] = []
            for key, value in rows:
                suffix = key.removeprefix(_STORAGE_PREFIX)
                if suffix.startswith("_meta:") or not suffix.isdigit():
                    continue
                try:
                    entries.append(_Entry.from_json(value))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    continue
        else:
            entries = _loaded
        missed = sorted(
            (e for e in entries if e.state == _STATE_MISSED),
            key=lambda e: e.scheduled_for,
        )
        self._missed_cache_value = missed


class _SkillBadInputError(Exception):
    """Raised by handlers to convert into a tool error envelope.

    Kept private — handlers convert to a JSON `{"error": ...}` payload
    inside `handle()`. Not part of the SDK surface.
    """
