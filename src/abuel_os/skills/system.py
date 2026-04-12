"""System skill — basic device control and information.

Provides tools for volume control, time queries, and other
system-level operations.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from abuel_os.types import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from abuel_os.media.mpv import MpvClient

logger = structlog.get_logger()


class SystemSkill:
    """Provides system-level tools."""

    def __init__(self, mpv: MpvClient) -> None:
        self._mpv = mpv

    @property
    def name(self) -> str:
        return "system"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="set_volume",
                description="Ajusta el volumen del dispositivo (0-100).",
                parameters={
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Nivel de volumen (0-100)",
                        }
                    },
                    "required": ["level"],
                },
            ),
            ToolDefinition(
                name="get_current_time",
                description="Devuelve la hora y fecha actual en Colombia.",
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "set_volume":
                level = max(0, min(100, args.get("level", 50)))
                await self._mpv.set_volume(level)
                return ToolResult(output=json.dumps({"volume": level, "ok": True}))
            case "get_current_time":
                # Colombia is UTC-5
                from zoneinfo import ZoneInfo

                now = datetime.now(tz=ZoneInfo("America/Bogota"))
                return ToolResult(
                    output=json.dumps(
                        {
                            "time": now.strftime("%I:%M %p"),
                            "date": now.strftime("%A %d de %B de %Y"),
                            "timezone": "America/Bogota",
                        }
                    )
                )
            case _:
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self) -> None:
        pass

    async def teardown(self) -> None:
        pass
