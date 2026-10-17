"""System skill — basic device control and information.

Provides tools for volume control, time queries and the device's battery
level. Volume control sends a
`set_volume` WebSocket command to the connected audio client — the client owns
its own speaker. This is deployment-agnostic: the server never touches audio
hardware directly.

Tool descriptions and date formatting flip per session language via
`reconfigure()`. Date/time output uses a tiny in-module locale table
(weekday + month names) rather than `babel` — three languages and a
couple of arrays is cheaper than a new dependency.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from huxley_sdk import (
    InjectPriority,
    SetVolume,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)

# --- Per-language tool descriptions -----------------------------------------

_TOOL_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "es": {
        "set_volume": (
            "Ajusta el volumen del dispositivo. Con un número dicho, `level` "
            "(0-100). Sin número ('súbele', 'más volumen', 'bájale'), `change` "
            "'up' o 'down': el sistema sube o baja 15 desde el nivel actual."
        ),
        "set_volume_level": "Nivel de volumen (0-100), solo si dijo un número",
        "set_volume_change": "'up' para subir, 'down' para bajar, sin número",
        "get_current_time": "Devuelve la hora y fecha actual.",
        "get_battery": (
            "Cuánta batería le queda al dispositivo, en porcentaje. Úsala cuando "
            "pregunten por la batería o la carga."
        ),
    },
    "en": {
        "set_volume": (
            "Adjust the device volume. With a number said, `level` (0-100). "
            "Without one ('turn it up', 'louder', 'turn it down'), `change` 'up' "
            "or 'down': the system moves it 15 from the current level."
        ),
        "set_volume_level": "Volume level (0-100), only when a number was said",
        "set_volume_change": "'up' to raise, 'down' to lower, without a number",
        "get_current_time": "Return the current time and date.",
        "get_battery": (
            "How much battery the device has left, as a percentage. Use it when "
            "asked about the battery or the charge."
        ),
    },
    "fr": {
        "set_volume": (
            "Ajuste le volume de l'appareil. Avec un nombre dit, `level` "
            "(0-100). Sans nombre ('monte', 'plus fort', 'baisse'), `change` 'up' "
            "ou 'down' : le système monte ou baisse de 15 depuis le niveau actuel."
        ),
        "set_volume_level": "Niveau de volume (0-100), seulement si un nombre a été dit",
        "set_volume_change": "'up' pour monter, 'down' pour baisser, sans nombre",
        "get_current_time": "Renvoie l'heure et la date actuelles.",
        "get_battery": (
            "Combien de batterie il reste à l'appareil, en pourcentage. À utiliser "
            "quand on demande la batterie ou la charge."
        ),
    },
}


# --- Battery (F-0025) --------------------------------------------------------

# The device reports `client_event system.battery {mv, pct}` on a change of
# percentage and every 5 min. Crossing these speaks once each, on the way
# down; charging back above BATTERY_REARM_PCT arms them again.
BATTERY_WARN_PCTS = (20, 10)
# "Súbele" without a number: 15 up or down from the last level, never below
# 20 (he couldn't hear it); with no level known yet, 80 up or 40 down.
VOLUME_STEP = 15
VOLUME_FLOOR = 20
VOLUME_UNKNOWN_UP = 80
VOLUME_UNKNOWN_DOWN = 40
BATTERY_REARM_PCT = 30
# Older than this, the tool says how old (the device reports every 5 min).
BATTERY_STALE_S = 15 * 60

# inject_turn prompts are instructions to the model: say what to tell the
# user, or it treats them as a silent note.
_BATTERY_WARNING: dict[str, str] = {
    "es": (
        "La batería del dispositivo bajó al {pct} por ciento. Díselo al usuario "
        "con calma, en una frase, y sugiérele que lo pongan a cargar."
    ),
    "en": (
        "The device's battery is down to {pct} percent. Tell the user calmly, "
        "in one sentence, and suggest they put it on to charge."
    ),
    "fr": (
        "La batterie de l'appareil est descendue à {pct} pour cent. Dis-le "
        "calmement à l'utilisateur, en une phrase, et suggère de le mettre en charge."
    ),
}


# --- Per-language locale tables for date formatting ------------------------

# Weekday names (Monday=0 .. Sunday=6) — used to format the date in the
# user's language without pulling in `babel`. Lowercased because the
# persona can re-capitalize if needed.
_WEEKDAYS: dict[str, list[str]] = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}

# Month names (Jan=1..Dec=12). Index 0 unused so month int maps directly.
_MONTHS: dict[str, list[str]] = {
    "es": [
        "",
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ],
    "en": [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "fr": [
        "",
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
}


def _bucket(language: str) -> str:
    """Collapse a language code to an entry we have translations for.

    Falls back to English rather than guessing — a stray regional
    variant ("es-CO") still resolves to "es" here.
    """
    code = language.lower()
    for key in ("es", "en", "fr"):
        if code.startswith(key):
            return key
    return "en"


def _format_date(now: datetime, language: str) -> str:
    """Compose "Weekday DD Month YYYY" in the given language.

    Kept in one place so both the tool output and any future
    diagnostics share a definition. Falls back to English when the
    language isn't in the locale table.
    """
    bucket = _bucket(language)
    weekday = _WEEKDAYS[bucket][now.weekday()]
    month = _MONTHS[bucket][now.month]
    if bucket in ("es", "fr"):
        # "lunes 24 de abril de 2026" / "lundi 24 avril 2026"
        # Spanish uses "de"; French doesn't.
        joiner = " de " if bucket == "es" else " "
        year_joiner = " de " if bucket == "es" else " "
        return f"{weekday} {now.day}{joiner}{month}{year_joiner}{now.year}"
    # English: "Monday, April 24, 2026"
    return f"{weekday}, {month} {now.day}, {now.year}"


class SystemSkill:
    """Provides system-level tools."""

    # The simplest first-party config_schema — system has exactly one
    # user-tunable field (`timezone`). v2's PWA can render this as a
    # plain text field with `x-huxley:help` for the IANA-zone hint.
    config_schema: ClassVar[dict[str, Any] | None] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "title": "Time zone",
                "default": "America/Bogota",
                "x-huxley:help": (
                    "IANA Time Zone Database name (e.g. `America/Bogota`, "
                    "`Europe/Madrid`). Defaults to America/Bogota if unset; "
                    "the `get_time` tool's date string formats against this "
                    "zone."
                ),
            }
        },
    }

    # No persisted state today (volume + time are both stateless
    # operations). Kept at 1 so a future feature that persists, e.g.,
    # a last-set-volume can bump.
    data_schema_version: ClassVar[int] = 1

    def __init__(self) -> None:
        self._logger: SkillLogger | None = None
        self._timezone: str = "America/Bogota"
        # Seeded at setup / reconfigure from `ctx.language`. Drives both
        # tool descriptions and the date-string locale.
        self._language: str = "en"
        self._ctx: SkillContext | None = None
        # The device's last reading: (pct, mv, time.monotonic()).
        self._battery: tuple[int, int, float] | None = None
        # The lowest warning spoken since the cell was last charged.
        self._battery_warned: int | None = None
        self._volume: int | None = None  # the last level set (storage "volume")
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def name(self) -> str:
        return "system"

    def _descriptions(self) -> dict[str, str]:
        return _TOOL_DESCRIPTIONS.get(_bucket(self._language), _TOOL_DESCRIPTIONS["en"])

    @property
    def tools(self) -> list[ToolDefinition]:
        d = self._descriptions()
        return [
            ToolDefinition(
                name="set_volume",
                description=d["set_volume"],
                parameters={
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": d["set_volume_level"],
                        },
                        "change": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": d["set_volume_change"],
                        },
                    },
                },
            ),
            ToolDefinition(
                name="get_current_time",
                description=d["get_current_time"],
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="get_battery",
                description=d["get_battery"],
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if self._logger is None:
            raise RuntimeError("SystemSkill: handle() called before setup()")
        match tool_name:
            case "set_volume":
                return await self._set_volume(args)
            case "get_current_time":
                now = datetime.now(tz=ZoneInfo(self._timezone))
                await self._logger.ainfo(
                    "system.time_query", time=now.isoformat(timespec="seconds")
                )
                return ToolResult(
                    output=json.dumps(
                        {
                            "time": now.strftime("%I:%M %p"),
                            "date": _format_date(now, self._language),
                            "timezone": self._timezone,
                            "language": _bucket(self._language),
                        },
                        ensure_ascii=False,
                    )
                )
            case "get_battery":
                return await self._get_battery()
            case _:
                await self._logger.awarning("system.unknown_tool", tool=tool_name)
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def _set_volume(self, args: dict[str, Any]) -> ToolResult:
        """A level said, or a step up or down from the last one set. The
        model kept the level itself and got it wrong: "more volume" twice
        set 80 twice ("sigo con el mismo nivel", 2026-10-17). The last level
        is kept in storage, so a server restart doesn't lose it."""
        assert self._logger is not None
        was = self._volume
        change = args.get("change")
        if change in ("up", "down") and args.get("level") is None:
            if was is None:
                level = VOLUME_UNKNOWN_UP if change == "up" else VOLUME_UNKNOWN_DOWN
            elif change == "up":
                level = min(100, was + VOLUME_STEP)
            else:
                level = max(VOLUME_FLOOR, was - VOLUME_STEP)
        else:
            raw = args.get("level", 50)
            level = max(0, min(100, raw if isinstance(raw, int) else 50))
        self._volume = level
        if self._ctx is not None:
            await self._ctx.storage.set_setting("volume", str(level))
        await self._logger.ainfo("system.volume_set", level=level, change=change, was=was)
        return ToolResult(
            output=json.dumps({"volume": level, "was": was, "ok": True}),
            side_effect=SetVolume(level=level),
        )

    async def _get_battery(self) -> ToolResult:
        assert self._logger is not None
        if self._battery is None:
            await self._logger.ainfo("system.battery_query", known=False)
            return ToolResult(
                output=json.dumps(
                    {"known": False, "why": "the device hasn't reported its battery yet"}
                )
            )
        pct, mv, at = self._battery
        age_s = int(time.monotonic() - at)
        await self._logger.ainfo("system.battery_query", known=True, pct=pct, age_s=age_s)
        out: dict[str, Any] = {"known": True, "percent": pct}
        if age_s > BATTERY_STALE_S:
            out["minutes_since_reading"] = age_s // 60
        return ToolResult(output=json.dumps(out))

    async def _on_battery(self, data: dict[str, Any]) -> None:
        """`client_event system.battery {mv, pct}` from the device."""
        assert self._logger is not None
        pct, mv = data.get("pct"), data.get("mv")
        if not isinstance(pct, int) or not isinstance(mv, int) or not 0 <= pct <= 100:
            await self._logger.awarning("system.battery_bad", data=data)
            return
        self._battery = (pct, mv, time.monotonic())
        if self._battery_warned is not None and pct >= BATTERY_REARM_PCT:
            self._battery_warned = None  # charged: the warnings apply again
        crossed = [t for t in BATTERY_WARN_PCTS if pct <= t]
        warn = min(crossed) if crossed else None
        speak = warn is not None and (self._battery_warned is None or warn < self._battery_warned)
        await self._logger.ainfo(
            "system.battery",
            pct=pct,
            mv=mv,
            warn=warn if speak else None,
            warned=self._battery_warned,
        )
        if speak and self._ctx is not None:
            self._battery_warned = warn
            prompt = _BATTERY_WARNING.get(_bucket(self._language), _BATTERY_WARNING["en"])
            # Its own task: this handler runs inside the server's receive
            # loop, which must not wait on a turn.
            task = asyncio.create_task(
                self._ctx.inject_turn(
                    prompt.format(pct=pct),
                    dedup_key="system.battery_low",
                    priority=InjectPriority.NORMAL,
                ),
                name="system.battery_warning",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._ctx = ctx
        stored = await ctx.storage.get_setting("volume")
        self._volume = int(stored) if stored and stored.isdigit() else None
        ctx.subscribe_client_event("system.battery", self._on_battery)
        tz_value = ctx.config.get("timezone")
        if isinstance(tz_value, str) and tz_value:
            self._timezone = tz_value
        self._language = ctx.language or "en"

    async def reconfigure(self, ctx: SkillContext) -> None:
        self._language = ctx.language or self._language
        await ctx.logger.ainfo("system.reconfigure", language=self._language)

    async def teardown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
