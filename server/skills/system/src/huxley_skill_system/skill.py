"""System skill — basic device control and information.

Provides tools for volume control and time queries. Volume control sends a
`set_volume` WebSocket command to the connected audio client — the client owns
its own speaker. This is deployment-agnostic: the server never touches audio
hardware directly.

Tool descriptions and date formatting flip per session language via
`reconfigure()`. Date/time output uses a tiny in-module locale table
(weekday + month names) rather than `babel` — three languages and a
couple of arrays is cheaper than a new dependency.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from huxley_sdk import SetVolume, SkillContext, SkillLogger, ToolDefinition, ToolResult

# --- Per-language tool descriptions -----------------------------------------

_TOOL_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "es": {
        "set_volume": "Ajusta el volumen del dispositivo (0-100).",
        "set_volume_level": "Nivel de volumen (0-100)",
        "get_current_time": "Devuelve la hora y fecha actual.",
    },
    "en": {
        "set_volume": "Adjust the device volume (0-100).",
        "set_volume_level": "Volume level (0-100)",
        "get_current_time": "Return the current time and date.",
    },
    "fr": {
        "set_volume": "Ajuste le volume de l'appareil (0-100).",
        "set_volume_level": "Niveau de volume (0-100)",
        "get_current_time": "Renvoie l'heure et la date actuelles.",
    },
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
                        }
                    },
                    "required": ["level"],
                },
            ),
            ToolDefinition(
                name="get_current_time",
                description=d["get_current_time"],
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if self._logger is None:
            raise RuntimeError("SystemSkill: handle() called before setup()")
        match tool_name:
            case "set_volume":
                level = max(0, min(100, args.get("level", 50)))
                await self._logger.ainfo("system.volume_set", level=level)
                return ToolResult(
                    output=json.dumps({"volume": level, "ok": True}),
                    side_effect=SetVolume(level=level),
                )
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
            case _:
                await self._logger.awarning("system.unknown_tool", tool=tool_name)
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        tz_value = ctx.config.get("timezone")
        if isinstance(tz_value, str) and tz_value:
            self._timezone = tz_value
        self._language = ctx.language or "en"

    async def reconfigure(self, ctx: SkillContext) -> None:
        self._language = ctx.language or self._language
        await ctx.logger.ainfo("system.reconfigure", language=self._language)

    async def teardown(self) -> None:
        pass
