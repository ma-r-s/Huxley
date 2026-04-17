"""System skill — basic device control and information.

Provides tools for volume control and time queries. Volume control
adjusts the server host's output (valid for localhost browser dev);
ESP32 will need its own volume command in the protocol.
"""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
from datetime import datetime
from typing import Any

import structlog

from huxley_sdk import SkillContext, ToolDefinition, ToolResult

logger = structlog.get_logger()


class SystemSkill:
    """Provides system-level tools."""

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
                await _set_system_volume(level)
                await logger.ainfo("system.volume_set", level=level)
                return ToolResult(output=json.dumps({"volume": level, "ok": True}))
            case "get_current_time":
                from zoneinfo import ZoneInfo

                now = datetime.now(tz=ZoneInfo("America/Bogota"))
                await logger.ainfo("system.time_query", time=now.isoformat(timespec="seconds"))
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
                await logger.awarning("system.unknown_tool", tool=tool_name)
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self, ctx: SkillContext) -> None:
        del ctx  # accepted for protocol compliance; unused

    async def teardown(self) -> None:
        pass


async def _set_system_volume(level: int) -> None:
    """Set OS output volume (0-100). Best-effort — logs on failure."""
    loop = asyncio.get_running_loop()
    try:
        if platform.system() == "Darwin":
            cmd = ["osascript", "-e", f"set volume output volume {level}"]
        else:
            cmd = ["amixer", "-D", "pulse", "sset", "Master", f"{level}%"]
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, check=True, capture_output=True),  # noqa: ASYNC221
        )
    except Exception:
        logger.warning("system.volume_failed", level=level)
